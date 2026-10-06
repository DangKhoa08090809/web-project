from __future__ import annotations

import copy

import requests

from tools.http_sim.esp_client import DeviceCredentials
from tools.http_sim.frame_factory import NativeFrameSample, pi_record_from_sample
from tools.http_sim.trace import HttpTrace


PI_SIGNAL_COLUMNS = ["rpm", "tps_voltage", "tps_raw", "battery_voltage", "iat_c", "ect_c"]


def pi_analysis_fixture(
    *,
    analysis_run_id: str = "analysis-http-sim-pi-001",
    window_count: int = 2,
    health_score: float = 91.0,
) -> dict:
    return {
        "analysis_run_id": analysis_run_id,
        "model_version": "iforest-baseline-20260920T091709Z",
        "telemetry_schema_version": "canonical-telemetry-v2",
        "feature_schema_version": "ecu-window-features-v1",
        "signal_columns": PI_SIGNAL_COLUMNS,
        "window_count": window_count,
        "anomaly_window_count": 0,
        "anomaly_ratio": 0.0,
        "health_score": health_score,
        "overall_status": "ok",
        "most_unusual_features": [],
        "model_loaded": True,
        "warnings": [],
        "note": "HTTP simulator fixture",
    }


class PiHttpSimulator:
    def __init__(self, base_url: str, *, trace: HttpTrace | None = None, timeout_seconds: float = 5.0):
        self.base_url = base_url.rstrip("/")
        self.trace = trace or HttpTrace(verbose=False)
        self.timeout_seconds = timeout_seconds

    def pair(
        self,
        pairing_code: str,
        *,
        device_id: str = "http-sim-pi-001",
        request_artifact: str = "pi_pair_request.json",
        response_artifact: str = "pi_pair_response.json",
    ) -> DeviceCredentials:
        payload = {
            "pairing_code": pairing_code,
            "device_id": device_id,
            "device_name": "HTTP Sim Pi5",
            "vehicle_name": "HTTP Sim Honda",
            "ecu_type": "Honda Keihin native 0x71 0x17",
            "firmware_version": "ecuread-pi5-http-sim-0.1.0",
            "hardware_version": "raspberry-pi-5",
        }
        response = requests.post(f"{self.base_url}/api/device/pair", json=payload, timeout=self.timeout_seconds)
        body = self.trace.log_exchange(
            request_label="PI -> CLOUD",
            response_label="CLOUD -> PI",
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

    def build_sync_payload(
        self,
        samples: list[NativeFrameSample],
        *,
        session_id: str = "http-sim-pi-v2-000001",
        batch_id: str | None = None,
        session_ended: bool = False,
        analysis: dict | None = None,
    ) -> dict:
        payload = {
            "session_id": session_id,
            "batch_id": batch_id or f"{session_id}-0000",
            "session_ended": session_ended,
            "telemetry_schema_version": "canonical-telemetry-v2",
            "ecu_profile_id": "honda_keihin_71_17",
            "decoder_id": "honda_keihin_71_17",
            "decoder_version": "1.0.0",
            "sampling": {
                "sample_interval_ms": 250,
                "sampling_rate_hz": 4,
            },
            "records": [pi_record_from_sample(sample) for sample in samples],
        }
        if analysis is not None:
            payload["analysis"] = copy.deepcopy(analysis)
        return payload

    def sync(
        self,
        credentials: DeviceCredentials,
        payload: dict,
        *,
        request_artifact: str | None = None,
        response_artifact: str | None = None,
    ) -> tuple[requests.Response, dict | list | str | None]:
        url = f"{self.base_url}/api/device/sync/session"
        headers = credentials.headers()
        response = requests.post(url, json=payload, headers=headers, timeout=self.timeout_seconds)
        body = self.trace.log_exchange(
            request_label="PI -> CLOUD",
            response_label="CLOUD -> PI",
            method="POST",
            url=url,
            headers=headers,
            body=payload,
            response=response,
            request_artifact=request_artifact,
            response_artifact=response_artifact,
        )
        return response, body


def chunk_samples(samples: list[NativeFrameSample], chunk_size: int) -> list[list[NativeFrameSample]]:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    return [samples[index : index + chunk_size] for index in range(0, len(samples), chunk_size)]
