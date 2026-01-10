import uuid
import os
import requests
import re
import io      
import qrcode
import base64
import difflib
import time
from datetime import datetime
from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app, jsonify, send_file, session
from flask_login import login_user, login_required, logout_user, current_user
from werkzeug.utils import secure_filename
from extensions import db
from models import User, Role, Location, MediaItem, Collection, Track, AppSetting
from sqlalchemy import or_
from backup_utils import create_backup_zip, restore_backup_zip

main = Blueprint('main', __name__)

# -- HELPER --

def get_config_value(key, default=None):
    try:
        setting = AppSetting.query.filter_by(key=key).first()
        if setting and setting.value: return setting.value
    except: pass
    return default

def set_config_value(key, value):
    setting = AppSetting.query.filter_by(key=key).first()
    if not setting:
        setting = AppSetting(key=key)
        db.session.add(setting)
    setting.value = value
    db.session.commit()

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in {'png', 'jpg', 'jpeg', 'gif'}

def save_image(file):
    if file and allowed_file(file.filename):
        ext = file.filename.rsplit('.', 1)[1].lower()
        new_filename = f"{uuid.uuid4().hex}.{ext}"
        path = os.path.join(current_app.config['UPLOAD_FOLDER'], new_filename)
        file.save(path)
        return new_filename
    return None

def download_remote_image(url):
    # Einfacher Check: Amazon liefert manchmal 1x1 Pixel Gifs als "nicht gefunden"
    # Wir laden erst, wenn wir sicher sind, dass es Bilddaten sind.
    print(f"DEBUG: Starte Download von {url}")
    try:
        headers = {'User-Agent': 'HomeInventoryApp/1.0'}
        response = requests.get(url, headers=headers, stream=True, timeout=10)
        
        if response.status_code == 200:
            # Check Content-Length (Amazon 1x1 Pixel ist ca 43 bytes)
            if len(response.content) < 100:
                print("DEBUG: Bild zu klein (wahrscheinlich Platzhalter), verwerfe.")
                return None

            ext = 'jpg'
            if 'png' in url.lower(): ext = 'png'
            new_filename = f"{uuid.uuid4().hex}.{ext}"
            path = os.path.join(current_app.config['UPLOAD_FOLDER'], new_filename)
            with open(path, 'wb') as f:
                f.write(response.content)
            return new_filename
    except Exception as e:
        print(f"Download Error: {e}")
    return None

def get_spotify_access_token():
    client_id = get_config_value('spotify_client_id')
    client_secret = get_config_value('spotify_client_secret')
    
    if not client_id or not client_secret:
        print(f"DEBUG: Spotify Credentials missing in DB. ID set: {bool(client_id)}, Secret set: {bool(client_secret)}")
        return None

    # Check if we have a valid token
    token = get_config_value('spotify_access_token')
    expiry = get_config_value('spotify_token_expiry')
    
    if token and expiry:
        try:
            # Check if token is still valid (with 60s buffer)
            if float(expiry) > time.time():
                return token
        except: pass
    
    # Request new token
    try:
        auth_str = f"{client_id}:{client_secret}"
        b64_auth = base64.b64encode(auth_str.encode()).decode()
        headers = {'Authorization': f'Basic {b64_auth}'}
        data = {'grant_type': 'client_credentials'}
        
        res = requests.post('https://accounts.spotify.com/api/token', headers=headers, data=data, timeout=5)
        if res.status_code == 200:
            js = res.json()
            new_token = js.get('access_token')
            expires_in = js.get('expires_in', 3600)
            set_config_value('spotify_access_token', new_token)
            set_config_value('spotify_token_expiry', str(time.time() + expires_in - 60))
            return new_token
        else:
            print(f"DEBUG: Spotify Auth Failed. Status: {res.status_code}, Response: {res.text}")
    except Exception as e:
        print(f"Spotify Auth Error: {e}")
    return None

def create_initial_data():
    if not Role.query.first():
        db.session.add(Role(name='Admin'))
        db.session.add(Role(name='User'))
        db.session.commit()
    if not User.query.first():
        r = Role.query.filter_by(name='Admin').first()
        u = User(username='admin', role=r)
        u.set_password('admin123')
        db.session.add(u)
        db.session.commit()
    if not Location.query.first():
        db.session.add(Location(name="Unsortiert"))
        db.session.commit()

