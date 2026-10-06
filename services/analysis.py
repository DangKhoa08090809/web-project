import json
import math
import statistics
from collections import Counter
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import func, or_

from models import AnalysisResult, Device, RideSession, SyncBatch, TelemetryRecord


PARAMETERS = [
    {"key": "rpm", "label": "RPM", "unit": "rpm", "precision": 0},
    {"key": "tps_voltage", "label": "TPS voltage", "unit": "V", "precision": 2},
    {"key": "tps_raw", "label": "TPS raw", "unit": "raw", "precision": 0},
    {"key": "tps_raw_candidate", "label": "TPS raw candidate", "unit": "raw", "precision": 0},
    {"key": "battery_voltage", "label": "Battery voltage", "unit": "V", "precision": 2},
    {"key": "iat_c", "label": "IAT", "unit": "C", "precision": 1},
    {"key": "ect_c", "label": "ECT", "unit": "C", "precision": 1},
    {"key": "ect_c_candidate", "label": "ECT candidate", "unit": "C", "precision": 1},
    {"key": "map_raw", "label": "MAP raw", "unit": "raw", "precision": 0},
    {"key": "tps", "label": "TPS", "unit": "%", "precision": 1},
    {"key": "ect", "label": "Engine temperature", "unit": "C", "precision": 1},
    {"key": "iat", "label": "Intake air temperature", "unit": "C", "precision": 1},
    {"key": "battery", "label": "Battery voltage", "unit": "V", "precision": 2},
    {"key": "injector_ms", "label": "Injector duration", "unit": "ms", "precision": 2},
    {"key": "ignition_deg", "label": "Ignition timing", "unit": "deg", "precision": 1},
]

PARAMETER_BY_KEY = {parameter["key"]: parameter for parameter in PARAMETERS}
ANOMALY_THRESHOLD = 0.65
MAX_ANALYSIS_RECORDS = 100_000
_UNSET = object()
SUMMARY_LOCAL_ANALYSIS_LIMIT = 8
BASELINE_ESTABLISHED_MIN_SESSIONS = 3
BASELINE_ESTABLISHED_MIN_SAMPLES = 30
BASELINE_LEARNING_MIN_SAMPLES = 10
BASELINE_ELIGIBLE_MIN_SAMPLES = 5
FINDING_LIMIT = 3
OVERVIEW_CONDITION_SESSION_LIMIT = 5
SEVERITY_ORDER = {"normal": 0, "monitor": 1, "attention": 2, "high": 3}
STRONG_ANOMALY_STATUSES = {
    "monitor",
    "minor_anomaly",
    "warning",
    "attention",
    "requires_attention",
    "high",
    "high_anomaly",
}
SUPPORTED_FINDING_SIGNALS = {
    "battery_voltage": {
        "keys": ("battery_voltage", "battery"),
        "label": "Điện áp hệ thống",
        "unit": "V",
        "monitor_code": "BATTERY_MONITOR",
        "attention_code": "BATTERY_CHECK_CHARGING",
    },
    "ect_c": {
        "keys": ("ect_c", "ect_c_candidate", "ect"),
        "label": "Nhiệt độ động cơ",
        "unit": "C",
        "monitor_code": "ECT_MONITOR",
        "attention_code": "ECT_CHECK_COOLING",
    },
    "iat_c": {
        "keys": ("iat_c", "iat"),
        "label": "Nhiệt độ khí nạp",
        "unit": "C",
        "monitor_code": "IAT_MONITOR",
        "attention_code": "IAT_CHECK_INTAKE",
    },
}
RECOMMENDATION_TEXT = {
    "BATTERY_MONITOR": "Điện áp hệ thống có khác biệt so với mức hoạt động thông thường của xe. Nên tiếp tục theo dõi trong các chuyến đi tiếp theo.",
    "BATTERY_CHECK_CHARGING": "Điện áp hệ thống có dấu hiệu thấp hoặc không ổn định. Nếu tình trạng tiếp tục xuất hiện, nên kiểm tra ắc quy, đầu cực và hệ thống sạc.",
    "ECT_MONITOR": "Nhiệt độ động cơ trong chuyến đi cao hơn mức thường thấy của xe. Nên theo dõi thêm trong các chuyến đi tiếp theo.",
    "ECT_CHECK_COOLING": "Nhiệt độ động cơ cao hơn đáng kể so với mức thông thường. Nếu tình trạng lặp lại, nên kiểm tra mức và tình trạng nước làm mát, cùng khả năng tản nhiệt của hệ thống.",
    "ECT_HIGH_CHECK_COOLING": "Hệ thống ghi nhận nhiệt độ động cơ cao bất thường trong chuyến đi. Nên kiểm tra hệ thống làm mát trước khi tiếp tục sử dụng xe thường xuyên.",
    "IAT_MONITOR": "Nhiệt độ khí nạp khác đáng kể so với hành vi thường thấy của xe. Nên theo dõi thêm trong các chuyến đi tiếp theo.",
    "IAT_CHECK_INTAKE": "Nhiệt độ khí nạp có dấu hiệu bất thường kéo dài hoặc lặp lại. Nên kiểm tra đường nạp, cảm biến IAT và các kết nối liên quan nếu hiện tượng tiếp tục xuất hiện.",
}
SEVERITY_LABELS = {
    "normal": "Bình thường",
    "monitor": "Theo dõi",
    "attention": "Đáng chú ý",
    "high": "Cao",
}
OVERVIEW_SEVERITY_LABELS = {
    "normal": "Bình thường",
    "monitor": "Theo dõi",
    "attention": "Đáng chú ý",
    "high": "Cao",
    "insufficient_data": "Chưa đủ dữ liệu",
}
EVIDENCE_STATE_LABELS = {
    "no_evidence": "No evidence",
    "single_detector_evidence": "Single detector evidence",
    "multiple_detector_evidence": "Multiple detector evidence",
    "detector_disagreement": "Detector disagreement",
    "insufficient_coverage": "Insufficient coverage",
}
CHECK_PRIORITY_ORDER = ("priority", "relevant", "deferred", "insufficient_evidence")
CHECK_PRIORITY_LABELS = {
    "priority": "Priority",
    "relevant": "Relevant",
    "deferred": "Deferred",
    "insufficient_evidence": "Insufficient evidence",
}


def as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def event_time(record: TelemetryRecord) -> datetime:
    return as_utc(record.timestamp) or as_utc(record.server_received_at)


def session_duration_seconds(session: RideSession) -> float:
    if getattr(session, "duration_ms", None) is not None:
        return max(0.0, float(session.duration_ms) / 1000.0)
    start = as_utc(session.started_at)
    end = as_utc(session.ended_at) or as_utc(session.last_record_at)
    if not start or not end:
        return 0.0
    return max(0.0, (end - start).total_seconds())


def format_duration(seconds: float | int | None) -> str:
    seconds = int(round(float(seconds or 0)))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def format_datetime(value: datetime | None) -> str:
    value = as_utc(value)
    if not value:
        return "--"
    return value.strftime("%Y-%m-%d %H:%M UTC")


def format_number(value, precision: int = 1, suffix: str = "") -> str:
    if value is None:
        return "--"
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "--"
    if not math.isfinite(numeric):
        return "--"
    if precision == 0:
        return f"{int(round(numeric))}{suffix}"
    return f"{numeric:.{precision}f}{suffix}"


def processing_state(session: RideSession) -> str:
    if session.record_count <= 0:
        return "Invalid data"
    if session.capture_status == "failed":
        return "Capture failed"
    if session.analysis_status in {"pending", "submitted"}:
        return "Analysis pending"
    if session.analysis_status == "failed":
        return "Analysis failed"
    if session.analysis_status == "completed":
        return "Analyzed"
    if session.sync_status == "failed":
        return "Capture failed"
    if session.sync_status == "syncing":
        return "Uploaded"
    if session.sync_status == "partial":
        return "Processing"
    return "Captured"


def capture_termination_summary(session: RideSession) -> dict:
    if session.capture_status == "interrupted" and session.termination_reason == "unclean_runtime_shutdown":
        return {
            "state": "interrupted",
            "label": "Capture interrupted",
            "reason": "Unclean runtime shutdown",
            "message": (
                "This capture was interrupted because the previous runtime ended uncleanly. "
                "The displayed data is the portion that was saved successfully."
            ),
        }
    if session.capture_status == "completed" and session.termination_reason == "normal_stop":
        return {
            "state": "completed",
            "label": "Capture completed normally",
            "reason": "Normal stop",
            "message": "This capture ended normally.",
        }
    return {
        "state": "unknown",
        "label": "Capture termination unknown",
        "reason": "Unknown",
        "message": "No capture termination metadata was supplied for this historical session.",
    }


def parameter_label(key: str) -> str:
    return PARAMETER_BY_KEY.get(key, {"label": key.replace("_", " ").title()})["label"]


