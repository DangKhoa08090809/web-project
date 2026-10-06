from __future__ import annotations

import time
from typing import Any

import requests


class AnalyzerClientError(RuntimeError):
    pass


class AnalyzerClient:
    def __init__(self, *, base_url: str, timeout_seconds: float):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    @property
    def configured(self) -> bool:
        return bool(self.base_url)

    def health(self) -> dict[str, Any]:
        if not self.configured:
            return {"available": False, "configured": False, "message": "ML API is not configured."}

        started = time.perf_counter()
        try:
            response = requests.get(f"{self.base_url}/health", timeout=self.timeout_seconds)
            latency_ms = round((time.perf_counter() - started) * 1000)
            response.raise_for_status()
            payload = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
        except requests.RequestException as exc:
            return {
                "available": False,
                "configured": True,
                "average_latency_ms": None,
                "message": f"ML API health check failed: {exc.__class__.__name__}.",
            }
        except ValueError:
            payload = {}
            latency_ms = round((time.perf_counter() - started) * 1000)

        return {
            "available": True,
            "configured": True,
            "average_latency_ms": latency_ms,
            "message": "ML API health check succeeded.",
            **payload,
        }

    def analyze_session(self, canonical_session) -> dict[str, Any]:
        if not self.configured:
            raise AnalyzerClientError("ML API is not configured.")
        payload = canonical_session.to_dict() if hasattr(canonical_session, "to_dict") else canonical_session
        return self._post_json("/api/v1/analysis", payload)

    def decode_analyze_raw_session(self, raw_session: dict[str, Any]) -> dict[str, Any]:
        if not self.configured:
            raise AnalyzerClientError("ML API is not configured.")
        return self._post_json("/api/v1/raw/decode-analyze", raw_session)

    def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = requests.post(
                f"{self.base_url}{path}",
                json=payload,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
        except requests.HTTPError as exc:
            detail = ""
            try:
                detail_payload = response.json()
                detail = f": {detail_payload}"
            except ValueError:
                if response.text:
                    detail = f": {response.text[:500]}"
            raise AnalyzerClientError(f"analysis request failed: HTTP {response.status_code}{detail}") from exc
        except requests.RequestException as exc:
            raise AnalyzerClientError(f"analysis request failed: {exc.__class__.__name__}") from exc
        try:
            return response.json()
        except ValueError as exc:
            raise AnalyzerClientError("analysis response was not valid JSON") from exc
