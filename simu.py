"""Development ECU telemetry simulator.

Set DEVICE_ID and DEVICE_TOKEN in .env after registering a dashboard device.
"""
import os
import random
import time
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv


load_dotenv()
URL = os.getenv("TELEMETRY_URL", "http://127.0.0.1:5000/api/telemetry")
DEVICE_ID = os.getenv("DEVICE_ID")
DEVICE_TOKEN = os.getenv("DEVICE_TOKEN")


def simulate_record(session_id: str, seq: int) -> dict:
    return {
        "session_id": session_id,
        "seq": seq,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "rpm": random.randint(850, 6500),
        "tps": round(random.uniform(2, 95), 1),
        "ect": round(random.uniform(75, 108), 1),
        "iat": round(random.uniform(22, 55), 1),
        "battery": round(random.uniform(12.8, 14.5), 2),
        "injector_ms": round(random.uniform(1.8, 12), 2),
        "ignition_deg": round(random.uniform(-5, 35), 1),
    }


def main() -> None:
    if not DEVICE_ID or not DEVICE_TOKEN:
        raise SystemExit("Set DEVICE_ID and DEVICE_TOKEN in .env before running the simulator")
    headers = {"X-Device-ID": DEVICE_ID, "Authorization": f"Bearer {DEVICE_TOKEN}"}
    session_id = f"sim-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    seq = 0
    while True:
        seq += 1
        record = simulate_record(session_id, seq)
        try:
            response = requests.post(URL, json=record, headers=headers, timeout=10)
            print(response.status_code, response.json())
        except requests.RequestException as exc:
            print(f"Telemetry request failed: {exc}")
        time.sleep(2)


if __name__ == "__main__":
    main()
