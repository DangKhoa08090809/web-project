from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


SECRET_KEYS = {"authorization", "device_token", "token", "access_token", "refresh_token"}


def sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if str(key).lower() in SECRET_KEYS:
                result[key] = "<redacted>"
            else:
                result[key] = sanitize(item)
        return result
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    return value


def response_json(response) -> dict | list | str | None:
    try:
        return response.json()
    except ValueError:
        text = response.text
        return text if text else None


class HttpTrace:
    def __init__(self, *, artifact_dir: str | Path | None = None, verbose: bool = True):
        self.artifact_dir = Path(artifact_dir) if artifact_dir else None
        self.verbose = verbose
        if self.artifact_dir:
            self.artifact_dir.mkdir(parents=True, exist_ok=True)

    def write_json(self, filename: str, payload: Any) -> None:
        if not self.artifact_dir:
            return
        path = self.artifact_dir / filename
        path.write_text(json.dumps(sanitize(payload), indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def write_text(self, filename: str, content: str) -> None:
        if not self.artifact_dir:
            return
        (self.artifact_dir / filename).write_text(content, encoding="utf-8")

    def log_request(self, *, label: str, method: str, url: str, headers: dict | None, body: Any, artifact_name: str | None = None) -> None:
        if artifact_name:
            self.write_json(artifact_name, body)
        if not self.verbose:
            return
        parsed = urlparse(url)
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        self._print_block(
            f"{label}\n{method.upper()} {path}",
            [
                "Headers:",
                json.dumps(sanitize(headers or {}), indent=2, sort_keys=True),
                "",
                "Body:",
                json.dumps(sanitize(body), indent=2, sort_keys=True),
            ],
        )

    def log_response(self, *, label: str, status_code: int, body: Any, artifact_name: str | None = None) -> None:
        if artifact_name:
            self.write_json(artifact_name, body)
        if not self.verbose:
            return
        self._print_block(
            f"{label}\nHTTP {status_code}",
            [json.dumps(sanitize(body), indent=2, sort_keys=True)],
        )

    def log_exchange(
        self,
        *,
        request_label: str,
        response_label: str,
        method: str,
        url: str,
        headers: dict | None,
        body: Any,
        response,
        request_artifact: str | None = None,
        response_artifact: str | None = None,
    ) -> Any:
        self.log_request(label=request_label, method=method, url=url, headers=headers, body=body, artifact_name=request_artifact)
        payload = response_json(response)
        self.log_response(
            label=response_label,
            status_code=response.status_code,
            body=payload,
            artifact_name=response_artifact,
        )
        return payload

    @staticmethod
    def _print_block(title: str, lines: list[str]) -> None:
        separator = "-" * 44
        print(separator)
        print(title)
        print(separator)
        print("\n".join(lines))
        print()
