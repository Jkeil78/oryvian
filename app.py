import os
from datetime import timedelta
from flask import Flask
from sqlalchemy import text, inspect
from extensions import db, login_manager, csrf
from routes import main, create_initial_data

# 1. instance_relative_config=True activates the separate "instance" folder for the DB
app = Flask(__name__, instance_relative_config=True)

# -- CONFIGURATION --

# 2. Set database path dynamically
# The database now lands in: /app/instance/inventory.db
db_filename = 'inventory.db'
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{os.path.join(app.instance_path, db_filename)}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Secret Key (Ideally load via environment variable later)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-key-bitte-aendern')

# Remember Me: Stay logged in for 7 days
app.config['REMEMBER_COOKIE_DURATION'] = timedelta(days=7)

# Upload Configuration
# We use app.root_path to ensure we stay in the app directory
app.config['UPLOAD_FOLDER'] = os.path.join(app.root_path, 'static/uploads')
app.config['MAX_CONTENT_LENGTH'] = 128 * 1024 * 1024  # Max 128 MB

# -- INITIALIZATION --
db.init_app(app)
login_manager.init_app(app)
csrf.init_app(app)
login_manager.login_view = 'main.login'

app.register_blueprint(main)

if __name__ == '__main__':
    # 3. IMPORTANT: Create folders if they don't exist
    # This prevents crashes when starting the app for the first time (or without Docker Volume).
    
    # Instance Folder (for database)
    try:
        os.makedirs(app.instance_path)
    except OSError:
        pass # Folder already exists, all good

    # Upload Folder (for images)
    if not os.path.exists(app.config['UPLOAD_FOLDER']):
        os.makedirs(app.config['UPLOAD_FOLDER'])

    # Database Initialization

    with app.app_context():
        # Creates tables only if the file inventory.db does not exist/is empty
        db.create_all()

        # -- MIGRATION CHECK --
        try:
            inspector = inspect(db.engine)
            if 'user' in inspector.get_table_names():
                columns = [col['name'] for col in inspector.get_columns('user')]
                if 'language' not in columns:
                    with db.engine.connect() as conn:
                        conn.execute(text("ALTER TABLE user ADD COLUMN language VARCHAR(10) DEFAULT 'en'"))
                        conn.commit()
                
                if 'theme' not in columns:
                    with db.engine.connect() as conn:
                        conn.execute(text("ALTER TABLE user ADD COLUMN theme VARCHAR(20) DEFAULT 'cerulean'"))
                        conn.commit()

                if 'sort_field' not in columns:
                    with db.engine.connect() as conn:
                        conn.execute(text("ALTER TABLE user ADD COLUMN sort_field VARCHAR(50) DEFAULT 'added'"))
                        conn.commit()

                if 'sort_order' not in columns:
                    with db.engine.connect() as conn:
                        conn.execute(text("ALTER TABLE user ADD COLUMN sort_order VARCHAR(10) DEFAULT 'desc'"))
                        conn.commit()

            if 'media_item' in inspector.get_table_names():
                # On SQLite, UNIQUE constraints can be represented as indexes OR unique constraints
                indexes = inspector.get_indexes('media_item')
                constraints = inspector.get_unique_constraints('media_item')
                
                has_unique_barcode = False
                # Check indexes
                for idx in indexes:
                    if 'barcode' in idx['column_names'] and idx['unique']:
                        has_unique_barcode = True
                        break
                
                # Check unique constraints if not found in indexes
                if not has_unique_barcode:
                    for cnst in constraints:
                        if 'barcode' in cnst['column_names']:
                            has_unique_barcode = True
                            break
                
                if has_unique_barcode:
                    try:
                        with db.engine.connect() as conn:
                            # 1. Disable FKs
                            conn.execute(text("PRAGMA foreign_keys=OFF"))
                            
                            # 2. Rename old table
                            conn.execute(text("ALTER TABLE media_item RENAME TO media_item_old"))
                            conn.commit()
                        
                        # 3. Create new table (with current model: unique=False)
                        db.create_all()
                        
                        with db.engine.connect() as conn:
                            # 4. Copy data (explicit columns to avoid issues with order/count)
                            cols = "id, inventory_number, barcode, title, category, author_artist, release_year, description, image_filename, location_id, collection_id, volume_number, lent_to, lent_at, created_at, user_id"
                            conn.execute(text(f"INSERT INTO media_item ({cols}) SELECT {cols} FROM media_item_old"))
                            
                            # 5. Drop old table
                            conn.execute(text("DROP TABLE media_item_old"))
                            
                            # 6. Re-enable FKs
                            conn.execute(text("PRAGMA foreign_keys=ON"))
                            conn.commit()
                    except Exception:
                        # Clean up if possible
                        try:
                            with db.engine.connect() as conn:
                                conn.execute(text("PRAGMA foreign_keys=ON"))
                                conn.commit()
                        except: pass
        except Exception:
            pass
        
        # Create Admin User & Default Data
        create_initial_data()
        
    # Start
    app.run(host='0.0.0.0', port=5000, debug=False)
