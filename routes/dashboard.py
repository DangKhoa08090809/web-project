import re
import secrets

from flask import Blueprint, jsonify, make_response, render_template, request
from flask_login import current_user, login_required
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from extensions import db
from models import Device, RideSession, TelemetryRecord


dashboard_bp = Blueprint("dashboard", __name__)
DEVICE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{2,63}$")


def _device_query():
    query = Device.query
    return query if current_user.is_admin else query.filter_by(user_id=current_user.id)


def _session_query():
    query = RideSession.query.join(Device)
    return query if current_user.is_admin else query.filter(Device.user_id == current_user.id)


def _device_payload(data):
    fields = {}
    for name in ("device_id", "device_name", "vehicle_name", "ecu_type"):
        value = str(data.get(name, "")).strip()
        if not value:
            raise ValueError(f"{name} is required")
        if len(value) > (64 if name == "device_id" else 100):
            raise ValueError(f"{name} is too long")
        fields[name] = value
    if not DEVICE_ID_PATTERN.fullmatch(fields["device_id"]):
        raise ValueError("device_id must use 3-64 letters, numbers, dots, colons, underscores, or hyphens")
    if Device.query.filter_by(device_id=fields["device_id"]).first():
        raise ValueError("device_id already exists")
    return fields


def create_device(data):
    fields = _device_payload(data)
    token = secrets.token_urlsafe(32)
    device = Device(user_id=current_user.id, **fields, token_hash="")
    device.set_token(token)
    db.session.add(device)
    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        raise ValueError("device_id already exists") from exc
    return device, token


@dashboard_bp.get("/")
@login_required
def home():
    devices = _device_query().order_by(Device.device_name).all()
    latest = {}
    for device in devices:
        latest[device.id] = TelemetryRecord.query.filter_by(device_id=device.id).order_by(
            TelemetryRecord.timestamp.desc()
        ).first()
    return render_template("dashboard.html", devices=devices, latest=latest)


@dashboard_bp.route("/devices", methods=["GET", "POST"])
@login_required
def devices():
    token = None
    created_device = None
    error = None
    if request.method == "POST":
        try:
            created_device, token = create_device(request.form)
        except ValueError as exc:
            error = str(exc)
    response = make_response(render_template(
        "devices.html",
        devices=_device_query().order_by(Device.created_at.desc()).all(),
        new_token=token,
        created_device=created_device,
        error=error,
    ))
    if token:
        response.headers["Cache-Control"] = "no-store"
    return response


@dashboard_bp.get("/sessions")
@login_required
def sessions():
    rows = _session_query().order_by(RideSession.started_at.desc()).limit(250).all()
    return render_template("sessions.html", sessions=rows)


@dashboard_bp.get("/sessions/<int:session_pk>")
@login_required
def session_detail(session_pk):
    ride_session = _session_query().filter(RideSession.id == session_pk).first_or_404()
    records = TelemetryRecord.query.filter_by(ride_session_id=ride_session.id).order_by(
        TelemetryRecord.timestamp.desc()
    ).limit(5000).all()
    records.reverse()
    chart_data = [record.to_dict() for record in records]
    return render_template("session_detail.html", session=ride_session, records=records, chart_data=chart_data)


@dashboard_bp.route("/api/devices", methods=["GET", "POST"])
@login_required
def device_api():
    if request.method == "GET":
        return jsonify({"devices": [device.to_dict() for device in _device_query().all()]})
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "request body must be a JSON object"}), 400
    try:
        device, token = create_device(data)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    payload = device.to_dict()
    payload["token"] = token
    response = jsonify({"message": "Device created. Save this token; it will not be shown again.", "device": payload})
    response.headers["Cache-Control"] = "no-store"
    return response, 201


@dashboard_bp.post("/api/devices/<int:device_pk>/rotate-token")
@login_required
def rotate_device_token(device_pk):
    device = _device_query().filter(Device.id == device_pk).first_or_404()
    token = secrets.token_urlsafe(32)
    device.set_token(token)
    db.session.commit()
    response = jsonify({"message": "Token rotated. The previous token is now invalid.", "token": token})
    response.headers["Cache-Control"] = "no-store"
    return response
