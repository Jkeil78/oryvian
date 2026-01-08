import uuid
import os
from datetime import datetime
from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app
from flask_login import login_user, login_required, logout_user, current_user
from werkzeug.utils import secure_filename
from extensions import db
from models import User, Role, Location, MediaItem, Collection, Track

# Blueprint Definition
main = Blueprint('main', __name__)

# -- HELPER FUNKTIONEN --

def allowed_file(filename):
    """Prüft auf erlaubte Dateiendungen für Uploads"""
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def save_image(file):
    """Speichert das Bild und gibt den neuen (UUID) Dateinamen zurück"""
    if file and allowed_file(file.filename):
        # Wir generieren einen zufälligen Namen, um Kollisionen zu vermeiden
        ext = file.filename.rsplit('.', 1)[1].lower()
        new_filename = f"{uuid.uuid4().hex}.{ext}"
        path = os.path.join(current_app.config['UPLOAD_FOLDER'], new_filename)
        file.save(path)
        return new_filename
    return None

def create_initial_data():
    """Initialisiert DB Daten (wird aus app.py aufgerufen)"""
    if not Role.query.filter_by(name='Admin').first():
        db.session.add(Role(name='Admin'))
        db.session.add(Role(name='User'))
        db.session.commit()
    
    if not User.query.filter_by(username='admin').first():
        admin_role = Role.query.filter_by(name='Admin').first()
        admin = User(username='admin', role=admin_role)
        admin.set_password('admin123')
        db.session.add(admin)
        db.session.commit()
    
    if not Location.query.first():
        db.session.add(Location(name="Unsortiert"))
        db.session.commit()

def generate_inventory_number():
    """Erzeugt eine eindeutige Inventarnummer, z.B. INV-2023-A1B2"""
    year = datetime.now().year
    unique_part = str(uuid.uuid4())[:8].upper()
    return f"INV-{year}-{unique_part}"


# -- ROUTEN: PUBLIC & AUTH --

@main.route('/')
def index():
    # Lädt Medien sortiert nach Erstellungsdatum (neueste zuerst)
    recent_items = MediaItem.query.order_by(MediaItem.created_at.desc()).all()
    return render_template('index.html', items=recent_items)

@main.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))
    
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        
        if user and user.check_password(password):
            login_user(user)
            flash('Erfolgreich eingeloggt.', 'success')
            return redirect(url_for('main.index'))
        else:
            flash('Login fehlgeschlagen.', 'error')
            
    return render_template('login.html')

@main.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Du wurdest ausgeloggt.', 'info')
    return redirect(url_for('main.login'))

@main.route('/profile/change_password', methods=['GET', 'POST'])
@login_required
def change_password():
    if request.method == 'POST':
        current_password = request.form.get('current_password')
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')

        if not current_user.check_password(current_password):
            flash('Aktuelles Passwort falsch.', 'error')
            return redirect(url_for('main.change_password'))
        
        if new_password != confirm_password:
            flash('Passwörter stimmen nicht überein.', 'error')
            return redirect(url_for('main.change_password'))
        
        current_user.set_password(new_password)
        db.session.commit()
        flash('Passwort geändert!', 'success')
        return redirect(url_for('main.index'))

    return render_template('change_password.html')


# -- ROUTEN: MEDIEN VERWALTUNG --

@main.route('/media/<int:item_id>')
@login_required
def media_detail(item_id):
    item = MediaItem.query.get_or_404(item_id)
    # Tracks laden und nach Position sortieren
    tracks = item.tracks.order_by(Track.position).all()
    return render_template('media_detail.html', item=item, tracks=tracks)

@main.route('/media/create', methods=['GET', 'POST'])
@login_required
def media_create():
    if request.method == 'POST':
        # Basisdaten
        title = request.form.get('title')
        category = request.form.get('category')
        barcode = request.form.get('barcode')
        author_artist = request.form.get('author_artist')
        description = request.form.get('description')
        location_id = request.form.get('location_id') or 1
        
        # Jahr
        release_year = request.form.get('release_year')
        # Prüfen ob leerer String, sonst int conversion
        release_year = int(release_year) if release_year and release_year.strip() else None

        # Bild Upload
        image_file = request.files.get('image')
        filename = save_image(image_file)

        new_item = MediaItem(
            inventory_number=generate_inventory_number(),
            title=title,
            category=category,
            barcode=barcode if barcode else None,
            author_artist=author_artist,
            release_year=release_year,
            description=description,
            location_id=int(location_id),
            image_filename=filename,
            user_id=current_user.id
        )
        
        db.session.add(new_item)
        db.session.commit()
        flash(f'Medium "{title}" angelegt.', 'success')
        return redirect(url_for('main.index'))

    locations = Location.query.all()
    categories = ["Buch", "Film (DVD/BluRay)", "CD", "Vinyl/LP", "Videospiel", "Sonstiges"]
    return render_template('media_create.html', locations=locations, categories=categories)

