"""LEGACY / NOT ACTIVE FOR NEW INGESTION.

Old Cloud-side Honda/Keihin semantic profile retained for historical fixtures
and debug tooling only. Analyzer owns Honda semantic decoding for new sessions.
"""

from __future__ import annotations

import hashlib

from ecu.profile import EcuProfileDefinition


DECODER_ID = "honda_keihin_legacy_29"
DECODER_VERSION = "0.1.0"
SCHEMA_VERSION = "canonical-telemetry-v1"

DECODER_FORMULAS = "\n".join(
    [
        "rpm=(b9<<8)|b10",
        "tps_voltage=b11*5/256",
        "tps_raw_candidate=b12",
        "battery_voltage=b15/10",
        "iat_c=b16-40",
        "ect_c_candidate=b17-40",
        "map_raw=b18",
        "signal_b19=b19",
        "signal_word_20_21=(b20<<8)|b21",
        "signal_b22=b22",
        "signal_b23=b23",
        "signal_b24=b24",
        "checksum=sum(frame)%256==251",
    ]
)

ECU_PROFILE = EcuProfileDefinition(
    profile_key=DECODER_ID,
    manufacturer="Honda",
    protocol_family="honda_kline",
    frame_length=29,
    decoder_id=DECODER_ID,
    decoder_version=DECODER_VERSION,
    schema_version=SCHEMA_VERSION,
    decoder_hash=hashlib.sha256(DECODER_FORMULAS.encode("utf-8")).hexdigest(),
    status="development",
    notes="Legacy 29-byte Honda/Keihin K-Line decoder. Some byte interpretations remain candidates.",
)
