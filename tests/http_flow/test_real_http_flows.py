from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from extensions import db
from models import AnalysisResult, Device, RideSession, TelemetryRecord
from tests.http_flow.mock_analyzer import MockAnalyzerState, create_mock_analyzer_app
from tools.http_sim.esp_client import EspHttpSimulator
from tools.http_sim.frame_factory import (
    NATIVE_FIXTURE_HEX,
    decode_native_v2,
    generate_native_frame,
    generate_native_session,
    native24_to_legacy29,
    pi_record_from_sample,
)
from tools.http_sim.local_server import create_http_flow_cloud_app, initialize_cloud_database, start_werkzeug_server
from tools.http_sim.pi_client import PiHttpSimulator, chunk_samples, pi_analysis_fixture
from tools.http_sim.trace import HttpTrace


class FrameFactoryTestCase(unittest.TestCase):
    def test_native_fixture_checksum_decode_and_legacy_conversion(self):
        frame = generate_native_frame(0)
        self.assertEqual(frame.hex().upper(), NATIVE_FIXTURE_HEX)
        self.assertEqual(len(frame), 24)
        self.assertEqual(frame[:4], bytes.fromhex("02187117"))
        self.assertEqual(sum(frame) % 256, 0)

        decoded = decode_native_v2(frame)
        self.assertEqual(decoded["rpm"], 1500)
        self.assertAlmostEqual(decoded["tps_voltage"], 0.5078125)
        self.assertEqual(decoded["tps_raw"], 2)
        self.assertAlmostEqual(decoded["battery_voltage"], 12.7)
        self.assertEqual(decoded["iat_c"], 37)
        self.assertEqual(decoded["ect_c"], 66)
        self.assertNotEqual(decoded["battery_voltage"], frame[10] / 10)
        self.assertNotEqual(decoded["ect_c"], frame[12] - 40)

        legacy = native24_to_legacy29(frame)
        self.assertEqual(len(legacy), 29)
        self.assertEqual(sum(legacy) % 256, 251)
        self.assertEqual(legacy.hex().upper(), f"FFFFFFFFFF{NATIVE_FIXTURE_HEX}")


