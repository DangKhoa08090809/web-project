from datetime import date
import math

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from extensions import db
from models import Maintenance, Scan, Vehicle


vehicles_bp = Blueprint("vehicles", __name__, url_prefix="/vehicles")


def _json_object():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else None


@vehicles_bp.get("")
@login_required
def get_vehicles():
    vehicles = Vehicle.query.filter_by(user_id=current_user.id).all()
    return jsonify([vehicle.to_dict() for vehicle in vehicles])


@vehicles_bp.post("")
@login_required
def add_vehicle():
    data = _json_object()
    if data is None:
        return jsonify({"error": "request body must be a JSON object"}), 400
    make = str(data.get("make", "")).strip()
    model = str(data.get("model", "")).strip()
    year = data.get("year")
    if not make or not model or isinstance(year, bool) or not isinstance(year, int) or not 1886 <= year <= 2200:
        return jsonify({"error": "make, model, and a valid integer year are required"}), 400
    vehicle = Vehicle(
        user_id=current_user.id,
        make=make[:50],
        model=model[:50],
        year=year,
        license_plate=str(data.get("license_plate", "")).strip()[:20] or None,
        vin=str(data.get("vin", "")).strip()[:17] or None,
    )
    db.session.add(vehicle)
    db.session.commit()
    return jsonify(vehicle.to_dict()), 201


@vehicles_bp.delete("/<int:vehicle_id>")
@login_required
def delete_vehicle(vehicle_id):
    vehicle = Vehicle.query.filter_by(id=vehicle_id, user_id=current_user.id).first_or_404()
    db.session.delete(vehicle)
    db.session.commit()
    return jsonify({"message": "Vehicle deleted"})


@vehicles_bp.get("/<int:vehicle_id>/scans")
@login_required
def get_scans(vehicle_id):
    Vehicle.query.filter_by(id=vehicle_id, user_id=current_user.id).first_or_404()
    scans = Scan.query.filter_by(vehicle_id=vehicle_id).order_by(Scan.scanned_at.desc()).limit(50).all()
    return jsonify([scan.to_dict() for scan in scans])


@vehicles_bp.get("/<int:vehicle_id>/maintenance")
@login_required
def get_maintenance(vehicle_id):
    Vehicle.query.filter_by(id=vehicle_id, user_id=current_user.id).first_or_404()
    records = Maintenance.query.filter_by(vehicle_id=vehicle_id).order_by(Maintenance.date.desc()).all()
    return jsonify([record.to_dict() for record in records])


@vehicles_bp.post("/<int:vehicle_id>/maintenance")
@login_required
def add_maintenance(vehicle_id):
    Vehicle.query.filter_by(id=vehicle_id, user_id=current_user.id).first_or_404()
    data = _json_object()
    if data is None:
        return jsonify({"error": "request body must be a JSON object"}), 400
    record_type = str(data.get("type", "")).strip()
    try:
        service_date = date.fromisoformat(str(data.get("date", "")))
    except ValueError:
        return jsonify({"error": "date must use YYYY-MM-DD format"}), 400
    mileage = data.get("mileage")
    if not record_type:
        return jsonify({"error": "type is required"}), 400
    if mileage is not None and (
        isinstance(mileage, bool) or not isinstance(mileage, (int, float)) or not math.isfinite(mileage) or mileage < 0
    ):
        return jsonify({"error": "mileage must be a non-negative finite number"}), 400
    record = Maintenance(
        vehicle_id=vehicle_id,
        date=service_date,
        type=record_type[:100],
        notes=str(data.get("notes", "")).strip() or None,
        mileage=float(mileage) if mileage is not None else None,
    )
    db.session.add(record)
    db.session.commit()
    return jsonify(record.to_dict()), 201
