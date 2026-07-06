from urllib.parse import urljoin, urlsplit

from flask import Blueprint, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from flask_wtf.csrf import generate_csrf

from models import User


auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


def _safe_next(target: str | None) -> str | None:
    if not target:
        return None
    host = urlsplit(request.host_url)
    candidate = urlsplit(urljoin(request.host_url, target))
    return target if candidate.scheme in ("http", "https") and candidate.netloc == host.netloc else None


@auth_bp.get("/csrf")
def csrf_token():
    return jsonify({"csrf_token": generate_csrf()})


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.home"))
    error = None
    if request.method == "POST":
        data = request.get_json(silent=True) if request.is_json else request.form
        data = data or {}
        email = str(data.get("email", "")).strip().lower()
        password = str(data.get("password", ""))
        user = User.query.filter_by(email=email, is_active=True).first()
        if not user or not user.check_password(password):
            error = "Invalid email or password."
        else:
            remember = data.get("remember") in (True, "1", "true", "on")
            login_user(user, remember=remember)
            if request.is_json:
                return jsonify({"message": "Logged in", "user": user.to_dict(), "csrf_token": generate_csrf()})
            return redirect(_safe_next(request.args.get("next")) or url_for("dashboard.home"))
        if request.is_json:
            return jsonify({"error": error}), 401
    return render_template("login.html", error=error)


@auth_bp.post("/logout")
@login_required
def logout():
    logout_user()
    if request.is_json:
        return jsonify({"message": "Logged out"})
    return redirect(url_for("auth.login"))


@auth_bp.get("/me")
@login_required
def me():
    return jsonify(current_user.to_dict())
