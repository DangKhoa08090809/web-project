"""LEGACY / NOT ACTIVE FOR NEW INGESTION.

Retained only for historical fixtures and debug helpers. Production Cloud
ingest routes do not import or call these local Honda semantic decoders.
Analyzer is the sole ECU semantic decode authority for new sessions.
"""

from ecu.decoder import decode_raw_frame_to_sample, decode_validated_frame
from ecu.decoder_registry import DEFAULT_PROFILE_KEY, get_profile
from ecu.parser import parse_raw_frame

__all__ = [
    "DEFAULT_PROFILE_KEY",
    "decode_raw_frame_to_sample",
    "decode_validated_frame",
    "get_profile",
    "parse_raw_frame",
]
