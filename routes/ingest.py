from functools import wraps

from flask import Blueprint, g, jsonify, request
from sqlalchemy.exc import SQLAlchemyError

from extensions import db
from models import Device
from services.telemetry import ValidationError, store_records, validate_batch, validate_record


ingest_bp = Blueprint("ingest", __name__, url_prefix="/api")


def device_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        device_id = request.headers.get("X-Device-ID", "").strip()
        authorization = request.headers.get("Authorization", "")
        scheme, separator, token = authorization.partition(" ")
        if not device_id or not separator or scheme.lower() != "bearer" or not token.strip():
            return jsonify({"error": "Valid X-Device-ID and Bearer token headers are required"}), 401
        device = Device.query.filter_by(device_id=device_id, is_active=True).first()
        if not device or not device.check_token(token.strip()):
            return jsonify({"error": "Invalid device credentials"}), 401
        g.device = device
        return view(*args, **kwargs)
    return wrapped


def _store(records, *, end_session=False):
    try:
        inserted, skipped = store_records(g.device, records, end_session=end_session)
    except ValidationError as exc:
        return jsonify({"error": str(exc)}), 400
    except SQLAlchemyError:
        db.session.rollback()
        return jsonify({"error": "Telemetry could not be stored"}), 500
    return jsonify({"message": "Telemetry accepted", "inserted": inserted, "skipped": skipped})


@ingest_bp.post("/telemetry")
@device_required
def telemetry():
    data = request.get_json(silent=True)
    if data is None:
        return jsonify({"error": "A valid JSON request body is required"}), 400
    try:
        record = validate_record(data)
    except ValidationError as exc:
        return jsonify({"error": str(exc)}), 400
    response = _store([record])
    if isinstance(response, tuple):
        return response
    response.status_code = 201 if response.get_json()["inserted"] else 200
    return response


@ingest_bp.post("/logs/upload")
@device_required
def logs_upload():
    data = request.get_json(silent=True)
    if data is None:
        return jsonify({"error": "A valid JSON request body is required"}), 400
    try:
        records = validate_batch(data)
    except ValidationError as exc:
        return jsonify({"error": str(exc)}), 400
    return _store(records, end_session=True)
