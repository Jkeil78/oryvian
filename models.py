from datetime import datetime
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from extensions import db, login_manager

# -- USER & RBAC --

class Role(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True)
    users = db.relationship('User', backref='role', lazy='dynamic')

    def __repr__(self):
        return f'<Role {self.name}>'

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    role_id = db.Column(db.Integer, db.ForeignKey('role.id'))
    items_created = db.relationship('MediaItem', backref='creator_user', lazy='dynamic')
    language = db.Column(db.String(10), default='en')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        # 1. Check if it is already a secure hash
        try:
            if check_password_hash(self.password_hash, password):
                return True
        except (ValueError, TypeError):
            pass

        # 2. Migration: Check for old format (assuming plaintext/simple here)
        if self.password_hash == password:
            self.set_password(password)
            db.session.commit()
            return True
            
        return False
    
    def has_role(self, role_name):
        if self.role is None:
            return False
        return self.role.name == role_name

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


class AppSetting(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(50), unique=True, nullable=False) # e.g. 'discogs_token'
    value = db.Column(db.String(255), nullable=True)          # e.g. 'a1b2c3...'

    def __repr__(self):
        return f'<AppSetting {self.key}>'

# -- MEDIA MODELS --

class Location(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    parent_id = db.Column(db.Integer, db.ForeignKey('location.id'), nullable=True)
    children = db.relationship('Location', backref=db.backref('parent', remote_side=[id]))
    items = db.relationship('MediaItem', backref='location', lazy='dynamic')

    def __repr__(self):
        return f'<Location {self.name}>'
    @property
    def full_path(self):
        """Returns the full path: 'Grandpa > Father > Child'"""
        chain = []
        current = self
        while current:
            chain.insert(0, current.name)
            current = current.parent
            # Safety brake against infinite loops (if A is parent of B and B is parent of A)
            if len(chain) > 20: 
                break 
        return " > ".join(chain)

class Collection(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    description = db.Column(db.Text, nullable=True)
    items = db.relationship('MediaItem', backref='collection', lazy='dynamic')

class MediaItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    inventory_number = db.Column(db.String(50), unique=True, nullable=False)
    barcode = db.Column(db.String(50), unique=True, nullable=True)
    title = db.Column(db.String(200), nullable=False)
    category = db.Column(db.String(50), nullable=False)
    author_artist = db.Column(db.String(200))
    release_year = db.Column(db.Integer)
    description = db.Column(db.Text)
    image_filename = db.Column(db.String(200))
    location_id = db.Column(db.Integer, db.ForeignKey('location.id'), nullable=True)
    collection_id = db.Column(db.Integer, db.ForeignKey('collection.id'), nullable=True)
    volume_number = db.Column(db.Integer, nullable=True)
    lent_to = db.Column(db.String(100), nullable=True)
    lent_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    tracks = db.relationship('Track', backref='media_item', cascade="all, delete-orphan", lazy='dynamic')

class Track(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    media_item_id = db.Column(db.Integer, db.ForeignKey('media_item.id'), nullable=False)
    position = db.Column(db.Integer)
    title = db.Column(db.String(200), nullable=False)
    duration = db.Column(db.String(20))
