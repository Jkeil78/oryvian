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
from translations import TRANSLATIONS

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
    # Simple check: Amazon sometimes returns 1x1 pixel gifs as "not found"
    # We only download if we are sure it is image data.
    print(f"DEBUG: Starte Download von {url}")
    try:
        headers = {'User-Agent': 'HomeInventoryApp/1.0'}
        response = requests.get(url, headers=headers, stream=True, timeout=10)
        
        if response.status_code == 200:
            # Check Content-Length (Amazon 1x1 Pixel is approx 43 bytes)
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

def get_text(key):
    lang = 'en'
    if current_user.is_authenticated and current_user.language:
        lang = current_user.language
    return TRANSLATIONS.get(lang, TRANSLATIONS['en']).get(key, key)

@main.context_processor
def inject_get_text():
    return dict(_=get_text)

# -- API: DISCOGS TEXT SEARCH --
@main.route('/api/search_discogs')
@login_required
def api_search_discogs():
    artist = request.args.get('artist', '').strip()
    title = request.args.get('title', '').strip()
    
    discogs_token = get_config_value('discogs_token')
    if not discogs_token:
        return jsonify({"success": False, "message": get_text("flash_no_permission")})

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

# -- API: SPOTIFY SEARCH --
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
            # market=DE improves hit rate for German users and filters unplayable content
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
                    # Exact substring or high similarity (Ratio > 0.6)
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

        # Attempt 1: Strict with fields
        res = do_search(f'artist:"{artist}" album:"{title}"')
        if res.status_code == 200:
            items = res.json().get("albums", {}).get("items", [])
            match_id = find_best_match(items)
            if match_id:
                return jsonify({"success": True, "spotify_id": match_id})
        
        # Attempt 2: Loose (Simple string concat) - also finds "Remastered" etc.
        res = do_search(f"{artist} {title}")
        if res.status_code == 200:
            items = res.json().get("albums", {}).get("items", [])
            match_id = find_best_match(items)
            if match_id:
                return jsonify({"success": True, "spotify_id": match_id})

    except Exception as e:
        print(f"Spotify Search Error: {e}")
        
    return jsonify({"success": False, "message": "Not found"})

