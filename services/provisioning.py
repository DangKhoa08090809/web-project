import re
import secrets
from datetime import timedelta

from flask import current_app
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from extensions import db
from models import Device, DevicePairingCode, utc_now


DEVICE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{2,63}$")
PAIRING_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


class PairingError(ValueError):
    def __init__(self, message: str, *, code: str, status_code: int):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def _as_utc(value):
    if value.tzinfo is None:
        return value.replace(tzinfo=utc_now().tzinfo)
    return value


def normalize_pairing_code(code: str) -> str:
    return "".join(str(code).upper().replace("-", " ").split())


def generate_pairing_secret() -> str:
    raw = "".join(secrets.choice(PAIRING_ALPHABET) for _ in range(16))
    return "-".join(raw[index : index + 4] for index in range(0, len(raw), 4))


def create_pairing_code(user_id: int) -> tuple[DevicePairingCode, str]:
    code = generate_pairing_secret()
    ttl = int(current_app.config["PAIRING_CODE_TTL_SECONDS"])
    pairing_code = DevicePairingCode(
        user_id=user_id,
        code_hash=DevicePairingCode.hash_code(normalize_pairing_code(code)),
        expires_at=utc_now() + timedelta(seconds=ttl),
    )
    db.session.add(pairing_code)
    db.session.commit()
    return pairing_code, code


def validate_device_metadata(data: dict, *, require_pairing_code: bool = False) -> dict:
    if not isinstance(data, dict):
        raise PairingError("request body must be a JSON object", code="MALFORMED_JSON", status_code=400)
    fields = {}
    if require_pairing_code:
        pairing_code = data.get("pairing_code")
        if not isinstance(pairing_code, str) or not normalize_pairing_code(pairing_code):
            raise PairingError("pairing_code is required", code="INVALID_PAIRING_CODE", status_code=400)
        fields["pairing_code"] = pairing_code
    for name in ("device_id", "device_name", "vehicle_name", "ecu_type"):
        value = str(data.get(name, "")).strip()
        if not value:
            raise PairingError(f"{name} is required", code="INVALID_DEVICE_METADATA", status_code=422)
        if len(value) > (64 if name == "device_id" else 100):
            raise PairingError(f"{name} is too long", code="INVALID_DEVICE_METADATA", status_code=422)
        fields[name] = value
    if not DEVICE_ID_PATTERN.fullmatch(fields["device_id"]):
        raise PairingError(
            "device_id must use 3-64 letters, numbers, dots, colons, underscores, or hyphens",
            code="INVALID_DEVICE_METADATA",
            status_code=422,
        )
    for name in ("firmware_version", "hardware_version"):
        value = data.get(name)
        if value in (None, ""):
            fields[name] = None
            continue
        value = str(value).strip()
        if len(value) > 100:
            raise PairingError(f"{name} is too long", code="INVALID_DEVICE_METADATA", status_code=422)
        fields[name] = value
    return fields


def create_manual_device(user_id: int, data: dict) -> tuple[Device, str]:
    fields = validate_device_metadata(data)
    if Device.query.filter_by(device_id=fields["device_id"]).first():
        raise PairingError("device_id already exists", code="DUPLICATE_DEVICE_ID", status_code=409)
    token = secrets.token_urlsafe(32)
    device = Device(user_id=user_id, token_hash="", paired_at=utc_now(), **fields)
    device.set_token(token)
    db.session.add(device)
    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        raise PairingError("device_id already exists", code="DUPLICATE_DEVICE_ID", status_code=409) from exc
    return device, token


def pair_device(data: dict) -> tuple[Device, str]:
    if not isinstance(data, dict):
        raise PairingError("request body must be a JSON object", code="MALFORMED_JSON", status_code=400)
    pairing_secret = data.get("pairing_code")
    if not isinstance(pairing_secret, str) or not normalize_pairing_code(pairing_secret):
        raise PairingError("pairing_code is required", code="INVALID_PAIRING_CODE", status_code=400)
    code_hash = DevicePairingCode.hash_code(normalize_pairing_code(pairing_secret))
    pairing_code = db.session.execute(
        select(DevicePairingCode).where(DevicePairingCode.code_hash == code_hash).with_for_update()
    ).scalar_one_or_none()
    if pairing_code is None:
        raise PairingError("Invalid pairing code", code="INVALID_PAIRING_CODE", status_code=400)
    if pairing_code.used_at is not None:
        raise PairingError("Pairing code has already been used", code="PAIRING_CODE_USED", status_code=409)
    if utc_now() >= _as_utc(pairing_code.expires_at):
        pairing_code.attempt_count += 1
        db.session.commit()
        raise PairingError("Pairing code has expired", code="PAIRING_CODE_EXPIRED", status_code=400)
    max_attempts = int(current_app.config["PAIRING_MAX_ATTEMPTS"])
    if pairing_code.attempt_count >= max_attempts:
        raise PairingError("Pairing code attempt limit exceeded", code="PAIRING_ATTEMPTS_EXCEEDED", status_code=429)
    pairing_code.attempt_count += 1
    if not pairing_code.owner.is_active:
        db.session.commit()
        raise PairingError("User account is disabled", code="USER_DISABLED", status_code=403)
    try:
        fields = validate_device_metadata(data)
    except PairingError:
        db.session.commit()
        raise
    if Device.query.filter_by(device_id=fields["device_id"]).first():
        db.session.commit()
        raise PairingError("device_id already exists", code="DUPLICATE_DEVICE_ID", status_code=409)

    token = secrets.token_urlsafe(32)
    device = Device(user_id=pairing_code.user_id, token_hash="", paired_at=utc_now(), **fields)
    device.set_token(token)
    db.session.add(device)
    db.session.flush()
    pairing_code.used_at = utc_now()
    pairing_code.device_id = device.id
    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        raise PairingError("device_id already exists", code="DUPLICATE_DEVICE_ID", status_code=409) from exc
    return device, token
