from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_user, login_required, logout_user, current_user
from extensions import db
from models import User, Role, Location

# Blueprint Definition
main = Blueprint('main', __name__)

# -- HELPER --
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

# -- ROUTEN: PUBLIC --

@main.route('/')
def index():
    return render_template('index.html')

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

# -- ROUTEN: ADMIN USER --

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

# -- ROUTEN: ADMIN LOCATIONS (NEU) --

@main.route('/admin/locations')
@login_required
def admin_locations():
    if not current_user.has_role('Admin'):
        flash('Zugriff verweigert.', 'error')
        return redirect(url_for('main.index'))
    
    # Wir laden alle Orte. Für die Anzeige der Hierarchie sortieren wir später im Template
    # oder nutzen rekursive Logik. Der Einfachheit halber zeigen wir hier eine flache Liste mit Parent-Info.
    locations = Location.query.all()
    return render_template('admin_locations.html', locations=locations)

@main.route('/admin/locations/create', methods=['POST'])
@login_required
def location_create():
    if not current_user.has_role('Admin'):
        return redirect(url_for('main.index'))
    
    name = request.form.get('name')
    parent_id = request.form.get('parent_id')
    
    # parent_id ist im HTML ein String ('1', '2' oder leer/None)
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
    
    # Sicherheitscheck: Hat dieser Ort Unterorte?
    if loc.children:
        flash(f'Fehler: Standort "{loc.name}" hat noch Unter-Kategorien. Bitte erst diese löschen oder verschieben.', 'error')
        return redirect(url_for('main.admin_locations'))
    
    # Sicherheitscheck: Sind Items hier gelagert?
    # Hinweis: items ist ein dynamischer Query, daher .count()
    if loc.items.count() > 0:
        flash(f'Fehler: Es befinden sich noch {loc.items.count()} Medien an diesem Ort.', 'error')
        return redirect(url_for('main.admin_locations'))
    
    db.session.delete(loc)
    db.session.commit()
    flash(f'Standort "{loc.name}" gelöscht.', 'success')
    
    return redirect(url_for('main.admin_locations'))
