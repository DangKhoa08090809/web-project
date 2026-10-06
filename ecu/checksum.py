"""LEGACY / NOT ACTIVE FOR NEW INGESTION.

Legacy 29-byte checksum helpers retained for historical fixtures and debug
tooling only. Analyzer owns validation for new RAW-backed sessions.
"""

from __future__ import annotations


EXPECTED_FRAME_LENGTH = 29
EXPECTED_SUM_MODULO = 251


def is_valid_checksum(frame: list[int]) -> bool:
    if len(frame) != EXPECTED_FRAME_LENGTH:
        return False
    if any(not isinstance(byte, int) or byte < 0 or byte > 255 for byte in frame):
        return False
    return sum(frame) % 256 == EXPECTED_SUM_MODULO


def expected_checksum_byte(frame_without_checksum: list[int]) -> int:
    if len(frame_without_checksum) != EXPECTED_FRAME_LENGTH - 1:
        raise ValueError("expected 28 bytes before checksum")
    if any(not isinstance(byte, int) or byte < 0 or byte > 255 for byte in frame_without_checksum):
        raise ValueError("frame bytes must be integers in 0..255")
    return (EXPECTED_SUM_MODULO - sum(frame_without_checksum)) % 256
