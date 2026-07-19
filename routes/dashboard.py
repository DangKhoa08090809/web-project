import csv
import io
import json
import secrets

from flask import Blueprint, Response, current_app, jsonify, make_response, redirect, render_template, request, stream_with_context, url_for
from flask_login import current_user, login_required

from extensions import db
from models import Device, DevicePairingCode, RideSession, TelemetryRecord, utc_now
from services.analysis import (
    apply_derived_filters,
    build_chart_payload,
    compare_sessions,
    database_summary_counts,
    device_sync_summary,
    format_duration,
    overview_context,
    processing_state,
    query_sessions_for_user,
    session_duration_seconds,
    session_records,
    session_summary,
    statistics_for_records,
    analysis_snapshot,
)
from services.live import LiveUnavailable, get_latest_sample, get_live_backend, live_status, sse_comment, sse_event
from services.ml_client import ml_status
from services.provisioning import PairingError, create_manual_device, create_pairing_code


dashboard_bp = Blueprint("dashboard", __name__)


def _device_query():
    query = Device.query
    return query if current_user.is_admin else query.filter_by(user_id=current_user.id)


def _session_query():
    query = RideSession.query.join(Device)
    return query if current_user.is_admin else query.filter(Device.user_id == current_user.id)


def _pairing_code_query():
    query = DevicePairingCode.query
    return query if current_user.is_admin else query.filter_by(user_id=current_user.id)


def _device_by_public_id(device_id: str):
    return _device_query().filter(Device.device_id == device_id).first_or_404()


def _session_by_pk(session_pk: int):
    return _session_query().filter(RideSession.id == session_pk).first_or_404()


def _recent_sessions(devices):
    result = {}
    for device in devices:
        result[device.id] = (
            RideSession.query.filter_by(device_id=device.id)
            .order_by(RideSession.last_record_at.desc())
            .limit(3)
            .all()
        )
    return result


def _live_statuses(devices):
    statuses = {}
    for device in devices:
        try:
            statuses[device.id] = live_status(get_latest_sample(device.device_id))
        except LiveUnavailable:
            statuses[device.id] = {
                "online": False,
                "state": "offline",
                "label": "Live status unavailable",
                "age_seconds": None,
            }
    return statuses


def _no_store_json(payload: dict, status_code: int = 200):
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store"
    return response, status_code


def _display_number(value, precision: int | None = None):
    if value is None:
        return "--"
    if precision is None:
        return f"{value:g}" if isinstance(value, float) else str(value)
    formatted = f"{float(value):.{precision}f}"
    return formatted if precision == 0 else formatted.rstrip("0").rstrip(".")


def _raw_frame_preview(raw_frame):
    if raw_frame is None:
        return "--"
    if isinstance(raw_frame, dict):
        value = raw_frame.get("hex")
        if value is None:
            value = json.dumps(raw_frame, sort_keys=True, separators=(",", ":"))
    else:
        value = str(raw_frame)
    return value[:77] + "..." if len(value) > 80 else value


def _record_rows(records):
    rows = []
    for record in records:
        timestamp = record.timestamp or record.server_received_at
        rows.append({
            "seq": record.seq,
            "timestamp": timestamp.strftime("%H:%M:%S.%f")[:-3] if timestamp else "--",
            "device_time_ms": _display_number(record.device_time_ms),
            "rpm": _display_number(record.rpm, 0),
            "tps": _display_number(record.tps, 2),
            "ect": _display_number(record.ect, 1),
            "iat": _display_number(record.iat, 1),
            "battery": _display_number(record.battery, 2),
            "raw_frame": _raw_frame_preview(record.raw_frame),
        })
    return rows


def _render_devices(**context):
    devices = _device_query().order_by(Device.created_at.desc()).all()
    active_pairing_codes = (
        _pairing_code_query()
        .filter(DevicePairingCode.used_at.is_(None), DevicePairingCode.expires_at > utc_now())
        .order_by(DevicePairingCode.expires_at.desc())
        .limit(20)
        .all()
    )
    response = make_response(
        render_template(
            "devices.html",
            devices=devices,
            active_pairing_codes=active_pairing_codes,
            recent_sessions=_recent_sessions(devices),
            live_statuses=_live_statuses(devices),
            sync_summary={row["id"]: row for row in device_sync_summary(devices)},
            **context,
        )
    )
    if context.get("new_token") or context.get("pairing_code"):
        response.headers["Cache-Control"] = "no-store"
    return response


@dashboard_bp.get("/")
@login_required
def home():
    devices = _device_query().order_by(Device.device_name).all()
    sessions = _session_query().order_by(RideSession.created_at.desc()).limit(250).all()
    return render_template("dashboard.html", **overview_context(devices, sessions))