# -- API: BARCODE LOOKUP (With Amazon Fallback) --
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
                if not data["success"]: # Only adopt metadata if Google had nothing
                    data.update({
                        "success": True, "title": bk.get("title", ""), 
                        "author": ", ".join([a["name"] for a in bk.get("authors", [])]),
                        "category": "Buch"
                    })
                    match = re.search(r'\d{4}', bk.get("publish_date", ""))
                    if match: data["year"] = match.group(0)

                # Get image
                if "cover" in bk: data["image_url"] = bk["cover"].get("large", "")
        except: pass

    # 3. Amazon Direct Image Fallback (NEW!)
    # Often works for German books if Google/OpenLibrary have no cover
    if data["success"] and not data["image_url"]:
        try:
            # Amazon image URL pattern
            amazon_url = f"https://images-na.ssl-images-amazon.com/images/P/{clean_isbn}.01.LZZZZZZZ.jpg"
            
            # We do a HEAD request to check if the image exists
            # and is larger than 1x1 pixel (Amazon returns 43 bytes for "not found")
            check = requests.get(amazon_url, timeout=3)
            if check.status_code == 200 and len(check.content) > 100:
                print(f"DEBUG: Amazon Cover gefunden für {clean_isbn}")
                data["image_url"] = amazon_url
        except Exception as e:
            print(f"DEBUG: Amazon Fallback Fehler: {e}")

    # 4. Discogs (for music)
    token = get_config_value('discogs_token')
    if token and (not data["success"] or data["category"] == ""): 
        try:
            h = {"User-Agent": "HomeInventoryApp/1.0", "Authorization": f"Discogs token={token}"}
            res = requests.get("https://api.discogs.com/database/search", headers=h, params={"barcode": barcode, "type": "release", "per_page": 1}, timeout=5)
            if res.status_code == 200 and res.json().get("results"):
                it = res.json()["results"][0]
                data["success"] = True
                
                # Title/Author separation at Discogs often "Artist - Title"
                full_title = it.get("title", "")
                if " - " in full_title and not data["author"]:
                    parts = full_title.split(" - ", 1)
                    data["author"] = parts[0].strip()
                    data["title"] = parts[1].strip()
                elif not data["title"]:
                    data["title"] = full_title
                
                if not data["year"]: data["year"] = it.get("year", "")
                
                # If we don't have an image yet (or Amazon/Google failed)
                if not data["image_url"]:
                    data["image_url"] = it.get("cover_image", "")
                
                # Determine category
                fmts = it.get("format", [])
                if "Vinyl" in fmts: data["category"] = "Vinyl/LP"
                elif "CD" in fmts: data["category"] = "CD"
                elif "DVD" in fmts: data["category"] = "Film (DVD/BluRay)"

                # Load tracks
                if it.get("resource_url"):
                    det = requests.get(it["resource_url"], headers=h, timeout=5).json()
                    for t in det.get("tracklist", []):
                        if t.get("type_") != "heading":
                            data["tracks"].append({"position": t.get("position"), "title": t.get("title"), "duration": t.get("duration")})
        except: pass
    
    # 5. Blu-ray.com (EAN Search)
    # Replaces TMDB and OFDb as they are unreliable for EANs
    if not data["success"] or data["category"] == "":
        try:
            url = f"https://www.blu-ray.com/search/?quicksearch=1&quicksearch_country=all&quicksearch_keyword={barcode}"
            # User-Agent is required for blu-ray.com
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
            res = requests.get(url, headers=headers, timeout=5)
            
            if res.status_code == 200:
                content = res.text
                movie_url = None
                d_content = ""

                # Check if redirected to detail page
                if "/movies/" in res.url:
                    movie_url = res.url
                    d_content = content
                else:
                    # Search results page - find first movie link
                    # Pattern: <a href="https://www.blu-ray.com/movies/..." ... title="...">
                    link_match = re.search(r'href="(https://www.blu-ray.com/movies/[^"]+)"[^>]*title="([^"]+)"', content)
                    if link_match:
                        movie_url = link_match.group(1)
                        # Fetch detail page
                        detail_res = requests.get(movie_url, headers=headers, timeout=5)
                        if detail_res.status_code == 200:
                            d_content = detail_res.text
                        else:
                            movie_url = None

                if movie_url and d_content:
                    data["success"] = True
                    data["category"] = "Film (DVD/BluRay)"
                    
                    # Title (OG Meta tag is usually reliable)
                    title_match = re.search(r'<meta property="og:title" content="([^"]+)"', d_content)
                    if title_match:
                        # Cleanup title (remove "Blu-ray", "4K", etc.)
                        t = title_match.group(1)
                        t = re.sub(r'\s+\(Blu-ray\)', '', t)
                        t = re.sub(r'\s+\(4K\)', '', t)
                        t = re.sub(r'\s+\(3D\)', '', t)
                        data["title"] = t.strip()
                    
                    # Description
                    desc_match = re.search(r'<meta property="og:description" content="([^"]+)"', d_content)
                    if desc_match:
                        data["description"] = desc_match.group(1)

                    # Image
                    img_match = re.search(r'<meta property="og:image" content="([^"]+)"', d_content)
                    if img_match:
                        data["image_url"] = img_match.group(1)
                        
                    # Year
                    year_match = re.search(r'href="https://www.blu-ray.com/movies/movies.php\?year=(\d{4})"', d_content)
                    if year_match:
                        data["year"] = year_match.group(1)
                        
                    # Director
                    dir_match = re.search(r'Directors?:.*?<a[^>]+>([^<]+)</a>', d_content, re.DOTALL)
                    if dir_match:
                        data["author"] = dir_match.group(1)

        except Exception as e:
            print(f"Blu-ray.com Error: {e}")

    return jsonify(data)

@main.route('/api/check_duplicate/<barcode>')
@login_required
def api_check_duplicate(barcode):
    exists = MediaItem.query.filter_by(barcode=barcode).first() is not None
    return jsonify({'exists': exists})

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