class RealHttpFlowTestCase(unittest.TestCase):
    def test_esp_to_analyzer_and_pi_to_cloud_real_http_flows(self):
        samples = generate_native_session(60)
        analyzer_state = MockAnalyzerState()
        servers = []

        with tempfile.TemporaryDirectory(prefix="drisafe-real-http-test-") as temp_dir:
            trace = HttpTrace(artifact_dir=Path(temp_dir) / "artifacts", verbose=False)
            analyzer_app = create_mock_analyzer_app(analyzer_state, trace=trace)
            analyzer_server = start_werkzeug_server(analyzer_app)
            servers.append(analyzer_server)
            cloud_app = create_http_flow_cloud_app(
                database_path=Path(temp_dir) / "cloud.sqlite",
                analyzer_url=analyzer_server.url,
            )
            esp_code, pi_code = initialize_cloud_database(cloud_app, pairing_code_count=2)
            cloud_server = start_werkzeug_server(cloud_app)
            servers.append(cloud_server)

            try:
                esp = EspHttpSimulator(cloud_server.url, trace=trace)
                pi = PiHttpSimulator(cloud_server.url, trace=trace)

                esp_credentials = esp.pair(esp_code)
                self.assertEqual(esp_credentials.device_id, "http-sim-esp-001")
                esp_response, esp_body = esp.upload(
                    esp_credentials,
                    esp.build_upload_payload(samples, session_id="http-test-esp-raw-000001"),
                )
                self.assertEqual(esp_response.status_code, 200)
                self.assertEqual(esp_body["inserted_count"], 60)
                self.assertEqual(esp_body["accepted_sequences"]["contiguous_until"], 59)
                self.assertEqual(analyzer_state.call_count, 1)

                analyzer_payload = analyzer_state.requests[0]
                self.assertEqual(analyzer_payload["raw_representation"], "legacy29_ff5")
                self.assertTrue(analyzer_payload["process_with_model"])
                self.assertEqual(len(analyzer_payload["records"]), 60)
                first_raw = analyzer_payload["records"][0]
                self.assertEqual(first_raw["sequence"], 0)
                self.assertEqual(first_raw["timestamp_ms"], 0)
                self.assertEqual(first_raw["raw_hex"], f"FFFFFFFFFF{NATIVE_FIXTURE_HEX}")

                pi_credentials = pi.pair(pi_code)
                self.assertEqual(pi_credentials.device_id, "http-sim-pi-001")
                pi_chunks = chunk_samples(samples, 20)
                pi_session_id = "http-test-pi-v2-000001"
                analysis = pi_analysis_fixture(analysis_run_id="analysis-http-test-pi-001")
                final_payload = None
                expected_contiguous = [19, 39, 59]
                for index, chunk in enumerate(pi_chunks, start=1):
                    final = index == len(pi_chunks)
                    payload = pi.build_sync_payload(
                        chunk,
                        session_id=pi_session_id,
                        batch_id=f"{pi_session_id}-{index:04d}",
                        session_ended=final,
                        analysis=analysis if final else None,
                    )
                    response, body = pi.sync(pi_credentials, payload)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(body["inserted_count"], 20)
                    self.assertEqual(body["duplicate_count"], 0)
                    self.assertEqual(body["accepted_sequences"]["contiguous_until"], expected_contiguous[index - 1])
                    self.assertEqual(analyzer_state.call_count, 1)
                    if final:
                        final_payload = payload
                        self.assertEqual(body["analysis"]["state"], "imported")

                with cloud_app.app_context():
                    esp_device = Device.query.filter_by(device_id=esp_credentials.device_id).one()
                    pi_device = Device.query.filter_by(device_id=pi_credentials.device_id).one()
                    esp_session = RideSession.query.filter_by(device_id=esp_device.id, session_id="http-test-esp-raw-000001").one()
                    pi_session = RideSession.query.filter_by(device_id=pi_device.id, session_id=pi_session_id).one()
                    self.assertNotEqual(esp_session.device_id, pi_session.device_id)
                    self.assertEqual(esp_session.record_count, 60)
                    self.assertEqual(pi_session.record_count, 60)
                    self.assertEqual(esp_session.telemetry_schema_version, "canonical-telemetry-v2")
                    self.assertEqual(esp_session.source_type, "esp_raw")
                    self.assertEqual(esp_session.raw_representation, "legacy29_ff5")
                    self.assertEqual(pi_session.telemetry_schema_version, "canonical-telemetry-v2")
                    self.assertEqual(pi_session.source_type, "pi_native")
                    self.assertEqual(pi_session.raw_representation, "native24_table17")
                    self.assertEqual(pi_session.duration_ms, 59 * 250)
                    esp_record = TelemetryRecord.query.filter_by(ride_session_id=esp_session.id, seq=0).one()
                    pi_record = TelemetryRecord.query.filter_by(ride_session_id=pi_session.id, seq=0).one()
                    for field in ("rpm", "tps_voltage", "tps_raw", "battery_voltage", "iat_c", "ect_c"):
                        self.assertEqual(getattr(esp_record, field), getattr(pi_record, field), field)
                    self.assertEqual(pi_record.rpm, 1500)
                    self.assertAlmostEqual(pi_record.tps_voltage, 0.5078125)
                    self.assertEqual(pi_record.tps_raw, 2)
                    self.assertAlmostEqual(pi_record.battery_voltage, 12.7)
                    self.assertEqual(pi_record.iat_c, 37)
                    self.assertEqual(pi_record.ect_c, 66)
                    self.assertIsNone(esp_record.map_raw)
                    self.assertIsNone(pi_record.map_raw)
                    self.assertEqual(pi_record.raw_hex, NATIVE_FIXTURE_HEX)
                    self.assertEqual(pi_record.raw_length, 24)
                    esp_imported = AnalysisResult.query.filter_by(ride_session_id=esp_session.id).one()
                    self.assertEqual(esp_imported.telemetry_schema_version, "canonical-telemetry-v2")
                    imported = AnalysisResult.query.filter_by(analysis_run_id="analysis-http-test-pi-001").one()
                    self.assertEqual(imported.telemetry_schema_version, "canonical-telemetry-v2")
                    self.assertEqual(imported.health_score, 91.0)

                duplicate_response, duplicate_body = pi.sync(pi_credentials, final_payload)
                self.assertEqual(duplicate_response.status_code, 200)
                self.assertEqual(duplicate_body["inserted_count"], 0)
                self.assertEqual(duplicate_body["duplicate_count"], 20)
                self.assertEqual(duplicate_body["analysis"]["state"], "duplicate")
                self.assertEqual(analyzer_state.call_count, 1)

                conflict_payload = pi.build_sync_payload(
                    [samples[10]],
                    session_id=pi_session_id,
                    batch_id=f"{pi_session_id}-record-conflict",
                )
                conflict_payload["records"][0]["rpm"] += 100
                conflict_response, conflict_body = pi.sync(pi_credentials, conflict_payload)
                self.assertEqual(conflict_response.status_code, 409)
                self.assertEqual(conflict_body["error"]["code"], "PI_RECORD_CONFLICT")

                analysis_conflict_payload = copy.deepcopy(final_payload)
                analysis_conflict_payload["batch_id"] = f"{pi_session_id}-analysis-conflict"
                analysis_conflict_payload["analysis"]["health_score"] = 70.0
                analysis_conflict_response, analysis_conflict_body = pi.sync(pi_credentials, analysis_conflict_payload)
                self.assertEqual(analysis_conflict_response.status_code, 409)
                self.assertEqual(analysis_conflict_body["error"]["code"], "PI_ANALYSIS_CONFLICT")
                self.assertEqual(analyzer_state.call_count, 1)

                isolated_payload = pi.build_sync_payload(
                    [samples[10]],
                    session_id=pi_session_id,
                    batch_id=f"{pi_session_id}-other-device",
                )
                isolated_payload["records"][0]["rpm"] += 100
                isolated_response, isolated_body = pi.sync(esp_credentials, isolated_payload)
                self.assertEqual(isolated_response.status_code, 200)
                self.assertEqual(isolated_body["inserted_count"], 1)
                self.assertEqual(analyzer_state.call_count, 1)
                with cloud_app.app_context():
                    self.assertEqual(RideSession.query.filter_by(session_id=pi_session_id).count(), 2)
            finally:
                for server in reversed(servers):
                    server.shutdown()


if __name__ == "__main__":
    unittest.main()
