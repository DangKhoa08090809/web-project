import json
import math
import queue
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Iterator

from flask import current_app

from models import utc_now
from services.telemetry import ValidationError, _parse_timestamp


HEX_RE = re.compile(r"^[0-9a-fA-F]*$")

STRING_LIMITS = {
    "session_id": 100,
    "parser_version": 64,
    "ecu_profile_id": 100,
}

NUMERIC_LIMITS = {
    "rpm": (0, 100_000),
    "tps": (0, 100),
    "tps_voltage": (0, 10),
    "tps_raw_candidate": (0, 255),
    "ect": (-80, 300),
    "iat": (-80, 300),
    "battery": (0, 100),
    "battery_voltage": (0, 100),
    "iat_c": (-80, 300),
    "ect_c_candidate": (-80, 300),
    "map_raw": (0, 255),
    "injector_ms": (0, 1000),
    "ignition_deg": (-180, 180),
}

INTEGER_LIMITS = {
    "seq": (0, 2**63 - 1),
    "device_time_ms": (0, 2**63 - 1),
    "injector_raw": (0, 2**31 - 1),
    "raw_length": (0, 4096),
}

BOOLEAN_FIELDS = {"fuel_cut_inferred"}

ALLOWED_FIELDS = (
    set(STRING_LIMITS)
    | set(NUMERIC_LIMITS)
    | set(INTEGER_LIMITS)
    | BOOLEAN_FIELDS
    | {"timestamp", "raw_frame"}
)


class LiveUnavailable(RuntimeError):
    pass


def isoformat_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_live_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _live_raw_frame_max_chars() -> int:
    try:
        return int(current_app.config.get("LIVE_RAW_FRAME_MAX_CHARS", 256))
    except RuntimeError:
        return 256


def _finite_number(payload: dict, name: str, minimum: float, maximum: float) -> float | None:
    value = payload.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValidationError(f"{name} must be a finite number")
    if not minimum <= float(value) <= maximum:
        raise ValidationError(f"{name} must be between {minimum} and {maximum}")
    return float(value)


def _integer(payload: dict, name: str, minimum: int, maximum: int, *, required: bool = False) -> int | None:
    value = payload.get(name)
    if value is None:
        if required:
            raise ValidationError(f"{name} is required")
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValidationError(f"{name} must be a non-negative integer")
    return value


def _string(payload: dict, name: str, maximum: int, *, required: bool = False) -> str | None:
    value = payload.get(name)
    if value in (None, ""):
        if required:
            raise ValidationError(f"{name} is required")
        return None
    if not isinstance(value, str):
        raise ValidationError(f"{name} must be a string")
    value = value.strip()
    if not value:
        if required:
            raise ValidationError(f"{name} is required")
        return None
    if len(value) > maximum:
        raise ValidationError(f"{name} must be {maximum} characters or fewer")
    return value