# -- ROUTES --

@main.route('/')
def index():
    if not current_user.is_authenticated: return redirect(url_for('main.login'))

    # 1. RESET
    if request.args.get('reset'):
        session.pop('filter_state', None)
        return redirect(url_for('main.index'))

    # 2. SESSION STATE MANAGEMENT
    # 'limit' also save
    params = ['q', 'category', 'location', 'lent', 'sort_field', 'sort_order', 'limit']
    
    # Check if new parameters are in URL
    active_args = {k: request.args.get(k) for k in params if request.args.get(k) is not None}

    if active_args:
        # New filter active -> Save
        session['filter_state'] = active_args
        
        # Save sorting preference to User
        if 'sort_field' in active_args or 'sort_order' in active_args:
            if 'sort_field' in active_args: current_user.sort_field = active_args['sort_field']
            if 'sort_order' in active_args: current_user.sort_order = active_args['sort_order']
            db.session.commit()
    elif 'filter_state' in session and not request.args:
        # No URL params, but session exists -> Restore
        return redirect(url_for('main.index', **session['filter_state']))

    # 3. READ VALUES (Set defaults)
    q_str = request.args.get('q', '')
    cat = request.args.get('category', '')
    loc = request.args.get('location', '')
    lent = request.args.get('lent', '') # ''=Alle, 'yes'=Verliehen, 'no'=Verfügbar
    limit = request.args.get('limit', '20')
    
    # Read page (do not save in session, otherwise you always land on page X)
    page = request.args.get('page', 1, type=int)
    
    # Defaults from User
    default_sort_field = current_user.sort_field or 'added'
    default_sort_order = current_user.sort_order or 'desc'
    
    sort_field = request.args.get('sort_field', default_sort_field) 
    sort_order = request.args.get('sort_order', default_sort_order)

    query = MediaItem.query

    # -- FILTERING --
    if q_str:
        s = f"%{q_str}%"
        query = query.filter(or_(
            MediaItem.title.ilike(s),
            MediaItem.author_artist.ilike(s),
            MediaItem.inventory_number.ilike(s),
            MediaItem.barcode.ilike(s),
            MediaItem.lent_to.ilike(s), # Also search for borrower!
            MediaItem.tracks.any(Track.title.ilike(s)) # Search in tracks
        ))
    
    if cat: 
        query = query.filter(MediaItem.category == cat)
    
    if loc: 
        query = query.filter(MediaItem.location_id == int(loc))

    # Rental status filter
    if lent == 'yes':
        query = query.filter(MediaItem.lent_to != None)
    elif lent == 'no':
        query = query.filter(MediaItem.lent_to == None)

    # -- FLEXIBLE SORTING WITH CASCADING --
    primary_sort = None
    secondary_sorts = []

    if sort_field == 'title':
        primary_sort = MediaItem.title
        secondary_sorts = [MediaItem.author_artist.asc()] 
    elif sort_field == 'author':
        primary_sort = MediaItem.author_artist
        secondary_sorts = [MediaItem.title.asc(), MediaItem.release_year.desc()]
    elif sort_field == 'year':
        primary_sort = MediaItem.release_year
        secondary_sorts = [MediaItem.author_artist.asc(), MediaItem.title.asc()]
    else: # 'added' oder Fallback
        primary_sort = MediaItem.id
        secondary_sorts = []

    # Apply direction
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
        
        # Use SQLAlchemy Pagination
        pagination = query.paginate(page=page, per_page=per_page, error_out=False)
        items = pagination.items

    locations = sorted(Location.query.all(), key=lambda x: x.full_path)
    categories = ["Buch", "Film (DVD/BluRay)", "CD", "Vinyl/LP", "Videospiel", "Sonstiges"]

    # Filter status for template
    current_filters = {
        'q': q_str, 'category': cat, 'location': loc, 'lent': lent,
        'sort_field': sort_field, 'sort_order': sort_order, 'limit': limit
    }
    # Helper: Check if filters are active (for the reset button)
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
            remember = True if request.form.get('remember') else False
            login_user(user, remember=remember)
            return redirect(url_for('main.index'))
        flash(get_text('flash_login_failed'), 'error')
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
    
    active_tab = request.args.get('tab', 'ownership')
    
    if request.method == 'POST':
        # Ownership Settings
        if 'owner_name' in request.form:
            set_config_value('owner_name', request.form.get('owner_name', '').strip())
            set_config_value('owner_address', request.form.get('owner_address', '').strip())
            set_config_value('owner_phone', request.form.get('owner_phone', '').strip())
            flash(get_text('settings_saved'), 'success')
            return redirect(url_for('main.settings', tab='ownership'))

        # System Settings (Language & Theme)
        if 'language' in request.form or 'theme' in request.form:
            lang = request.form.get('language')
            if lang in ['en', 'de', 'es', 'fr']:
                current_user.language = lang
            
            theme = request.form.get('theme')
            if theme in ['auto', 'cerulean', 'zephyr', 'flatly', 'materia', 'quartz', 'morph', 'journal', 'pulse', 'yeti', 'darkly', 'cyborg', 'slate', 'solar', 'superhero', 'sandstone', 'united']:
                current_user.theme = theme
                
                db.session.commit()
            
            # Duplicate check toggle
            set_config_value('duplicate_check', 'true' if 'duplicate_check' in request.form else 'false')
            
            flash(get_text('settings_saved'), 'success')
            return redirect(url_for('main.settings', tab='system'))
        
        if 'discogs_token' in request.form:
            set_config_value('discogs_token', request.form.get('discogs_token', '').strip())
            set_config_value('spotify_client_id', request.form.get('spotify_client_id', '').strip())
            set_config_value('spotify_client_secret', request.form.get('spotify_client_secret', '').strip())
            flash(get_text('settings_saved'), 'success')
            return redirect(url_for('main.settings', tab='api'))
        
    return render_template('settings.html',
                           active_tab=active_tab,
                           users=User.query.all(),
                           roles=Role.query.all(),
                           locations=sorted(Location.query.all(), key=lambda x: x.full_path),
                           discogs_token=get_config_value('discogs_token', ''),
                           spotify_client_id=get_config_value('spotify_client_id', ''),
                           spotify_client_secret=get_config_value('spotify_client_secret', ''),
                           duplicate_check=get_config_value('duplicate_check', 'false'),
                           owner_name=get_config_value('owner_name', ''),
                           owner_address=get_config_value('owner_address', ''),
                           owner_phone=get_config_value('owner_phone', ''))

