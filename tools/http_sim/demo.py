from __future__ import annotations

import argparse
import copy
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from tests.http_flow.mock_analyzer import MockAnalyzerState, create_mock_analyzer_app
from tools.http_sim.esp_client import EspHttpSimulator
from tools.http_sim.frame_factory import (
    NATIVE_FIXTURE_HEX,
    generate_native_frame,
    generate_native_session,
    native24_to_legacy29_hex,
    pi_record_from_sample,
)
from tools.http_sim.local_server import (
    create_http_flow_cloud_app,
    ensure_demo_user_and_pairing_codes,
    initialize_cloud_database,
    start_werkzeug_server,
)
from tools.http_sim.pi_client import PiHttpSimulator, chunk_samples, pi_analysis_fixture
from tools.http_sim.trace import HttpTrace


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a real HTTP ESP/Pi Cloud flow simulator.")
    parser.add_argument("--base-url", help="Existing Cloud base URL. If omitted, a local Cloud server is started.")
    parser.add_argument("--esp-pairing-code", help="Pairing code for --base-url mode.")
    parser.add_argument("--pi-pairing-code", help="Pairing code for --base-url mode.")
    parser.add_argument("--artifact-dir", default="artifacts/http-flow-demo", help="Directory for sanitized JSON/Markdown artifacts.")
    parser.add_argument("--sample-count", type=int, default=60, help="Number of deterministic native samples to generate.")
    parser.add_argument("--quiet", action="store_true", help="Write artifacts without printing full HTTP bodies.")
    parser.add_argument(
        "--persist-demo-user",
        action="store_true",
        help="Use the configured app database, create/update a demo user, and keep uploaded history.",
    )
    parser.add_argument("--demo-email", default="http-flow@example.com", help="Persistent demo login email.")
    parser.add_argument("--demo-password", default="http-flow-password", help="Persistent demo login password.")
    parser.add_argument("--demo-name", default="HTTP Flow Demo", help="Persistent demo user's display name.")
    parser.add_argument("--run-id", help="Stable suffix for generated device/session ids. Defaults to UTC timestamp.")
    return parser


