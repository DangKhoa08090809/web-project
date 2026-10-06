from __future__ import annotations

from typing import Iterable

from telemetry.models import CanonicalTelemetrySession, SamplingMetadata, TelemetrySample


class CanonicalSessionBuilder:
    def __init__(
        self,
        *,
        session_id: str | None,
        vehicle_id: str | None,
        device_id: str | None,
        firmware_version: str | None,
        session_note: str | None,
        ecu_profile_id: str | None,
        decoder_id: str,
        decoder_version: str,
        sample_interval_ms: float | None = None,
        sampling_rate_hz: float | None = None,
        source_type: str = "canonical",
    ):
        self.session_id = session_id
        self.vehicle_id = vehicle_id
        self.device_id = device_id
        self.firmware_version = firmware_version
        self.session_note = session_note
        self.ecu_profile_id = ecu_profile_id
        self.decoder_id = decoder_id
        self.decoder_version = decoder_version
        self.sampling = SamplingMetadata(sample_interval_ms=sample_interval_ms, sampling_rate_hz=sampling_rate_hz)
        self.source_type = source_type
        self._samples: list[TelemetrySample] = []

    def append(self, sample: TelemetrySample) -> None:
        self._samples.append(sample)

    def extend(self, samples: Iterable[TelemetrySample]) -> None:
        for sample in samples:
            self.append(sample)

    def build(self) -> CanonicalTelemetrySession:
        samples = sorted(self._samples, key=lambda sample: sample.sequence)
        return CanonicalTelemetrySession(
            session_id=self.session_id,
            vehicle_id=self.vehicle_id,
            device_id=self.device_id,
            firmware_version=self.firmware_version,
            session_note=self.session_note,
            ecu_profile_id=self.ecu_profile_id,
            decoder_id=self.decoder_id,
            decoder_version=self.decoder_version,
            sampling=self.sampling,
            samples=samples,
            source_type=self.source_type,
        )


def sample_from_record(record) -> TelemetrySample:
    timestamp_ms = record.timestamp_ms if getattr(record, "timestamp_ms", None) is not None else record.device_time_ms
    return TelemetrySample(
        sequence=int(record.seq),
        timestamp_ms=float(timestamp_ms) if timestamp_ms is not None else None,
        rpm=record.rpm,
        tps_voltage=getattr(record, "tps_voltage", None),
        tps_raw_candidate=getattr(record, "tps_raw_candidate", None),
        battery_voltage=getattr(record, "battery_voltage", None),
        iat_c=getattr(record, "iat_c", None),
        ect_c_candidate=getattr(record, "ect_c_candidate", None),
        map_raw=getattr(record, "map_raw", None),
        frame_valid=bool(getattr(record, "frame_valid", True)),
        checksum_valid=bool(getattr(record, "checksum_valid", True)),
        quality_flags=dict(getattr(record, "quality_flags", None) or {}),
        candidate_signals=dict(getattr(record, "candidate_signals", None) or {}),
    )


def build_canonical_session_from_records(ride_session, records) -> CanonicalTelemetrySession:
    decoder_version = getattr(ride_session, "decoder_version_ref", None)
    ecu_profile = getattr(decoder_version, "ecu_profile", None)
    device = ride_session.device
    builder = CanonicalSessionBuilder(
        session_id=ride_session.session_id,
        vehicle_id=device.vehicle_name,
        device_id=device.device_id,
        firmware_version=device.firmware_version,
        session_note=ride_session.notes,
        ecu_profile_id=getattr(ecu_profile, "profile_key", None),
        decoder_id=getattr(decoder_version, "decoder_id", None) or "unknown",
        decoder_version=getattr(decoder_version, "version", None) or "unknown",
        sample_interval_ms=getattr(ride_session, "sample_interval_ms", None),
        sampling_rate_hz=getattr(ride_session, "sampling_rate_hz", None),
        source_type="canonical",
    )
    builder.extend(sample_from_record(record) for record in records)
    return builder.build()