def available_parameters(records: Iterable[TelemetryRecord]) -> list[dict]:
    available = []
    for parameter in PARAMETERS:
        if any(getattr(record, parameter["key"]) is not None for record in records):
            available.append(parameter)
    return available


def session_records(session: RideSession, *, limit: int | None = MAX_ANALYSIS_RECORDS) -> list[TelemetryRecord]:
    query = TelemetryRecord.query.filter_by(ride_session_id=session.id).order_by(TelemetryRecord.seq)
    if limit:
        query = query.limit(limit)
    return query.all()


def downsample_records(records: list[TelemetryRecord], max_points: int = 1200) -> list[TelemetryRecord]:
    if max_points <= 0 or len(records) <= max_points:
        return records
    step = math.ceil(len(records) / max_points)
    sampled = records[::step]
    if sampled[-1].id != records[-1].id:
        sampled.append(records[-1])
    return sampled[: max_points - 1] + [records[-1]] if len(sampled) > max_points else sampled


def _values(records: Iterable[TelemetryRecord], key: str) -> list[float]:
    values = []
    for record in records:
        value = getattr(record, key)
        if isinstance(value, (int, float)) and math.isfinite(value):
            values.append(float(value))
    return values


def statistics_for_records(records: list[TelemetryRecord], session: RideSession | None = None) -> dict:
    stats = {}
    for parameter in PARAMETERS:
        values = _values(records, parameter["key"])
        if not values:
            continue
        mean = statistics.fmean(values)
        stats[parameter["key"]] = {
            "label": parameter["label"],
            "unit": parameter["unit"],
            "precision": parameter["precision"],
            "count": len(values),
            "minimum": min(values),
            "maximum": max(values),
            "mean": mean,
            "stddev": statistics.pstdev(values) if len(values) > 1 else 0.0,
        }

    invalid_frames = sum(1 for record in records if record.raw_frame is not None and record.frame_valid is False)
    checksum_failures = sum(1 for record in records if record.raw_frame is not None and record.checksum_valid is False)
    if invalid_frames == 0 and checksum_failures == 0:
        for frame in [record.raw_frame for record in records if record.raw_frame is not None]:
            if not isinstance(frame, dict):
                continue
            validity = frame.get("valid")
            decode_status = str(frame.get("decode_status", frame.get("status", ""))).lower()
            if validity is False or decode_status in {"invalid", "failed", "error", "out_of_range"}:
                invalid_frames += 1
            if frame.get("checksum_ok") is False or frame.get("checksum") in {"fail", "failed", False}:
                checksum_failures += 1

    expected_samples = None
    missing_samples = 0
    if session and session.first_seq is not None and session.last_seq is not None:
        expected_samples = max(0, int(session.last_seq) - int(session.first_seq) + 1)
        missing_samples = max(0, expected_samples - int(session.record_count or len(records)))

    stats["_quality"] = {
        "sample_count": session.record_count if session else len(records),
        "expected_samples": expected_samples,
        "missing_samples": missing_samples,
        "missing_sample_percentage": (missing_samples / expected_samples * 100) if expected_samples else 0.0,
        "invalid_frame_count": invalid_frames,
        "checksum_failure_count": checksum_failures,
    }
    return stats


def signal_value(record: TelemetryRecord, canonical_key: str, legacy_key: str | None = None):
    value = getattr(record, canonical_key, None)
    if value is not None:
        return value
    if legacy_key is not None:
        return getattr(record, legacy_key, None)
    return None


def _embedded_analysis(record: TelemetryRecord) -> tuple[float | None, bool | None, list[str]]:
    frame = record.raw_frame if isinstance(record.raw_frame, dict) else {}
    score = None
    for key in ("anomaly_score", "ml_anomaly_score", "score"):
        value = frame.get(key)
        if isinstance(value, (int, float)) and math.isfinite(value):
            score = max(0.0, min(1.0, float(value)))
            break
    is_anomaly = None
    for key in ("is_anomaly", "anomaly", "ml_is_anomaly"):
        if isinstance(frame.get(key), bool):
            is_anomaly = frame[key]
            break
    raw_signals = frame.get("signals") or frame.get("affected_signals") or []
    if isinstance(raw_signals, str):
        raw_signals = [raw_signals]
    signals = [str(signal)[:40] for signal in raw_signals if str(signal).strip()]
    return score, is_anomaly, signals


def _baseline(records: list[TelemetryRecord]) -> dict:
    baselines = {}
    for parameter in PARAMETERS:
        values = _values(records, parameter["key"])
        if not values:
            continue
        median = statistics.median(values)
        deviations = [abs(value - median) for value in values]
        mad = statistics.median(deviations) or statistics.pstdev(values) or 1.0
        baselines[parameter["key"]] = {"median": median, "mad": mad}
    return baselines


def _rule_score(record: TelemetryRecord, baselines: dict, previous: TelemetryRecord | None) -> tuple[float, list[str]]:
    score = 0.0
    signals = []
    for parameter in PARAMETERS:
        key = parameter["key"]
        value = getattr(record, key)
        if not isinstance(value, (int, float)) or not math.isfinite(value) or key not in baselines:
            continue
        baseline = baselines[key]
        robust_z = abs(float(value) - baseline["median"]) / max(baseline["mad"], 0.001)
        if robust_z >= 6:
            score = max(score, min(1.0, robust_z / 12))
            signals.append(parameter["label"])

    battery_value = signal_value(record, "battery_voltage", "battery")
    ect_value = signal_value(record, "ect_c")
    if ect_value is None:
        ect_value = signal_value(record, "ect_c_candidate", "ect")
    if isinstance(battery_value, (int, float)):
        if battery_value < 11.8:
            score = max(score, 0.78)
            signals.append("Battery voltage")
        elif battery_value > 15.2:
            score = max(score, 0.72)
            signals.append("Battery voltage")
    if isinstance(ect_value, (int, float)) and ect_value >= 112:
        score = max(score, 0.76)
        signals.append("ECT")
    if isinstance(record.tps, (int, float)) and not 0 <= record.tps <= 100:
        score = max(score, 0.9)
        signals.append("TPS")
    if previous and isinstance(record.rpm, (int, float)) and isinstance(previous.rpm, (int, float)):
        rpm_delta = abs(record.rpm - previous.rpm)
        tps_delta = None
        if isinstance(record.tps, (int, float)) and isinstance(previous.tps, (int, float)):
            tps_delta = abs(record.tps - previous.tps)
        if rpm_delta >= 1800 and (tps_delta is None or tps_delta <= 3):
            score = max(score, 0.82)
            signals.extend(["RPM stability", "TPS"])

    unique_signals = []
    for signal in signals:
        if signal not in unique_signals:
            unique_signals.append(signal)
    return round(score, 4), unique_signals[:5]


def scored_samples(records: list[TelemetryRecord]) -> list[dict]:
    if not records:
        return []
    baselines = _baseline(records)
    embedded_available = False
    samples = []
    start_time = event_time(records[0])
    previous = None
    for record in records:
        embedded_score, embedded_is_anomaly, embedded_signals = _embedded_analysis(record)
        if embedded_score is not None:
            embedded_available = True
            score = embedded_score
            signals = embedded_signals
        else:
            score, signals = _rule_score(record, baselines, previous)
        is_anomalous = embedded_is_anomaly if embedded_is_anomaly is not None else score >= ANOMALY_THRESHOLD
        current_time = event_time(record)
        elapsed = 0.0
        if record.timestamp_ms is not None:
            elapsed = max(0.0, float(record.timestamp_ms) / 1000.0)
        elif record.device_time_ms is not None:
            elapsed = max(0.0, float(record.device_time_ms) / 1000.0)
        elif current_time and start_time:
            elapsed = max(0.0, (current_time - start_time).total_seconds())
        samples.append({
            "seq": int(record.seq),
            "timestamp": current_time.isoformat().replace("+00:00", "Z") if current_time else None,
            "elapsed_seconds": elapsed,
            "anomaly_score": round(score, 4),
            "is_anomalous": bool(is_anomalous),
            "signals": signals,
            "source": "embedded-ml" if embedded_score is not None else "local-screening",
        })
        previous = record
    source = "embedded-ml" if embedded_available else "local-screening"
    for sample in samples:
        sample["source"] = source if sample["source"] == "local-screening" else sample["source"]
    return samples


def severity_from_score(score: float, ratio: float = 0.0) -> str:
    if score >= 0.9 or ratio >= 0.25:
        return "High"
    if score >= 0.75 or ratio >= 0.08:
        return "Requires attention"
    return "Minor"


