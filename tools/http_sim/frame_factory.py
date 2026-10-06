from __future__ import annotations

from dataclasses import dataclass


NATIVE_FIXTURE_HEX = "0218711705DC1A02FFFF904D506A7F0064000000000000E9"
NATIVE_HEADER = bytes.fromhex("02187117")
LEGACY_PREFIX = bytes([0xFF] * 5)
SAMPLE_PERIOD_MS = 250


@dataclass(frozen=True, slots=True)
class NativeFrameSample:
    seq: int
    timestamp_ms: int
    frame: bytes

    @property
    def hex(self) -> str:
        return self.frame.hex().upper()

    @property
    def decoded_v2(self) -> dict:
        return decode_native_v2(self.frame)


def assert_native_frame(frame: bytes) -> None:
    assert isinstance(frame, (bytes, bytearray)), "frame must be bytes"
    assert len(frame) == 24, f"native frame must be 24 bytes, got {len(frame)}"
    assert bytes(frame[:4]) == NATIVE_HEADER, "native frame must start with 02 18 71 17"
    assert sum(frame) % 256 == 0, "native frame checksum must make sum(frame) % 256 == 0"


def checksum_byte(prefix: bytes) -> int:
    if len(prefix) != 23:
        raise ValueError("native checksum prefix must contain the first 23 bytes")
    return (-sum(prefix)) & 0xFF


def generate_native_frame(seq: int) -> bytes:
    if seq < 0:
        raise ValueError("seq must be non-negative")

    rpm = 1500 + ((seq * 37) % 701)
    tps_voltage_raw = 0x1A + ((seq * 3) % 12)
    tps_raw = 2 + (seq % 8)
    battery_raw = 127 + [0, 1, 2, 3, -2, -1][seq % 6]
    iat_raw = 40 + 37 + [0, 1, -1, 0, 1, -1][seq % 6]
    ect_raw = 40 + 66 + [0, 1, 2, 3, 4, -2, -1][seq % 7]
    nonsemantic_b10 = 0x90 + (seq % 5)
    nonsemantic_b12 = 0x50 + (seq % 3)

    prefix = bytes(
        [
            0x02,
            0x18,
            0x71,
            0x17,
            (rpm >> 8) & 0xFF,
            rpm & 0xFF,
            tps_voltage_raw,
            tps_raw,
            0xFF,
            0xFF,
            nonsemantic_b10,
            iat_raw,
            nonsemantic_b12,
            ect_raw,
            battery_raw,
            0x00,
            0x64,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
        ]
    )
    frame = prefix + bytes([checksum_byte(prefix)])
    assert_native_frame(frame)
    return frame


def generate_native_session(count: int = 60, *, sample_period_ms: int = SAMPLE_PERIOD_MS) -> list[NativeFrameSample]:
    if count <= 0:
        raise ValueError("count must be positive")
    return [
        NativeFrameSample(seq=seq, timestamp_ms=seq * sample_period_ms, frame=generate_native_frame(seq))
        for seq in range(count)
    ]


def decode_native_v2(frame: bytes) -> dict:
    assert_native_frame(frame)
    return {
        "rpm": (frame[4] << 8) | frame[5],
        "tps_voltage": frame[6] * 5.0 / 256.0,
        "tps_raw": frame[7],
        "battery_voltage": frame[14] / 10.0,
        "iat_c": frame[11] - 40,
        "ect_c": frame[13] - 40,
        "frame_valid": True,
        "checksum_valid": True,
        "candidate_signals": {},
    }


def native24_to_legacy29(frame: bytes) -> bytes:
    assert_native_frame(frame)
    legacy = LEGACY_PREFIX + bytes(frame)
    assert len(legacy) == 29, f"legacy frame must be 29 bytes, got {len(legacy)}"
    assert sum(legacy) % 256 == 251, "legacy frame checksum must make sum(frame) % 256 == 251"
    return legacy


def native24_to_legacy29_hex(frame: bytes) -> str:
    return native24_to_legacy29(frame).hex().upper()


def pi_record_from_sample(sample: NativeFrameSample) -> dict:
    return {
        "seq": sample.seq,
        "timestamp_ms": sample.timestamp_ms,
        "raw_hex": sample.hex,
        "raw_length": len(sample.frame),
        **sample.decoded_v2,
    }
