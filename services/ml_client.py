from flask import current_app
from sqlalchemy.exc import SQLAlchemyError

from models import AnalysisResult, RideSession
from services.analysis_submission import analyzer_client_from_config


def _format_datetime(value) -> str:
    if not value:
        return "Not reported"
    return value.strftime("%Y-%m-%d %H:%M UTC")


def _apply_stored_analysis_fallback(status: dict) -> dict:
    try:
        latest = AnalysisResult.query.order_by(AnalysisResult.analyzed_at.desc()).first()
        failed_count = RideSession.query.filter_by(analysis_status="failed").count()
    except SQLAlchemyError:
        return status
    status["failed_analysis_count"] = failed_count
    if latest is None:
        return status
    status["stored_analysis_available"] = True
    status["latest_analysis_run_id"] = latest.analysis_run_id
    status["last_successful_analysis"] = _format_datetime(latest.analyzed_at)
    if latest.model_version:
        status["model_version"] = latest.model_version
    if latest.feature_schema_version:
        status["feature_schema_version"] = latest.feature_schema_version
    if latest.telemetry_schema_version:
        status["telemetry_schema_version"] = latest.telemetry_schema_version
    if latest.decoder_id:
        status["decoder_id"] = latest.decoder_id
    if latest.decoder_version:
        status["decoder_version"] = latest.decoder_version
    if not status["available"]:
        status["message"] = f"{status['message']} Showing latest stored analysis metadata."
    return status


def ml_status() -> dict:
    base_url = current_app.config.get("ML_API_BASE_URL", "")
    configured = bool(base_url)
    status = {
        "available": False,
        "configured": configured,
        "base_url": base_url or "",
        "model_name": current_app.config.get("ML_MODEL_NAME") or "Isolation Forest",
        "model_version": current_app.config.get("ML_MODEL_VERSION") or "Not reported",
        "training_data_version": current_app.config.get("ML_TRAINING_DATA_VERSION") or "Not reported",
        "feature_schema_version": current_app.config.get("ML_FEATURE_SCHEMA_VERSION") or "Not reported",
        "detection_threshold": current_app.config.get("ML_DETECTION_THRESHOLD") or "Not reported",
        "last_successful_analysis": "Not reported",
        "average_latency_ms": None,
        "failed_analysis_count": "Not reported",
        "stored_analysis_available": False,
        "latest_analysis_run_id": "Not reported",
        "telemetry_schema_version": "Not reported",
        "decoder_id": "Not reported",
        "decoder_version": "Not reported",
        "message": "ML API is not configured.",
    }
    if not configured:
        return _apply_stored_analysis_fallback(status)

    payload = analyzer_client_from_config().health()
    status["average_latency_ms"] = payload.get("average_latency_ms")
    if not payload.get("available"):
        status["message"] = payload.get("message", "ML API health check failed.")
        return _apply_stored_analysis_fallback(status)

    status.update({
        "available": True,
        "message": payload.get("message") or "ML API health check succeeded.",
        "model_name": payload.get("model_name") or status["model_name"],
        "model_version": payload.get("model_version") or status["model_version"],
        "training_data_version": payload.get("training_data_version") or status["training_data_version"],
        "feature_schema_version": payload.get("feature_schema_version") or status["feature_schema_version"],
        "detection_threshold": payload.get("detection_threshold") or status["detection_threshold"],
        "last_successful_analysis": payload.get("last_successful_analysis") or status["last_successful_analysis"],
        "failed_analysis_count": payload.get("failed_analysis_count", status["failed_analysis_count"]),
        "telemetry_schema_version": payload.get("telemetry_schema_version") or status["telemetry_schema_version"],
        "decoder_id": payload.get("decoder_id") or status["decoder_id"],
        "decoder_version": payload.get("decoder_version") or status["decoder_version"],
    })
    return _apply_stored_analysis_fallback(status)
