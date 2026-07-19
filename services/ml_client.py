import time

import requests
from flask import current_app


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
        "message": "ML API is not configured.",
    }
    if not configured:
        return status

    started = time.perf_counter()
    try:
        response = requests.get(f"{base_url}/health", timeout=current_app.config["ML_API_TIMEOUT_SECONDS"])
        status["average_latency_ms"] = round((time.perf_counter() - started) * 1000)
        response.raise_for_status()
        payload = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
    except requests.RequestException as exc:
        status["message"] = f"ML API health check failed: {exc.__class__.__name__}."
        return status
    except ValueError:
        payload = {}

    status.update({
        "available": True,
        "message": "ML API health check succeeded.",
        "model_name": payload.get("model_name") or status["model_name"],
        "model_version": payload.get("model_version") or status["model_version"],
        "training_data_version": payload.get("training_data_version") or status["training_data_version"],
        "feature_schema_version": payload.get("feature_schema_version") or status["feature_schema_version"],
        "detection_threshold": payload.get("detection_threshold") or status["detection_threshold"],
        "last_successful_analysis": payload.get("last_successful_analysis") or status["last_successful_analysis"],
        "failed_analysis_count": payload.get("failed_analysis_count", status["failed_analysis_count"]),
    })
    return status