def generate_inventory_number():
    year = datetime.now().year
    unique = str(uuid.uuid4())[:8].upper()
    return f"INV-{year}-{unique}"

# -- API: DISCOGS TEXT SUCHE --
@main.route('/api/search_discogs')
@login_required
def api_search_discogs():
    artist = request.args.get('artist', '').strip()
    title = request.args.get('title', '').strip()
    
    discogs_token = get_config_value('discogs_token')
    if not discogs_token:
        return jsonify({"success": False, "message": "Kein Token."})

    data = {"success": False, "images": [], "tracks": [], "year": "", "category": ""}

    try:
        headers = {"User-Agent": "HomeInventoryApp/1.0", "Authorization": f"Discogs token={discogs_token}"}
        url = "https://api.discogs.com/database/search"
        params = {"artist": artist, "release_title": title, "type": "release", "per_page": 1}
        
        res = requests.get(url, headers=headers, params=params, timeout=5)
        if res.status_code == 200 and res.json().get("results"):
            item = res.json()["results"][0]
            data["success"] = True
            data["year"] = item.get("year", "")
            
            formats = item.get("format", [])
            if "Vinyl" in formats: data["category"] = "Vinyl/LP"
            elif "CD" in formats: data["category"] = "CD"
            
            resource_url = item.get("resource_url")
            if resource_url:
                det_res = requests.get(resource_url, headers=headers, timeout=5)
                if det_res.status_code == 200:
                    det = det_res.json()
                    
                    if "images" in det:
                        data["images"] = [img.get("uri", "") for img in det["images"] if img.get("uri")]
                    
                    if not data["images"]:
                        thumb = item.get("cover_image") or item.get("thumb")
                        if thumb: data["images"].append(thumb)

                    for t in det.get("tracklist", []):
                        if t.get("type_") == "heading": continue
                        data["tracks"].append({
                            "position": t.get("position", ""),
                            "title": t.get("title", ""),
                            "duration": t.get("duration", "")
                        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})

    return jsonify(data)

# -- API: SPOTIFY SUCHE --
@main.route('/api/spotify/search')
@login_required
def api_spotify_search():
    artist = request.args.get('artist', '').strip()
    title = request.args.get('title', '').strip()
    
    if not artist or not title:
        return jsonify({"success": False, "message": "Missing artist or title"})
        
    token = get_spotify_access_token()
    if not token:
        print("DEBUG: Spotify Token missing")
        return jsonify({"success": False, "message": "Spotify not configured or auth failed"})
        
    try:
        headers = {"Authorization": f"Bearer {token}"}
        
        def do_search(q_param):
            # market=DE verbessert Trefferquote für deutsche Nutzer und filtert nicht abspielbare Inhalte
            print(f"DEBUG: Spotify Search Q='{q_param}'")
            return requests.get("https://api.spotify.com/v1/search", 
                                headers=headers, 
                                params={"q": q_param, "type": "album", "limit": 5, "market": "DE"}, 
                                timeout=5)

        def find_best_match(items):
            if not items: return None
            target_artist = artist.lower()
            target_title = title.lower()
            
            for item in items:
                # 1. Artist Check
                sp_artists = [a.get('name', '').lower() for a in item.get('artists', [])]
                artist_match = False
                for sp_a in sp_artists:
                    # Exakter Substring oder hohe Ähnlichkeit (Ratio > 0.6)
                    if target_artist in sp_a or sp_a in target_artist:
                        artist_match = True
                        break
                    if difflib.SequenceMatcher(None, target_artist, sp_a).ratio() > 0.6:
                        artist_match = True
                        break
                
                if not artist_match: continue

                # 2. Title Check
                sp_album = item.get('name', '').lower()
                if target_title in sp_album or sp_album in target_title:
                    return item.get('id')
                if difflib.SequenceMatcher(None, target_title, sp_album).ratio() > 0.6:
                    return item.get('id')
            
            return None

        # Versuch 1: Strikt mit Feldern
        res = do_search(f'artist:"{artist}" album:"{title}"')
        if res.status_code == 200:
            items = res.json().get("albums", {}).get("items", [])
            match_id = find_best_match(items)
            if match_id:
                return jsonify({"success": True, "spotify_id": match_id})
        
        # Versuch 2: Locker (Einfach String concat) - findet auch "Remastered" etc.
        res = do_search(f"{artist} {title}")
        if res.status_code == 200:
            items = res.json().get("albums", {}).get("items", [])
            match_id = find_best_match(items)
            if match_id:
                return jsonify({"success": True, "spotify_id": match_id})

    except Exception as e:
        print(f"Spotify Search Error: {e}")
        
    return jsonify({"success": False, "message": "Not found"})

