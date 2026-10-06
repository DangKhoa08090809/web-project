from functools import wraps

from flask import Blueprint, current_app, g, jsonify, request
from sqlalchemy.exc import SQLAlchemyError

from extensions import db
from models import Device
from services.live_control import ControlError, acknowledge_command, poll_for_command
from services.pi_sync import store_pi_sync, validate_pi_sync
from services.provisioning import PairingError, pair_device
from services.live import LiveUnavailable, check_live_rate_limit, store_live_sample, validate_live_sample
from services.telemetry import ValidationError, store_upload, validate_batch, validate_single_upload


ingest_bp = Blueprint("ingest", __name__, url_prefix="/api")


def api_error(code: str, message: str, status_code: int):
    return jsonify({"ok": False, "error": {"code": code, "message": message}}), status_code


def device_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        device_id = request.headers.get("X-Device-ID", "").strip()
        authorization = request.headers.get("Authorization", "")
        scheme, separator, token = authorization.partition(" ")
        token = token.strip()
        if not device_id or not separator or scheme.lower() != "bearer" or not token:
            return api_error("DEVICE_AUTH_REQUIRED", "Valid X-Device-ID and Bearer token headers are required", 401)

        device = Device.query.filter_by(device_id=device_id).first()
        if not device or not device.check_token(token):
            return api_error("INVALID_DEVICE_CREDENTIALS", "Invalid device credentials", 401)
        if not device.is_active:
            return api_error("INACTIVE_DEVICE", "Device is inactive", 403)
        g.device = device
        return view(*args, **kwargs)

    return wrapped


def _json_body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return None
    return data


def _live_body_too_large() -> bool:
    max_bytes = int(current_app.config["LIVE_MAX_REQUEST_BYTES"])
    if request.content_length is not None and request.content_length > max_bytes:
        return True
    return len(request.get_data(cache=True)) > max_bytes


@ingest_bp.post("/device/pair")
def device_pair():
    data = _json_body()
    if data is None:
        return api_error("MALFORMED_JSON", "A valid JSON object is required", 400)
    try:
        device, token = pair_device(data)
    except PairingError as exc:
        return api_error(exc.code, str(exc), exc.status_code)
    response = jsonify({
        "ok": True,
        "device": {
            "device_id": device.device_id,
            "device_name": device.device_name,
            "vehicle_name": device.vehicle_name,
            "ecu_type": device.ecu_type,
            "firmware_version": device.firmware_version,
            "hardware_version": device.hardware_version,
        },
        "device_token": token,
    })
    response.headers["Cache-Control"] = "no-store"
    return response, 201


def _store(upload: dict, *, created_status: bool = False):
    try:
        acknowledgement = store_upload(g.device, upload)
    except ValidationError as exc:
        return api_error(exc.code, str(exc), exc.status_code)
    except SQLAlchemyError:
        db.session.rollback()
        return api_error("TELEMETRY_STORE_FAILED", "Telemetry could not be stored", 500)
    status_code = 201 if created_status and acknowledgement["inserted_count"] else 200
    return jsonify(acknowledgement), status_code


@ingest_bp.post("/telemetry")
@device_required
def telemetry():
    data = _json_body()
    if data is None:
        return api_error("MALFORMED_JSON", "A valid JSON object is required", 400)
    try:
        upload = validate_single_upload(data)
    except ValidationError as exc:
        return api_error(exc.code, str(exc), exc.status_code)
    return _store(upload, created_status=True)


@ingest_bp.post("/telemetry/live")
@device_required
def telemetry_live():
    if not request.is_json:
        return api_error("UNSUPPORTED_MEDIA_TYPE", "Content-Type must be application/json", 415)
    if _live_body_too_large():
        return api_error("REQUEST_TOO_LARGE", "Live telemetry request body is too large", 413)
    try:
        allowed, retry_after = check_live_rate_limit(g.device.device_id)
    except LiveUnavailable:
        return api_error("LIVE_PREVIEW_UNAVAILABLE", "Live Preview cache is temporarily unavailable", 503)
    if not allowed:
        response, status = api_error("LIVE_RATE_LIMITED", "Live telemetry rate limit exceeded", 429)
        response.headers["Retry-After"] = str(retry_after or 1)
        return response, status

    data = _json_body()
    if data is None:
        return api_error("MALFORMED_JSON", "A valid JSON object is required", 400)
    try:
        sample = validate_live_sample(data, device_id=g.device.device_id)
        store_live_sample(g.device.device_id, sample)
    except ValidationError as exc:
        return api_error(exc.code, str(exc), exc.status_code)
    except LiveUnavailable:
        return api_error("LIVE_PREVIEW_UNAVAILABLE", "Live Preview cache is temporarily unavailable", 503)

    response = jsonify({
        "ok": True,
        "device_id": g.device.device_id,
        "server_received_at": sample["server_received_at"],
    })
    response.headers["Cache-Control"] = "no-store"
    return response, 200


@ingest_bp.post("/logs/upload")
@device_required
def logs_upload():
    data = _json_body()
    if data is None:
        return api_error("MALFORMED_JSON", "A valid JSON object is required", 400)
    try:
        upload = validate_batch(data)
    except ValidationError as exc:
        return api_error(exc.code, str(exc), exc.status_code)
    return _store(upload)


@ingest_bp.post("/device/sync/session")
@device_required
def device_sync_session():
    if not request.is_json:
        return api_error("UNSUPPORTED_MEDIA_TYPE", "Content-Type must be application/json", 415)
    data = _json_body()
    if data is None:
        return api_error("MALFORMED_JSON", "A valid JSON object is required", 400)
    try:
        upload = validate_pi_sync(data)
        acknowledgement = store_pi_sync(g.device, upload)
    except ValidationError as exc:
        db.session.rollback()
        return api_error(exc.code, str(exc), exc.status_code)
    except SQLAlchemyError:
        db.session.rollback()
        return api_error("PI_SYNC_STORE_FAILED", "Pi session sync could not be stored", 500)
    return jsonify(acknowledgement), 200


@ingest_bp.post("/device/control/poll")
@device_required
def device_control_poll():
    if not request.is_json:
        return api_error("UNSUPPORTED_MEDIA_TYPE", "Content-Type must be application/json", 415)
    data = _json_body()
    if data is None:
        return api_error("MALFORMED_JSON", "A valid JSON object is required", 400)
    try:
        return jsonify(poll_for_command(g.device, data)), 200
    except ControlError as exc:
        return api_error(exc.code, str(exc), exc.status_code)
    except SQLAlchemyError:
        db.session.rollback()
        return api_error("CONTROL_STORE_FAILED", "Device control state could not be stored", 500)


@ingest_bp.post("/device/control/ack")
@device_required
def device_control_ack():
    if not request.is_json:
        return api_error("UNSUPPORTED_MEDIA_TYPE", "Content-Type must be application/json", 415)
    data = _json_body()
    if data is None:
        return api_error("MALFORMED_JSON", "A valid JSON object is required", 400)
    try:
        return jsonify(acknowledge_command(g.device, data)), 200
    except ControlError as exc:
        return api_error(exc.code, str(exc), exc.status_code)
    except SQLAlchemyError:
        db.session.rollback()
        return api_error("CONTROL_ACK_FAILED", "Device control acknowledgement could not be stored", 500)
