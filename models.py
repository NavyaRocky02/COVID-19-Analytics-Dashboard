
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class CovidRecord(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    zip_code = db.Column(db.String(20), nullable=True)
    week_start = db.Column(db.Date, nullable=True)
    cases_weekly = db.Column(db.Float, nullable=True)
    deaths_weekly = db.Column(db.Float, nullable=True)
    tests_weekly = db.Column(db.Float, nullable=True)
    cases_cumulative = db.Column(db.Float, nullable=True)
