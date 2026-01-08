import os
from flask import Flask
from extensions import db, login_manager
from routes import main, create_initial_data

app = Flask(__name__)

# -- KONFIGURATION --
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///medien.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = 'dev-key-bitte-aendern'

# Upload Konfiguration
# Pfad: ./static/uploads
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['UPLOAD_FOLDER'] = os.path.join(basedir, 'static/uploads')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # Max 16 MB pro Datei

# -- INITIALISIERUNG --
db.init_app(app)
login_manager.init_app(app)
login_manager.login_view = 'main.login'

app.register_blueprint(main)

# -- START --
if __name__ == '__main__':
    # Sicherstellen, dass der Upload-Ordner existiert
    upload_path = app.config['UPLOAD_FOLDER']
    if not os.path.exists(upload_path):
        os.makedirs(upload_path)
        print(f"Ordner erstellt: {upload_path}")

    with app.app_context():
        db.create_all()
        create_initial_data()
    
    app.run(host='0.0.0.0', port=5000, debug=True)
