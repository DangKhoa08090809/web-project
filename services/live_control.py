import math
import re
import secrets
from datetime import timedelta

from flask import current_app

from extensions import db
from models import Device, DeviceControlCommand, DeviceControlRuntime, utc_now


COMMAND_ENABLE_LIVE_MODE = "ENABLE_LIVE_MODE"
COMMAND_DISABLE_LIVE_MODE = "DISABLE_LIVE_MODE"
COMMAND_TYPES = {COMMAND_ENABLE_LIVE_MODE, COMMAND_DISABLE_LIVE_MODE}
ACK_STATUSES = {"applied", "rejected", "failed"}
ACTIVE_STATUSES = {"pending", "delivered"}
TERMINAL_STATUSES = {"applied", "rejected", "failed", "superseded", "stale"}
CONTROL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}$")


class ControlError(ValueError):
    def __init__(self, message: str, *, code: str = "CONTROL_ERROR", status_code: int = 422):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def _server_time() -> str:
    return utc_now().isoformat().replace("+00:00", "Z")


def _control_stale_seconds() -> int:
    return int(current_app.config.get("CONTROL_POLL_STALE_SECONDS", 10))


def _is_online(runtime: DeviceControlRuntime | None) -> bool:
    if runtime is None or not runtime.current_boot_id or runtime.last_control_poll_at is None:
        return False
    polled_at = runtime.last_control_poll_at
    if polled_at.tzinfo is None:
        polled_at = polled_at.replace(tzinfo=utc_now().tzinfo)
    return utc_now() - polled_at <= timedelta(seconds=_control_stale_seconds())


def _reject_unknown(payload: dict, allowed: set[str]) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ControlError(f"unknown control field: {unknown[0]}")


