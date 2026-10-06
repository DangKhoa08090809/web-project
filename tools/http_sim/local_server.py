from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path

import requests
from werkzeug.serving import WSGIRequestHandler, make_server

from app import create_app
from extensions import db
from models import User
from services.provisioning import create_pairing_code


@dataclass(slots=True)
class RunningServer:
    app: object
    server: object
    thread: threading.Thread
    url: str

    def shutdown(self) -> None:
        self.server.shutdown()
        self.thread.join(timeout=5)


class QuietRequestHandler(WSGIRequestHandler):
    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        return


def start_werkzeug_server(app, *, host: str = "127.0.0.1") -> RunningServer:
    server = make_server(host, 0, app, threaded=True, request_handler=QuietRequestHandler)
    port = int(server.server_port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    running = RunningServer(app=app, server=server, thread=thread, url=f"http://{host}:{port}")
    wait_for_http(f"{running.url}/health")
    return running


def wait_for_http(url: str, *, timeout_seconds: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            requests.get(url, timeout=0.25)
            return
        except requests.RequestException as exc:
            last_error = exc
            time.sleep(0.05)
    raise RuntimeError(f"server did not become ready at {url}") from last_error


def create_http_flow_cloud_app(
    *,
    database_path: str | Path | None = None,
    analyzer_url: str = "",
    max_log_records: int = 200,
):
    config = {
        "WTF_CSRF_ENABLED": False,
        "MAX_LOG_RECORDS": max_log_records,
        "PAIRING_CODE_TTL_SECONDS": 900,
        "PAIRING_MAX_ATTEMPTS": 5,
        "LIVE_REDIS_REQUIRED": False,
        "LIVE_SAMPLE_TTL_SECONDS": 15,
        "LIVE_RATE_LIMIT_PER_SECOND": 100,
        "LIVE_RATE_LIMIT_BURST": 100,
        "LIVE_MAX_REQUEST_BYTES": 1024 * 1024,
        "LIVE_SSE_HEARTBEAT_SECONDS": 0,
        "LIVE_RAW_FRAME_MAX_CHARS": 256,
        "CONTROL_POLL_STALE_SECONDS": 10,
        "ML_API_BASE_URL": analyzer_url,
        "ML_API_TIMEOUT_SECONDS": 5,
        "TRUSTED_HOSTS": None,
    }
    if database_path is not None:
        config.update(
            {
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": f"sqlite:///{Path(database_path)}",
                "SECRET_KEY": "http-flow-demo-secret",
            }
        )
    return create_app(config)


def initialize_cloud_database(app, *, pairing_code_count: int = 2) -> list[str]:
    with app.app_context():
        db.create_all()
        user = User(full_name="HTTP Flow User", email="http-flow@example.com", role="user")
        user.set_password("http-flow-password")
        db.session.add(user)
        db.session.commit()
        codes = []
        for _ in range(pairing_code_count):
            _, code = create_pairing_code(user.id)
            codes.append(code)
        return codes


def ensure_demo_user_and_pairing_codes(
    app,
    *,
    email: str,
    password: str,
    full_name: str = "HTTP Flow Demo",
    pairing_code_count: int = 2,
) -> list[str]:
    with app.app_context():
        db.create_all()
        user = User.query.filter_by(email=email).first()
        if user is None:
            user = User(full_name=full_name, email=email, role="user", is_active=True)
            db.session.add(user)
        user.full_name = full_name
        user.role = "user"
        user.is_active = True
        user.set_password(password)
        db.session.commit()

        codes = []
        for _ in range(pairing_code_count):
            _, code = create_pairing_code(user.id)
            codes.append(code)
        return codes