# -- API: BARCODE LOOKUP (Mit Amazon Fallback) --
@main.route('/api/lookup/<barcode>')
@login_required
def api_lookup(barcode):
    data = { "success": False, "title": "", "author": "", "year": "", "description": "", "image_url": "", "category": "", "tracks": [] }
    clean_isbn = ''.join(c for c in barcode if c.isdigit() or c.upper() == 'X')
    
    # 1. Google Books
    try:
        res = requests.get(f"https://www.googleapis.com/books/v1/volumes?q=isbn:{clean_isbn}", timeout=5)
        if res.status_code == 200:
            g = res.json()
            if "items" in g and len(g["items"]) > 0:
                info = g["items"][0].get("volumeInfo", {})
                data.update({
                    "success": True, "title": info.get("title", ""), 
                    "author": ", ".join(info.get("authors", [])),
                    "description": info.get("description", "")[:800], "category": "Buch",
                    "year": info.get("publishedDate", "")[:4] if len(info.get("publishedDate", ""))>=4 else ""
                })
                imgs = info.get("imageLinks", {})
                if imgs.get("thumbnail"): data["image_url"] = imgs.get("thumbnail").replace("http://", "https://")
    except: pass

    # 2. Open Library
    if not data["success"] or not data["image_url"]:
        try:
            res = requests.get(f"https://openlibrary.org/api/books?bibkeys=ISBN:{clean_isbn}&format=json&jscmd=data", timeout=5)
            if res.status_code == 200 and res.json():
                bk = list(res.json().values())[0]
                if not data["success"]: # Metadaten nur übernehmen wenn Google nichts hatte
                    data.update({
                        "success": True, "title": bk.get("title", ""), 
                        "author": ", ".join([a["name"] for a in bk.get("authors", [])]),
                        "category": "Buch"
                    })
                    match = re.search(r'\d{4}', bk.get("publish_date", ""))
                    if match: data["year"] = match.group(0)

                # Bild holen
                if "cover" in bk: data["image_url"] = bk["cover"].get("large", "")
        except: pass

    # 3. Amazon Direct Image Fallback (NEU!)
    # Funktioniert oft für deutsche Bücher, wenn Google/OpenLibrary kein Cover haben
    if data["success"] and not data["image_url"]:
        try:
            # Amazon Bild-URL Muster
            amazon_url = f"https://images-na.ssl-images-amazon.com/images/P/{clean_isbn}.01.LZZZZZZZ.jpg"
            
            # Wir machen einen HEAD request um zu prüfen, ob das Bild existiert
            # und größer als 1x1 Pixel ist (Amazon liefert 43 bytes für "nicht gefunden")
            check = requests.get(amazon_url, timeout=3)
            if check.status_code == 200 and len(check.content) > 100:
                print(f"DEBUG: Amazon Cover gefunden für {clean_isbn}")
                data["image_url"] = amazon_url
        except Exception as e:
            print(f"DEBUG: Amazon Fallback Fehler: {e}")

    # 4. Discogs (für Musik)
    token = get_config_value('discogs_token')
    if token and (not data["success"] or data["category"] == ""): 
        try:
            h = {"User-Agent": "HomeInventoryApp/1.0", "Authorization": f"Discogs token={token}"}
            res = requests.get("https://api.discogs.com/database/search", headers=h, params={"barcode": barcode, "type": "release", "per_page": 1}, timeout=5)
            if res.status_code == 200 and res.json().get("results"):
                it = res.json()["results"][0]
                data["success"] = True
                
                # Titel/Autor Trennung bei Discogs oft "Artist - Title"
                full_title = it.get("title", "")
                if " - " in full_title and not data["author"]:
                    parts = full_title.split(" - ", 1)
                    data["author"] = parts[0].strip()
                    data["title"] = parts[1].strip()
                elif not data["title"]:
                    data["title"] = full_title
                
                if not data["year"]: data["year"] = it.get("year", "")
                
                # Wenn wir noch kein Bild haben (oder Amazon/Google fehlschlug)
                if not data["image_url"]:
                    data["image_url"] = it.get("cover_image", "")
                
                # Kategorie ermitteln
                fmts = it.get("format", [])
                if "Vinyl" in fmts: data["category"] = "Vinyl/LP"
                elif "CD" in fmts: data["category"] = "CD"
                elif "DVD" in fmts: data["category"] = "Film (DVD/BluRay)"

                # Tracks laden
                if it.get("resource_url"):
                    det = requests.get(it["resource_url"], headers=h, timeout=5).json()
                    for t in det.get("tracklist", []):
                        if t.get("type_") != "heading":
                            data["tracks"].append({"position": t.get("position"), "title": t.get("title"), "duration": t.get("duration")})
        except: pass

    return jsonify(data)