def run_demo(args: argparse.Namespace) -> dict:
    artifact_dir = Path(args.artifact_dir)
    trace = HttpTrace(artifact_dir=artifact_dir, verbose=not args.quiet)
    analyzer_state = MockAnalyzerState()
    servers = []
    temp_dir = None
    base_url = args.base_url.rstrip("/") if args.base_url else None
    esp_pairing_code = args.esp_pairing_code
    pi_pairing_code = args.pi_pairing_code
    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    persistent_suffix = run_id if args.persist_demo_user else "001"

    try:
        if base_url is None and args.persist_demo_user:
            analyzer_app = create_mock_analyzer_app(analyzer_state, trace=trace)
            analyzer_server = start_werkzeug_server(analyzer_app)
            servers.append(analyzer_server)
            cloud_app = create_http_flow_cloud_app(analyzer_url=analyzer_server.url)
            esp_pairing_code, pi_pairing_code = ensure_demo_user_and_pairing_codes(
                cloud_app,
                email=args.demo_email,
                password=args.demo_password,
                full_name=args.demo_name,
                pairing_code_count=2,
            )
            cloud_server = start_werkzeug_server(cloud_app)
            servers.append(cloud_server)
            base_url = cloud_server.url
        elif base_url is None:
            temp_dir = tempfile.TemporaryDirectory(prefix="drisafe-http-flow-")
            analyzer_app = create_mock_analyzer_app(analyzer_state, trace=trace)
            analyzer_server = start_werkzeug_server(analyzer_app)
            servers.append(analyzer_server)
            cloud_app = create_http_flow_cloud_app(
                database_path=Path(temp_dir.name) / "cloud.sqlite",
                analyzer_url=analyzer_server.url,
            )
            esp_pairing_code, pi_pairing_code = initialize_cloud_database(cloud_app, pairing_code_count=2)
            cloud_server = start_werkzeug_server(cloud_app)
            servers.append(cloud_server)
            base_url = cloud_server.url
        else:
            if args.persist_demo_user and (not esp_pairing_code or not pi_pairing_code):
                cloud_app = create_http_flow_cloud_app()
                esp_pairing_code, pi_pairing_code = ensure_demo_user_and_pairing_codes(
                    cloud_app,
                    email=args.demo_email,
                    password=args.demo_password,
                    full_name=args.demo_name,
                    pairing_code_count=2,
                )
            if not esp_pairing_code or not pi_pairing_code:
                raise SystemExit("--esp-pairing-code and --pi-pairing-code are required with --base-url")

        first_frame = generate_native_frame(0)
        assert first_frame.hex().upper() == NATIVE_FIXTURE_HEX
        samples = generate_native_session(args.sample_count)

        esp = EspHttpSimulator(base_url, trace=trace)
        pi = PiHttpSimulator(base_url, trace=trace)

        esp_device_id = f"http-sim-esp-{persistent_suffix}"
        pi_device_id = f"http-sim-pi-{persistent_suffix}"
        esp_session_id = f"http-sim-esp-raw-{persistent_suffix}"
        pi_session_id = f"http-sim-pi-v2-{persistent_suffix}"

        esp_credentials = esp.pair(esp_pairing_code, device_id=esp_device_id)
        esp_payload = esp.build_upload_payload(samples, session_id=esp_session_id)
        esp_response, esp_body = esp.upload(esp_credentials, esp_payload)
        esp_response.raise_for_status()
        analyzer_calls_after_esp = analyzer_state.call_count

        pi_credentials = pi.pair(pi_pairing_code, device_id=pi_device_id)
        pi_chunks = chunk_samples(samples, 20)
        pi_bodies = []
        analysis = pi_analysis_fixture(
            analysis_run_id=f"analysis-http-sim-pi-{persistent_suffix}",
            window_count=max(1, args.sample_count // 30),
        )
        for index, chunk in enumerate(pi_chunks, start=1):
            is_final = index == len(pi_chunks)
            payload = pi.build_sync_payload(
                chunk,
                session_id=pi_session_id,
                batch_id=f"{pi_session_id}-{index:04d}",
                session_ended=is_final,
                analysis=analysis if is_final else None,
            )
            request_name = "pi_sync_final_request.json" if is_final else f"pi_sync_batch_{index:04d}_request.json"
            response_name = "pi_sync_final_response.json" if is_final else f"pi_sync_batch_{index:04d}_response.json"
            response, body = pi.sync(
                pi_credentials,
                payload,
                request_artifact=request_name,
                response_artifact=response_name,
            )
            response.raise_for_status()
            pi_bodies.append(body)
            if is_final:
                final_pi_payload = payload
        analyzer_calls_after_pi = analyzer_state.call_count

        duplicate_response, duplicate_body = pi.sync(
            pi_credentials,
            final_pi_payload,
            request_artifact="pi_duplicate_request.json",
            response_artifact="pi_duplicate_response.json",
        )
        duplicate_response.raise_for_status()

        conflict_sample = copy.deepcopy(samples[10])
        conflict_record = pi_record_from_sample(conflict_sample)
        conflict_record["rpm"] = conflict_record["rpm"] + 123
        record_conflict_payload = pi.build_sync_payload(
            [conflict_sample],
            session_id=pi_session_id,
            batch_id=f"{pi_session_id}-record-conflict",
            session_ended=False,
        )
        record_conflict_payload["records"] = [conflict_record]
        record_conflict_response, record_conflict_body = pi.sync(
            pi_credentials,
            record_conflict_payload,
            request_artifact="pi_record_conflict_request.json",
            response_artifact="pi_record_conflict_response.json",
        )

        analysis_conflict_payload = copy.deepcopy(final_pi_payload)
        analysis_conflict_payload["batch_id"] = f"{pi_session_id}-analysis-conflict"
        analysis_conflict_payload["analysis"]["health_score"] = 72.5
        analysis_conflict_response, analysis_conflict_body = pi.sync(
            pi_credentials,
            analysis_conflict_payload,
            request_artifact="pi_analysis_conflict_request.json",
            response_artifact="pi_analysis_conflict_response.json",
        )

        summary = {
            "base_url": base_url,
            "artifact_dir": str(artifact_dir),
            "persistent_demo_user": args.persist_demo_user,
            "demo_email": args.demo_email if args.persist_demo_user else None,
            "run_id": run_id,
            "esp_upload_response": esp_body,
            "pi_batch_responses": pi_bodies,
            "pi_duplicate_response": duplicate_body,
            "pi_record_conflict_status": record_conflict_response.status_code,
            "pi_record_conflict_response": record_conflict_body,
            "pi_analysis_conflict_status": analysis_conflict_response.status_code,
            "pi_analysis_conflict_response": analysis_conflict_body,
            "analyzer_calls_after_esp": analyzer_calls_after_esp,
            "analyzer_calls_after_pi": analyzer_calls_after_pi,
            "mock_analyzer_requests": analyzer_state.requests,
            "mock_analyzer_responses": analyzer_state.responses,
        }
        trace.write_text(
            "FLOW_SUMMARY.md",
            flow_summary(
                samples=samples,
                esp_body=esp_body,
                pi_bodies=pi_bodies,
                duplicate_body=duplicate_body,
                record_conflict_status=record_conflict_response.status_code,
                record_conflict_body=record_conflict_body,
                analysis_conflict_status=analysis_conflict_response.status_code,
                analysis_conflict_body=analysis_conflict_body,
                analyzer_calls_after_esp=analyzer_calls_after_esp,
                analyzer_calls_after_pi=analyzer_calls_after_pi,
                analyzer_payload=analyzer_state.requests[0] if analyzer_state.requests else None,
                analyzer_response=analyzer_state.responses[0] if analyzer_state.responses else None,
                demo_email=args.demo_email if args.persist_demo_user else None,
                run_id=run_id,
            ),
        )
        return summary
    finally:
        for server in reversed(servers):
            server.shutdown()
        if temp_dir is not None:
            temp_dir.cleanup()


def flow_summary(
    *,
    samples,
    esp_body,
    pi_bodies,
    duplicate_body,
    record_conflict_status,
    record_conflict_body,
    analysis_conflict_status,
    analysis_conflict_body,
    analyzer_calls_after_esp,
    analyzer_calls_after_pi,
    analyzer_payload,
    analyzer_response=None,
    demo_email=None,
    run_id=None,
) -> str:
    first = samples[0]
    pi_record = pi_record_from_sample(first)
    analyzer_sample = ((analyzer_response or {}).get("canonical_session") or {}).get("samples", [{}])[0]
    core_fields = ["rpm", "tps_voltage", "tps_raw", "battery_voltage", "iat_c", "ect_c"]
    semantic_match = all(analyzer_sample.get(field) == pi_record.get(field) for field in core_fields)
    return f"""# HTTP Flow Demo Summary

## ESP Flow

- Run id: `{run_id or "ephemeral"}`
- Demo user: `{demo_email or "temporary DB user"}`
- Native ECU: 24 bytes
- ESP upload: 29-byte `raw_frame`
- Cloud: stores RAW unchanged
- Analyzer: normalizes RAW29, decodes native24, returns canonical V2 + analysis
- Analyzer calls after ESP upload: {analyzer_calls_after_esp}

## Pi Flow

- Native ECU: 24 bytes
- Pi decode: local
- Canonical: V2
- Pi upload: decoded telemetry + analysis
- Cloud: stores telemetry and imports analysis
- Analyzer: NOT called
- Analyzer calls after Pi sync: {analyzer_calls_after_pi}

## First Record Comparison

SEMANTIC VALUES MATCH: `{semantic_match}`

Native frame:

```text
{first.hex}
```

ESP upload:

```text
{native24_to_legacy29_hex(first.frame)}
```

Cloud -> Analyzer RAW request:

```json
{_json_block(analyzer_payload or {})}
```

Analyzer -> Cloud canonical V2 sample:

```json
{_json_block(analyzer_sample)}
```

Pi upload V2 record:

```json
{_json_block(pi_record)}
```

## ACKs And Conflict Results

- ESP upload response: `{_brief(esp_body)}`
- Pi ACK progression: `{[body.get("accepted_sequences", {}).get("contiguous_until") for body in pi_bodies if isinstance(body, dict)]}`
- Pi duplicate response: `{_brief(duplicate_body)}`
- Pi record conflict: HTTP {record_conflict_status}, `{_brief(record_conflict_body)}`
- Pi analysis conflict: HTTP {analysis_conflict_status}, `{_brief(analysis_conflict_body)}`
"""


def _json_block(payload) -> str:
    import json

    return json.dumps(payload, indent=2, sort_keys=True)


def _brief(payload) -> str:
    if not isinstance(payload, dict):
        return str(payload)
    if "error" in payload:
        return payload["error"].get("code", str(payload["error"]))
    if "accepted_sequences" in payload:
        return str(payload["accepted_sequences"])
    return str(payload)


def main() -> None:
    args = build_parser().parse_args()
    summary = run_demo(args)
    print("HTTP flow demo complete.")
    print(f"Artifacts: {summary['artifact_dir']}")
    if summary.get("persistent_demo_user"):
        print("Persistent demo user:")
        print(f"  email: {summary['demo_email']}")
        print(f"  password: {args.demo_password}")
        print(f"  run_id: {summary['run_id']}")
    print(f"Analyzer calls after ESP: {summary['analyzer_calls_after_esp']}")
    print(f"Analyzer calls after Pi: {summary['analyzer_calls_after_pi']}")
    analyzer_sample = ((summary.get("mock_analyzer_responses") or [{}])[0].get("canonical_session") or {}).get("samples", [{}])[0]
    pi_sample = pi_record_from_sample(generate_native_session(1)[0])
    core_fields = ["rpm", "tps_voltage", "tps_raw", "battery_voltage", "iat_c", "ect_c"]
    print(f"SEMANTIC VALUES MATCH: {all(analyzer_sample.get(field) == pi_sample.get(field) for field in core_fields)}")
    print(f"Pi duplicate response: {summary['pi_duplicate_response']}")
    print(
        "Pi record conflict: "
        f"HTTP {summary['pi_record_conflict_status']} {summary['pi_record_conflict_response']}"
    )
    print(
        "Pi analysis conflict: "
        f"HTTP {summary['pi_analysis_conflict_status']} {summary['pi_analysis_conflict_response']}"
    )


if __name__ == "__main__":
    main()
