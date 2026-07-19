import math
import statistics
from collections import Counter
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import func, or_

from models import Device, RideSession, SyncBatch, TelemetryRecord


PARAMETERS = [
    {"key": "rpm", "label": "RPM", "unit": "rpm", "precision": 0},
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


def as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def event_time(record: TelemetryRecord) -> datetime:
    return as_utc(record.timestamp) or as_utc(record.server_received_at)


def session_duration_seconds(session: RideSession) -> float:
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
    if session.sync_status == "failed":
        return "Analysis failed"
    if session.sync_status == "syncing":
        return "Uploaded"
    if session.sync_status == "partial":
        return "Processing"
    return "Analyzed"


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

    raw_frames = [record.raw_frame for record in records if record.raw_frame is not None]
    invalid_frames = 0
    checksum_failures = 0
    for frame in raw_frames:
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

    if isinstance(record.battery, (int, float)):
        if record.battery < 11.8:
            score = max(score, 0.78)
            signals.append("Battery voltage")
        elif record.battery > 15.2:
            score = max(score, 0.72)
            signals.append("Battery voltage")
    if isinstance(record.ect, (int, float)) and record.ect >= 112:
        score = max(score, 0.76)
        signals.append("Engine temperature")
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
        if record.device_time_ms is not None:
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


def analysis_snapshot(session: RideSession, records: list[TelemetryRecord] | None = None) -> dict:
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
        "duration_seconds": session_duration_seconds(session),
        "duration_label": format_duration(session_duration_seconds(session)),
        "sample_count": int(session.record_count or len(records)),
        "processing_state": processing_state(session),
        "result": result_from_scores(max_score, anomalous_percentage, bool(records)),
        "max_anomaly_score": max_score,
        "mean_anomaly_score": mean_score,
        "anomalous_sample_count": anomalous_count,
        "anomalous_sample_percentage": anomalous_percentage,
        "event_count": len(events),
        "events": events,
        "main_signals": [signal for signal, _ in signals.most_common(4)],
        "model_version": "stored ML score" if source == "embedded-ml" else "local statistical screening",
        "source": source,
    }


def session_summary(session: RideSession) -> dict:
    snapshot = analysis_snapshot(session)
    latest_batch = (
        SyncBatch.query.filter_by(ride_session_id=session.id)
        .order_by(SyncBatch.received_at.desc())
        .first()
    )
    return {
        **snapshot,
        "id": session.id,
        "device_id": session.device.device_id,
        "sync_status": session.sync_status,
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
    return {
        "session": session_summary(session),
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
        query = query.filter(RideSession.sync_status == "complete")
    elif state == "Invalid data":
        query = query.filter(RideSession.record_count <= 0)
    elif state == "Analysis failed":
        query = query.filter(RideSession.sync_status == "failed")
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


def apply_derived_filters(sessions: list[RideSession], args) -> list[RideSession]:
    result_filter = (args.get("result") or "").strip()
    attention_filter = args.get("attention") == "1"
    events_filter = args.get("has_events") == "1"
    if not any((result_filter, attention_filter, events_filter)):
        return sessions
    filtered = []
    for session in sessions:
        summary = analysis_snapshot(session)
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


def overview_context(devices: list[Device], sessions: list[RideSession]) -> dict:
    summaries = [session_summary(session) for session in sessions]
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
        "recent_diagnoses": summaries[:8],
        "recent_events": recent_events[:8],
        "device_sync": device_sync_summary(devices),
    }


def device_sync_summary(devices: list[Device]) -> list[dict]:
    rows = []
    for device in devices:
        latest_session = (
            RideSession.query.filter_by(device_id=device.id)
            .order_by(RideSession.created_at.desc())
            .first()
        )
        pending = RideSession.query.filter_by(device_id=device.id, sync_status="syncing").count()
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


def compare_sessions(baseline: RideSession, comparison: RideSession) -> dict:
    baseline_records = session_records(baseline)
    comparison_records = session_records(comparison)
    baseline_stats = statistics_for_records(baseline_records, baseline)
    comparison_stats = statistics_for_records(comparison_records, comparison)
    baseline_analysis = analysis_snapshot(baseline, baseline_records)
    comparison_analysis = analysis_snapshot(comparison, comparison_records)

    def metric(label: str, left, right, unit: str = "", precision: int = 1):
        left_value = left() if callable(left) else left
        right_value = right() if callable(right) else right
        delta = None
        if isinstance(left_value, (int, float)) and isinstance(right_value, (int, float)):
            delta = right_value - left_value
        return {
            "label": label,
            "baseline": left_value,
            "comparison": right_value,
            "delta": delta,
            "unit": unit,
            "precision": precision,
        }

    def stat(stats, key, field):
        return stats.get(key, {}).get(field)

    metrics = [
        metric("Duration", baseline_analysis["duration_seconds"], comparison_analysis["duration_seconds"], "s", 0),
        metric("Sample count", baseline.record_count, comparison.record_count, "", 0),
        metric("Mean RPM", stat(baseline_stats, "rpm", "mean"), stat(comparison_stats, "rpm", "mean"), "rpm", 0),
        metric("RPM std deviation", stat(baseline_stats, "rpm", "stddev"), stat(comparison_stats, "rpm", "stddev"), "rpm", 0),
        metric("Mean TPS", stat(baseline_stats, "tps", "mean"), stat(comparison_stats, "tps", "mean"), "%", 1),
        metric("Mean engine temp", stat(baseline_stats, "ect", "mean"), stat(comparison_stats, "ect", "mean"), "C", 1),
        metric("Minimum battery voltage", stat(baseline_stats, "battery", "minimum"), stat(comparison_stats, "battery", "minimum"), "V", 2),
        metric("Maximum anomaly score", baseline_analysis["max_anomaly_score"], comparison_analysis["max_anomaly_score"], "", 2),
        metric("Anomalous sample ratio", baseline_analysis["anomalous_sample_percentage"], comparison_analysis["anomalous_sample_percentage"], "%", 1),
        metric("Event count", baseline_analysis["event_count"], comparison_analysis["event_count"], "", 0),
    ]
    return {
        "baseline": session_summary(baseline),
        "comparison": session_summary(comparison),
        "metrics": metrics,
        "baseline_samples": build_chart_payload(baseline, max_points=900)["samples"],
        "comparison_samples": build_chart_payload(comparison, max_points=900)["samples"],
        "parameters": available_parameters(baseline_records + comparison_records),
    }


def database_summary_counts() -> dict:
    return {
        "sessions": RideSession.query.count(),
        "devices": Device.query.count(),
        "records": TelemetryRecord.query.count(),
        "sync_batches": SyncBatch.query.count(),
        "latest_upload": format_datetime(RideSession.query.with_entities(func.max(RideSession.created_at)).scalar()),
    }