# -- QR Code --
@main.route('/qrcode_image/<inventory_number>')
def qrcode_image(inventory_number):
    qr = qrcode.QRCode(box_size=10, border=4)
    qr.add_data(inventory_number)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    fp = io.BytesIO()
    img.save(fp, 'PNG')
    fp.seek(0)
    return send_file(fp, mimetype='image/png')

# -- ROUTEN --

@main.route('/')
def index():
    if not current_user.is_authenticated: return redirect(url_for('main.login'))

    # 1. RESET
    if request.args.get('reset'):
        session.pop('filter_state', None)
        return redirect(url_for('main.index'))

    # 2. SESSION STATE MANAGEMENT
    # 'limit' auch speichern
    params = ['q', 'category', 'location', 'lent', 'sort_field', 'sort_order', 'limit']
    
    # Prüfen, ob neue Parameter in der URL sind
    active_args = {k: request.args.get(k) for k in params if request.args.get(k) is not None}

    if active_args:
        # Neuer Filter aktiv -> Speichern
        session['filter_state'] = active_args
    elif 'filter_state' in session and not request.args:
        # Keine URL-Params, aber Session vorhanden -> Restore
        return redirect(url_for('main.index', **session['filter_state']))

    # 3. WERTE AUSLESEN (Defaults setzen)
    q_str = request.args.get('q', '')
    cat = request.args.get('category', '')
    loc = request.args.get('location', '')
    lent = request.args.get('lent', '') # ''=Alle, 'yes'=Verliehen, 'no'=Verfügbar
    limit = request.args.get('limit', '20')
    
    # Seite auslesen (nicht in Session speichern, da man sonst immer auf Seite X landet)
    page = request.args.get('page', 1, type=int)
    
    sort_field = request.args.get('sort_field', 'added') # default: Hinzugefügt
    sort_order = request.args.get('sort_order', 'desc')  # default: Absteigend

    query = MediaItem.query

    # -- FILTERING --
    if q_str:
        s = f"%{q_str}%"
        query = query.filter(or_(
            MediaItem.title.ilike(s),
            MediaItem.author_artist.ilike(s),
            MediaItem.inventory_number.ilike(s),
            MediaItem.barcode.ilike(s),
            MediaItem.lent_to.ilike(s), # Auch nach Entleiher suchen!
            MediaItem.tracks.any(Track.title.ilike(s)) # Suche in Tracks
        ))
    
    if cat: 
        query = query.filter(MediaItem.category == cat)
    
    if loc: 
        query = query.filter(MediaItem.location_id == int(loc))

    # Verleih-Status Filter
    if lent == 'yes':
        query = query.filter(MediaItem.lent_to != None)
    elif lent == 'no':
        query = query.filter(MediaItem.lent_to == None)

    # -- FLEXIBLE SORTIERUNG MIT KASKADIERUNG --
    primary_sort = None
    secondary_sorts = []

    if sort_field == 'title':
        primary_sort = MediaItem.title
        secondary_sorts = [MediaItem.author_artist.asc()] 
    elif sort_field == 'author':
        primary_sort = MediaItem.author_artist
        secondary_sorts = [MediaItem.release_year.desc(), MediaItem.title.asc()]
    elif sort_field == 'year':
        primary_sort = MediaItem.release_year
        secondary_sorts = [MediaItem.author_artist.asc(), MediaItem.title.asc()]
    else: # 'added' oder Fallback
        primary_sort = MediaItem.id
        secondary_sorts = []

    # Richtung anwenden
    if sort_order == 'asc':
        query = query.order_by(primary_sort.asc(), *secondary_sorts)
    else:
        query = query.order_by(primary_sort.desc(), *secondary_sorts)

    # -- PAGINATION LOGIC --
    items = []
    pagination = None
    
    if limit == 'all':
        items = query.all()
    else:
        try:
            per_page = int(limit)
        except ValueError:
            per_page = 20
        
        # SQLAlchemy Pagination nutzen
        pagination = query.paginate(page=page, per_page=per_page, error_out=False)
        items = pagination.items

    locations = sorted(Location.query.all(), key=lambda x: x.full_path)
    categories = ["Buch", "Film (DVD/BluRay)", "CD", "Vinyl/LP", "Videospiel", "Sonstiges"]

    # Filter-Status für Template
    current_filters = {
        'q': q_str, 'category': cat, 'location': loc, 'lent': lent,
        'sort_field': sort_field, 'sort_order': sort_order, 'limit': limit
    }
    # Helper: Checken ob Filter aktiv sind (für den Reset Button)
    filter_active = any(x for x in [q_str, cat, loc, lent] if x) or sort_field != 'added' or limit != '20'

    return render_template('index.html', 
                           items=items, 
                           locations=locations, 
                           categories=categories, 
                           filters=current_filters,
                           filter_active=filter_active,
                           pagination=pagination) # Pagination Objekt übergeben