@main.route('/profile/change_password', methods=['GET', 'POST'])
@login_required
def change_password():
    if request.method == 'POST':
        curr = request.form.get('current_password')
        new = request.form.get('new_password')
        conf = request.form.get('confirm_password')
        if not current_user.check_password(curr): flash(get_text('password_wrong'), 'error')
        elif new != conf: flash(get_text('passwords_mismatch'), 'error')
        else:
            current_user.set_password(new)
            db.session.commit()
            flash(get_text('flash_saved'), 'success')
            return redirect(url_for('main.index'))
    return render_template('change_password.html')

@main.route('/admin/backup/download')
@login_required
def admin_backup_download():
    if not current_user.has_role('Admin'): return redirect(url_for('main.index'))
    try:
        path, fname = create_backup_zip()
        return send_file(path, as_attachment=True, download_name=fname)
    except Exception as e:
        flash(f'Error: {e}', 'error')
        return redirect(url_for('main.settings', tab='backup'))

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
            flash(get_text('flash_backup_restore'), 'success')
            if os.path.exists(p): os.remove(p)
            return redirect(url_for('main.index'))
        except Exception as e:
            flash(f'Error: {e}', 'error')
            return redirect(url_for('main.settings', tab='backup'))
    flash(get_text('flash_invalid_file'), 'error')
    return redirect(url_for('main.settings', tab='backup'))

