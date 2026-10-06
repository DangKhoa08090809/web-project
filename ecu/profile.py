from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EcuProfileDefinition:
    profile_key: str
    manufacturer: str
    protocol_family: str
    frame_length: int
    decoder_id: str
    decoder_version: str
    schema_version: str
    decoder_hash: str
    status: str = "development"
    notes: str | None = None