def result_from_scores(max_score: float | None, anomalous_ratio: float | None, has_records: bool) -> str:
    if not has_records or max_score is None:
        return "Analysis unavailable"
    ratio = anomalous_ratio or 0.0
    if max_score >= 0.9 or ratio >= 25:
        return "High anomaly"
    if max_score >= 0.75 or ratio >= 8:
        return "Requires attention"
    if max_score >= ANOMALY_THRESHOLD or ratio > 0:
        return "Minor anomaly"
    return "Normal"


def result_from_external_status(overall_status: str | None) -> str | None:
    if not overall_status:
        return None
    status = str(overall_status).strip().lower().replace("-", "_").replace(" ", "_")
    return {
        "ok": "Normal",
        "normal": "Normal",
        "healthy": "Normal",
        "monitor": "Monitor",
        "minor_anomaly": "Minor anomaly",
        "warning": "Minor anomaly",
        "attention": "Requires attention",
        "requires_attention": "Requires attention",
        "high_anomaly": "High anomaly",
        "limited_data": "Limited data",
        "no_windows": "Analysis unavailable",
        "model_unavailable": "Analysis unavailable",
        "not_scored": "Analysis unavailable",
    }.get(status)


def _status_key(status: str | None) -> str:
    return str(status or "").strip().lower().replace("-", "_").replace(" ", "_")


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * percentile
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[int(rank)]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (rank - lower)


def _series_stats(values: list[float]) -> dict | None:
    values = [float(value) for value in values if isinstance(value, (int, float)) and math.isfinite(value)]
    if not values:
        return None
    return {
        "sample_count": len(values),
        "minimum": min(values),
        "maximum": max(values),
        "median": statistics.median(values),
        "p05": _percentile(values, 0.05),
        "p25": _percentile(values, 0.25),
        "p75": _percentile(values, 0.75),
        "p95": _percentile(values, 0.95),
    }


def _signal_values(records: Iterable[TelemetryRecord], signal_key: str) -> list[float]:
    config = SUPPORTED_FINDING_SIGNALS[signal_key]
    values = []
    for record in records:
        value = None
        for key in config["keys"]:
            value = getattr(record, key, None)
            if value is not None:
                break
        if isinstance(value, (int, float)) and math.isfinite(value):
            values.append(float(value))
    return values


def _baseline_signal_status(sample_count: int, session_count: int) -> str:
    if session_count >= BASELINE_ESTABLISHED_MIN_SESSIONS and sample_count >= BASELINE_ESTABLISHED_MIN_SAMPLES:
        return "established"
    if session_count >= 1 and sample_count >= BASELINE_LEARNING_MIN_SAMPLES:
        return "learning"
    return "insufficient_data"


def _baseline_session_is_clean(session: RideSession, latest_external: AnalysisResult | None = None) -> bool:
    if int(session.record_count or 0) < BASELINE_ELIGIBLE_MIN_SAMPLES:
        return False
    if session.sync_status == "failed" or session.capture_status == "failed" or session.analysis_status == "failed":
        return False
    latest_external = latest_analysis_result(session) if latest_external is None else latest_external
    if not latest_external:
        return True
    status = _status_key(latest_external.overall_status)
    if status in STRONG_ANOMALY_STATUSES:
        return False
    if latest_external.anomaly_ratio is not None and latest_external.anomaly_ratio >= 0.25:
        return False
    return True


def vehicle_signal_baseline(session: RideSession, *, max_sessions: int = 20) -> dict:
    candidates = (
        RideSession.query.filter(RideSession.device_id == session.device_id, RideSession.id != session.id)
        .filter(RideSession.started_at < session.started_at)
        .order_by(RideSession.started_at.desc(), RideSession.id.desc())
        .limit(max_sessions * 3)
        .all()
    )
    latest_external = latest_analysis_results_for_sessions(candidates)
    clean_sessions = [
        candidate for candidate in candidates if _baseline_session_is_clean(candidate, latest_external.get(candidate.id))
    ][:max_sessions]
    values_by_signal = {signal: [] for signal in SUPPORTED_FINDING_SIGNALS}
    sessions_by_signal = {signal: 0 for signal in SUPPORTED_FINDING_SIGNALS}
    for candidate in clean_sessions:
        records = session_records(candidate, limit=5000)
        for signal in SUPPORTED_FINDING_SIGNALS:
            values = _signal_values(records, signal)
            if values:
                values_by_signal[signal].extend(values)
                sessions_by_signal[signal] += 1
    signals = {}
    statuses = []
    for signal, values in values_by_signal.items():
        stats = _series_stats(values)
        session_count = sessions_by_signal[signal]
        sample_count = len(values)
        status = _baseline_signal_status(sample_count, session_count)
        statuses.append(status)
        if stats:
            stats.update({
                "status": status,
                "session_count": session_count,
                "signal": signal,
                "label": SUPPORTED_FINDING_SIGNALS[signal]["label"],
                "unit": SUPPORTED_FINDING_SIGNALS[signal]["unit"],
            })
        else:
            stats = {
                "status": status,
                "session_count": session_count,
                "sample_count": sample_count,
                "signal": signal,
                "label": SUPPORTED_FINDING_SIGNALS[signal]["label"],
                "unit": SUPPORTED_FINDING_SIGNALS[signal]["unit"],
            }
        signals[signal] = stats
    if "established" in statuses:
        status = "established"
    elif "learning" in statuses:
        status = "learning"
    else:
        status = "insufficient_data"
    return {
        "status": status,
        "session_count": len(clean_sessions),
        "sample_count": sum(len(values) for values in values_by_signal.values()),
        "signals": signals,
        "message": baseline_status_message(status),
    }


def baseline_status_message(status: str) -> str:
    if status == "established":
        return "DriveSafe có đủ lịch sử của xe để so sánh với hành vi thông thường."
    if status == "learning":
        return "Đường cơ sở đang được xây dựng. DriveSafe chưa nên kết luận mạnh về mức lệch cá nhân hóa."
    return (
        "Đường cơ sở đang được xây dựng. DriveSafe chưa có đủ lịch sử của xe để đánh giá mức độ lệch "
        "so với hoạt động thông thường."
    )


def _analysis_has_anomaly(latest_external: AnalysisResult | None, anomalous_percentage: float | None, events: list[dict]) -> bool:
    if latest_external and _status_key(latest_external.overall_status) not in {"", "ok", "normal", "healthy"}:
        return _status_key(latest_external.overall_status) not in {"limited_data", "no_windows", "model_unavailable", "not_scored"}
    if anomalous_percentage is not None and anomalous_percentage > 0:
        return True
    return bool(events)


def _physical_signal_evidence(signal: str, current: dict, values: list[float]) -> dict | None:
    if not values:
        return None
    if signal == "battery_voltage":
        low_count = sum(1 for value in values if value < 11.8)
        high_count = sum(1 for value in values if value > 15.2)
        if low_count or high_count:
            direction = "low" if low_count >= high_count else "high"
            ratio = max(low_count, high_count) / len(values)
            return {
                "direction": direction,
                "ratio": ratio,
                "severity": "attention",
                "rule": "existing_local_screening_voltage_guardrail",
            }
    if signal == "ect_c":
        high_count = sum(1 for value in values if value >= 112)
        if high_count:
            ratio = high_count / len(values)
            return {
                "direction": "high",
                "ratio": ratio,
                "severity": "attention",
                "rule": "existing_local_screening_ect_guardrail",
            }
    return None


def _recommendation_code(signal: str, severity: str) -> str:
    config = SUPPORTED_FINDING_SIGNALS[signal]
    if signal == "ect_c" and severity == "high":
        return "ECT_HIGH_CHECK_COOLING"
    return config["monitor_code"] if severity == "monitor" else config["attention_code"]


def _finding_evidence(signal: str, current: dict, baseline: dict | None, direction: str, strength: float, outside_ratio: float, physical: dict | None) -> dict:
    return {
        "session_min": current.get("minimum"),
        "session_max": current.get("maximum"),
        "session_median": current.get("median"),
        "session_p05": current.get("p05"),
        "session_p95": current.get("p95"),
        "session_sample_count": current.get("sample_count", 0),
        "baseline_status": baseline.get("status") if baseline else "insufficient_data",
        "baseline_median": baseline.get("median") if baseline else None,
        "baseline_p05": baseline.get("p05") if baseline else None,
        "baseline_p95": baseline.get("p95") if baseline else None,
        "baseline_sample_count": baseline.get("sample_count", 0) if baseline else 0,
        "baseline_session_count": baseline.get("session_count", 0) if baseline else 0,
        "direction": direction,
        "deviation": round(strength, 4),
        "outside_baseline_ratio": round(outside_ratio, 4),
        "physical_rule": physical.get("rule") if physical else None,
        "physical_rule_ratio": round(physical["ratio"], 4) if physical else 0.0,
    }


