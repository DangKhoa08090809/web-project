import os
import secrets
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url and os.getenv("DB_HOST"):
        user = quote(os.environ["POSTGRES_USER"], safe="")
        password = quote(os.environ["POSTGRES_PASSWORD"], safe="")
        database = quote(os.environ["POSTGRES_DB"], safe="")
        url = f"postgresql+psycopg://{user}:{password}@{os.environ['DB_HOST']}:{os.getenv('DB_PORT', '5432')}/{database}"
    url = url or f"sqlite:///{BASE_DIR / 'instance' / 'ecu_dashboard.db'}"
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def _csv_env(name: str, default: str = "") -> list[str] | None:
    value = os.getenv(name, default)
    items = [item.strip() for item in value.split(",") if item.strip()]
    return items or None


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value.lower() == "true"


class Config:
    ENV_NAME = os.getenv("APP_ENV", "development").lower()
    SECRET_KEY = os.getenv("SECRET_KEY") or secrets.token_hex(32)
    SQLALCHEMY_DATABASE_URI = _database_url()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}
    MAX_CONTENT_LENGTH = int(os.getenv("MAX_CONTENT_LENGTH", 2 * 1024 * 1024))
    MAX_LOG_RECORDS = int(os.getenv("MAX_LOG_RECORDS", 1000))
    PAIRING_CODE_TTL_SECONDS = int(os.getenv("PAIRING_CODE_TTL_SECONDS", 900))
    PAIRING_MAX_ATTEMPTS = int(os.getenv("PAIRING_MAX_ATTEMPTS", 10))
    RAW_FRAME_MAX_BYTES = int(os.getenv("RAW_FRAME_MAX_BYTES", 16_384))
    REDIS_URL = os.getenv("REDIS_URL")
    LIVE_REDIS_REQUIRED = _bool_env("LIVE_REDIS_REQUIRED", ENV_NAME == "production")
    LIVE_SAMPLE_TTL_SECONDS = int(os.getenv("LIVE_SAMPLE_TTL_SECONDS", 15))
    LIVE_RATE_LIMIT_PER_SECOND = int(os.getenv("LIVE_RATE_LIMIT_PER_SECOND", 5))
    LIVE_RATE_LIMIT_BURST = int(os.getenv("LIVE_RATE_LIMIT_BURST", 10))
    LIVE_MAX_REQUEST_BYTES = int(os.getenv("LIVE_MAX_REQUEST_BYTES", 4096))
    LIVE_SSE_HEARTBEAT_SECONDS = int(os.getenv("LIVE_SSE_HEARTBEAT_SECONDS", 15))
    LIVE_RAW_FRAME_MAX_CHARS = int(os.getenv("LIVE_RAW_FRAME_MAX_CHARS", 256))
    CONTROL_POLL_STALE_SECONDS = int(os.getenv("CONTROL_POLL_STALE_SECONDS", 10))
    ML_API_BASE_URL = os.getenv("ML_API_BASE_URL", "").rstrip("/")
    ML_API_TIMEOUT_SECONDS = float(os.getenv("ML_API_TIMEOUT_SECONDS", "2.5"))
    ML_MODEL_NAME = os.getenv("ML_MODEL_NAME", "Isolation Forest")
    ML_MODEL_VERSION = os.getenv("ML_MODEL_VERSION", "")
    ML_TRAINING_DATA_VERSION = os.getenv("ML_TRAINING_DATA_VERSION", "")
    ML_FEATURE_SCHEMA_VERSION = os.getenv("ML_FEATURE_SCHEMA_VERSION", "")
    ML_DETECTION_THRESHOLD = os.getenv("ML_DETECTION_THRESHOLD", "")
    TRUST_PROXY = os.getenv("TRUST_PROXY", "true" if ENV_NAME == "production" else "false").lower() == "true"
    TRUSTED_HOSTS = _csv_env("TRUSTED_HOSTS", "drivesafe.top" if ENV_NAME == "production" else "")
    PREFERRED_URL_SCHEME = "https" if ENV_NAME == "production" else "http"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "true" if ENV_NAME == "production" else "false").lower() == "true"
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_SECURE = SESSION_COOKIE_SECURE
    WTF_CSRF_TIME_LIMIT = 3600
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

    @classmethod
    def validate(cls) -> None:
        if cls.ENV_NAME == "production" and not os.getenv("SECRET_KEY"):
            raise RuntimeError("SECRET_KEY must be set when APP_ENV=production")
        if cls.ENV_NAME == "production" and cls.SQLALCHEMY_DATABASE_URI.startswith("sqlite:"):
            raise RuntimeError("PostgreSQL DATABASE_URL or DB_HOST settings are required when APP_ENV=production")
        if cls.ENV_NAME == "production" and cls.LIVE_REDIS_REQUIRED and not cls.REDIS_URL:
            raise RuntimeError("REDIS_URL is required for Live Preview when APP_ENV=production")
        live_positive = {
            "LIVE_SAMPLE_TTL_SECONDS": cls.LIVE_SAMPLE_TTL_SECONDS,
            "LIVE_RATE_LIMIT_PER_SECOND": cls.LIVE_RATE_LIMIT_PER_SECOND,
            "LIVE_RATE_LIMIT_BURST": cls.LIVE_RATE_LIMIT_BURST,
            "LIVE_MAX_REQUEST_BYTES": cls.LIVE_MAX_REQUEST_BYTES,
            "LIVE_SSE_HEARTBEAT_SECONDS": cls.LIVE_SSE_HEARTBEAT_SECONDS,
            "LIVE_RAW_FRAME_MAX_CHARS": cls.LIVE_RAW_FRAME_MAX_CHARS,
            "CONTROL_POLL_STALE_SECONDS": cls.CONTROL_POLL_STALE_SECONDS,
        }
        for name, value in live_positive.items():
            if cls.ENV_NAME == "production" and value <= 0:
                raise RuntimeError(f"{name} must be greater than zero when APP_ENV=production")
