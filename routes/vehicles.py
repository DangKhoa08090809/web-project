# routes/vehicles.py — vehicle CRUD, scans, maintenance
from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user
from models import Vehicle, Scan, Maintenance
from database import db
import json

vehicles_bp = Blueprint("vehicles", __name__, url_prefix="/vehicles")

# ─── VEHICLES ─────────────────────────────────

@vehicles_bp.route("", methods=["GET"])
@login_required
def get_vehicles():
    vehicles = Vehicle.query.filter_by(user_id=current_user.id).all()
    return jsonify([v.to_dict() for v in vehicles])


@vehicles_bp.route("", methods=["POST"])
@login_required
def add_vehicle():
    data = request.get_json()
    v = Vehicle(
        user_id       = current_user.id,
        make          = data.get("make"),
        model         = data.get("model"),
        year          = data.get("year"),
        license_plate = data.get("license_plate"),
        vin           = data.get("vin"),
    )
    db.session.add(v)
    db.session.commit()
    return jsonify(v.to_dict()), 201


@vehicles_bp.route("/<int:vehicle_id>", methods=["DELETE"])
@login_required
def delete_vehicle(vehicle_id):
    v = Vehicle.query.filter_by(id=vehicle_id, user_id=current_user.id).first_or_404()
    db.session.delete(v)
    db.session.commit()
    return jsonify({"message": "Vehicle deleted"})


# ─── SCAN HISTORY ─────────────────────────────

@vehicles_bp.route("/<int:vehicle_id>/scans", methods=["GET"])
@login_required
def get_scans(vehicle_id):
    Vehicle.query.filter_by(id=vehicle_id, user_id=current_user.id).first_or_404()
    scans = Scan.query.filter_by(vehicle_id=vehicle_id).order_by(Scan.scanned_at.desc()).limit(50).all()
    return jsonify([s.to_dict() for s in scans])


# ─── MAINTENANCE RECORDS ──────────────────────

@vehicles_bp.route("/<int:vehicle_id>/maintenance", methods=["GET"])
@login_required
def get_maintenance(vehicle_id):
    Vehicle.query.filter_by(id=vehicle_id, user_id=current_user.id).first_or_404()
    records = Maintenance.query.filter_by(vehicle_id=vehicle_id).order_by(Maintenance.date.desc()).all()
    return jsonify([r.to_dict() for r in records])


@vehicles_bp.route("/<int:vehicle_id>/maintenance", methods=["POST"])
@login_required
def add_maintenance(vehicle_id):
    Vehicle.query.filter_by(id=vehicle_id, user_id=current_user.id).first_or_404()
    data   = request.get_json()
    record = Maintenance(
        vehicle_id = vehicle_id,
        date       = data.get("date"),
        type       = data.get("type"),
        notes      = data.get("notes"),
        mileage    = data.get("mileage"),
    )
    db.session.add(record)
    db.session.commit()
    return jsonify(record.to_dict()), 201