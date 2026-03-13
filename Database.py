# database.py — DB connection setup
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

def init_db(app):
    """Call this once in app.py to connect the database."""
    db.init_app(app)
    with app.app_context():
        db.create_all()  # creates all tables if they don't exist