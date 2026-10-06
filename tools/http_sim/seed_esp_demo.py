from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import requests

from tests.http_flow.mock_analyzer import MockAnalyzerState, create_mock_analyzer_app
from tools.http_sim.esp_client import EspHttpSimulator
from tools.http_sim.frame_factory import native24_to_legacy29_hex
from tools.http_sim.local_server import create_http_flow_cloud_app, ensure_demo_user_and_pairing_codes, start_werkzeug_server


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Seed a persistent customer demo user through the ESP HTTP flow.")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--email", default="demo@drisafe.local")
    parser.add_argument("--password", default="Demo123456!")
    parser.add_argument("--name", default="Customer Demo")
    parser.add_argument("--run-id", default=datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S"))
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--anomaly-count", type=int, default=1200)
    parser.add_argument("--base-url", help="Existing Cloud URL. If omitted, starts a local Cloud server.")
    parser.add_argument("--pairing-code", help="Pairing code to use with --base-url.")
    return parser


def native_frame_from_values(
    *,
    rpm: int,
    tps_voltage_raw: int,
    tps_raw: int,
    battery_raw: int,
    iat_c: int,
    ect_c: int,
    map_raw: int,
) -> bytes:
    prefix = bytes(
        [
            0x02,
            0x18,
            0x71,
            0x17,
            (rpm >> 8) & 0xFF,
            rpm & 0xFF,
            tps_voltage_raw & 0xFF,
            tps_raw & 0xFF,
            0xFF,
            0xFF,
            0x90,
            (iat_c + 40) & 0xFF,
            map_raw & 0xFF,
            (ect_c + 40) & 0xFF,
            battery_raw & 0xFF,
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
    frame = prefix + bytes([(-sum(prefix)) & 0xFF])
    if len(frame) != 24 or frame[:4] != bytes.fromhex("02187117") or sum(frame) % 256 != 0:
        raise ValueError("generated invalid native frame")
    return frame


def anomaly_records(kind: str, count: int) -> list[dict]:
    records = []
    for seq in range(count):
        rpm = 1650 + (seq % 80) * 3
        tps_voltage_raw = 28 + (seq % 5)
        tps_raw = 4 + (seq % 6)
        battery_raw = 138
        iat_c = 37 + (seq % 3)
        ect_c = 72 + (seq % 4)
        map_raw = 105 + (seq % 5)

        if kind == "low-battery" and 350 <= seq < 620:
            battery_raw = 106 + (seq % 3)
        elif kind == "overheat" and 420 <= seq < 760:
            ect_c = 116 + (seq % 7)
        elif kind == "rpm-spike" and 500 <= seq < 720:
            rpm = 5400 + (seq % 40) * 20

        frame = native_frame_from_values(
            rpm=rpm,
            tps_voltage_raw=tps_voltage_raw,
            tps_raw=tps_raw,
            battery_raw=battery_raw,
            iat_c=iat_c,
            ect_c=ect_c,
            map_raw=map_raw,
        )
        records.append({
            "seq": seq,
            "device_time_ms": seq * 250,
            "raw_frame": native24_to_legacy29_hex(frame),
        })
    return records


def records_from_jsonl(path: Path) -> list[dict]:
    records = []
    with path.open(encoding="utf-8") as handle:
        for seq, line in enumerate(handle):
            if not line.strip():
                continue
            payload = json.loads(line)
            frame = bytes.fromhex(payload["raw_hex"])
            records.append({
                "seq": seq,
                "device_time_ms": int(payload.get("elapsed_ms", seq * 250)),
                "raw_frame": native24_to_legacy29_hex(frame),
            })
    return records


def post_session(base_url: str, headers: dict, *, session_id: str, records: list[dict], batch_size: int) -> tuple[int, dict]:
    inserted = 0
    final_body = {}
    for batch_index, start in enumerate(range(0, len(records), batch_size), start=1):
        chunk = records[start : start + batch_size]
        final = start + batch_size >= len(records)
        payload = {
            "session_id": session_id,
            "session_ended": final,
            "batch_id": f"{session_id}-{batch_index:04d}",
            "ecu_profile_id": "honda_keihin_legacy_29",
            "sample_interval_ms": 250,
            "records": chunk,
        }
        response = requests.post(f"{base_url}/api/logs/upload", json=payload, headers=headers, timeout=30)
        try:
            body = response.json()
        except ValueError:
            body = {"text": response.text}
        if response.status_code >= 400:
            raise RuntimeError(f"{session_id} batch {batch_index} failed: HTTP {response.status_code} {body}")
        inserted += int(body.get("inserted_count", 0))
        final_body = body
    return inserted, final_body


def main() -> None:
    args = build_parser().parse_args()
    data_dir = Path(args.data_dir)
    if not data_dir.is_dir():
        raise SystemExit(f"data dir not found: {data_dir}")

    analyzer_state = MockAnalyzerState()
    analyzer_server = None
    cloud_server = None
    try:
        if args.base_url:
            if not args.pairing_code:
                raise SystemExit("--pairing-code is required with --base-url")
            base_url = args.base_url.rstrip("/")
            pairing_code = args.pairing_code
        else:
            analyzer_app = create_mock_analyzer_app(analyzer_state, capture_requests=False, score_payload=True)
            analyzer_server = start_werkzeug_server(analyzer_app)
            cloud_app = create_http_flow_cloud_app(analyzer_url=analyzer_server.url, max_log_records=args.batch_size)
            pairing_code = ensure_demo_user_and_pairing_codes(
                cloud_app,
                email=args.email,
                password=args.password,
                full_name=args.name,
                pairing_code_count=1,
            )[0]
            cloud_server = start_werkzeug_server(cloud_app)
            base_url = cloud_server.url

        esp = EspHttpSimulator(base_url)
        credentials = esp.pair(pairing_code, device_id=f"esp-demo-{args.run_id}")
        headers = credentials.headers()

        total_sessions = 0
        total_records = 0
        for path in sorted(data_dir.glob("*.jsonl")):
            session_id = f"esp-real-{path.stem}-{args.run_id}"
            records = records_from_jsonl(path)
            inserted, ack = post_session(
                base_url,
                headers,
                session_id=session_id,
                records=records,
                batch_size=args.batch_size,
            )
            total_sessions += 1
            total_records += inserted
            print(f"imported {session_id}: {inserted}/{len(records)} records, ack={ack.get('accepted_sequences')}")

        for kind in ("low-battery", "overheat", "rpm-spike"):
            session_id = f"esp-fake-{kind}-{args.run_id}"
            records = anomaly_records(kind, args.anomaly_count)
            inserted, ack = post_session(
                base_url,
                headers,
                session_id=session_id,
                records=records,
                batch_size=args.batch_size,
            )
            total_sessions += 1
            total_records += inserted
            print(f"seeded {session_id}: {inserted}/{len(records)} records, ack={ack.get('accepted_sequences')}")

        print("DONE")
        print(f"login_email={args.email}")
        print(f"login_password={args.password}")
        print(f"device_id={credentials.device_id}")
        print(f"run_id={args.run_id}")
        print(f"sessions={total_sessions}")
        print(f"records_inserted={total_records}")
        print(f"mock_analyzer_calls={analyzer_state.call_count}")
    finally:
        if cloud_server is not None:
            cloud_server.shutdown()
        if analyzer_server is not None:
            analyzer_server.shutdown()


if __name__ == "__main__":
    main()