@main.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated: return redirect(url_for('main.index'))
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form.get('username')).first()
        if user and user.check_password(request.form.get('password')):
            login_user(user)
            return redirect(url_for('main.index'))
        flash('Login fehlgeschlagen.', 'error')
    return render_template('login.html')

@main.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('main.login'))

@main.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    if not current_user.has_role('Admin'): return redirect(url_for('main.index'))
    if request.method == 'POST':
        set_config_value('discogs_token', request.form.get('discogs_token', '').strip())
        set_config_value('spotify_client_id', request.form.get('spotify_client_id', '').strip())
        set_config_value('spotify_client_secret', request.form.get('spotify_client_secret', '').strip())
        flash('Einstellungen gespeichert.', 'success')
        return redirect(url_for('main.settings'))
    return render_template('settings.html', 
                           discogs_token=get_config_value('discogs_token', ''),
                           spotify_client_id=get_config_value('spotify_client_id', ''),
                           spotify_client_secret=get_config_value('spotify_client_secret', ''))

@main.route('/profile/change_password', methods=['GET', 'POST'])
@login_required
def change_password():
    if request.method == 'POST':
        curr = request.form.get('current_password')
        new = request.form.get('new_password')
        conf = request.form.get('confirm_password')
        if not current_user.check_password(curr): flash('Passwort falsch.', 'error')
        elif new != conf: flash('Passwörter ungleich.', 'error')
        else:
            current_user.set_password(new)
            db.session.commit()
            flash('Gespeichert.', 'success')
            return redirect(url_for('main.index'))
    return render_template('change_password.html')

# -- ADMIN BACKUP --
@main.route('/admin/backup')
@login_required
def admin_backup():
    if not current_user.has_role('Admin'): return redirect(url_for('main.index'))
    return render_template('admin_backup.html')

@main.route('/admin/backup/download')
@login_required
def admin_backup_download():
    if not current_user.has_role('Admin'): return redirect(url_for('main.index'))
    try:
        path, fname = create_backup_zip()
        return send_file(path, as_attachment=True, download_name=fname)
    except Exception as e:
        flash(f'Error: {e}', 'error')
        return redirect(url_for('main.admin_backup'))

@main.route('/admin/restore', methods=['POST'])
@login_required
def admin_restore():
    if not current_user.has_role('Admin'): return redirect(url_for('main.index'))
    f = request.files.get('backup_file')
    if f and f.filename.endswith('.zip'):
        if not os.path.exists(current_app.instance_path): os.makedirs(current_app.instance_path)
        p = os.path.join(current_app.instance_path, 'restore.zip')
        f.save(p)
        try:
            restore_backup_zip(p)
            flash('Restore erfolgreich. Bitte neu einloggen.', 'success')
            if os.path.exists(p): os.remove(p)
            return redirect(url_for('main.index'))
        except Exception as e:
            flash(f'Error: {e}', 'error')
            return redirect(url_for('main.admin_backup'))
    flash('Ungültige Datei.', 'error')
    return redirect(url_for('main.admin_backup'))

