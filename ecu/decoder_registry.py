"""LEGACY / NOT ACTIVE FOR NEW INGESTION.

Profile lookup for the old local decoder. New Cloud ingestion treats uploaded
ESP profile strings as transport metadata and delegates semantic decoding to
Analyzer.
"""

from __future__ import annotations

from ecu.profile import EcuProfileDefinition
from ecu.profiles import HONDA_KEIHIN_LEGACY_29


DEFAULT_PROFILE_KEY = HONDA_KEIHIN_LEGACY_29.profile_key

PROFILES: dict[str, EcuProfileDefinition] = {
    HONDA_KEIHIN_LEGACY_29.profile_key: HONDA_KEIHIN_LEGACY_29,
}


def get_profile(profile_key: str | None = None) -> EcuProfileDefinition:
    key = profile_key or DEFAULT_PROFILE_KEY
    try:
        return PROFILES[key]
    except KeyError as exc:
        raise ValueError(f"unknown ECU profile {key!r}") from exc
