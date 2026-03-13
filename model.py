# models.py — all database table definitions
from database import db
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime

# ─── USER ─────────────────────────────────────
class User(UserMixin, db.Model):
    __tablename__ = "users"

    id            = db.Column(db.Integer, primary_key=True)
    full_name     = db.Column(db.String(100), nullable=False)
    email         = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)

    vehicles = db.relationship("Vehicle", backref="owner", lazy=True, cascade="all, delete")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def to_dict(self):
        return {
            "id":         self.id,
            "full_name":  self.full_name,
            "email":      self.email,
            "created_at": self.created_at.isoformat(),
        }


# ─── VEHICLE ──────────────────────────────────
class Vehicle(db.Model):
    __tablename__ = "vehicles"

    id            = db.Column(db.Integer, primary_key=True)
    user_id       = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    make          = db.Column(db.String(50), nullable=False)
    model         = db.Column(db.String(50), nullable=False)
    year          = db.Column(db.Integer, nullable=False)
    license_plate = db.Column(db.String(20))
    vin           = db.Column(db.String(17))
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)

    scans       = db.relationship("Scan",        backref="vehicle", lazy=True, cascade="all, delete")
    maintenance = db.relationship("Maintenance", backref="vehicle", lazy=True, cascade="all, delete")

    def to_dict(self):
        return {
            "id":            self.id,
            "make":          self.make,
            "model":         self.model,
            "year":          self.year,
            "license_plate": self.license_plate,
            "vin":           self.vin,
        }


# ─── SCAN ─────────────────────────────────────
class Scan(db.Model):
    __tablename__ = "scans"

    id                 = db.Column(db.Integer, primary_key=True)
    vehicle_id         = db.Column(db.Integer, db.ForeignKey("vehicles.id"), nullable=False)
    scanned_at         = db.Column(db.DateTime, default=datetime.utcnow)
    battery_voltage    = db.Column(db.Float)
    alternator_voltage = db.Column(db.Float)
    temperature        = db.Column(db.Float)
    fuel_instant       = db.Column(db.Float)
    fuel_avg           = db.Column(db.Float)
    odometer           = db.Column(db.Float)
    alerts             = db.Column(db.Text)  # stored as JSON string

    def to_dict(self):
        return {
            "id":                 self.id,
            "scanned_at":         self.scanned_at.isoformat(),
            "battery_voltage":    self.battery_voltage,
            "alternator_voltage": self.alternator_voltage,
            "temperature":        self.temperature,
            "fuel_instant":       self.fuel_instant,
            "fuel_avg":           self.fuel_avg,
            "odometer":           self.odometer,
            "alerts":             self.alerts,
        }


# ─── MAINTENANCE ──────────────────────────────
class Maintenance(db.Model):
    __tablename__ = "maintenance"

    id         = db.Column(db.Integer, primary_key=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicles.id"), nullable=False)
    date       = db.Column(db.Date, nullable=False)
    type       = db.Column(db.String(100), nullable=False)
    notes      = db.Column(db.Text)
    mileage    = db.Column(db.Float)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id":      self.id,
            "date":    self.date.isoformat(),
            "type":    self.type,
            "notes":   self.notes,
            "mileage": self.mileage,
        }