def _identifier(payload: dict, name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ControlError(f"{name} is required")
    value = value.strip()
    if not CONTROL_ID_RE.fullmatch(value):
        raise ControlError(f"{name} must be 1-100 characters using letters, numbers, dots, colons, underscores, or hyphens")
    return value


def _bool(payload: dict, name: str) -> bool:
    value = payload.get(name)
    if not isinstance(value, bool):
        raise ControlError(f"{name} must be a boolean")
    return value


def _optional_string(payload: dict, name: str, maximum: int) -> str | None:
    value = payload.get(name)
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ControlError(f"{name} must be a string")
    value = value.strip()
    if len(value) > maximum:
        raise ControlError(f"{name} must be {maximum} characters or fewer")
    return value or None


def _optional_uptime(payload: dict) -> int | None:
    value = payload.get("uptime_ms")
    if value in (None, ""):
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 2**63 - 1:
        raise ControlError("uptime_ms must be a non-negative integer")
    return value


def _status_message(payload: dict) -> str | None:
    value = payload.get("message")
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ControlError("message must be a string")
    return value.strip()[:255] or None


def validate_poll_payload(payload) -> dict:
    if not isinstance(payload, dict):
        raise ControlError("request body must be a JSON object", code="MALFORMED_JSON", status_code=400)
    _reject_unknown(payload, {"boot_id", "live_mode", "firmware_version", "uptime_ms"})
    return {
        "boot_id": _identifier(payload, "boot_id"),
        "live_mode": _bool(payload, "live_mode"),
        "firmware_version": _optional_string(payload, "firmware_version", 100),
        "uptime_ms": _optional_uptime(payload),
    }


def validate_ack_payload(payload) -> dict:
    if not isinstance(payload, dict):
        raise ControlError("request body must be a JSON object", code="MALFORMED_JSON", status_code=400)
    _reject_unknown(payload, {"boot_id", "command_id", "status", "live_mode", "message"})
    status = payload.get("status")
    if status not in ACK_STATUSES:
        raise ControlError("status must be applied, rejected, or failed")
    return {
        "boot_id": _identifier(payload, "boot_id"),
        "command_id": _identifier(payload, "command_id"),
        "status": status,
        "live_mode": _bool(payload, "live_mode"),
        "message": _status_message(payload),
    }


def _runtime_for_device(device: Device) -> DeviceControlRuntime | None:
    return DeviceControlRuntime.query.filter_by(device_id=device.id).first()


def _active_commands(device: Device, boot_id: str) -> list[DeviceControlCommand]:
    return (
        DeviceControlCommand.query.filter(
            DeviceControlCommand.device_id == device.id,
            DeviceControlCommand.boot_id == boot_id,
            DeviceControlCommand.status.in_(ACTIVE_STATUSES),
        )
        .order_by(DeviceControlCommand.created_at.desc(), DeviceControlCommand.id.desc())
        .all()
    )


def _latest_active_command(device: Device, boot_id: str) -> DeviceControlCommand | None:
    commands = _active_commands(device, boot_id)
    if not commands:
        return None
    latest = commands[0]
    now = utc_now()
    for command in commands[1:]:
        command.status = "superseded"
        command.updated_at = now
    return latest


def _stale_old_boot_commands(device: Device, current_boot_id: str) -> None:
    now = utc_now()
    old_commands = DeviceControlCommand.query.filter(
        DeviceControlCommand.device_id == device.id,
        DeviceControlCommand.boot_id != current_boot_id,
        DeviceControlCommand.status.in_(ACTIVE_STATUSES),
    ).all()
    for command in old_commands:
        command.status = "stale"
        command.updated_at = now


def _command_response(command: DeviceControlCommand | None) -> dict | None:
    return command.to_public_dict() if command else None


def poll_for_command(device: Device, payload) -> dict:
    data = validate_poll_payload(payload)
    now = utc_now()
    runtime = _runtime_for_device(device)
    if runtime is None:
        runtime = DeviceControlRuntime(
            device_id=device.id,
            current_boot_id=data["boot_id"],
            last_control_poll_at=now,
            reported_live_mode=bool(data["live_mode"]),
            firmware_version=data["firmware_version"],
            uptime_ms=data["uptime_ms"],
            created_at=now,
            updated_at=now,
        )
        db.session.add(runtime)
    elif runtime.current_boot_id != data["boot_id"]:
        runtime.current_boot_id = data["boot_id"]
        runtime.last_control_poll_at = now
        runtime.reported_live_mode = False
        runtime.firmware_version = data["firmware_version"]
        runtime.uptime_ms = data["uptime_ms"]
        runtime.last_command_id = None
        runtime.updated_at = now
        _stale_old_boot_commands(device, data["boot_id"])
    else:
        runtime.last_control_poll_at = now
        runtime.reported_live_mode = bool(data["live_mode"])
        runtime.firmware_version = data["firmware_version"]
        runtime.uptime_ms = data["uptime_ms"]
        runtime.updated_at = now

    command = _latest_active_command(device, data["boot_id"])
    if command and command.status == "pending":
        command.status = "delivered"
        command.delivered_at = command.delivered_at or now
        command.updated_at = now
        runtime.last_command_id = command.command_id
    db.session.commit()
    return {"ok": True, "command": _command_response(command), "server_time": _server_time()}


def _command_id() -> str:
    for _ in range(5):
        candidate = "cmd_" + secrets.token_urlsafe(18).rstrip("=")
        if not DeviceControlCommand.query.filter_by(command_id=candidate).first():
            return candidate
    raise ControlError("Could not generate a unique command id", code="COMMAND_ID_GENERATION_FAILED", status_code=500)


def create_live_command(device: Device, command_type: str, requested_by_user_id: int) -> dict:
    if command_type not in COMMAND_TYPES:
        raise ControlError("Unsupported command type")
    runtime = _runtime_for_device(device)
    if not _is_online(runtime):
        raise ControlError(
            "ECU Reader is offline for Live Mode control",
            code="DEVICE_OFFLINE",
            status_code=409,
        )

    now = utc_now()
    active = _active_commands(device, runtime.current_boot_id)
    same = next((command for command in active if command.command_type == command_type), None)
    if same is not None:
        for command in active:
            if command.id != same.id:
                command.status = "superseded"
                command.updated_at = now
        db.session.commit()
        return {"command": _command_response(same), "created": False, "control": control_state(device)}

    for command in active:
        command.status = "superseded"
        command.updated_at = now

    if command_type == COMMAND_ENABLE_LIVE_MODE and runtime.reported_live_mode:
        db.session.commit()
        return {"command": None, "created": False, "control": control_state(device)}

    command = DeviceControlCommand(
        device_id=device.id,
        requested_by_user_id=requested_by_user_id,
        command_id=_command_id(),
        boot_id=runtime.current_boot_id,
        command_type=command_type,
        status="pending",
        created_at=now,
        updated_at=now,
    )
    db.session.add(command)
    runtime.last_command_id = command.command_id
    runtime.updated_at = now
    db.session.commit()
    return {"command": _command_response(command), "created": True, "control": control_state(device)}


def acknowledge_command(device: Device, payload) -> dict:
    data = validate_ack_payload(payload)
    runtime = _runtime_for_device(device)
    command = DeviceControlCommand.query.filter_by(
        device_id=device.id,
        boot_id=data["boot_id"],
        command_id=data["command_id"],
    ).first()
    if command is None:
        raise ControlError("Command was not found for this device boot", code="COMMAND_NOT_FOUND", status_code=404)

    if runtime is None or runtime.current_boot_id != data["boot_id"]:
        raise ControlError("Command belongs to a stale device boot", code="STALE_BOOT_ID", status_code=409)

    now = utc_now()
    if command.status in TERMINAL_STATUSES:
        if command.status not in {"superseded", "stale"}:
            runtime.reported_live_mode = bool(data["live_mode"])
            runtime.last_command_id = command.command_id
            runtime.updated_at = now
            db.session.commit()
        return {
            "ok": True,
            "command_id": command.command_id,
            "status": command.status,
            "live_mode": runtime.reported_live_mode,
            "server_time": _server_time(),
        }

    command.status = data["status"]
    command.acknowledged_at = command.acknowledged_at or now
    command.reported_live_mode = bool(data["live_mode"])
    command.status_message = data["message"]
    command.updated_at = now
    runtime.reported_live_mode = bool(data["live_mode"])
    runtime.last_command_id = command.command_id
    runtime.updated_at = now
    db.session.commit()
    return {
        "ok": True,
        "command_id": command.command_id,
        "status": command.status,
        "live_mode": runtime.reported_live_mode,
        "server_time": _server_time(),
    }


def control_state(device: Device) -> dict:
    runtime = _runtime_for_device(device)
    online = _is_online(runtime)
    pending = None
    if online:
        pending = _latest_active_command(device, runtime.current_boot_id)
        db.session.flush()
    if not online:
        state = "offline"
        reported_live_mode = False
    elif pending and pending.command_type == COMMAND_ENABLE_LIVE_MODE:
        state = "enabling"
        reported_live_mode = bool(runtime.reported_live_mode)
    elif pending and pending.command_type == COMMAND_DISABLE_LIVE_MODE:
        state = "disabling"
        reported_live_mode = bool(runtime.reported_live_mode)
    elif runtime.reported_live_mode:
        state = "on"
        reported_live_mode = True
    else:
        state = "off"
        reported_live_mode = False

    last_poll = runtime.last_control_poll_at if runtime else None
    age_seconds = None
    if last_poll is not None:
        if last_poll.tzinfo is None:
            last_poll = last_poll.replace(tzinfo=utc_now().tzinfo)
        age_seconds = max(0, math.floor((utc_now() - last_poll).total_seconds()))

    return {
        "control_online": online,
        "state": state,
        "reported_live_mode": reported_live_mode,
        "current_boot_id": runtime.current_boot_id if runtime and online else None,
        "last_control_poll_at": last_poll.isoformat().replace("+00:00", "Z") if last_poll else None,
        "last_control_poll_age_seconds": age_seconds,
        "last_command_id": runtime.last_command_id if runtime and online else None,
        "pending_command": _command_response(pending) if pending else None,
        "stale_after_seconds": _control_stale_seconds(),
    }