# -- MEDIA --
@main.route('/media/<int:item_id>')
@login_required
def media_detail(item_id):
    item = MediaItem.query.get_or_404(item_id)
    spotify_enabled = bool(get_config_value('spotify_client_id'))
    return render_template('media_detail.html', item=item, tracks=item.tracks.order_by(Track.position).all(), spotify_enabled=spotify_enabled)

@main.route('/media/create', methods=['GET', 'POST'])
@login_required
def media_create():
    if request.method == 'POST':
        # Bildverarbeitung
        img = request.files.get('image')
        url = request.form.get('remote_image_url')
        fn = None
        if img and img.filename:
            fn = save_image(img)
        elif url and url.strip():
            fn = download_remote_image(url) # Hier greift jetzt auch die Amazon 1x1 Prüfung
        
        ry = request.form.get('release_year')
        
        # Standort merken (für Massenerfassung im gleichen Raum)
        loc_id = int(request.form.get('location_id') or 1)
        session['last_location_id'] = loc_id

        item = MediaItem(
            inventory_number=generate_inventory_number(),
            title=request.form.get('title'),
            category=request.form.get('category'),
            barcode=request.form.get('barcode') or None,
            author_artist=request.form.get('author_artist'),
            release_year=int(ry) if ry else None,
            description=request.form.get('description'),
            location_id=loc_id,
            image_filename=fn,
            user_id=current_user.id
        )
        db.session.add(item)
        db.session.commit()

        # Tracks
        titles = request.form.getlist('track_title')
        pos = request.form.getlist('track_position')
        dur = request.form.getlist('track_duration')
        for i, t in enumerate(titles):
            if t.strip():
                try: p = int(pos[i])
                except: p = i + 1
                db.session.add(Track(media_item_id=item.id, title=t, position=p, duration=dur[i]))
        db.session.commit()

        flash('Erstellt.', 'success')
        if request.form.get('commit_action') == 'save_next': return redirect(url_for('main.media_create'))
        return redirect(url_for('main.index'))

    default_location_id = session.get('last_location_id', 1)
    return render_template('media_create.html', locations=sorted(Location.query.all(), key=lambda x: x.full_path), categories=["Buch", "Film (DVD/BluRay)", "CD", "Vinyl/LP", "Videospiel", "Sonstiges"], default_location_id=default_location_id)

@main.route('/media/edit/<int:item_id>', methods=['GET', 'POST'])
@login_required
def media_edit(item_id):
    item = MediaItem.query.get_or_404(item_id)
    if request.method == 'POST':
        item.title = request.form.get('title')
        item.category = request.form.get('category')
        item.author_artist = request.form.get('author_artist')
        ry = request.form.get('release_year')
        item.release_year = int(ry) if ry and ry.strip() else None
        item.barcode = request.form.get('barcode')
        item.description = request.form.get('description')
        item.location_id = int(request.form.get('location_id') or 1)
        item.lent_to = request.form.get('lent_to') or None
        if item.lent_to and not item.lent_at: item.lent_at = datetime.now()
        if not item.lent_to: item.lent_at = None

        img = request.files.get('image')
        url = request.form.get('remote_image_url')
        if img and img.filename: item.image_filename = save_image(img)
        elif url and url != "": item.image_filename = download_remote_image(url)

        # Tracks überschreiben
        if request.form.get('overwrite_tracks') == 'yes':
            Track.query.filter_by(media_item_id=item.id).delete()
            titles = request.form.getlist('track_title')
            pos = request.form.getlist('track_position')
            dur = request.form.getlist('track_duration')
            for i, t in enumerate(titles):
                if t.strip():
                    try: p = int(pos[i])
                    except: p = i + 1
                    db.session.add(Track(media_item_id=item.id, title=t, position=p, duration=dur[i]))

        db.session.commit()
        flash('Gespeichert.', 'success')
        return redirect(url_for('main.media_detail', item_id=item.id))

    return render_template('media_edit.html', item=item, locations=sorted(Location.query.all(), key=lambda x: x.full_path), categories=["Buch", "Film (DVD/BluRay)", "CD", "Vinyl/LP", "Videospiel", "Sonstiges"])

@main.route('/media/delete/<int:item_id>')
@login_required
def media_delete(item_id):
    item = MediaItem.query.get_or_404(item_id)
    db.session.delete(item)
    db.session.commit()
    return redirect(url_for('main.index'))

