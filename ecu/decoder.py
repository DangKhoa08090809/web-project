"""LEGACY / NOT ACTIVE FOR NEW INGESTION.

This module contains the old Cloud-side Honda semantic decoder. It is retained
only for historical fixtures and debugging; production Cloud ingest routes must
store RAW and delegate semantic ECU decoding to Analyzer over HTTP.
"""

from __future__ import annotations

from typing import Any

from ecu.parser import ValidatedEcuFrame, parse_raw_frame
from ecu.profile import EcuProfileDefinition
from telemetry.models import TelemetrySample


SIGNAL_LIMITS: dict[str, tuple[float, float]] = {
    "rpm": (0, 16000),
    "tps_voltage": (0.0, 5.0),
    "tps_raw_candidate": (0, 255),
    "battery_voltage": (6.0, 18.0),
    "iat_c": (-40, 150),
    "ect_c_candidate": (-40, 180),
    "map_raw": (0, 255),
}


def _validate_decoded_signals(decoded: dict[str, Any]) -> tuple[bool, list[str]]:
    errors: list[str] = []
    for field_name, (minimum, maximum) in SIGNAL_LIMITS.items():
        value = decoded.get(field_name)
        if value is None:
            errors.append(f"{field_name} is missing")
            continue
        if value < minimum or value > maximum:
            errors.append(f"{field_name}={value} outside configured range {minimum}..{maximum}")
    return len(errors) == 0, errors


def _decode_honda_legacy_29(frame: list[int]) -> dict[str, Any]:
    if len(frame) != 29:
        raise ValueError("Honda legacy decoder expects exactly 29 bytes")
    return {
        "rpm": (frame[9] << 8) | frame[10],
        "tps_voltage": frame[11] * 5.0 / 256.0,
        "tps_raw_candidate": frame[12],
        "battery_voltage": frame[15] / 10.0,
        "iat_c": frame[16] - 40,
        "ect_c_candidate": frame[17] - 40,
        "map_raw": frame[18],
        "signal_b19": frame[19],
        "signal_word_20_21": (frame[20] << 8) | frame[21],
        "signal_b22": frame[22],
        "signal_b23": frame[23],
        "signal_b24": frame[24],
    }


def decode_validated_frame(
    parsed: ValidatedEcuFrame,
    profile: EcuProfileDefinition,
    *,
    timestamp_ms: float | None = None,
) -> TelemetrySample:
    decoded: dict[str, Any] = {}
    validation_errors: list[str] = []
    decoded_valid = False
    decode_error: str | None = None

    if parsed.parse_ok:
        try:
            decoded = _decode_honda_legacy_29(parsed.bytes)
            decoded_valid, validation_errors = _validate_decoded_signals(decoded)
        except ValueError as exc:
            decode_error = str(exc)

    quality_flags = {
        "parse_ok": parsed.parse_ok,
        "parse_error": parsed.parse_error,
        "decoded_signals_valid": decoded_valid,
        "decode_error": decode_error,
        "validation_errors": validation_errors,
        "ecu_profile_id": profile.profile_key,
        "decoder_id": profile.decoder_id,
        "decoder_version": profile.decoder_version,
    }
    if parsed.raw_hex is not None:
        quality_flags["raw_hex"] = parsed.raw_hex

    return TelemetrySample(
        sequence=parsed.sequence,
        timestamp_ms=timestamp_ms,
        rpm=decoded.get("rpm"),
        tps_voltage=decoded.get("tps_voltage"),
        tps_raw_candidate=decoded.get("tps_raw_candidate"),
        battery_voltage=decoded.get("battery_voltage"),
        iat_c=decoded.get("iat_c"),
        ect_c_candidate=decoded.get("ect_c_candidate"),
        map_raw=decoded.get("map_raw"),
        frame_valid=bool(parsed.parse_ok and decoded_valid),
        checksum_valid=bool(parsed.checksum_valid),
        quality_flags=quality_flags,
        candidate_signals={
            "signal_b19": decoded.get("signal_b19"),
            "signal_word_20_21": decoded.get("signal_word_20_21"),
            "signal_b22": decoded.get("signal_b22"),
            "signal_b23": decoded.get("signal_b23"),
            "signal_b24": decoded.get("signal_b24"),
        },
    )


def decode_raw_frame_to_sample(
    raw_frame: Any,
    profile: EcuProfileDefinition,
    *,
    sequence: int,
    timestamp_ms: float | None = None,
) -> TelemetrySample:
    parsed = parse_raw_frame(raw_frame, expected_length=profile.frame_length, sequence=sequence)
    return decode_validated_frame(parsed, profile, timestamp_ms=timestamp_ms)
