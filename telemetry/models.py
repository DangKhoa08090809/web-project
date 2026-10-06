from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


CANONICAL_TELEMETRY_SCHEMA_VERSION = "canonical-telemetry-v1"

CORE_SIGNAL_COLUMNS = [
    "rpm",
    "tps_voltage",
    "tps_raw_candidate",
    "battery_voltage",
    "iat_c",
    "ect_c_candidate",
    "map_raw",
]

CANDIDATE_SIGNAL_COLUMNS = [
    "tps_raw_candidate",
    "ect_c_candidate",
    "map_raw",
]

SIGNAL_DEFINITIONS: dict[str, dict[str, str]] = {
    "rpm": {"status": "high_confidence_candidate", "unit": "rpm"},
    "tps_voltage": {"status": "high_confidence_candidate", "unit": "V"},
    "tps_raw_candidate": {"status": "candidate", "unit": "raw"},
    "battery_voltage": {"status": "high_confidence_candidate", "unit": "V"},
    "iat_c": {"status": "high_confidence_candidate", "unit": "degC"},
    "ect_c_candidate": {"status": "candidate", "unit": "degC"},
    "map_raw": {"status": "candidate", "unit": "raw"},
}


@dataclass(slots=True)
class SamplingMetadata:
    sample_interval_ms: float | None = None
    sampling_rate_hz: float | None = None

    def to_dict(self) -> dict[str, float]:
        payload: dict[str, float] = {}
        if self.sample_interval_ms is not None:
            payload["sample_interval_ms"] = float(self.sample_interval_ms)
        if self.sampling_rate_hz is not None:
            payload["sampling_rate_hz"] = float(self.sampling_rate_hz)
        return payload


@dataclass(slots=True)
class TelemetrySample:
    sequence: int
    timestamp_ms: float | None = None
    rpm: float | None = None
    tps_voltage: float | None = None
    tps_raw_candidate: float | None = None
    battery_voltage: float | None = None
    iat_c: float | None = None
    ect_c_candidate: float | None = None
    map_raw: float | None = None
    frame_valid: bool = True
    checksum_valid: bool = True
    quality_flags: dict[str, Any] = field(default_factory=dict)
    candidate_signals: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": int(self.sequence),
            "timestamp_ms": self.timestamp_ms,
            "rpm": self.rpm,
            "tps_voltage": self.tps_voltage,
            "tps_raw_candidate": self.tps_raw_candidate,
            "battery_voltage": self.battery_voltage,
            "iat_c": self.iat_c,
            "ect_c_candidate": self.ect_c_candidate,
            "map_raw": self.map_raw,
            "frame_valid": bool(self.frame_valid),
            "checksum_valid": bool(self.checksum_valid),
            "quality_flags": dict(self.quality_flags),
            "candidate_signals": dict(self.candidate_signals),
        }


@dataclass(slots=True)
class CanonicalTelemetrySession:
    session_id: str | None
    vehicle_id: str | None
    device_id: str | None
    ecu_profile_id: str | None
    decoder_id: str
    decoder_version: str
    samples: list[TelemetrySample]
    firmware_version: str | None = None
    session_note: str | None = None
    telemetry_schema_version: str = CANONICAL_TELEMETRY_SCHEMA_VERSION
    sampling: SamplingMetadata = field(default_factory=SamplingMetadata)
    signal_definitions: dict[str, dict[str, str]] = field(default_factory=lambda: SIGNAL_DEFINITIONS.copy())
    source_type: str = "canonical"

    @property
    def decoder_version_key(self) -> str:
        return f"{self.decoder_id}:{self.decoder_version}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "vehicle_id": self.vehicle_id,
            "device_id": self.device_id,
            "firmware_version": self.firmware_version,
            "session_note": self.session_note,
            "ecu_profile_id": self.ecu_profile_id,
            "decoder_id": self.decoder_id,
            "decoder_version": self.decoder_version,
            "telemetry_schema_version": self.telemetry_schema_version,
            "sampling": self.sampling.to_dict(),
            "samples": [sample.to_dict() for sample in self.samples],
            "signal_definitions": self.signal_definitions,
            "source_type": self.source_type,
        }