@dashboard_bp.route("/devices", methods=["GET", "POST"])
@login_required
def devices():
    token = None
    created_device = None
    error = None
    if request.method == "POST":
        try:
            created_device, token = create_manual_device(current_user.id, request.form)
        except PairingError as exc:
            error = str(exc)
    return _render_devices(new_token=token, created_device=created_device, error=error)


@dashboard_bp.post("/devices/pairing-codes")
@login_required
def generate_pairing_code():
    pairing_code, code = create_pairing_code(current_user.id)
    if request.is_json:
        response = jsonify({
            "pairing_code": code,
            "expires_at": pairing_code.expires_at.isoformat(),
            "id": pairing_code.id,
        })
        response.headers["Cache-Control"] = "no-store"
        return response, 201
    return _render_devices(pairing_code=code, pairing_code_row=pairing_code)


@dashboard_bp.post("/devices/pairing-codes/<int:code_pk>/revoke")
@login_required
def revoke_pairing_code(code_pk):
    pairing_code = _pairing_code_query().filter(DevicePairingCode.id == code_pk).first_or_404()
    if pairing_code.used_at is None:
        pairing_code.used_at = utc_now()
        db.session.commit()
    if request.is_json:
        return jsonify({"message": "Pairing code revoked"})
    return redirect(url_for("dashboard.devices"))


@dashboard_bp.get("/sessions")
@login_required
def sessions():
    query = query_sessions_for_user(_session_query(), request.args)
    rows = query.order_by(RideSession.started_at.desc()).limit(500).all()
    rows = apply_derived_filters(rows, request.args)
    devices = _device_query().order_by(Device.device_name).all()
    vehicles = sorted({device.vehicle_name for device in devices if device.vehicle_name})
    return render_template(
        "sessions.html",
        sessions=[session_summary(row) for row in rows],
        devices=devices,
        vehicles=vehicles,
        filters=request.args,
        session_states=("Uploaded", "Processing", "Analyzed", "Analysis failed", "Invalid data"),
        analysis_results=("Normal", "Minor anomaly", "Requires attention", "High anomaly", "Analysis unavailable"),
    )


@dashboard_bp.get("/sessions/<int:session_pk>")
@login_required
def session_detail(session_pk):
    ride_session = _session_by_pk(session_pk)
    records = session_records(ride_session)
    recent_records = list(reversed(records[-30:]))
    stats = statistics_for_records(records, ride_session)
    analysis = analysis_snapshot(ride_session, records)
    return render_template(
        "session_detail.html",
        session=ride_session,
        summary=session_summary(ride_session),
        statistics=stats,
        analysis=analysis,
        records=_record_rows(recent_records),
        samples_url=url_for("dashboard.session_samples_api", session_pk=ride_session.id),
        csv_url=url_for("dashboard.session_csv", session_pk=ride_session.id),
        json_url=url_for("dashboard.session_json", session_pk=ride_session.id),
    )


@dashboard_bp.get("/sessions/<int:session_pk>.json")
@login_required
def session_json(session_pk):
    ride_session = _session_by_pk(session_pk)
    records = session_records(ride_session)
    return jsonify({
        "session": session_summary(ride_session),
        "statistics": statistics_for_records(records, ride_session),
        "analysis": analysis_snapshot(ride_session, records),
        "records": [record.to_dict() for record in records],
    })


@dashboard_bp.post("/sessions/<int:session_pk>/notes")
@login_required
def update_session_notes(session_pk):
    ride_session = _session_by_pk(session_pk)
    data = request.get_json(silent=True) if request.is_json else request.form
    ride_session.notes = str(data.get("notes", "")).strip()[:2000] or None
    db.session.commit()
    if request.is_json:
        return jsonify({"session": session_summary(ride_session)})
    return redirect(url_for("dashboard.session_detail", session_pk=ride_session.id))


@dashboard_bp.post("/sessions/<int:session_pk>/delete")
@login_required
def delete_session(session_pk):
    ride_session = _session_by_pk(session_pk)
    TelemetryRecord.query.filter_by(ride_session_id=ride_session.id).delete()
    from models import SyncBatch

    SyncBatch.query.filter_by(ride_session_id=ride_session.id).delete()
    db.session.delete(ride_session)
    db.session.commit()
    if request.is_json:
        return jsonify({"message": "Session deleted"})
    return redirect(url_for("dashboard.sessions"))


@dashboard_bp.get("/api/sessions")
@login_required
def sessions_api():
    query = query_sessions_for_user(_session_query(), request.args)
    rows = query.order_by(RideSession.started_at.desc()).limit(500).all()
    rows = apply_derived_filters(rows, request.args)
    return jsonify({"sessions": [session_summary(row) for row in rows]})