@main.route('/media/edit/<int:item_id>', methods=['GET', 'POST'])
@login_required
def media_edit(item_id):
    item = MediaItem.query.get_or_404(item_id)
    
    if request.method == 'POST':
        item.title = request.form.get('title')
        item.category = request.form.get('category')
        item.author_artist = request.form.get('author_artist')
        
        # Jahr Update
        release_year = request.form.get('release_year')
        item.release_year = int(release_year) if release_year and release_year.strip() else None

        barcode_input = request.form.get('barcode')
        item.barcode = barcode_input if barcode_input else None
        
        item.description = request.form.get('description')
        
        loc_id = request.form.get('location_id')
        item.location_id = int(loc_id) if loc_id else 1
        
        # Verleih Status
        lent_to_input = request.form.get('lent_to')
        if lent_to_input and lent_to_input.strip() != "":
            if not item.lent_to:
                item.lent_at = datetime.now()
            item.lent_to = lent_to_input
        else:
            item.lent_to = None
            item.lent_at = None

        # Bild Update
        image_file = request.files.get('image')
        if image_file and image_file.filename != '':
            new_filename = save_image(image_file)
            if new_filename:
                item.image_filename = new_filename

        db.session.commit()
        flash('Änderungen gespeichert.', 'success')
        return redirect(url_for('main.media_detail', item_id=item.id))

    locations = Location.query.all()
    categories = ["Buch", "Film (DVD/BluRay)", "CD", "Vinyl/LP", "Videospiel", "Sonstiges"]
    return render_template('media_edit.html', item=item, locations=locations, categories=categories)

@main.route('/media/delete/<int:item_id>')
@login_required
def media_delete(item_id):
    item = MediaItem.query.get_or_404(item_id)
    db.session.delete(item)
    db.session.commit()
    flash(f'Eintrag "{item.title}" gelöscht.', 'success')
    return redirect(url_for('main.index'))


# -- ROUTEN: TRACKS (CD/LP) --

@main.route('/media/<int:item_id>/add_track', methods=['POST'])
@login_required
def track_add(item_id):
    item = MediaItem.query.get_or_404(item_id)
    
    title = request.form.get('title')
    position = request.form.get('position')
    duration = request.form.get('duration')
    
    if not title:
        flash('Titel darf nicht leer sein.', 'error')
        return redirect(url_for('main.media_detail', item_id=item_id))

    new_track = Track(
        media_item_id=item.id,
        title=title,
        position=int(position) if position else 0,
        duration=duration
    )
    
    db.session.add(new_track)
    db.session.commit()
    flash('Track hinzugefügt.', 'success')
    return redirect(url_for('main.media_detail', item_id=item.id))

@main.route('/track/delete/<int:track_id>')
@login_required
def track_delete(track_id):
    track = Track.query.get_or_404(track_id)
    media_id = track.media_item_id # Merken für Redirect
    
    db.session.delete(track)
    db.session.commit()
    flash('Track gelöscht.', 'success')
    return redirect(url_for('main.media_detail', item_id=media_id))


# -- ROUTEN: ADMIN BEREICH --

@main.route('/admin')
@login_required
def admin_redirect():
    return redirect(url_for('main.admin_users'))

@main.route('/admin/users')
@login_required
def admin_users():
    if not current_user.has_role('Admin'):
        flash('Zugriff verweigert.', 'error')
        return redirect(url_for('main.index'))
    
    users = User.query.all()
    roles = Role.query.all()
    return render_template('admin_users.html', users=users, roles=roles)

@main.route('/admin/users/create', methods=['POST'])
@login_required
def user_create():
    if not current_user.has_role('Admin'):
        return redirect(url_for('main.index'))

    username = request.form.get('username')
    password = request.form.get('password')
    role_id = request.form.get('role_id')

    if User.query.filter_by(username=username).first():
        flash(f'User {username} existiert bereits.', 'error')
    else:
        new_user = User(username=username, role_id=role_id)
        new_user.set_password(password)
        db.session.add(new_user)
        db.session.commit()
        flash(f'User {username} angelegt.', 'success')

    return redirect(url_for('main.admin_users'))

@main.route('/admin/users/delete/<int:user_id>')
@login_required
def user_delete(user_id):
    if not current_user.has_role('Admin'):
        return redirect(url_for('main.index'))
    
    user_to_delete = User.query.get_or_404(user_id)
    if user_to_delete.id == current_user.id:
        flash('Selbstlöschung nicht möglich!', 'error')
    else:
        db.session.delete(user_to_delete)
        db.session.commit()
        flash(f'User {user_to_delete.username} gelöscht.', 'success')

    return redirect(url_for('main.admin_users'))

@main.route('/admin/locations')
@login_required
def admin_locations():
    if not current_user.has_role('Admin'):
        flash('Zugriff verweigert.', 'error')
        return redirect(url_for('main.index'))
    locations = Location.query.all()
    return render_template('admin_locations.html', locations=locations)

@main.route('/admin/locations/create', methods=['POST'])
@login_required
def location_create():
    if not current_user.has_role('Admin'):
        return redirect(url_for('main.index'))
    
    name = request.form.get('name')
    parent_id = request.form.get('parent_id')
    
    if not parent_id or parent_id == '':
        parent_id = None
    else:
        parent_id = int(parent_id)
    
    new_loc = Location(name=name, parent_id=parent_id)
    db.session.add(new_loc)
    db.session.commit()
    flash(f'Standort "{name}" erstellt.', 'success')
    
    return redirect(url_for('main.admin_locations'))

@main.route('/admin/locations/delete/<int:loc_id>')
@login_required
def location_delete(loc_id):
    if not current_user.has_role('Admin'):
        return redirect(url_for('main.index'))
    
    loc = Location.query.get_or_404(loc_id)
    
    if loc.children:
        flash(f'Standort "{loc.name}" hat Unter-Kategorien. Nicht löschbar.', 'error')
        return redirect(url_for('main.admin_locations'))
    
    if loc.items.count() > 0:
        flash(f'Standort "{loc.name}" ist nicht leer.', 'error')
        return redirect(url_for('main.admin_locations'))
    
    db.session.delete(loc)
    db.session.commit()
    flash(f'Standort "{loc.name}" gelöscht.', 'success')
    
    return redirect(url_for('main.admin_locations'))
