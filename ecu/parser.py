"""LEGACY / NOT ACTIVE FOR NEW INGESTION.

Frame parsing helpers are retained for historical fixtures and debug tooling.
Production Cloud ingest routes persist RAW transport bytes and do not use this
module to derive semantic telemetry.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ecu.checksum import is_valid_checksum


HEX_TOKEN_RE = re.compile(r"^[0-9a-fA-F]{1,2}$")
COMPACT_HEX_RE = re.compile(r"^[0-9a-fA-F]+$")
RAW_FRAME_HEX_KEYS = ("hex", "raw_hex", "frame_hex")


@dataclass(slots=True)
class ValidatedEcuFrame:
    sequence: int
    raw_text: str
    raw_hex: str | None
    bytes: list[int]
    parse_ok: bool
    parse_error: str | None
    checksum_valid: bool


def raw_frame_text(raw_frame: Any) -> str | None:
    if isinstance(raw_frame, str):
        return raw_frame.strip()
    if isinstance(raw_frame, dict):
        for key in RAW_FRAME_HEX_KEYS:
            value = raw_frame.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def parse_frame_text(value: str, *, expected_length: int, sequence: int = 0) -> ValidatedEcuFrame:
    raw_text = value.strip()
    parsed: list[int] = []
    parse_error: str | None = None

    if not raw_text:
        parse_error = "empty RAW frame"
    elif ";" in raw_text or "," in raw_text or any(char.isspace() for char in raw_text):
        tokens = [token for token in re.split(r"[;,\s]+", raw_text) if token]
        token_errors: list[str] = []
        for position, token in enumerate(tokens):
            if not HEX_TOKEN_RE.fullmatch(token):
                token_errors.append(f"token {position} is not 1-2 digit hex: {token!r}")
                continue
            parsed.append(int(token, 16))
        if token_errors:
            parse_error = "; ".join(token_errors)
    elif len(raw_text) % 2 == 0 and COMPACT_HEX_RE.fullmatch(raw_text):
        parsed = [int(raw_text[index : index + 2], 16) for index in range(0, len(raw_text), 2)]
    else:
        parse_error = "RAW frame must be compact hex or separated 1-2 digit hex bytes"

    if parse_error is None and len(parsed) != expected_length:
        parse_error = f"expected {expected_length} bytes, got {len(parsed)}"

    parse_ok = parse_error is None
    raw_hex = "".join(f"{byte:02X}" for byte in parsed) if parsed else None
    return ValidatedEcuFrame(
        sequence=sequence,
        raw_text=raw_text,
        raw_hex=raw_hex,
        bytes=parsed,
        parse_ok=parse_ok,
        parse_error=parse_error,
        checksum_valid=is_valid_checksum(parsed) if parse_ok else False,
    )


def parse_raw_frame(raw_frame: Any, *, expected_length: int, sequence: int = 0) -> ValidatedEcuFrame:
    value = raw_frame_text(raw_frame)
    if value is None:
        return ValidatedEcuFrame(
            sequence=sequence,
            raw_text="",
            raw_hex=None,
            bytes=[],
            parse_ok=False,
            parse_error="raw_frame metadata did not include ECU bytes",
            checksum_valid=False,
        )
    return parse_frame_text(value, expected_length=expected_length, sequence=sequence)