def validate_live_sample(payload: dict, *, device_id: str) -> dict:
    if not isinstance(payload, dict):
        raise ValidationError("request body must be a JSON object", code="MALFORMED_JSON", status_code=400)

    unknown = sorted(set(payload) - ALLOWED_FIELDS)
    if unknown:
        raise ValidationError(f"unknown live field: {unknown[0]}")

    now = utc_now()
    ttl = int(current_app.config.get("LIVE_SAMPLE_TTL_SECONDS", 15))
    sample = {
        "device_id": device_id,
        "server_received_at": isoformat_z(now),
        "live_expires_at": isoformat_z(now + timedelta(seconds=ttl)),
        "session_id": _string(payload, "session_id", STRING_LIMITS["session_id"], required=True),
        "seq": _integer(payload, "seq", *INTEGER_LIMITS["seq"], required=True),
        "device_time_ms": _integer(payload, "device_time_ms", *INTEGER_LIMITS["device_time_ms"], required=True),
    }

    timestamp = _parse_timestamp(payload.get("timestamp"), field_name="timestamp")
    sample["timestamp"] = isoformat_z(timestamp) if timestamp else None

    for name, (minimum, maximum) in NUMERIC_LIMITS.items():
        value = _finite_number(payload, name, minimum, maximum)
        if value is not None:
            sample[name] = value

    for name, (minimum, maximum) in INTEGER_LIMITS.items():
        if name in sample:
            continue
        value = _integer(payload, name, minimum, maximum)
        if value is not None:
            sample[name] = value

    for name in BOOLEAN_FIELDS:
        value = payload.get(name)
        if value is None:
            continue
        if not isinstance(value, bool):
            raise ValidationError(f"{name} must be a boolean")
        sample[name] = value

    parser_version = _string(payload, "parser_version", STRING_LIMITS["parser_version"], required=False)
    if parser_version is not None:
        sample["parser_version"] = parser_version
    ecu_profile_id = _string(payload, "ecu_profile_id", STRING_LIMITS["ecu_profile_id"], required=False)
    if ecu_profile_id is not None:
        sample["ecu_profile_id"] = ecu_profile_id

    raw_frame = payload.get("raw_frame")
    if raw_frame is not None:
        if not isinstance(raw_frame, str):
            raise ValidationError("raw_frame must be a hexadecimal string")
        raw_frame = raw_frame.strip()
        if len(raw_frame) > _live_raw_frame_max_chars():
            raise ValidationError(f"raw_frame must be {_live_raw_frame_max_chars()} hexadecimal characters or fewer")
        if len(raw_frame) % 2 != 0 or not HEX_RE.fullmatch(raw_frame):
            raise ValidationError("raw_frame must contain an even number of hexadecimal characters")
        sample["raw_frame"] = raw_frame.upper()
        sample.setdefault("raw_length", len(raw_frame) // 2)

    return sample


def sample_is_online(sample: dict | None) -> bool:
    if not sample:
        return False
    expires_at = parse_live_time(sample.get("live_expires_at"))
    return bool(expires_at and expires_at > utc_now())


def live_status(sample: dict | None) -> dict:
    if not sample:
        return {"online": False, "state": "offline", "label": "Offline", "age_seconds": None}
    received_at = parse_live_time(sample.get("server_received_at"))
    if not received_at or not sample_is_online(sample):
        return {"online": False, "state": "offline", "label": "Offline", "age_seconds": None}
    age = max(0, int((utc_now() - received_at).total_seconds()))
    if age <= 2:
        return {"online": True, "state": "live", "label": "Live now", "age_seconds": age}
    return {
        "online": True,
        "state": "recent",
        "label": f"Last live sample {age} seconds ago",
        "age_seconds": age,
    }


def latest_key(device_id: str) -> str:
    return f"drisafe:live:{device_id}:latest"


def channel_key(device_id: str) -> str:
    return f"drisafe:live:{device_id}:channel"


def rate_tokens_key(device_id: str) -> str:
    return f"drisafe:live:{device_id}:rate:tokens"


def rate_ts_key(device_id: str) -> str:
    return f"drisafe:live:{device_id}:rate:ts"


class UnavailableLiveBackend:
    name = "unavailable"

    def __init__(self, reason: str):
        self.reason = reason

    def health(self) -> tuple[bool, str]:
        return False, self.reason

    def set_latest(self, device_id: str, sample: dict, ttl_seconds: int) -> None:
        raise LiveUnavailable(self.reason)

    def get_latest(self, device_id: str) -> dict | None:
        raise LiveUnavailable(self.reason)

    def check_rate_limit(self, device_id: str, *, rate_per_second: int, burst: int) -> tuple[bool, int]:
        raise LiveUnavailable(self.reason)

    def listen(self, device_id: str, heartbeat_seconds: int) -> Iterator[dict | None]:
        raise LiveUnavailable(self.reason)


class InMemoryLiveBackend:
    name = "memory"

    def __init__(self):
        self._lock = Lock()
        self._latest = {}
        self._subscribers = defaultdict(list)
        self._rate = {}

    def health(self) -> tuple[bool, str]:
        return True, "memory"

    def set_latest(self, device_id: str, sample: dict, ttl_seconds: int) -> None:
        payload = json.dumps(sample, separators=(",", ":"))
        expires_at = time.time() + ttl_seconds
        with self._lock:
            self._latest[device_id] = (payload, expires_at)
            subscribers = list(self._subscribers[channel_key(device_id)])
        for subscriber in subscribers:
            subscriber.put(payload)

    def get_latest(self, device_id: str) -> dict | None:
        with self._lock:
            stored = self._latest.get(device_id)
            if not stored:
                return None
            payload, expires_at = stored
            if expires_at <= time.time():
                self._latest.pop(device_id, None)
                return None
        try:
            sample = json.loads(payload)
        except json.JSONDecodeError:
            return None
        if not sample_is_online(sample):
            with self._lock:
                self._latest.pop(device_id, None)
            return None
        return sample

    def check_rate_limit(self, device_id: str, *, rate_per_second: int, burst: int) -> tuple[bool, int]:
        now = time.monotonic()
        with self._lock:
            tokens, last_seen = self._rate.get(device_id, (float(burst), now))
            tokens = min(float(burst), tokens + max(0.0, now - last_seen) * rate_per_second)
            if tokens >= 1:
                self._rate[device_id] = (tokens - 1, now)
                return True, 0
            retry_after = max(1, math.ceil((1 - tokens) / rate_per_second))
            self._rate[device_id] = (tokens, now)
            return False, retry_after

    def listen(self, device_id: str, heartbeat_seconds: int) -> Iterator[dict | None]:
        channel = channel_key(device_id)
        subscriber = queue.Queue()
        with self._lock:
            self._subscribers[channel].append(subscriber)
        try:
            while True:
                try:
                    payload = subscriber.get(timeout=heartbeat_seconds)
                except queue.Empty:
                    yield None
                    continue
                try:
                    yield json.loads(payload)
                except json.JSONDecodeError:
                    continue
        finally:
            with self._lock:
                if subscriber in self._subscribers[channel]:
                    self._subscribers[channel].remove(subscriber)

    def subscriber_count(self, device_id: str) -> int:
        with self._lock:
            return len(self._subscribers[channel_key(device_id)])


class RedisLiveBackend:
    name = "redis"

    RATE_LIMIT_SCRIPT = """
local tokens_key = KEYS[1]
local ts_key = KEYS[2]
local now_ms = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])
local burst = tonumber(ARGV[3])
local ttl = tonumber(ARGV[4])
local tokens = tonumber(redis.call("GET", tokens_key))
local last_ms = tonumber(redis.call("GET", ts_key))
if tokens == nil then
  tokens = burst
end
if last_ms == nil then
  last_ms = now_ms
end
local delta_ms = math.max(0, now_ms - last_ms)
tokens = math.min(burst, tokens + (delta_ms * rate / 1000))
local allowed = 0
local retry_ms = 0
if tokens >= 1 then
  allowed = 1
  tokens = tokens - 1
else
  retry_ms = math.ceil((1 - tokens) * 1000 / rate)
end
redis.call("SET", tokens_key, tokens, "EX", ttl)
redis.call("SET", ts_key, now_ms, "EX", ttl)
return {allowed, retry_ms}
"""

    def __init__(self, redis_url: str):
        try:
            from redis import Redis
        except ImportError as exc:
            raise LiveUnavailable("The redis Python package is not installed") from exc
        self.client = Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=1.0,
            socket_timeout=20.0,
            health_check_interval=30,
        )

    def _redis_error(self):
        try:
            from redis.exceptions import RedisError
        except ImportError:
            return Exception
        return RedisError

    def health(self) -> tuple[bool, str]:
        try:
            self.client.ping()
        except self._redis_error():
            return False, "unavailable"
        return True, "ok"

    def set_latest(self, device_id: str, sample: dict, ttl_seconds: int) -> None:
        payload = json.dumps(sample, separators=(",", ":"))
        try:
            pipe = self.client.pipeline()
            pipe.set(latest_key(device_id), payload, ex=ttl_seconds)
            pipe.publish(channel_key(device_id), payload)
            pipe.execute()
        except self._redis_error() as exc:
            raise LiveUnavailable("Redis is unavailable") from exc

    def get_latest(self, device_id: str) -> dict | None:
        try:
            payload = self.client.get(latest_key(device_id))
        except self._redis_error() as exc:
            raise LiveUnavailable("Redis is unavailable") from exc
        if not payload:
            return None
        try:
            sample = json.loads(payload)
        except json.JSONDecodeError:
            return None
        if not sample_is_online(sample):
            try:
                self.client.delete(latest_key(device_id))
            except self._redis_error():
                pass
            return None
        return sample

    def check_rate_limit(self, device_id: str, *, rate_per_second: int, burst: int) -> tuple[bool, int]:
        try:
            allowed, retry_ms = self.client.eval(
                self.RATE_LIMIT_SCRIPT,
                2,
                rate_tokens_key(device_id),
                rate_ts_key(device_id),
                int(time.time() * 1000),
                int(rate_per_second),
                int(burst),
                60,
            )
        except self._redis_error() as exc:
            raise LiveUnavailable("Redis is unavailable") from exc
        retry_after = max(1, math.ceil(int(retry_ms) / 1000)) if int(retry_ms) > 0 else 0
        return bool(int(allowed)), retry_after

    def listen(self, device_id: str, heartbeat_seconds: int) -> Iterator[dict | None]:
        pubsub = self.client.pubsub(ignore_subscribe_messages=True)
        try:
            pubsub.subscribe(channel_key(device_id))
            while True:
                message = pubsub.get_message(timeout=heartbeat_seconds)
                if message is None:
                    yield None
                    continue
                if message.get("type") != "message":
                    continue
                try:
                    yield json.loads(message.get("data") or "{}")
                except json.JSONDecodeError:
                    continue
        except self._redis_error() as exc:
            raise LiveUnavailable("Redis is unavailable") from exc
        finally:
            try:
                pubsub.unsubscribe(channel_key(device_id))
                pubsub.close()
            except self._redis_error():
                pass


