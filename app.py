import math
import os
from pathlib import Path

import click
from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from flask_wtf.csrf import CSRFError
from sqlalchemy import select, text
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.middleware.proxy_fix import ProxyFix

from config import Config
from extensions import csrf, db, login_manager, migrate
from models import User
from routes.auth import auth_bp
from routes.dashboard import dashboard_bp
from routes.ingest import ingest_bp
from routes.vehicles import vehicles_bp
from services.live import get_live_backend


load_dotenv(Path(__file__).with_name(".env"))


def create_app(test_config=None):
    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)
        if app.config.get("TESTING") and "TRUSTED_HOSTS" not in test_config:
            app.config["TRUSTED_HOSTS"] = None
        if app.config.get("TESTING") and "ML_API_BASE_URL" not in test_config:
            app.config["ML_API_BASE_URL"] = ""
    else:
        Config.validate()
    if app.config.get("TRUST_PROXY"):
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Please sign in to access the ECU dashboard."

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(vehicles_bp)
    app.register_blueprint(ingest_bp)
    csrf.exempt(ingest_bp)

    register_login(app)
    register_legacy_routes(app)
    register_cli(app)
    register_errors(app)

    @app.get("/health")
    def health():
        database_status = "ok"
        redis_status = "ok"
        status_code = 200
        try:
            db.session.execute(text("SELECT 1"))
        except Exception:
            database_status = "error"
            status_code = 503
            app.logger.exception("Health check failed")
        live_backend = get_live_backend()
        redis_ok, redis_status = live_backend.health()
        if not redis_ok:
            status_code = 503
        return jsonify({
            "status": "ok" if status_code == 200 else "error",
            "database": database_status,
            "redis": redis_status,
        }), status_code

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        if app.config.get("SESSION_COOKIE_SECURE"):
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response

    return app


def register_login(app):
    @login_manager.user_loader
    def load_user(user_id):
        try:
            return db.session.get(User, int(user_id))
        except (TypeError, ValueError):
            return None

    @login_manager.unauthorized_handler
    def unauthorized():
        if request.path.startswith("/api/") or request.is_json:
            return jsonify({"error": "User authentication required"}), 401
        return redirect(url_for("auth.login", next=request.full_path))


def register_legacy_routes(app):
    pages = {
        "/errors": ("errors", "errorCodes.html"),
        "/vehicle": ("vehicle", "vehicleInfo.html"),
        "/maintenance": ("maintenance", "maintenance.html"),
    }
    for rule, (endpoint, template) in pages.items():
        def view(template_name=template):
            return render_template(template_name)
        app.add_url_rule(rule, endpoint, login_required(view))

    @app.get("/classic")
    @login_required
    def classic():
        return redirect(url_for("dashboard.tool"))


    app.extensions["latest_analysis"] = {}

    @app.post("/analyze")
    @login_required
    def analyze_post():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"error": "A JSON object is required"}), 400
        names = ("battery_voltage", "alternator_voltage", "temperature", "fuel_instant", "fuel_avg", "odometer")
        values = {}
        for name in names:
            value = data.get(name, 0 if name == "odometer" else None)
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)):
                return jsonify({"error": f"{name} must be a finite number"}), 400
            values[name] = value
        if any(values[name] is None for name in ("battery_voltage", "alternator_voltage", "temperature")):
            return jsonify({"error": "battery_voltage, alternator_voltage, and temperature are required"}), 400

        alerts = []
        maintenance = []
        alerts.append("Battery voltage is low." if values["battery_voltage"] < 12 else "Battery voltage is normal.")
        alerts.append("Charging system needs attention." if values["alternator_voltage"] < 13.5 else "Charging system is normal.")
        if values["temperature"] > 105:
            alerts.append(f"Engine temperature is high ({values['temperature']} C).")
        elif values["temperature"] < 70:
            alerts.append(f"Engine is not yet at operating temperature ({values['temperature']} C).")
        else:
            alerts.append(f"Engine temperature is stable ({values['temperature']} C).")
        if values["fuel_instant"] is not None and values["fuel_avg"]:
            if values["fuel_instant"] > values["fuel_avg"] * 1.3:
                alerts.append("Instant fuel consumption is unusually high.")
        for mileage, message in ((5000, "Oil service due."), (10000, "Inspect air filter."), (15000, "Inspect spark plugs."), (20000, "Inspect coolant.")):
            if values["odometer"] > mileage:
                maintenance.append(message)
        result = {**values, "alerts": alerts, "maintenance": maintenance}
        app.extensions["latest_analysis"] = result
        return jsonify({"alerts": alerts, "maintenance": maintenance})

    @app.get("/analyze")
    @login_required
    def analyze_get():
        if not app.extensions["latest_analysis"]:
            return jsonify({"error": "No simulator data has been received"}), 404
        return jsonify(app.extensions["latest_analysis"])

    @app.post("/chat")
    @login_required
    def chat():
        data = request.get_json(silent=True) or {}
        messages = data.get("messages")
        if not isinstance(messages, list) or not messages:
            return jsonify({"error": "messages must be a non-empty array"}), 400
        api_key = app.config.get("GEMINI_API_KEY")
        if not api_key:
            return jsonify({"error": "AI chat is not configured"}), 503
        try:
            from google import genai
            client = genai.Client(api_key=api_key)
            prompt = str(data.get("system", "")) + "\n\n"
            for message in messages[-20:]:
                if isinstance(message, dict):
                    role = "User" if message.get("role") == "user" else "Assistant"
                    prompt += f"{role}: {str(message.get('content', ''))[:4000]}\n"
            response = client.models.generate_content(model="gemini-2.0-flash", contents=prompt + "Assistant:")
            return jsonify({"reply": response.text})
        except Exception:
            app.logger.exception("AI chat request failed")
            return jsonify({"error": "AI chat is temporarily unavailable"}), 502


def register_cli(app):
    @app.cli.command("create-user")
    @click.option("--email", prompt=True)
    @click.option("--name", prompt="Full name")
    @click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True)
    @click.option("--admin/--no-admin", default=False)
    def create_user(email, name, password, admin):
        """Create a dashboard user (use --admin for the first administrator)."""
        email = email.strip().lower()
        if not email or "@" not in email:
            raise click.ClickException("A valid email is required")
        if not name.strip():
            raise click.ClickException("Full name is required")
        if len(password) < 12:
            raise click.ClickException("Password must contain at least 12 characters")
        if db.session.scalar(select(User).where(User.email == email)):
            raise click.ClickException("That email already exists")
        user = User(email=email, full_name=name.strip(), role="admin" if admin else "user")
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        click.echo(f"Created {user.role} user {user.email}")


def register_errors(app):
    @app.errorhandler(CSRFError)
    def csrf_error(error):
        if request.path.startswith("/api/") or request.is_json:
            return jsonify({"ok": False, "error": {"code": "INVALID_CSRF", "message": "CSRF token is missing or invalid"}}), 400
        return render_template("error.html", message="Your form expired. Please go back and try again."), 400

    @app.errorhandler(RequestEntityTooLarge)
    def too_large(error):
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "error": {"code": "REQUEST_TOO_LARGE", "message": "Request body is too large"}}), 413
        return render_template("error.html", message="The submitted request is too large."), 413


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)