def _finding_for_signal(signal: str, records: list[TelemetryRecord], baseline_stats: dict | None, has_model_anomaly: bool) -> dict | None:
    values = _signal_values(records, signal)
    current = _series_stats(values)
    if not current:
        return None
    physical = _physical_signal_evidence(signal, current, values)
    direction = "stable"
    strength = 0.0
    outside_ratio = 0.0
    severity = None
    if baseline_stats and baseline_stats.get("status") == "established" and baseline_stats.get("p95") is not None:
        spread = max(
            float(baseline_stats.get("p75") or 0) - float(baseline_stats.get("p25") or 0),
            (float(baseline_stats.get("p95") or 0) - float(baseline_stats.get("p05") or 0)) / 3.0,
            0.001,
        )
        high_strength = max(0.0, (float(current["median"]) - float(baseline_stats["p95"])) / spread)
        low_strength = max(0.0, (float(baseline_stats["p05"]) - float(current["median"])) / spread)
        if high_strength >= low_strength:
            direction = "high" if high_strength > 0 else "stable"
            strength = high_strength
            outside_ratio = sum(1 for value in values if value > float(baseline_stats["p95"])) / len(values)
        else:
            direction = "low"
            strength = low_strength
            outside_ratio = sum(1 for value in values if value < float(baseline_stats["p05"])) / len(values)
        if direction != "stable" and (strength >= 0.35 or outside_ratio >= 0.1):
            if strength >= 4.0 and outside_ratio >= 0.8:
                severity = "high"
            elif strength >= 1.25 or outside_ratio >= 0.3:
                severity = "attention"
            else:
                severity = "monitor"
    if physical:
        direction = physical["direction"]
        severity = max(severity or "monitor", physical["severity"], key=lambda item: SEVERITY_ORDER[item])
        outside_ratio = max(outside_ratio, physical["ratio"])
        strength = max(strength, 1.0)
    if not severity:
        return None
    code = _recommendation_code(signal, severity)
    evidence = _finding_evidence(signal, current, baseline_stats, direction, strength, outside_ratio, physical)
    return {
        "signal": signal,
        "signal_label": SUPPORTED_FINDING_SIGNALS[signal]["label"],
        "severity": severity,
        "severity_label": SEVERITY_LABELS[severity],
        "direction": direction,
        "recommendation_code": code,
        "recommendation": RECOMMENDATION_TEXT[code],
        "evidence": evidence,
        "evidence_strength": round(max(strength, outside_ratio), 4),
    }


def maintenance_findings(
    session: RideSession,
    records: list[TelemetryRecord],
    *,
    latest_external: AnalysisResult | None = None,
    anomalous_percentage: float | None = None,
    events: list[dict] | None = None,
) -> dict:
    baseline = vehicle_signal_baseline(session)
    has_model_anomaly = _analysis_has_anomaly(latest_external, anomalous_percentage, events or [])
    findings = []
    for signal in SUPPORTED_FINDING_SIGNALS:
        finding = _finding_for_signal(signal, records, baseline["signals"].get(signal), has_model_anomaly)
        if finding:
            findings.append(finding)
    findings.sort(key=lambda item: (SEVERITY_ORDER[item["severity"]], item["evidence_strength"]), reverse=True)
    findings = findings[:FINDING_LIMIT]
    return {
        "baseline": baseline,
        "baseline_status": baseline["status"],
        "baseline_message": baseline["message"],
        "findings": findings,
        "overall_severity": findings[0]["severity"] if findings else ("monitor" if has_model_anomaly else "normal"),
    }


def latest_analysis_result(session: RideSession) -> AnalysisResult | None:
    return (
        AnalysisResult.query.filter_by(ride_session_id=session.id)
        .order_by(AnalysisResult.analyzed_at.desc(), AnalysisResult.id.desc())
        .first()
    )


def latest_analysis_results_for_sessions(sessions: list[RideSession]) -> dict[int, AnalysisResult]:
    session_ids = [session.id for session in sessions]
    if not session_ids:
        return {}
    results = (
        AnalysisResult.query.filter(AnalysisResult.ride_session_id.in_(session_ids))
        .order_by(AnalysisResult.ride_session_id, AnalysisResult.analyzed_at.desc(), AnalysisResult.id.desc())
        .all()
    )
    latest = {}
    for result in results:
        latest.setdefault(result.ride_session_id, result)
    return latest


def latest_sync_batches_for_sessions(sessions: list[RideSession]) -> dict[int, SyncBatch]:
    session_ids = [session.id for session in sessions]
    if not session_ids:
        return {}
    batches = (
        SyncBatch.query.filter(SyncBatch.ride_session_id.in_(session_ids))
        .order_by(SyncBatch.ride_session_id, SyncBatch.received_at.desc(), SyncBatch.id.desc())
        .all()
    )
    latest = {}
    for batch in batches:
        latest.setdefault(batch.ride_session_id, batch)
    return latest


def _json_list(value) -> list[str]:
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        text = str(item).strip()
        if text:
            result.append(text[:80])
    return result


def _finite_float(value):
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _ratio_to_percentage(value):
    numeric = _finite_float(value)
    if numeric is None:
        return None
    return numeric * 100 if 0 <= numeric <= 1 else numeric


def _integer_count(value) -> int:
    numeric = _finite_float(value)
    return max(0, int(numeric)) if numeric is not None else 0


def _summary_metric(summary: dict, *keys):
    for key in keys:
        if key in summary:
            return summary[key]
    return None


def _diagnostic_evidence_payload(summary: dict) -> dict | None:
    evidence = summary.get("diagnostic_evidence") if isinstance(summary, dict) else None
    return evidence if isinstance(evidence, dict) else None


def _token(value) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _human_label(value) -> str:
    return str(value or "").strip().replace("_", " ").replace("-", " ").title()


def _display_value(value, *, limit: int = 240) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, int):
        text = str(value)
    elif isinstance(value, float):
        if not math.isfinite(value):
            return None
        text = f"{value:.4g}"
    elif isinstance(value, (dict, list, tuple)):
        try:
            text = json.dumps(value, sort_keys=True, ensure_ascii=False)
        except (TypeError, ValueError):
            text = str(value)
    else:
        text = str(value)
    text = text.strip()
    if not text:
        return None
    return f"{text[: limit - 3]}..." if len(text) > limit and limit > 3 else text


def _first_present_value(mapping: dict, *keys):
    if not isinstance(mapping, dict):
        return None
    for key in keys:
        value = mapping.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _first_text(mapping: dict, *keys, limit: int = 240) -> str | None:
    return _display_value(_first_present_value(mapping, *keys), limit=limit)


def _mapping(value) -> dict:
    return value if isinstance(value, dict) else {}


def _detail_item(label: str, value, *, limit: int = 240) -> dict | None:
    text = _display_value(value, limit=limit)
    if text is None:
        return None
    return {"label": label, "value": text}


def _detail_items(
    mapping: dict,
    *,
    exclude: set[str] | frozenset[str] = frozenset(),
    include: set[str] | frozenset[str] | None = None,
    limit: int = 12,
) -> list[dict]:
    if not isinstance(mapping, dict):
        return []
    items = []
    normalized_exclude = {_token(item) for item in exclude}
    normalized_include = {_token(item) for item in include} if include is not None else None
    for key, value in mapping.items():
        if value in (None, "", [], {}):
            continue
        normalized_key = _token(key)
        if normalized_key in normalized_exclude:
            continue
        if normalized_include is not None and normalized_key not in normalized_include:
            continue
        item = _detail_item(_human_label(key), value)
        if item is not None:
            items.append(item)
        if len(items) >= limit:
            break
    return items


def _rca_sections(evidence: dict) -> tuple[dict, dict]:
    rca = _mapping(
        _first_present_value(
            evidence,
            "rca_v2",
            "root_cause_analysis_v2",
            "root_cause_analysis",
            "rca",
        )
    )
    interpretation = _mapping(_first_present_value(rca, "interpretation", "rca_v2_interpretation"))
    return rca, interpretation


def _aggregate_section(evidence: dict) -> dict:
    aggregate = _mapping(_first_present_value(evidence, "aggregate", "aggregate_evidence"))
    aggregate_state = evidence.get("aggregate_state")
    if isinstance(aggregate_state, dict):
        aggregate = {**aggregate_state, **aggregate}
    return aggregate


def _diagnostic_evidence_state(evidence: dict) -> dict:
    aggregate = _aggregate_section(evidence)
    aggregate_state = evidence.get("aggregate_state")
    raw_state = _first_present_value(evidence, "evidence_state", "state")
    if raw_state is None and isinstance(aggregate_state, str):
        raw_state = aggregate_state
    if raw_state is None:
        raw_state = _first_present_value(aggregate, "evidence_state", "state")
    state_key = _token(raw_state)
    return {
        "value": state_key or None,
        "label": EVIDENCE_STATE_LABELS.get(state_key, _human_label(raw_state) if raw_state else "--"),
    }