@dashboard_bp.get("/api/sessions/<int:session_pk>/samples")
@login_required
def session_samples_api(session_pk):
    ride_session = _session_by_pk(session_pk)
    try:
        max_points = max(100, min(5000, int(request.args.get("max_points", 1200))))
    except ValueError:
        max_points = 1200
    return jsonify(build_chart_payload(ride_session, max_points=max_points))


@dashboard_bp.get("/api/sessions/<int:session_pk>/statistics")
@login_required
def session_statistics_api(session_pk):
    ride_session = _session_by_pk(session_pk)
    records = session_records(ride_session)
    return jsonify({"statistics": statistics_for_records(records, ride_session)})


@dashboard_bp.get("/api/sessions/<int:session_pk>/analysis")
@login_required
def session_analysis_api(session_pk):
    ride_session = _session_by_pk(session_pk)
    return jsonify({"analysis": analysis_snapshot(ride_session), "ml_status": ml_status()})


@dashboard_bp.get("/api/sessions/<int:session_pk>/events")
@login_required
def session_events_api(session_pk):
    ride_session = _session_by_pk(session_pk)
    return jsonify({"events": analysis_snapshot(ride_session)["events"]})


@dashboard_bp.post("/api/sessions/<int:session_pk>/analysis/re-run")
@login_required
def rerun_analysis_api(session_pk):
    ride_session = _session_by_pk(session_pk)
    status = ml_status()
    snapshot = analysis_snapshot(ride_session)
    if not status["available"]:
        return jsonify({
            "state": "failed",
            "error": status["message"],
            "analysis": snapshot,
        }), 503
    return jsonify({
        "state": "completed",
        "message": "Local dashboard analysis snapshot refreshed. Persisted ML re-analysis is not available in this repository yet.",
        "analysis": snapshot,
    })


@dashboard_bp.get("/analysis")
@login_required
def analysis():
    rows = _session_query().order_by(RideSession.started_at.desc()).limit(250).all()
    return render_template(
        "analysis.html",
        ml_status=ml_status(),
        analyses=[analysis_snapshot(row) for row in rows],
    )


@dashboard_bp.get("/api/analysis/status")
@login_required
def analysis_status_api():
    status = ml_status()
    status["database"] = database_summary_counts()
    return jsonify(status)


@dashboard_bp.get("/compare")
@login_required
def compare():
    rows = _session_query().order_by(RideSession.started_at.desc()).limit(250).all()
    return render_template("compare.html", sessions=[session_summary(row) for row in rows])


@dashboard_bp.get("/api/compare")
@login_required
def compare_api():
    try:
        baseline_id = int(request.args.get("baseline", ""))
        comparison_id = int(request.args.get("comparison", ""))
    except ValueError:
        return jsonify({"error": "baseline and comparison session ids are required"}), 400
    baseline = _session_by_pk(baseline_id)
    comparison = _session_by_pk(comparison_id)
    return jsonify(compare_sessions(baseline, comparison))


@dashboard_bp.get("/tool")
@login_required
def tool():
    rows = _session_query().order_by(RideSession.started_at.desc()).limit(250).all()
    return render_template("tool.html", sessions=[session_summary(row) for row in rows])


@dashboard_bp.get("/settings")
@login_required
def settings():
    return render_template("settings.html")


@dashboard_bp.get("/sessions/<int:session_pk>.csv")
@login_required
def session_csv(session_pk):
    ride_session = _session_query().filter(RideSession.id == session_pk).first_or_404()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "session_id",
        "seq",
        "timestamp",
        "device_time_ms",
        "server_received_at",
        "rpm",
        "tps",
        "ect",
        "iat",
        "battery",
        "injector_ms",
        "ignition_deg",
        "raw_frame",
    ])
    rows = TelemetryRecord.query.filter_by(ride_session_id=ride_session.id).order_by(TelemetryRecord.seq).limit(100_000)
    for record in rows:
        writer.writerow([
            record.session_id,
            record.seq,
            record.timestamp.isoformat() if record.timestamp else "",
            record.device_time_ms if record.device_time_ms is not None else "",
            record.server_received_at.isoformat() if record.server_received_at else "",
            record.rpm if record.rpm is not None else "",
            record.tps if record.tps is not None else "",
            record.ect if record.ect is not None else "",
            record.iat if record.iat is not None else "",
            record.battery if record.battery is not None else "",
            record.injector_ms if record.injector_ms is not None else "",
            record.ignition_deg if record.ignition_deg is not None else "",
            record.raw_frame if record.raw_frame is not None else "",
        ])
    filename = f"{ride_session.session_id}.csv".replace("/", "_")
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@dashboard_bp.route("/api/devices", methods=["GET", "POST"])
@login_required
def device_api():
    if request.method == "GET":
        return jsonify({"devices": [device.to_dict() for device in _device_query().all()]})
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "request body must be a JSON object"}), 400
    try:
        device, token = create_manual_device(current_user.id, data)
    except PairingError as exc:
        return jsonify({"error": str(exc)}), exc.status_code
    payload = device.to_dict()
    payload["token"] = token
    response = jsonify({"message": "Device created. Save this token; it will not be shown again.", "device": payload})
    response.headers["Cache-Control"] = "no-store"
    return response, 201