def build_live_backend(config) -> RedisLiveBackend | InMemoryLiveBackend | UnavailableLiveBackend:
    redis_url = config.get("REDIS_URL")
    redis_required = bool(config.get("LIVE_REDIS_REQUIRED"))
    if redis_url:
        try:
            backend = RedisLiveBackend(redis_url)
            if redis_required:
                return backend
            redis_ok, _ = backend.health()
            return backend if redis_ok else InMemoryLiveBackend()
        except LiveUnavailable as exc:
            if redis_required:
                return UnavailableLiveBackend(str(exc))
    if redis_required:
        return UnavailableLiveBackend("REDIS_URL is required for Live Preview")
    return InMemoryLiveBackend()


def get_live_backend():
    backend = current_app.extensions.get("live_backend")
    if backend is None:
        backend = build_live_backend(current_app.config)
        current_app.extensions["live_backend"] = backend
    return backend


def get_latest_sample(device_id: str) -> dict | None:
    return get_live_backend().get_latest(device_id)


def store_live_sample(device_id: str, sample: dict) -> None:
    ttl = int(current_app.config.get("LIVE_SAMPLE_TTL_SECONDS", 15))
    get_live_backend().set_latest(device_id, sample, ttl)


def check_live_rate_limit(device_id: str) -> tuple[bool, int]:
    return get_live_backend().check_rate_limit(
        device_id,
        rate_per_second=int(current_app.config.get("LIVE_RATE_LIMIT_PER_SECOND", 5)),
        burst=int(current_app.config.get("LIVE_RATE_LIMIT_BURST", 10)),
    )


def sse_event(event: str, sample: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(sample, separators=(',', ':'))}\n\n"


def sse_comment(comment: str) -> str:
    return f": {comment}\n\n"