def _statement_items(value, *, noun: str, limit: int = 6) -> list[dict]:
    if value in (None, "", [], {}):
        return []
    raw_items = value if isinstance(value, list) else [value]
    items = []
    text_keys = (
        noun,
        "label",
        "title",
        "name",
        "summary",
        "message",
        "description",
        "signal",
        "status",
        "state",
    )
    excluded = set(text_keys) | {"possible_causes", "recommended_checks"}
    for raw in raw_items:
        if isinstance(raw, dict):
            text = _first_text(raw, *text_keys)
            meta = _detail_items(raw, exclude=excluded, limit=3)
        else:
            text = _display_value(raw)
            meta = []
        if text:
            items.append({"text": text, "meta": meta})
        if len(items) >= limit:
            break
    return items


def _first_section_value(sections: tuple[dict, ...], *keys):
    for section in sections:
        value = _first_present_value(section, *keys)
        if value not in (None, "", [], {}):
            return value
    return None


def _historical_items(history) -> list[dict]:
    if not isinstance(history, dict):
        return _statement_items(history, noun="historical")
    items = []
    for key in (
        "recurrence",
        "recurrences",
        "historical_recurrence",
        "trend",
        "trends",
        "possible_trend",
        "summary",
        "message",
        "comparison",
        "status",
        "state",
    ):
        value = history.get(key)
        if value in (None, "", [], {}):
            continue
        if isinstance(value, (list, dict)):
            items.extend(_statement_items(value, noun="historical", limit=3))
        else:
            item = _detail_item(_human_label(key), value)
            if item:
                items.append({"text": f"{item['label']}: {item['value']}", "meta": []})
        if len(items) >= 6:
            break
    if not items:
        for detail in _detail_items(history, exclude={"cutoff", "historical_cutoff", "provenance"}, limit=6):
            items.append({"text": f"{detail['label']}: {detail['value']}", "meta": []})
    return items[:6]


def _check_items(value, *, grouped_priority: str | None = None, start_index: int = 0) -> list[dict]:
    if value in (None, "", [], {}):
        return []
    raw_items = value if isinstance(value, list) else [value]
    checks = []
    text_keys = ("check", "recommended_check", "label", "title", "name", "summary", "message", "description")
    excluded = set(text_keys) | {
        "priority",
        "check_priority",
        "priority_state",
        "reason",
        "rationale",
        "supporting_evidence",
        "why",
    }
    for offset, raw in enumerate(raw_items):
        data = dict(raw) if isinstance(raw, dict) else {"check": raw}
        priority_value = _first_present_value(data, "priority", "check_priority", "priority_state")
        if priority_value is None and grouped_priority is not None:
            priority_value = grouped_priority
        priority = _token(priority_value)
        checks.append({
            "text": _first_text(data, *text_keys) or _display_value(raw) or "Recommended check",
            "reason": _first_text(data, "reason", "rationale", "supporting_evidence", "why"),
            "priority": priority or None,
            "priority_label": CHECK_PRIORITY_LABELS.get(priority, _human_label(priority_value) if priority_value else "Unspecified"),
            "details": _detail_items(data, exclude=excluded, limit=4),
            "_index": start_index + offset,
        })
    return checks


def _recommended_checks(evidence: dict, rca: dict, interpretation: dict) -> list[dict]:
    raw = _first_section_value(
        (evidence, rca, interpretation),
        "recommended_checks",
        "checks",
        "recommendations",
    )
    if raw in (None, "", [], {}):
        return []
    checks = []
    if isinstance(raw, dict):
        index = 0
        seen_groups = set()
        for group in CHECK_PRIORITY_ORDER:
            group_value = raw.get(group)
            group_checks = _check_items(group_value, grouped_priority=group, start_index=index)
            checks.extend(group_checks)
            index += len(group_checks)
            seen_groups.add(group)
        for group, group_value in raw.items():
            group_key = _token(group)
            if group_key in seen_groups:
                continue
            group_checks = _check_items(group_value, grouped_priority=str(group), start_index=index)
            checks.extend(group_checks)
            index += len(group_checks)
    else:
        checks = _check_items(raw)

    def sort_key(item: dict) -> tuple[int, int]:
        priority = item.get("priority")
        rank = CHECK_PRIORITY_ORDER.index(priority) if priority in CHECK_PRIORITY_ORDER else len(CHECK_PRIORITY_ORDER)
        return rank, item["_index"]

    checks.sort(key=sort_key)
    for check in checks:
        check.pop("_index", None)
    return checks


def _version_details(evidence: dict) -> list[dict]:
    details = []
    for key in ("detector_versions", "model_versions", "rule_versions", "versions"):
        value = evidence.get(key)
        if isinstance(value, dict):
            details.extend(_detail_items(value, limit=12 - len(details)))
        elif isinstance(value, list):
            for index, item in enumerate(value, start=1):
                detail = _detail_item(f"{_human_label(key)} {index}", item)
                if detail:
                    details.append(detail)
        if len(details) >= 12:
            break
    return details[:12]


def _detector_rows(evidence: dict) -> list[dict]:
    detectors = _first_present_value(evidence, "detectors", "detector_evidence", "detector_results")
    versions = _mapping(evidence.get("detector_versions"))
    rows = []
    if isinstance(detectors, dict):
        raw_items = list(detectors.items())
    elif isinstance(detectors, list):
        raw_items = [(None, item) for item in detectors]
    else:
        raw_items = []

    for fallback_name, raw in raw_items:
        data = raw if isinstance(raw, dict) else {"value": raw}
        name = (
            _first_text(data, "detector_id", "id", "name", "label")
            or _display_value(fallback_name)
            or "Detector"
        )
        status = _first_text(data, "status", "state", "result")
        version = (
            _first_text(data, "version", "detector_version", "model_version", "rule_version")
            or _display_value(versions.get(fallback_name) if fallback_name is not None else None)
        )
        score_keys = {key for key in data if any(token in _token(key) for token in ("score", "ratio", "confidence"))}
        scores = _detail_items(data, include=score_keys, limit=4)
        rows.append({
            "name": name,
            "status": status or "--",
            "status_key": _token(status) or "unknown",
            "version": version or "--",
            "scores": "; ".join(f"{item['label']} {item['value']}" for item in scores) or "--",
        })
        if len(rows) >= 12:
            break

    if not rows and versions:
        for detector, version in versions.items():
            rows.append({
                "name": _human_label(detector),
                "status": "--",
                "status_key": "unknown",
                "version": _display_value(version) or "--",
                "scores": "--",
            })
            if len(rows) >= 12:
                break
    return rows


def _coverage_details(evidence: dict) -> list[dict]:
    aggregate = _aggregate_section(evidence)
    wanted = {
        key
        for source in (aggregate, evidence)
        for key in source
        if any(token in _token(key) for token in ("coverage", "disagreement", "agreement", "detector_count", "support"))
    }
    details = _detail_items(aggregate, include=wanted, exclude={"evidence_state", "state"}, limit=8)
    if len(details) < 8:
        details.extend(_detail_items(evidence, include=wanted, exclude={"diagnostic_evidence"}, limit=8 - len(details)))
    return details[:8]


def _provenance_details(evidence: dict) -> list[dict]:
    provenance = _mapping(evidence.get("provenance"))
    details = _detail_items(provenance, limit=12)
    if details:
        return details
    wanted = {
        key
        for key in evidence
        if any(token in _token(key) for token in ("provenance", "cutoff", "generated", "analyzed", "source"))
    }
    return _detail_items(evidence, include=wanted, limit=12)


def diagnostic_evidence_view(summary: dict) -> dict | None:
    evidence = _diagnostic_evidence_payload(summary)
    if not evidence:
        return None

    rca, interpretation = _rca_sections(evidence)
    history = _first_section_value((evidence, rca, interpretation), "historical_evidence", "history", "historical")
    provenance = _mapping(evidence.get("provenance"))
    state = _diagnostic_evidence_state(evidence)
    cutoff = (
        _first_present_value(provenance, "historical_cutoff", "cutoff", "cutoff_at", "as_of")
        or _first_present_value(_mapping(history), "historical_cutoff", "cutoff", "cutoff_at", "as_of")
        or _first_present_value(evidence, "historical_cutoff", "cutoff", "cutoff_at")
    )
    summary_details = [
        _detail_item("Evidence schema", _first_present_value(evidence, "evidence_schema_version", "schema_version", "version")),
        _detail_item("Evidence state", state["label"]),
        _detail_item(
            "Analysis version",
            _first_present_value(evidence, "analysis_version", "analyzer_version")
            or _first_present_value(summary, "analysis_version", "analyzer_version", "model_version"),
        ),
        _detail_item(
            "Maturity",
            _first_present_value(evidence, "maturity", "research_maturity", "production_maturity", "maturity_markers"),
        ),
        _detail_item("Historical cutoff", cutoff),
    ]
    observations = _statement_items(
        _first_section_value((evidence, rca, interpretation), "observations", "detected_observations"),
        noun="observation",
    )
    symptoms = _statement_items(
        _first_section_value((evidence, rca, interpretation), "symptoms", "observed_symptoms"),
        noun="symptom",
    )
    return {
        "state": state,
        "summary": {
            "details": [item for item in summary_details if item],
            "observations": observations,
            "symptoms": symptoms,
            "historical": _historical_items(history),
        },
        "checks": _recommended_checks(evidence, rca, interpretation),
        "technical": {
            "detectors": _detector_rows(evidence),
            "coverage": _coverage_details(evidence),
            "versions": _version_details(evidence),
            "provenance": _provenance_details(evidence),
        },
    }