@dashboard_bp.get("/devices/<device_id>/live")
@login_required
def device_live(device_id):
    device = _device_by_public_id(device_id)
    return render_template(
        "device_live.html",
        device=device,
        ttl_seconds=int(current_app.config["LIVE_SAMPLE_TTL_SECONDS"]),
        latest_url=url_for("dashboard.device_live_latest", device_id=device.device_id),
        stream_url=url_for("dashboard.device_live_stream", device_id=device.device_id),
    )


@dashboard_bp.get("/api/devices/<device_id>/live/latest")
@login_required
def device_live_latest(device_id):
    device = _device_by_public_id(device_id)
    try:
        sample = get_latest_sample(device.device_id)
    except LiveUnavailable:
        return _no_store_json(
            {"ok": False, "error": {"code": "LIVE_PREVIEW_UNAVAILABLE", "message": "Live Preview cache is unavailable"}},
            503,
        )
    status = live_status(sample)
    return _no_store_json({"ok": True, "online": status["online"], "sample": sample if status["online"] else None})


@dashboard_bp.get("/api/devices/<device_id>/live/stream")
@login_required
def device_live_stream(device_id):
    device = _device_by_public_id(device_id)
    backend = get_live_backend()
    heartbeat_seconds = int(current_app.config["LIVE_SSE_HEARTBEAT_SECONDS"])

    def generate():
        yield sse_comment("connected")
        try:
            initial = backend.get_latest(device.device_id)
            if initial:
                yield sse_event("telemetry", initial)
            for sample in backend.listen(device.device_id, heartbeat_seconds):
                if sample is None:
                    yield sse_comment("heartbeat")
                    continue
                yield sse_event("telemetry", sample)
        except LiveUnavailable:
            yield sse_event("live-error", {"message": "Live Preview cache is unavailable"})

    response = Response(stream_with_context(generate()), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache, no-store"
    response.headers["Connection"] = "keep-alive"
    response.headers["X-Accel-Buffering"] = "no"
    return response


@dashboard_bp.post("/api/devices/<int:device_pk>/rotate-token")
@login_required
def rotate_device_token(device_pk):
    device = _device_query().filter(Device.id == device_pk).first_or_404()
    token = secrets.token_urlsafe(32)
    device.set_token(token)
    db.session.commit()
    response = jsonify({"message": "Token rotated. The previous token is now invalid.", "token": token})
    response.headers["Cache-Control"] = "no-store"
    return response


@dashboard_bp.post("/devices/<int:device_pk>/rotate-token")
@login_required
def rotate_device_token_browser(device_pk):
    device = _device_query().filter(Device.id == device_pk).first_or_404()
    token = secrets.token_urlsafe(32)
    device.set_token(token)
    db.session.commit()
    return _render_devices(new_token=token, created_device=device)


@dashboard_bp.post("/devices/<int:device_pk>/update")
@login_required
def update_device(device_pk):
    device = _device_query().filter(Device.id == device_pk).first_or_404()
    data = request.get_json(silent=True) if request.is_json else request.form
    for name in ("device_name", "vehicle_name", "ecu_type", "firmware_version", "hardware_version"):
        if request.is_json and name not in data:
            continue
        value = str(data.get(name, "")).strip()
        if name in ("device_name", "vehicle_name", "ecu_type") and not value:
            if request.is_json:
                return jsonify({"error": f"{name} is required"}), 400
            return _render_devices(error=f"{name} is required")
        setattr(device, name, value[:100] or None)
    db.session.commit()
    if request.is_json:
        return jsonify({"device": device.to_dict()})
    return redirect(url_for("dashboard.devices"))


@dashboard_bp.post("/devices/<int:device_pk>/disable")
@login_required
def disable_device(device_pk):
    device = _device_query().filter(Device.id == device_pk).first_or_404()
    device.is_active = False
    db.session.commit()
    if request.is_json:
        return jsonify({"message": "Device disabled"})
    return redirect(url_for("dashboard.devices"))


@dashboard_bp.post("/api/devices/<int:device_pk>/disable")
@login_required
def disable_device_api(device_pk):
    device = _device_query().filter(Device.id == device_pk).first_or_404()
    device.is_active = False
    db.session.commit()
    return jsonify({"message": "Device disabled"})
