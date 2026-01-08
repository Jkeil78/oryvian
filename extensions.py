from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager

# Wir erstellen die Instanzen hier, verknüpfen sie aber noch nicht mit der App
db = SQLAlchemy()
login_manager = LoginManager()