@main.route('/media/<int:item_id>/add_track', methods=['POST'])
@login_required
def track_add(item_id):
    t = request.form.get('title')
    if t:
        db.session.add(Track(media_item_id=item_id, title=t, position=request.form.get('position', 0), duration=request.form.get('duration')))
        db.session.commit()
    return redirect(url_for('main.media_detail', item_id=item_id))

@main.route('/track/delete/<int:track_id>')
@login_required
def track_delete(track_id):
    t = Track.query.get_or_404(track_id)
    mid = t.media_item_id
    db.session.delete(t)
    db.session.commit()
    return redirect(url_for('main.media_detail', item_id=mid))

# -- VERLEIH ÜBERSICHT --
@main.route('/lent')
@login_required
def lent_overview():
    # Alle verliehenen Items laden
    items = MediaItem.query.filter(MediaItem.lent_to != None).order_by(MediaItem.lent_to, MediaItem.lent_at).all()
    # Liste aller Personen erstellen, die etwas ausgeliehen haben (für das Dropdown)
    borrowers = sorted(list(set([i.lent_to for i in items if i.lent_to])))
    return render_template('lent_items.html', items=items, borrowers=borrowers)

@main.route('/lent/export')
@login_required
def lent_export():
    person = request.args.get('person')
    query = MediaItem.query.filter(MediaItem.lent_to != None)
    if person:
        query = query.filter(MediaItem.lent_to == person)
    
    items = query.order_by(MediaItem.lent_to, MediaItem.lent_at).all()
    return render_template('lent_export.html', items=items, person=person, now=datetime.now())

@main.route('/admin/users')
@login_required
def admin_users():
    if not current_user.has_role('Admin'): return redirect(url_for('main.index'))
    return render_template('admin_users.html', users=User.query.all(), roles=Role.query.all())

@main.route('/admin/users/create', methods=['POST'])
@login_required
def user_create():
    if not current_user.has_role('Admin'): return redirect(url_for('main.index'))
    if not User.query.filter_by(username=request.form.get('username')).first():
        u = User(username=request.form.get('username'), role_id=request.form.get('role_id'))
        u.set_password(request.form.get('password'))
        db.session.add(u); db.session.commit()
    return redirect(url_for('main.admin_users'))

@main.route('/admin/users/delete/<int:user_id>')
@login_required
def user_delete(user_id):
    if not current_user.has_role('Admin'): return redirect(url_for('main.index'))
    u = User.query.get_or_404(user_id)
    if u.id != current_user.id: db.session.delete(u); db.session.commit()
    return redirect(url_for('main.admin_users'))

@main.route('/admin/locations')
@login_required
def admin_locations():
    if not current_user.has_role('Admin'): return redirect(url_for('main.index'))
    return render_template('admin_locations.html', locations=sorted(Location.query.all(), key=lambda x: x.full_path))

@main.route('/admin/locations/edit/<int:loc_id>', methods=['GET', 'POST'])
@login_required
def location_edit(loc_id):
    if not current_user.has_role('Admin'): return redirect(url_for('main.index'))
    loc = Location.query.get_or_404(loc_id)
    if request.method == 'POST':
        loc.name = request.form.get('name')
        pid = request.form.get('parent_id')
        if pid:
            if int(pid) == loc.id: flash('Fehler.', 'error')
            else: loc.parent_id = int(pid)
        else: loc.parent_id = None
        db.session.commit()
        return redirect(url_for('main.admin_locations'))
    return render_template('location_edit.html', location=loc, all_locations=sorted(Location.query.filter(Location.id!=loc_id).all(), key=lambda x: x.full_path))

@main.route('/admin/locations/create', methods=['POST'])
@login_required
def location_create():
    if not current_user.has_role('Admin'): return redirect(url_for('main.index'))
    pid = request.form.get('parent_id')
    db.session.add(Location(name=request.form.get('name'), parent_id=int(pid) if pid else None))
    db.session.commit()
    return redirect(url_for('main.admin_locations'))

@main.route('/admin/locations/delete/<int:loc_id>')
@login_required
def location_delete(loc_id):
    if not current_user.has_role('Admin'): return redirect(url_for('main.index'))
    l = Location.query.get_or_404(loc_id)
    if not l.children and l.items.count() == 0: db.session.delete(l); db.session.commit()
    return redirect(url_for('main.admin_locations'))

@main.route('/admin')
@login_required
def admin_redirect(): return redirect(url_for('main.admin_users'))
