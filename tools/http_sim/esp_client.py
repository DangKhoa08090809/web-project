from __future__ import annotations

from dataclasses import dataclass

import requests

from tools.http_sim.frame_factory import NativeFrameSample, native24_to_legacy29_hex
from tools.http_sim.trace import HttpTrace


@dataclass(frozen=True, slots=True)
class DeviceCredentials:
    device_id: str
    token: str

    def headers(self) -> dict[str, str]:
        return {
            "X-Device-ID": self.device_id,
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }


class EspHttpSimulator:
    def __init__(self, base_url: str, *, trace: HttpTrace | None = None, timeout_seconds: float = 5.0):
        self.base_url = base_url.rstrip("/")
        self.trace = trace or HttpTrace(verbose=False)
        self.timeout_seconds = timeout_seconds

    def pair(
        self,
        pairing_code: str,
        *,
        device_id: str = "http-sim-esp-001",
        request_artifact: str = "esp_pair_request.json",
        response_artifact: str = "esp_pair_response.json",
    ) -> DeviceCredentials:
        payload = {
            "pairing_code": pairing_code,
            "device_id": device_id,
            "device_name": "HTTP Sim ESP",
            "vehicle_name": "HTTP Sim Honda",
            "ecu_type": "Honda Keihin legacy 29-byte compatibility",
            "firmware_version": "http-sim-esp-0.1.0",
            "hardware_version": "esp32-http-sim",
        }
        response = requests.post(f"{self.base_url}/api/device/pair", json=payload, timeout=self.timeout_seconds)
        body = self.trace.log_exchange(
            request_label="ESP -> CLOUD",
            response_label="CLOUD -> ESP",
            method="POST",
            url=f"{self.base_url}/api/device/pair",
            headers={"Content-Type": "application/json"},
            body=payload,
            response=response,
            request_artifact=request_artifact,
            response_artifact=response_artifact,
        )
        response.raise_for_status()
        return DeviceCredentials(device_id=device_id, token=body["device_token"])

    def build_upload_payload(
        self,
        samples: list[NativeFrameSample],
        *,
        session_id: str = "http-sim-esp-raw-000001",
        batch_id: str | None = None,
        session_ended: bool = True,
    ) -> dict:
        return {
            "session_id": session_id,
            "session_ended": session_ended,
            "batch_id": batch_id or f"{session_id}-0000",
            "ecu_profile_id": "honda_keihin_legacy_29",
            "sample_interval_ms": 250,
            "records": [
                {
                    "seq": sample.seq,
                    "device_time_ms": sample.timestamp_ms,
                    "raw_frame": native24_to_legacy29_hex(sample.frame),
                }
                for sample in samples
            ],
        }

    def upload(
        self,
        credentials: DeviceCredentials,
        payload: dict,
        *,
        request_artifact: str = "esp_upload_request.json",
        response_artifact: str = "esp_upload_response.json",
    ) -> tuple[requests.Response, dict | list | str | None]:
        url = f"{self.base_url}/api/logs/upload"
        headers = credentials.headers()
        response = requests.post(url, json=payload, headers=headers, timeout=self.timeout_seconds)
        body = self.trace.log_exchange(
            request_label="ESP -> CLOUD",
            response_label="CLOUD -> ESP",
            method="POST",
            url=url,
            headers=headers,
            body=payload,
            response=response,
            request_artifact=request_artifact,
            response_artifact=response_artifact,
        )
        return response, body