def compact_analysis_snapshot(session: RideSession, latest_external=_UNSET) -> dict:
    latest_external = latest_analysis_result(session) if latest_external is _UNSET else latest_external
    result_summary = latest_external.result_summary if latest_external and isinstance(latest_external.result_summary, dict) else {}
    ratio_source = _summary_metric(result_summary, "anomalous_sample_percentage", "anomaly_ratio")
    if ratio_source is None and latest_external:
        ratio_source = latest_external.anomaly_ratio
    anomalous_percentage = _ratio_to_percentage(ratio_source)
    anomalous_count = _summary_metric(result_summary, "anomalous_sample_count", "anomaly_window_count")
    if anomalous_count is None and latest_external:
        anomalous_count = latest_external.anomaly_window_count or 0
    event_count = _summary_metric(result_summary, "event_count", "anomaly_window_count")
    if event_count is None and latest_external:
        event_count = latest_external.anomaly_window_count or 0
    max_score = _finite_float(_summary_metric(result_summary, "max_anomaly_score", "max_score"))
    mean_score = _finite_float(_summary_metric(result_summary, "mean_anomaly_score", "mean_score"))
    external_result = result_from_external_status(latest_external.overall_status if latest_external else None)
    if external_result:
        result = external_result
    elif latest_external:
        result = result_from_scores(max_score, anomalous_percentage, session.record_count > 0)
    else:
        result = "Analysis unavailable"
    main_signals = _json_list(
        _summary_metric(result_summary, "main_signals", "most_unusual_features", "affected_signals", "signals")
    )
    return {
        "session_pk": session.id,
        "session_id": session.session_id,
        "vehicle": session.device.vehicle_name,
        "device": session.device.device_name,
        "start_time": format_datetime(session.started_at),
        "upload_time": format_datetime(session.created_at),
        "analysis_time": format_datetime(latest_external.analyzed_at if latest_external else None),
        "duration_seconds": session_duration_seconds(session),
        "duration_label": format_duration(session_duration_seconds(session)),
        "sample_count": int(session.record_count or 0),
        "processing_state": processing_state(session),
        "result": result,
        "max_anomaly_score": max_score,
        "mean_anomaly_score": mean_score,
        "anomalous_sample_count": _integer_count(anomalous_count),
        "anomalous_sample_percentage": anomalous_percentage,
        "event_count": _integer_count(event_count),
        "events": [],
        "main_signals": main_signals[:4],
        "model_version": (
            latest_external.model_version
            if latest_external and latest_external.model_version
            else ("stored analysis" if latest_external else "Open session for local screening")
        ),
        "analysis_status": session.analysis_status,
        "analysis_run_id": session.analysis_run_id,
        "telemetry_schema_version": session.telemetry_schema_version,
        "source_type": session.source_type,
        "capture_started_at": format_datetime(session.capture_started_at),
        "capture_ended_at": format_datetime(session.capture_ended_at),
        "duration_ms": session.duration_ms,
        "external_overall_status": latest_external.overall_status if latest_external else None,
        "external_anomaly_ratio": latest_external.anomaly_ratio if latest_external else None,
        "external_telemetry_schema_version": latest_external.telemetry_schema_version if latest_external else None,
        "external_ecu_profile_id": latest_external.ecu_profile_id if latest_external else None,
        "external_decoder_id": latest_external.decoder_id if latest_external else None,
        "external_decoder_version": latest_external.decoder_version if latest_external else None,
        "analysis_signal_columns": latest_external.signal_columns if latest_external else None,
        "feature_schema_version": latest_external.feature_schema_version if latest_external else None,
        "analysis_run_id_external": latest_external.analysis_run_id if latest_external else None,
        "health_score": latest_external.health_score if latest_external else None,
        "source": "stored-analysis" if latest_external else "summary",
    }


def group_anomaly_events(records: list[TelemetryRecord], samples: list[dict] | None = None, *, merge_gap_seconds: float = 5.0) -> list[dict]:
    samples = samples or scored_samples(records)
    lookup = {int(sample["seq"]): sample for sample in samples}
    abnormal_records = [record for record in records if lookup.get(int(record.seq), {}).get("is_anomalous")]
    if not abnormal_records:
        return []

    events = []
    current = []
    last_time = None
    last_seq = None
    for record in abnormal_records:
        sample = lookup[int(record.seq)]
        current_time = event_time(record)
        gap_seconds = (current_time - last_time).total_seconds() if current_time and last_time else 0
        seq_gap = int(record.seq) - int(last_seq) if last_seq is not None else 0
        if current and (gap_seconds > merge_gap_seconds or seq_gap > 5):
            events.append(_event_from_records(current, lookup))
            current = []
        current.append(record)
        last_time = current_time
        last_seq = int(record.seq)
    if current:
        events.append(_event_from_records(current, lookup))
    return events


def _event_from_records(records: list[TelemetryRecord], samples_by_seq: dict[int, dict]) -> dict:
    start = event_time(records[0])
    end = event_time(records[-1])
    scores = [samples_by_seq[int(record.seq)]["anomaly_score"] for record in records]
    signals = Counter()
    for record in records:
        for signal in samples_by_seq[int(record.seq)].get("signals", []):
            signals[signal] += 1
    top_signals = [signal for signal, _ in signals.most_common(4)] or ["Stored telemetry pattern"]
    max_score = max(scores) if scores else 0.0
    average_score = statistics.fmean(scores) if scores else 0.0
    duration = max(0.0, (end - start).total_seconds()) if start and end else 0.0
    title = f"{'/'.join(top_signals[:2])} anomaly"
    suggestion = (
        f"Unusual {', '.join(top_signals[:3]).lower()} behavior was detected in this time range. "
        "Inspect the related signal path, connectors, and riding context before drawing a mechanical conclusion."
    )
    return {
        "id": f"event-{int(records[0].seq)}-{int(records[-1].seq)}",
        "title": title,
        "start_seq": int(records[0].seq),
        "end_seq": int(records[-1].seq),
        "start_time": start.isoformat().replace("+00:00", "Z") if start else None,
        "end_time": end.isoformat().replace("+00:00", "Z") if end else None,
        "start_elapsed": samples_by_seq[int(records[0].seq)]["elapsed_seconds"],
        "end_elapsed": samples_by_seq[int(records[-1].seq)]["elapsed_seconds"],
        "duration_seconds": duration,
        "duration_label": format_duration(duration),
        "severity": severity_from_score(max_score),
        "max_anomaly_score": round(max_score, 4),
        "average_anomaly_score": round(average_score, 4),
        "abnormal_sample_count": len(records),
        "signals": top_signals,
        "suggestion": suggestion,
    }


