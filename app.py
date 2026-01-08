from flask import Flask
from extensions import db, login_manager
from routes import main, create_initial_data

app = Flask(__name__)

# -- KONFIGURATION --
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///medien.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = 'dev-key-bitte-aendern'

# -- INITIALISIERUNG --
# Extensions mit der App verbinden
db.init_app(app)
login_manager.init_app(app)
login_manager.login_view = 'main.login' # Wichtig: 'main.' Prefix wegen Blueprint

# Blueprint registrieren
app.register_blueprint(main)

# -- START --
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        create_initial_data()
    
    app.run(host='0.0.0.0', port=5000, debug=True)
