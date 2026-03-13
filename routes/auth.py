# routes/auth.py — register, login, logout
from flask import Blueprint, request, jsonify
from flask_login import login_user, logout_user, login_required, current_user
from models import User
from database import db

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")

@auth_bp.route("/register", methods=["POST"])
def register():
    data      = request.get_json()
    full_name = data.get("full_name", "").strip()
    email     = data.get("email", "").strip().lower()
    password  = data.get("password", "")

    if not full_name or not email or not password:
        return jsonify({"error": "All fields are required"}), 400
    if User.query.filter_by(email=email).first():
        return jsonify({"error": "Email already registered"}), 409

    user = User(full_name=full_name, email=email)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    login_user(user)
    return jsonify({"message": "Registered successfully", "user": user.to_dict()}), 201


@auth_bp.route("/login", methods=["POST"])
def login():
    data     = request.get_json()
    email    = data.get("email", "").strip().lower()
    password = data.get("password", "")

    user = User.query.filter_by(email=email).first()
    if not user or not user.check_password(password):
        return jsonify({"error": "Invalid email or password"}), 401

    login_user(user)
    return jsonify({"message": "Logged in", "user": user.to_dict()})


@auth_bp.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    return jsonify({"message": "Logged out"})


@auth_bp.route("/me", methods=["GET"])
@login_required
def me():
    return jsonify(current_user.to_dict())