def analysis_snapshot(
    session: RideSession,
    records: list[TelemetryRecord] | None = None,
    latest_external=_UNSET,
) -> dict:
    records = records if records is not None else session_records(session)
    samples = scored_samples(records)
    events = group_anomaly_events(records, samples)
    if samples:
        max_score = max(sample["anomaly_score"] for sample in samples)
        mean_score = statistics.fmean(sample["anomaly_score"] for sample in samples)
        anomalous_count = sum(1 for sample in samples if sample["is_anomalous"])
        anomalous_percentage = anomalous_count / len(samples) * 100
        source = samples[0]["source"]
    else:
        max_score = None
        mean_score = None
        anomalous_count = 0
        anomalous_percentage = None
        source = "unavailable"
    latest_external = latest_analysis_result(session) if latest_external is _UNSET else latest_external
    result_summary = latest_external.result_summary if latest_external and isinstance(latest_external.result_summary, dict) else {}
    diagnostic_evidence = _diagnostic_evidence_payload(result_summary)
    external_result = result_from_external_status(latest_external.overall_status if latest_external else None)
    maintenance = maintenance_findings(
        session,
        records,
        latest_external=latest_external,
        anomalous_percentage=anomalous_percentage,
        events=events,
    )
    signals = Counter()
    for event in events:
        for signal in event["signals"]:
            signals[signal] += 1
    return {
        "session_pk": session.id,
        "session_id": session.session_id,
        "vehicle": session.device.vehicle_name,
        "device": session.device.device_name,
        "start_time": format_datetime(session.started_at),
        "upload_time": format_datetime(session.created_at),
        "analysis_time": format_datetime(latest_external.analyzed_at if latest_external else None),
        "duration_seconds": session_duration_seconds(session),
        "duration_label": format_duration(session_duration_seconds(session)),
        "sample_count": int(session.record_count or len(records)),
        "processing_state": processing_state(session),
        "result": external_result or result_from_scores(max_score, anomalous_percentage, bool(records)),
        "max_anomaly_score": max_score,
        "mean_anomaly_score": mean_score,
        "anomalous_sample_count": anomalous_count,
        "anomalous_sample_percentage": anomalous_percentage,
        "event_count": len(events),
        "events": events,
        "main_signals": [signal for signal, _ in signals.most_common(4)],
        "model_version": (
            latest_external.model_version
            if latest_external and latest_external.model_version
            else ("stored ML score" if source == "embedded-ml" else "local statistical screening")
        ),
        "analysis_status": session.analysis_status,
        "analysis_run_id": session.analysis_run_id,
        "telemetry_schema_version": session.telemetry_schema_version,
        "source_type": session.source_type,
        "capture_started_at": format_datetime(session.capture_started_at),
        "capture_ended_at": format_datetime(session.capture_ended_at),
        "duration_ms": session.duration_ms,
        "external_overall_status": latest_external.overall_status if latest_external else None,
        "external_anomaly_ratio": latest_external.anomaly_ratio if latest_external else None,
        "external_telemetry_schema_version": latest_external.telemetry_schema_version if latest_external else None,
        "external_ecu_profile_id": latest_external.ecu_profile_id if latest_external else None,
        "external_decoder_id": latest_external.decoder_id if latest_external else None,
        "external_decoder_version": latest_external.decoder_version if latest_external else None,
        "analysis_signal_columns": latest_external.signal_columns if latest_external else None,
        "feature_schema_version": latest_external.feature_schema_version if latest_external else None,
        "analysis_run_id_external": latest_external.analysis_run_id if latest_external else None,
        "health_score": latest_external.health_score if latest_external else None,
        "diagnostic_evidence": diagnostic_evidence,
        "diagnostic_evidence_view": diagnostic_evidence_view(result_summary),
        "baseline_status": maintenance["baseline_status"],
        "baseline_message": maintenance["baseline_message"],
        "baseline": maintenance["baseline"],
        "findings": maintenance["findings"],
        "overall_recommendation_severity": maintenance["overall_severity"],
        "source": source,
        "capture": capture_termination_summary(session),
    }


def session_summary(
    session: RideSession,
    *,
    analysis: dict | None = None,
    include_analysis: bool = True,
    latest_external=_UNSET,
    latest_batch=_UNSET,
) -> dict:
    if analysis is not None:
        snapshot = analysis
    elif include_analysis:
        snapshot = analysis_snapshot(session, latest_external=latest_external)
    else:
        snapshot = compact_analysis_snapshot(session, latest_external=latest_external)
    if latest_batch is _UNSET:
        latest_batch = (
            SyncBatch.query.filter_by(ride_session_id=session.id)
            .order_by(SyncBatch.received_at.desc(), SyncBatch.id.desc())
            .first()
        )
    return {
        **snapshot,
        "id": session.id,
        "device_id": session.device.device_id,
        "sync_status": session.sync_status,
        "capture_status": session.capture_status,
        "termination_reason": session.termination_reason,
        "capture": snapshot.get("capture", capture_termination_summary(session)),
        "analysis_status": session.analysis_status,
        "analysis_run_id": session.analysis_run_id,
        "telemetry_schema_version": session.telemetry_schema_version,
        "source_type": session.source_type,
        "capture_started_at": format_datetime(session.capture_started_at),
        "capture_ended_at": format_datetime(session.capture_ended_at),
        "duration_ms": session.duration_ms,
        "upload_time_raw": session.created_at.isoformat().replace("+00:00", "Z") if session.created_at else None,
        "end_time": format_datetime(session.ended_at),
        "last_record_time": format_datetime(session.last_record_at),
        "seq_range": (
            f"{session.first_seq if session.first_seq is not None else '--'}-"
            f"{session.last_seq if session.last_seq is not None else '--'}"
        ),
        "notes": session.notes or "",
        "last_batch_status": latest_batch.status if latest_batch else "--",
    }


def session_summaries(
    sessions: list[RideSession],
    *,
    include_analysis: bool = False,
    local_analysis_limit: int = 0,
    analysis_cache: dict[int, dict] | None = None,
) -> list[dict]:
    analysis_cache = analysis_cache or {}
    latest_external = latest_analysis_results_for_sessions(sessions)
    latest_batches = latest_sync_batches_for_sessions(sessions)
    summaries = []
    for index, session in enumerate(sessions):
        analysis = analysis_cache.get(session.id)
        use_full_analysis = include_analysis or index < local_analysis_limit
        summaries.append(
            session_summary(
                session,
                analysis=analysis,
                include_analysis=use_full_analysis if analysis is None else True,
                latest_external=latest_external.get(session.id),
                latest_batch=latest_batches.get(session.id),
            )
        )
    return summaries


def build_chart_payload(session: RideSession, *, max_points: int = 1200) -> dict:
    records = session_records(session)
    scored = scored_samples(records)
    score_by_seq = {sample["seq"]: sample for sample in scored}
    sampled_records = downsample_records(records, max_points=max_points)
    samples = []
    for record in sampled_records:
        row = {
            **record.to_dict(),
            **score_by_seq.get(int(record.seq), {}),
        }
        if row.get("timestamp") is None:
            row["timestamp"] = row.get("server_received_at")
        samples.append(row)
    stats = statistics_for_records(records, session)
    events = group_anomaly_events(records, scored)
    quality = stats["_quality"]
    quality["anomalous_sample_count"] = sum(1 for sample in scored if sample["is_anomalous"])
    quality["anomalous_sample_percentage"] = (
        quality["anomalous_sample_count"] / len(scored) * 100 if scored else 0.0
    )
    analysis = analysis_snapshot(session, records)
    return {
        "session": session_summary(session, analysis=analysis),
        "parameters": available_parameters(records),
        "samples": samples,
        "statistics": stats,
        "events": events,
        "downsampled": len(sampled_records) < len(records),
        "source_count": len(records),
        "sample_count": len(sampled_records),
    }


def query_sessions_for_user(query, args):
    search = (args.get("search") or "").strip()
    if search:
        pattern = f"%{search}%"
        query = query.filter(
            or_(
                RideSession.session_id.ilike(pattern),
                Device.device_name.ilike(pattern),
                Device.vehicle_name.ilike(pattern),
                RideSession.notes.ilike(pattern),
            )
        )
    device_id = (args.get("device_id") or "").strip()
    if device_id:
        query = query.filter(Device.device_id == device_id)
    vehicle = (args.get("vehicle") or "").strip()
    if vehicle:
        query = query.filter(Device.vehicle_name == vehicle)
    state = (args.get("state") or "").strip()
    if state == "Uploaded":
        query = query.filter(RideSession.sync_status == "syncing")
    elif state == "Processing":
        query = query.filter(RideSession.sync_status == "partial")
    elif state == "Analyzed":
        query = query.filter(RideSession.analysis_status == "completed")
    elif state == "Captured":
        query = query.filter(RideSession.sync_status == "complete", RideSession.analysis_status != "completed")
    elif state == "Analysis pending":
        query = query.filter(RideSession.analysis_status.in_(("pending", "submitted")))
    elif state == "Invalid data":
        query = query.filter(RideSession.record_count <= 0)
    elif state == "Analysis failed":
        query = query.filter(RideSession.analysis_status == "failed")
    date_from = (args.get("date_from") or "").strip()
    date_to = (args.get("date_to") or "").strip()
    if date_from:
        try:
            query = query.filter(RideSession.started_at >= datetime.fromisoformat(date_from).replace(tzinfo=timezone.utc))
        except ValueError:
            pass
    if date_to:
        try:
            end = datetime.fromisoformat(date_to).replace(hour=23, minute=59, second=59, tzinfo=timezone.utc)
            query = query.filter(RideSession.started_at <= end)
        except ValueError:
            pass
    if args.get("has_errors") == "1":
        query = query.filter(RideSession.sync_status.in_(("partial", "failed")))
    return query