@main.route('/admin/cleanup_images', methods=['POST'])
@login_required
def admin_cleanup_images():
    if not current_user.has_role('Admin'): 
        return redirect(url_for('main.index'))
    
    upload_folder = current_app.config['UPLOAD_FOLDER']
    if not os.path.exists(upload_folder):
        flash('Upload folder does not exist.', 'error')
        return redirect(url_for('main.settings', tab='system'))
    
    # 1. Get all files in uploads
    all_files = set(os.listdir(upload_folder))
    
    # 2. Get all referenced filenames in DB
    referenced_files = {item.image_filename for item in MediaItem.query.all() if item.image_filename}
    
    # 3. Calculate orphaned files
    orphaned_files = all_files - referenced_files
    
    # 4. Delete orphaned files
    count = 0
    for filename in orphaned_files:
        if filename == '.gitkeep': continue # Keep placeholder if exists
        try:
            os.remove(os.path.join(upload_folder, filename))
            count += 1
        except Exception as e:
            print(f"Error deleting {filename}: {e}")
            
    flash(get_text('flash_cleanup_success').format(count=count), 'success')
    return redirect(url_for('main.settings', tab='system'))

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
        # Image processing
        img = request.files.get('image')
        url = request.form.get('remote_image_url')
        fn = None
        if img and img.filename:
            fn = save_image(img)
        elif url and url.strip():
            fn = download_remote_image(url) # Amazon 1x1 check also applies here
        
        ry = request.form.get('release_year')
        
        # Remember location (for bulk entry in the same room)
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

        flash(get_text('flash_created'), 'success')
        if request.form.get('commit_action') == 'save_next': return redirect(url_for('main.media_create'))
        return redirect(url_for('main.index'))

    default_location_id = session.get('last_location_id', 1)
    return render_template('media_create.html', 
                           locations=sorted(Location.query.all(), key=lambda x: x.full_path), 
                           categories=["Buch", "Film (DVD/BluRay)", "CD", "Vinyl/LP", "Videospiel", "Sonstiges"], 
                           default_location_id=default_location_id,
                           duplicate_check=get_config_value('duplicate_check', 'false'))

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

        # Overwrite tracks
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
        flash(get_text('flash_saved'), 'success')
        return redirect(url_for('main.media_detail', item_id=item.id))

    return render_template('media_edit.html', item=item, locations=sorted(Location.query.all(), key=lambda x: x.full_path), categories=["Buch", "Film (DVD/BluRay)", "CD", "Vinyl/LP", "Videospiel", "Sonstiges"])

@main.route('/media/delete/<int:item_id>')
@login_required
def media_delete(item_id):
    if not current_user.has_role('Admin'):
        flash(get_text('flash_no_permission'), 'error')
        return redirect(url_for('main.index'))
    item = MediaItem.query.get_or_404(item_id)
    db.session.delete(item)
    db.session.commit()
    return redirect(url_for('main.index'))

@main.route('/media/bulk_move', methods=['POST'])
@login_required
def bulk_move():
    if not current_user.has_role('Admin'):
        flash(get_text('flash_no_permission'), 'error')
        return redirect(url_for('main.index'))

    item_ids = request.form.getlist('item_ids')
    target_location_id = request.form.get('target_location_id')
    
    if not item_ids:
        flash(get_text('no_selection'), 'warning')
        return redirect(url_for('main.index'))
        
    if not target_location_id:
        flash(get_text('no_target'), 'warning')
        return redirect(url_for('main.index'))

    try:
        target_loc = Location.query.get(int(target_location_id))
        if not target_loc:
            flash(get_text('invalid_target'), 'error')
            return redirect(url_for('main.index'))
            
        count = MediaItem.query.filter(MediaItem.id.in_(item_ids)).update({MediaItem.location_id: target_loc.id}, synchronize_session=False)
        db.session.commit()
        flash(f'{count} {get_text("item_moved")}', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Fehler beim Verschieben: {str(e)}', 'error')
        
    return redirect(url_for('main.index'))

@main.route('/media/<int:item_id>/add_track', methods=['POST'])
@login_required
def track_add(item_id):
    t = request.form.get('title')
    if t:
        db.session.add(Track(media_item_id=item_id, title=t, position=request.form.get('position', 0), duration=request.form.get('duration')))
        db.session.commit()
        flash(get_text('track_added'), 'success')
    return redirect(url_for('main.media_detail', item_id=item_id))

@main.route('/track/delete/<int:track_id>')
@login_required
def track_delete(track_id):
    t = Track.query.get_or_404(track_id)
    mid = t.media_item_id
    db.session.delete(t)
    db.session.commit()
    flash(get_text('track_deleted'), 'success')
    return redirect(url_for('main.media_detail', item_id=mid))

# -- LENT OVERVIEW --
@main.route('/lent')
@login_required
def lent_overview():
    # Load all lent items
    items = MediaItem.query.filter(MediaItem.lent_to != None).order_by(MediaItem.lent_to, MediaItem.lent_at).all()
    # Create list of all persons who have borrowed something (for the dropdown)
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

@main.route('/admin/users/create', methods=['POST'])
@login_required
def user_create():
    if not current_user.has_role('Admin'): return redirect(url_for('main.index'))
    if not User.query.filter_by(username=request.form.get('username')).first():
        u = User(username=request.form.get('username'), role_id=request.form.get('role_id'))
        u.set_password(request.form.get('password'))
        db.session.add(u); db.session.commit()
    return redirect(url_for('main.settings', tab='users'))

@main.route('/admin/users/delete/<int:user_id>')
@login_required
def user_delete(user_id):
    if not current_user.has_role('Admin'): return redirect(url_for('main.index'))
    u = User.query.get_or_404(user_id)
    if u.id != current_user.id: db.session.delete(u); db.session.commit()
    return redirect(url_for('main.settings', tab='users'))

@main.route('/admin/locations/edit/<int:loc_id>', methods=['GET', 'POST'])
@login_required
def location_edit(loc_id):
    if not current_user.has_role('Admin'): return redirect(url_for('main.index'))
    loc = Location.query.get_or_404(loc_id)
    if request.method == 'POST':
        loc.name = request.form.get('name')
        pid = request.form.get('parent_id')
        if pid:
            if int(pid) == loc.id: flash(get_text('flash_error'), 'error')
            else: loc.parent_id = int(pid)
        else: loc.parent_id = None
        db.session.commit()
        return redirect(url_for('main.settings', tab='locations'))
    return render_template('location_edit.html', location=loc, all_locations=sorted(Location.query.filter(Location.id!=loc_id).all(), key=lambda x: x.full_path))

@main.route('/admin/locations/create', methods=['POST'])
@login_required
def location_create():
    if not current_user.has_role('Admin'): return redirect(url_for('main.index'))
    pid = request.form.get('parent_id')
    db.session.add(Location(name=request.form.get('name'), parent_id=int(pid) if pid else None))
    db.session.commit()
    return redirect(url_for('main.settings', tab='locations'))

@main.route('/admin/locations/delete/<int:loc_id>')
@login_required
def location_delete(loc_id):
    if not current_user.has_role('Admin'): return redirect(url_for('main.index'))
    l = Location.query.get_or_404(loc_id)
    if not l.children and l.items.count() == 0: db.session.delete(l); db.session.commit()
    return redirect(url_for('main.settings', tab='locations'))

@main.route('/labels/config', methods=['POST'])
@login_required
def labels_config():
    if not current_user.has_role('Admin'):
        flash(get_text('flash_no_permission'), 'error')
        return redirect(url_for('main.index'))
    
    item_ids = request.form.getlist('item_ids')
    if not item_ids:
        flash(get_text('no_selection'), 'warning')
        return redirect(url_for('main.index'))

    # Fetch custom presets
    import json
    custom_presets_raw = get_config_value('custom_label_presets', '{}')
    try:
        custom_presets = json.loads(custom_presets_raw)
    except:
        custom_presets = {}

    return render_template('labels_config.html', item_ids=item_ids, custom_presets=custom_presets)

@main.route('/labels/print', methods=['POST'])
@login_required
def labels_print():
    if not current_user.has_role('Admin'):
        flash(get_text('flash_no_permission'), 'error')
        return redirect(url_for('main.index'))
    
    item_ids = request.form.getlist('item_ids')
    if not item_ids:
        flash(get_text('no_selection'), 'warning')
        return redirect(url_for('main.index'))

    # Fetch items and MAINTAIN ORDER of item_ids
    items_map = {str(item.id): item for item in MediaItem.query.filter(MediaItem.id.in_(item_ids)).all()}
    items = [items_map[str(iid)] for iid in item_ids if str(iid) in items_map]
    
    try:
        width = float(request.form.get('width', '62'))
        height = float(request.form.get('height', '29'))
        padding = float(request.form.get('padding', '2'))
        font_size = float(request.form.get('font_size', '10'))
        columns = int(request.form.get('columns', '1'))
        margin_top = float(request.form.get('margin_top', '0'))
        margin_left = float(request.form.get('margin_left', '0'))
        start_at = int(request.form.get('start_at', '1'))
    except ValueError:
        width, height, padding, font_size, columns, margin_top, margin_left, start_at = 62.0, 29.0, 2.0, 10.0, 1, 0.0, 0.0, 1

    # Inject empty items for start_at logic
    if start_at > 1:
        # We use None as placeholders for empty labels
        items = [None] * (start_at - 1) + items

    # Calculate QR size
    if 'vertical_layout' in request.form:
        qr_size = (height - (2 * padding)) * 0.5
    else:
        qr_size = (height - (2 * padding)) * 0.9
        
    if qr_size < 5: qr_size = 5

    config = {
        'width': width,
        'height': height,
        'margin_top': margin_top,
        'margin_left': margin_left,
        'padding': padding,
        'columns': columns,
        'qr_size': qr_size,
        'show_qr': 'show_qr' in request.form,
        'show_title': 'show_title' in request.form,
        'show_artist': 'show_artist' in request.form,
        'show_id': 'show_id' in request.form,
        'show_owner': 'show_owner' in request.form,
        'show_address': 'show_address' in request.form,
        'show_phone': 'show_phone' in request.form,
        'vertical_layout': 'vertical_layout' in request.form,
        'font_size': font_size,
        'start_at': start_at
    }

    owner_info = {
        'name': get_config_value('owner_name', ''),
        'address': get_config_value('owner_address', ''),
        'phone': get_config_value('owner_phone', '')
    }
    
    return render_template('labels_print.html', items=items, config=config, owner=owner_info)

@main.route('/labels/save_preset', methods=['POST'])
@login_required
def save_label_preset():
    if not current_user.has_role('Admin'):
        return jsonify({'success': False, 'message': 'No permission'}), 403

    import json
    data = request.json
    name = data.get('name')
    if not name:
        return jsonify({'success': False, 'message': 'Name required'}), 400

    custom_presets_raw = get_config_value('custom_label_presets', '{}')
    try:
        custom_presets = json.loads(custom_presets_raw)
    except:
        custom_presets = {}

    custom_presets[name] = {
        'width': data.get('width'),
        'height': data.get('height'),
        'padding': data.get('padding'),
        'columns': data.get('columns'),
        'margin_top': data.get('margin_top'),
        'margin_left': data.get('margin_left'),
        'font_size': data.get('font_size'),
        'vertical': data.get('vertical')
    }

    set_config_value('custom_label_presets', json.dumps(custom_presets))
    return jsonify({'success': True})

@main.route('/labels/delete_preset/<name>', methods=['POST'])
@login_required
def delete_label_preset(name):
    if not current_user.has_role('Admin'):
        return jsonify({'success': False, 'message': 'No permission'}), 403

    import json
    custom_presets_raw = get_config_value('custom_label_presets', '{}')
    try:
        custom_presets = json.loads(custom_presets_raw)
    except:
        custom_presets = {}

    if name in custom_presets:
        del custom_presets[name]
        set_config_value('custom_label_presets', json.dumps(custom_presets))
        return jsonify({'success': True})
    
    return jsonify({'success': False, 'message': 'Preset not found'}), 404

@main.route('/admin')
@login_required
def admin_redirect(): return redirect(url_for('main.settings'))