def apply_derived_filters(
    sessions: list[RideSession],
    args,
    *,
    analysis_cache: dict[int, dict] | None = None,
) -> list[RideSession]:
    analysis_cache = analysis_cache if analysis_cache is not None else {}
    result_filter = (args.get("result") or "").strip()
    attention_filter = args.get("attention") == "1"
    events_filter = args.get("has_events") == "1"
    if not any((result_filter, attention_filter, events_filter)):
        return sessions
    filtered = []
    for session in sessions:
        summary = analysis_cache.get(session.id)
        if summary is None:
            summary = analysis_snapshot(session)
            analysis_cache[session.id] = summary
        keep = True
        if result_filter:
            if result_filter == "anomalous":
                keep = summary["result"] in {"Minor anomaly", "Requires attention", "High anomaly"}
            elif result_filter == "normal":
                keep = summary["result"] == "Normal"
            else:
                keep = summary["result"] == result_filter
        if attention_filter:
            keep = keep and summary["result"] in {"Requires attention", "High anomaly"}
        if events_filter:
            keep = keep and summary["event_count"] > 0
        if keep:
            filtered.append(session)
    return filtered


def _overview_observation(finding: dict) -> str:
    signal = finding.get("signal")
    direction = finding.get("direction")
    if signal == "ect_c" and direction == "high":
        return "Nhiệt độ động cơ cao hơn mức thông thường của xe."
    if signal == "battery_voltage":
        if direction == "low":
            return "Điện áp hệ thống cho thấy xu hướng thấp trong dữ liệu sau chuyến đi gần đây."
        if direction == "high":
            return "Điện áp hệ thống cho thấy xu hướng cao trong dữ liệu sau chuyến đi gần đây."
        return "Điện áp hệ thống cho thấy xu hướng khác thường."
    if signal == "iat_c" and direction == "high":
        return "Nhiệt độ khí nạp cao hơn mức thông thường của xe."
    if direction in {"high", "low"}:
        direction_label = "cao" if direction == "high" else "thấp"
        return f"{finding.get('signal_label', 'Tín hiệu')} cho thấy xu hướng {direction_label}."
    return f"{finding.get('signal_label', 'Tín hiệu')} cho thấy xu hướng khác thường sau chuyến đi."


def overview_condition_summary(summaries: list[dict], *, limit: int = OVERVIEW_CONDITION_SESSION_LIMIT) -> dict:
    analyzed = summaries[:limit]
    if not analyzed:
        return {
            "status": "insufficient_data",
            "label": OVERVIEW_SEVERITY_LABELS["insufficient_data"],
            "message": "Hãy hoàn tất thêm vài chuyến đi để bắt đầu xây dựng mức hoạt động thông thường của xe.",
            "scope": "Chưa có chuyến đi nào được phân tích",
            "findings": [],
        }
    best_by_signal = {}
    overall = "normal"
    for recency, summary in enumerate(analyzed):
        severity = summary.get("overall_recommendation_severity") or "normal"
        if SEVERITY_ORDER.get(severity, 0) > SEVERITY_ORDER.get(overall, 0):
            overall = severity
        for finding in summary.get("findings", []) or []:
            signal = finding.get("signal")
            if not signal:
                continue
            candidate = {
                "signal": signal,
                "signal_label": finding.get("signal_label", signal.replace("_", " ").title()),
                "severity": finding.get("severity", "monitor"),
                "severity_label": OVERVIEW_SEVERITY_LABELS.get(finding.get("severity"), finding.get("severity_label", "Monitor")),
                "observation": _overview_observation(finding),
                "recommendation": finding.get("recommendation"),
                "session_pk": summary["id"],
                "session_id": summary["session_id"],
                "recency": recency,
            }
            current = best_by_signal.get(signal)
            if current is None:
                best_by_signal[signal] = candidate
                continue
            current_rank = (SEVERITY_ORDER.get(current["severity"], 0), -current["recency"])
            candidate_rank = (SEVERITY_ORDER.get(candidate["severity"], 0), -candidate["recency"])
            if candidate_rank > current_rank:
                best_by_signal[signal] = candidate
    findings = sorted(
        best_by_signal.values(),
        key=lambda item: (SEVERITY_ORDER.get(item["severity"], 0), -item["recency"]),
        reverse=True,
    )
    count = len(analyzed)
    scope = f"Dựa trên {count} chuyến đi đã phân tích gần nhất"
    if findings:
        message = "DriveSafe ghi nhận các khuyến nghị bảo dưỡng sau chuyến đi gần đây cần xem lại."
    elif overall == "monitor":
        message = "DriveSafe ghi nhận một lệch nhẹ đáng để theo dõi trong các chuyến đi tiếp theo."
    else:
        message = "Chưa ghi nhận khuyến nghị bảo dưỡng đáng chú ý trong các chuyến đi đã phân tích gần đây."
    return {
        "status": overall,
        "label": OVERVIEW_SEVERITY_LABELS.get(overall, overall.title()),
        "message": message,
        "scope": scope,
        "findings": findings,
    }


def overview_context(devices: list[Device], sessions: list[RideSession]) -> dict:
    analysis_cache = {}
    condition_sessions = [
        session for session in sessions if session.analysis_status == "completed"
    ][:OVERVIEW_CONDITION_SESSION_LIMIT]
    condition_summaries = []
    for session in condition_sessions:
        analysis = analysis_snapshot(session)
        analysis_cache[session.id] = analysis
        condition_summaries.append(session_summary(session, analysis=analysis))
    summaries = session_summaries(
        sessions,
        local_analysis_limit=SUMMARY_LOCAL_ANALYSIS_LIMIT,
        analysis_cache=analysis_cache,
    )
    summaries.sort(key=lambda item: item["upload_time_raw"] or "", reverse=True)
    requiring_attention = [item for item in summaries if item["result"] in {"Requires attention", "High anomaly"}]
    recent_events = []
    for summary in summaries[:20]:
        for event in summary["events"]:
            recent_events.append({**event, "session_id": summary["session_id"], "session_pk": summary["id"]})
    recent_events.sort(key=lambda item: item.get("start_time") or "", reverse=True)
    last_sync = max((as_utc(device.last_seen_at) for device in devices if device.last_seen_at), default=None)
    latest_session = summaries[0] if summaries else None
    return {
        "summary_cards": [
            {"label": "Total uploaded sessions", "value": len(sessions), "hint": "Durable uploaded ride logs"},
            {"label": "Sessions analyzed", "value": sum(1 for item in summaries if item["processing_state"] == "Analyzed"), "hint": "Completed local screening"},
            {"label": "Sessions requiring attention", "value": len(requiring_attention), "hint": "Requires attention or high anomaly"},
            {"label": "Recent anomaly events", "value": len(recent_events[:25]), "hint": "Grouped events in recent sessions"},
            {"label": "Last synchronization time", "value": format_datetime(last_sync), "hint": "Most recent device upload/contact"},
            {"label": "Registered devices", "value": len(devices), "hint": "Enabled and disabled readers"},
        ],
        "latest_session": latest_session,
        "vehicle_condition": overview_condition_summary(condition_summaries),
        "recent_diagnoses": summaries[:8],
        "recent_events": recent_events[:8],
        "device_sync": device_sync_summary(devices),
    }


def device_sync_summary(devices: list[Device]) -> list[dict]:
    device_ids = [device.id for device in devices]
    latest_sessions = {}
    pending_counts = {}
    if device_ids:
        latest_created = (
            RideSession.query.with_entities(
                RideSession.device_id,
                func.max(RideSession.created_at).label("created_at"),
            )
            .filter(RideSession.device_id.in_(device_ids))
            .group_by(RideSession.device_id)
            .subquery()
        )
        latest_rows = (
            RideSession.query.join(
                latest_created,
                (RideSession.device_id == latest_created.c.device_id)
                & (RideSession.created_at == latest_created.c.created_at),
            )
            .all()
        )
        for session in latest_rows:
            latest_sessions.setdefault(session.device_id, session)
        pending_counts = dict(
            RideSession.query.with_entities(RideSession.device_id, func.count(RideSession.id))
            .filter(RideSession.device_id.in_(device_ids), RideSession.sync_status == "syncing")
            .group_by(RideSession.device_id)
            .all()
        )
    rows = []
    for device in devices:
        latest_session = latest_sessions.get(device.id)
        pending = int(pending_counts.get(device.id, 0))
        rows.append({
            "id": device.id,
            "device_id": device.device_id,
            "device_name": device.device_name,
            "vehicle": device.vehicle_name,
            "state": "Offline" if not device.last_seen_at else "Synced",
            "last_sync": format_datetime(device.last_seen_at),
            "last_session": latest_session.session_id if latest_session else "--",
            "last_session_pk": latest_session.id if latest_session else None,
            "firmware_version": device.firmware_version or "--",
            "configuration_version": device.hardware_version or "--",
            "pending_upload_count": pending,
            "error_state": "Inactive" if not device.is_active else "--",
        })
    return rows


def database_summary_counts() -> dict:
    return {
        "sessions": RideSession.query.count(),
        "devices": Device.query.count(),
        "records": TelemetryRecord.query.count(),
        "sync_batches": SyncBatch.query.count(),
        "latest_upload": format_datetime(RideSession.query.with_entities(func.max(RideSession.created_at)).scalar()),
    }
