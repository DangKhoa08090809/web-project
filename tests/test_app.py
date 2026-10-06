import csv
import io
import json
from datetime import timedelta
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from app import create_app
from extensions import db
from integrations.analyzer_client import AnalyzerClient, AnalyzerClientError
from models import (
    AnalysisResult,
    DecoderVersion,
    Device,
    DeviceControlCommand,
    DeviceControlRuntime,
    DevicePairingCode,
    EcuProfile,
    RawArtifact,
    RideSession,
    SyncBatch,
    TelemetryRecord,
    User,
    utc_now,
)
from services.analysis import analysis_snapshot, format_duration, format_number, overview_condition_summary, session_records
from services.live import InMemoryLiveBackend, isoformat_z


class DashboardTestCase(unittest.TestCase):
    VALID_RAW_FRAME = "FF;FF;FF;FF;FF;02;18;71;17;00;00;19;00;FF;FF;81;49;5C;59;7D;00;00;58;7C;00;00;00;00;77"
    VALID_RAW_FRAME_COMPACT = VALID_RAW_FRAME.replace(";", "")

    def setUp(self):
        self.app = create_app({
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite://",
            "WTF_CSRF_ENABLED": False,
            "SECRET_KEY": "test-only-secret",
            "MAX_LOG_RECORDS": 10,
            "PAIRING_CODE_TTL_SECONDS": 60,
            "PAIRING_MAX_ATTEMPTS": 3,
            "LIVE_REDIS_REQUIRED": False,
            "LIVE_SAMPLE_TTL_SECONDS": 15,
            "LIVE_RATE_LIMIT_PER_SECOND": 5,
            "LIVE_RATE_LIMIT_BURST": 10,
            "LIVE_MAX_REQUEST_BYTES": 4096,
            "LIVE_SSE_HEARTBEAT_SECONDS": 0,
            "LIVE_RAW_FRAME_MAX_CHARS": 256,
            "CONTROL_POLL_STALE_SECONDS": 10,
        })
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.admin = self._user("Admin", "admin@example.com", "admin")
        self.user_a = self._user("User A", "a@example.com", "user")
        self.user_b = self._user("User B", "b@example.com", "user")
        self.disabled_user = self._user("Disabled", "disabled@example.com", "user", active=False)
        self.device_a = self._device(self.user_a, "xiao-a", "token-a")
        self.device_b = self._device(self.user_b, "xiao-b", "token-b")
        db.session.commit()
        self.live_backend = InMemoryLiveBackend()
        self.app.extensions["live_backend"] = self.live_backend
        self.client = self.app.test_client()
        self.headers_a = {"X-Device-ID": "xiao-a", "Authorization": "Bearer token-a"}
        self.headers_b = {"X-Device-ID": "xiao-b", "Authorization": "Bearer token-b"}
        self._pairing_counter = 0

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def _user(self, name, email, role, active=True):
        user = User(full_name=name, email=email, role=role, is_active=active)
        user.set_password("a-long-test-password")
        db.session.add(user)
        db.session.flush()
        return user

    def _device(self, user, device_id, token):
        device = Device(
            user_id=user.id,
            device_id=device_id,
            device_name=f"Reader {device_id}",
            vehicle_name=f"Vehicle {device_id}",
            ecu_type="CAN",
            token_hash="",
        )
        device.set_token(token)
        db.session.add(device)
        db.session.flush()
        return device

    def login(self, email="a@example.com", password="a-long-test-password"):
        return self.client.post("/auth/login", json={"email": email, "password": password})

    def pairing_code(self, user=None):
        user = user or self.user_a
        self._pairing_counter += 1
        code = f"ABCD-EFGH-JKLM-{self._pairing_counter:04d}"
        pairing = DevicePairingCode(
            user_id=user.id,
            code_hash=DevicePairingCode.hash_code(code),
            expires_at=utc_now() + timedelta(minutes=10),
        )
        db.session.add(pairing)
        db.session.commit()
        return pairing, code

    def batch(self, session_id="ride-1", seqs=(1, 2), ended=False):
        return {
            "session_id": session_id,
            "session_ended": ended,
            "records": [
                {"seq": seq, "device_time_ms": seq * 100, "raw_frame": f"02187117{seq:02X}00"}
                for seq in seqs
            ],
        }

    def pi_record(self, seq=0, timestamp_ms=None, **overrides):
        record = {
            "seq": seq,
            "timestamp_ms": float(seq * 250 if timestamp_ms is None else timestamp_ms),
            "raw_hex": f"02187117{seq:040X}",
            "raw_length": 24,
            "rpm": 1500 + seq * 25,
            "tps_voltage": 0.72 + seq * 0.01,
            "tps_raw": 37 + seq,
            "battery_voltage": 13.6,
            "iat_c": 31.5,
            "ect_c": 74.0 + seq,
            "frame_valid": True,
            "checksum_valid": True,
            "candidate_signals": {"load_hint": seq % 2},
        }
        record.update(overrides)
        return record

    def pi_analysis(self, run_id="pi-analysis-1", **overrides):
        analysis = {
            "analysis_run_id": run_id,
            "model_version": "iforest-baseline-20260920T091709Z",
            "telemetry_schema_version": "canonical-telemetry-v2",
            "feature_schema_version": "ecu-window-features-v1",
            "signal_columns": ["rpm", "tps_voltage", "tps_raw", "battery_voltage", "iat_c", "ect_c"],
            "window_count": 3,
            "anomaly_window_count": 1,
            "anomaly_ratio": 1 / 3,
            "health_score": 86.5,
            "overall_status": "minor_anomaly",
            "most_unusual_features": ["rpm", "ect_c"],
            "model_loaded": True,
            "warnings": [],
            "note": "Imported from Pi5 local analyzer.",
        }
        analysis.update(overrides)
        return analysis

    def pi_payload(self, session_id="pi-ride", seqs=(0, 1), ended=False, analysis=None, **overrides):
        payload = {
            "session_id": session_id,
            "batch_id": f"{session_id}-batch-{min(seqs)}-{max(seqs)}",
            "session_ended": ended,
            "telemetry_schema_version": "canonical-telemetry-v2",
            "ecu_profile_id": "honda_keihin_71_17",
            "decoder_id": "honda_keihin_71_17",
            "decoder_version": "1.0.0",
            "sampling": {"sample_interval_ms": 250, "sampling_rate_hz": 4},
            "records": [self.pi_record(seq) for seq in seqs],
        }
        if analysis is not None:
            payload["analysis"] = analysis
        payload.update(overrides)
        return payload

    def post_pi_v2_session(
        self,
        session_id,
        *,
        count=10,
        battery_voltage=13.5,
        ect_c=74.4,
        iat_c=31.2,
        overall_status="ok",
        anomaly_ratio=0.0,
        anomaly_window_count=0,
        headers=None,
        device=None,
    ):
        def value_at(value, seq):
            if callable(value):
                return value(seq)
            if isinstance(value, (list, tuple)):
                return value[seq]
            return value

        records = [
            self.pi_record(
                seq,
                battery_voltage=value_at(battery_voltage, seq),
                ect_c=value_at(ect_c, seq),
                iat_c=value_at(iat_c, seq),
            )
            for seq in range(count)
        ]
        analysis = self.pi_analysis(
            run_id=f"pi-run-{session_id}",
            window_count=max(1, count),
            anomaly_window_count=anomaly_window_count,
            anomaly_ratio=anomaly_ratio,
            overall_status=overall_status,
            health_score=91.0 if overall_status in {"ok", "normal"} else 75.0,
        )
        payload = self.pi_payload(
            session_id=session_id,
            seqs=tuple(range(count)),
            ended=True,
            records=records,
            analysis=analysis,
        )
        response = self.client.post("/api/device/sync/session", json=payload, headers=headers or self.headers_a)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        device = device or self.device_a
        return RideSession.query.filter_by(device_id=device.id, session_id=session_id).one()

    def seed_v2_baseline(self, prefix="baseline", count=10):
        sessions = []
        for index in range(3):
            sessions.append(
                self.post_pi_v2_session(
                    f"{prefix}-{index}",
                    count=count,
                    battery_voltage=lambda seq, index=index: 13.45 + index * 0.03 + seq * 0.005,
                    ect_c=lambda seq, index=index: 74.0 + index * 0.4 + seq * 0.02,
                    iat_c=lambda seq, index=index: 31.0 + index * 0.2 + seq * 0.01,
                    overall_status="ok",
                )
            )
        return sessions

    def analysis_batch(self, session_id="analysis-1", count=8, anomaly=False):
        records = []
        for seq in range(1, count + 1):
            rpm = 1200 + seq * 20
            battery = 13.4
            if anomaly and seq in (5, 6):
                rpm = 4300 + seq * 10
                battery = 10.9
            records.append({
                "seq": seq,
                "device_time_ms": seq * 1000,
                "rpm": rpm,
                "tps": 12,
                "ect": 82 + seq * 0.2,
                "iat": 31,
                "battery": battery,
                "injector_ms": 2.1,
                "ignition_deg": 12,
            })
        return {"session_id": session_id, "session_ended": True, "records": records}

    def upload_analysis_session(self, session_id="analysis-1", count=8, anomaly=False):
        response = self.client.post(
            "/api/logs/upload",
            json=self.analysis_batch(session_id=session_id, count=count, anomaly=anomaly),
            headers=self.headers_a,
        )
        self.assertEqual(response.status_code, 200)
        return RideSession.query.filter_by(device_id=self.device_a.id, session_id=session_id).one()

    def live_payload(self, **overrides):
        payload = {
            "session_id": "live-boot-1",
            "seq": 1,
            "device_time_ms": 25000,
            "rpm": 1800,
            "tps": 9.5,
            "tps_voltage": 0.72,
            "ect": 81,
            "iat": 34,
            "battery": 13.8,
            "injector_ms": 2.3,
            "injector_raw": 230,
            "fuel_cut_inferred": False,
            "raw_frame": "02187117",
            "parser_version": "honda-table17-v1",
        }
        payload.update(overrides)
        return payload

    def control_poll(self, boot_id="boot-a", live_mode=False, headers=None, **overrides):
        payload = {"boot_id": boot_id, "live_mode": live_mode}
        payload.update(overrides)
        return self.client.post("/api/device/control/poll", json=payload, headers=headers or self.headers_a)

    def control_ack(self, command_id, boot_id="boot-a", status="applied", live_mode=True, headers=None, **overrides):
        payload = {"boot_id": boot_id, "command_id": command_id, "status": status, "live_mode": live_mode}
        payload.update(overrides)
        return self.client.post("/api/device/control/ack", json=payload, headers=headers or self.headers_a)

    def live_control_action(self, action, device_id="xiao-a"):
        return self.client.post(f"/api/devices/{device_id}/live/{action}")

    def canonical_raw_record(self, seq=1, **overrides):
        record = {
            "seq": seq,
            "device_time_ms": seq * 100,
            "rpm": 9999,
            "battery": 42,
            "raw_frame": self.VALID_RAW_FRAME,
        }
        record.update(overrides)
        return record

    def raw_analyzer_response(self, payload, *, run_id="raw-run-1", health_score=88.0, **analysis_overrides):
        samples = []
        for record in payload["records"]:
            sequence = int(record["sequence"])
            samples.append({
                "sequence": sequence,
                "timestamp_ms": record.get("timestamp_ms"),
                "rpm": 1500 + sequence,
                "tps_voltage": 0.5,
                "tps_raw": 20 + sequence,
                "battery_voltage": 12.7,
                "iat_c": 37,
                "ect_c": 66,
                "frame_valid": True,
                "checksum_valid": True,
                "candidate_signals": {},
                "quality_flags": {"mock": True},
            })
        canonical = {
            "session_id": payload["session_id"],
            "device_id": payload["device_id"],
            "vehicle_id": payload.get("vehicle_id"),
            "ecu_profile_id": "honda_keihin_71_17",
            "decoder_id": "honda_keihin_71_17",
            "decoder_version": "1.0.0",
            "telemetry_schema_version": "canonical-telemetry-v2",
            "sampling": payload.get("sampling") or {},
            "samples": samples,
        }
        analysis = {
            "analysis_run_id": run_id,
            "session_id": payload["session_id"],
            "model_version": "model-v2",
            "telemetry_schema_version": "canonical-telemetry-v2",
            "feature_schema_version": "ecu-window-features-v1",
            "signal_columns": ["rpm", "tps_voltage", "tps_raw", "battery_voltage", "iat_c", "ect_c"],
            "ecu_profile_id": "honda_keihin_71_17",
            "decoder_id": "honda_keihin_71_17",
            "decoder_version": "1.0.0",
            "window_count": max(1, len(samples)),
            "anomaly_window_count": 0,
            "anomaly_ratio": 0.0,
            "health_score": health_score,
            "overall_status": "ok",
            "most_unusual_features": [],
            "model_loaded": True,
            "warnings": [],
            "note": "unit raw analyzer fixture",
        }
        analysis.update(analysis_overrides)
        return {"canonical_session": canonical, "analysis": analysis}

    def test_login_failures_are_safe_and_pages_require_login(self):
        self.assertEqual(self.client.get("/").status_code, 302)
        self.assertEqual(self.client.get("/api/devices").status_code, 401)
        missing = self.client.post("/auth/login", json={"email": "missing@example.com", "password": "bad"})
        wrong = self.client.post("/auth/login", json={"email": "a@example.com", "password": "bad"})
        self.assertEqual(missing.status_code, 401)
        self.assertEqual(wrong.status_code, 401)
        self.assertEqual(missing.json["error"], wrong.json["error"])
        self.assertEqual(self.login().status_code, 200)
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_generate_pairing_code_requires_login(self):
        self.assertEqual(self.client.post("/devices/pairing-codes", json={}).status_code, 401)
        self.login()
        response = self.client.post("/devices/pairing-codes", json={})
        self.assertEqual(response.status_code, 201)
        self.assertIn("pairing_code", response.json)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(DevicePairingCode.query.filter_by(user_id=self.user_a.id).count(), 1)

    def test_valid_pairing_returns_token_once_and_hashes_it(self):
        pairing, code = self.pairing_code()
        payload = {
            "pairing_code": code,
            "device_id": "ecu-new",
            "device_name": "ECU Reader",
            "vehicle_name": "Test Motorcycle",
            "ecu_type": "Unknown ECU",
            "firmware_version": "1.0.0",
            "hardware_version": "xiao-esp32",
        }
        response = self.client.post("/api/device/pair", json=payload)
        self.assertEqual(response.status_code, 201)
        token = response.json["device_token"]
        self.assertTrue(token)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        device = Device.query.filter_by(device_id="ecu-new").one()
        self.assertEqual(device.user_id, self.user_a.id)
        self.assertNotEqual(device.token_hash, token)
        self.assertTrue(device.check_token(token))
        self.assertIsNotNone(db.session.get(DevicePairingCode, pairing.id).used_at)

        reused = self.client.post("/api/device/pair", json=payload)
        self.assertEqual(reused.status_code, 409)
        self.assertNotIn("device_token", reused.json)

    def test_pi_pairing_reuses_existing_device_pair_endpoint(self):
        _, code = self.pairing_code()
        pair = self.client.post(
            "/api/device/pair",
            json={
                "pairing_code": code,
                "device_id": "pi5-native",
                "device_name": "Pi5 ECU Reader",
                "vehicle_name": "Honda test bike",
                "ecu_type": "Honda Keihin 0x71 0x17",
                "firmware_version": "ecuread-pi5-0.1.0",
                "hardware_version": "raspberry-pi-5",
            },
        )
        self.assertEqual(pair.status_code, 201)
        headers = {"X-Device-ID": "pi5-native", "Authorization": f"Bearer {pair.json['device_token']}"}
        sync = self.client.post("/api/device/sync/session", json=self.pi_payload(session_id="paired-pi"), headers=headers)
        self.assertEqual(sync.status_code, 200)
        self.assertEqual(sync.json["inserted_count"], 2)
        self.assertEqual(Device.query.filter_by(device_id="pi5-native").one().hardware_version, "raspberry-pi-5")

    def test_pairing_rejects_expired_invalid_duplicate_and_disabled_owner(self):
        expired, expired_code = self.pairing_code()
        expired.expires_at = utc_now() - timedelta(seconds=1)
        db.session.commit()
        payload = {
            "pairing_code": expired_code,
            "device_id": "ecu-expired",
            "device_name": "ECU Reader",
            "vehicle_name": "Bike",
            "ecu_type": "CAN",
        }
        self.assertEqual(self.client.post("/api/device/pair", json=payload).status_code, 400)

        payload["pairing_code"] = "NOPE-NOPE-NOPE-NOPE"
        self.assertEqual(self.client.post("/api/device/pair", json=payload).status_code, 400)

        _, duplicate_code = self.pairing_code()
        payload.update({"pairing_code": duplicate_code, "device_id": "xiao-a"})
        self.assertEqual(self.client.post("/api/device/pair", json=payload).status_code, 409)

        _, disabled_code = self.pairing_code(self.disabled_user)
        payload.update({"pairing_code": disabled_code, "device_id": "ecu-disabled"})
        self.assertEqual(self.client.post("/api/device/pair", json=payload).status_code, 403)

    def test_pairing_attempt_limit_blocks_known_code(self):
        _, code = self.pairing_code()
        payload = {
            "pairing_code": code,
            "device_id": "xiao-a",
            "device_name": "ECU Reader",
            "vehicle_name": "Bike",
            "ecu_type": "CAN",
        }
        for _ in range(3):
            self.assertEqual(self.client.post("/api/device/pair", json=payload).status_code, 409)
        response = self.client.post("/api/device/pair", json=payload)
        self.assertEqual(response.status_code, 429)

    def test_device_authentication_and_token_rotation(self):
        payload = {"session_id": "ride-auth", "seq": 1, "device_time_ms": 10}
        self.assertEqual(self.client.post("/api/telemetry", json=payload).status_code, 401)
        wrong = {"X-Device-ID": "xiao-a", "Authorization": "Bearer wrong"}
        self.assertEqual(self.client.post("/api/telemetry", json=payload, headers=wrong).status_code, 401)

        valid = self.client.post("/api/telemetry", json=payload, headers=self.headers_a)
        self.assertEqual(valid.status_code, 201)
        self.assertIsNotNone(db.session.get(Device, self.device_a.id).last_seen_at)

        self.login()
        rotated = self.client.post(f"/api/devices/{self.device_a.id}/rotate-token")
        self.assertEqual(rotated.status_code, 200)
        new_headers = {"X-Device-ID": "xiao-a", "Authorization": f"Bearer {rotated.json['token']}"}
        self.assertEqual(self.client.post("/api/telemetry", json={**payload, "seq": 2}, headers=self.headers_a).status_code, 401)
        self.assertEqual(self.client.post("/api/telemetry", json={**payload, "seq": 2}, headers=new_headers).status_code, 201)

        self.device_a.is_active = False
        db.session.commit()
        self.assertEqual(self.client.post("/api/telemetry", json={**payload, "seq": 3}, headers=new_headers).status_code, 403)

    def test_batch_upload_is_idempotent_and_acknowledges_contiguous_sequences(self):
        first = self.client.post("/api/logs/upload", json=self.batch(seqs=(1, 3)), headers=self.headers_a)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json["inserted_count"], 2)
        self.assertEqual(first.json["duplicate_count"], 0)
        self.assertEqual(first.json["accepted_sequences"]["contiguous_until"], 1)

        retry = self.client.post("/api/logs/upload", json=self.batch(seqs=(1, 3)), headers=self.headers_a)
        self.assertEqual(retry.json["inserted_count"], 0)
        self.assertEqual(retry.json["duplicate_count"], 2)
        self.assertEqual(TelemetryRecord.query.count(), 2)

        overlap = self.client.post("/api/logs/upload", json=self.batch(seqs=(2, 3), ended=True), headers=self.headers_a)
        self.assertEqual(overlap.json["inserted_count"], 1)
        self.assertEqual(overlap.json["duplicate_count"], 1)
        self.assertEqual(overlap.json["accepted_sequences"]["contiguous_until"], 3)
        session = RideSession.query.filter_by(device_id=self.device_a.id, session_id="ride-1").one()
        self.assertEqual(session.record_count, 3)
        self.assertEqual(session.first_seq, 1)
        self.assertEqual(session.last_seq, 3)
        self.assertEqual(session.sync_status, "complete")

    def test_pi_v2_sync_multibatch_retry_conflict_final_analysis_and_browser_payloads(self):
        self.app.config["ML_API_BASE_URL"] = "http://analyzer.example"
        first_payload = self.pi_payload(
            session_id="pi-main",
            seqs=(0, 2),
            capture_started_at="2026-09-20T09:00:00Z",
        )
        final_payload = self.pi_payload(
            session_id="pi-main",
            seqs=(1,),
            ended=True,
            capture_ended_at="2026-09-20T09:00:01Z",
            analysis=self.pi_analysis(
                run_id="pi-run-main",
                overall_status="limited_data",
                evidence_window_count=3,
                minimum_windows_for_status=10,
                evidence_sufficient=False,
                diagnostic_evidence={
                    "schema_version": "diagnostic-evidence-v1",
                    "aggregate_state": {"evidence_state": "insufficient_coverage", "coverage": "limited_windows"},
                    "rca_v2": {
                        "observations": ["limited post-ride evidence"],
                        "symptoms": [],
                        "recommended_checks": [
                            {"label": "Review another ride", "priority": "deferred"},
                        ],
                    },
                    "historical_evidence": {
                        "state": "historical comparison unavailable",
                        "historical_cutoff": "2026-09-20T09:00:00Z",
                    },
                    "detectors": {
                        "isolation_forest": {"status": "skipped", "detector_version": "iforest-v2"},
                    },
                    "provenance": {"historical_cutoff": "2026-09-20T09:00:00Z"},
                },
            ),
        )

        with patch("integrations.analyzer_client.AnalyzerClient.decode_analyze_raw_session") as analyzer:
            first = self.client.post("/api/device/sync/session", json=first_payload, headers=self.headers_a)
            self.assertEqual(first.status_code, 200)
            self.assertEqual(first.json["inserted_count"], 2)
            self.assertEqual(first.json["duplicate_count"], 0)
            self.assertEqual(first.json["accepted_sequences"]["contiguous_until"], 0)

            retry = self.client.post("/api/device/sync/session", json=first_payload, headers=self.headers_a)
            self.assertEqual(retry.status_code, 200)
            self.assertEqual(retry.json["inserted_count"], 0)
            self.assertEqual(retry.json["duplicate_count"], 2)

            conflict_payload = self.pi_payload(session_id="pi-main", seqs=(0,), records=[self.pi_record(0, rpm=2500)])
            conflict = self.client.post("/api/device/sync/session", json=conflict_payload, headers=self.headers_a)
            self.assertEqual(conflict.status_code, 409)
            self.assertEqual(conflict.json["error"]["code"], "PI_RECORD_CONFLICT")

            raw_conflict_record = self.pi_record(0)
            raw_conflict_record["raw_hex"] = "02187117" + ("A" * 40)
            raw_conflict_payload = self.pi_payload(session_id="pi-main", seqs=(0,), records=[raw_conflict_record])
            raw_conflict = self.client.post("/api/device/sync/session", json=raw_conflict_payload, headers=self.headers_a)
            self.assertEqual(raw_conflict.status_code, 409)
            self.assertEqual(raw_conflict.json["error"]["code"], "PI_RECORD_CONFLICT")

            final = self.client.post("/api/device/sync/session", json=final_payload, headers=self.headers_a)
            self.assertEqual(final.status_code, 200)
            self.assertEqual(final.json["inserted_count"], 1)
            self.assertEqual(final.json["accepted_sequences"]["contiguous_until"], 2)
            self.assertEqual(final.json["analysis"]["state"], "imported")
            analyzer.assert_not_called()

        session = RideSession.query.filter_by(device_id=self.device_a.id, session_id="pi-main").one()
        self.assertEqual(session.telemetry_schema_version, "canonical-telemetry-v2")
        self.assertEqual(session.source_type, "pi_native")
        self.assertEqual(session.sync_status, "complete")
        self.assertEqual(session.capture_status, "completed")
        self.assertEqual(session.duration_ms, 500)
        self.assertIsNotNone(session.capture_started_at)
        self.assertIsNotNone(session.capture_ended_at)
        self.assertEqual(session.sample_interval_ms, 250)
        self.assertEqual(session.sampling_rate_hz, 4)
        self.assertEqual(session.raw_representation, "native24_table17")
        self.assertEqual(session.ecu_profile_id, "honda_keihin_71_17")
        self.assertEqual(session.decoder_id, "honda_keihin_71_17")
        self.assertEqual(session.decoder_version, "1.0.0")
        self.assertEqual(session.decoder_version_ref.decoder_id, "honda_keihin_71_17")

        record = TelemetryRecord.query.filter_by(ride_session_id=session.id, seq=1).one()
        self.assertEqual(record.raw_hex, self.pi_record(1)["raw_hex"])
        self.assertEqual(record.raw_length, 24)
        self.assertEqual(record.raw_representation, "native24_table17")
        self.assertEqual(record.tps_raw, 38)
        self.assertEqual(record.ect_c, 75)
        self.assertIsNone(record.tps_raw_candidate)
        self.assertIsNone(record.ect_c_candidate)
        self.assertIsNone(record.map_raw)
        self.assertEqual(record.quality_flags["telemetry_schema_version"], "canonical-telemetry-v2")
        self.assertTrue(record.record_fingerprint)

        analysis = AnalysisResult.query.filter_by(analysis_run_id="pi-run-main").one()
        self.assertEqual(analysis.telemetry_schema_version, "canonical-telemetry-v2")
        self.assertEqual(analysis.ecu_profile_id, "honda_keihin_71_17")
        self.assertEqual(analysis.decoder_id, "honda_keihin_71_17")
        self.assertEqual(analysis.decoder_version, "1.0.0")
        self.assertEqual(analysis.signal_columns, ["rpm", "tps_voltage", "tps_raw", "battery_voltage", "iat_c", "ect_c"])
        self.assertEqual(analysis.overall_status, "limited_data")
        self.assertEqual(analysis.result_summary["evidence_window_count"], 3)
        self.assertEqual(analysis.result_summary["minimum_windows_for_status"], 10)
        self.assertFalse(analysis.result_summary["evidence_sufficient"])
        self.assertEqual(analysis.result_summary["diagnostic_evidence"]["schema_version"], "diagnostic-evidence-v1")
        self.assertTrue(analysis.result_fingerprint)

        duplicate_analysis = self.client.post("/api/device/sync/session", json=final_payload, headers=self.headers_a)
        self.assertEqual(duplicate_analysis.status_code, 200)
        self.assertEqual(duplicate_analysis.json["analysis"]["state"], "duplicate")
        conflict_analysis_payload = self.pi_payload(
            session_id="pi-main",
            seqs=(1,),
            ended=True,
            analysis=self.pi_analysis(run_id="pi-run-main", health_score=55),
        )
        conflict_analysis = self.client.post("/api/device/sync/session", json=conflict_analysis_payload, headers=self.headers_a)
        self.assertEqual(conflict_analysis.status_code, 409)
        self.assertEqual(conflict_analysis.json["error"]["code"], "PI_ANALYSIS_CONFLICT")

        self.login()
        samples = self.client.get(f"/api/sessions/{session.id}/samples")
        self.assertEqual(samples.status_code, 200)
        parameter_keys = [parameter["key"] for parameter in samples.json["parameters"]]
        self.assertIn("tps_raw", parameter_keys)
        self.assertIn("ect_c", parameter_keys)
        self.assertNotIn("map_raw", parameter_keys)
        self.assertEqual(samples.json["samples"][1]["tps_raw"], 38)
        self.assertEqual(samples.json["samples"][1]["ect_c"], 75)
        self.assertEqual(samples.json["session"]["duration_ms"], 500)

        analysis_api = self.client.get(f"/api/sessions/{session.id}/analysis")
        self.assertEqual(analysis_api.status_code, 200)
        self.assertEqual(analysis_api.json["analysis"]["analysis_status"], "completed")
        self.assertEqual(analysis_api.json["analysis"]["result"], "Limited data")
        self.assertEqual(analysis_api.json["analysis"]["external_overall_status"], "limited_data")
        self.assertEqual(analysis_api.json["analysis"]["model_version"], "iforest-baseline-20260920T091709Z")
        self.assertEqual(analysis_api.json["analysis"]["health_score"], 86.5)
        self.assertEqual(analysis_api.json["analysis"]["analysis_signal_columns"], ["rpm", "tps_voltage", "tps_raw", "battery_voltage", "iat_c", "ect_c"])
        self.assertEqual(analysis_api.json["analysis"]["diagnostic_evidence"]["schema_version"], "diagnostic-evidence-v1")
        self.assertEqual(analysis_api.json["analysis"]["diagnostic_evidence_view"]["state"]["value"], "insufficient_coverage")
        rerun = self.client.post(f"/api/sessions/{session.id}/analysis/re-run")
        self.assertEqual(rerun.status_code, 200)
        self.assertIn("not rerun by Cloud", rerun.json["message"])

    def test_pi_finalization_with_gaps_uses_relative_duration(self):
        response = self.client.post(
            "/api/device/sync/session",
            json=self.pi_payload(session_id="pi-gaps", seqs=(0, 2), ended=True),
            headers=self.headers_a,
        )
        self.assertEqual(response.status_code, 200)
        session = RideSession.query.filter_by(device_id=self.device_a.id, session_id="pi-gaps").one()
        self.assertEqual(session.sync_status, "partial")
        self.assertEqual(session.capture_status, "completed_with_gaps")
        self.assertEqual(session.duration_ms, 500)
        self.assertEqual(response.json["accepted_sequences"]["contiguous_until"], 0)

    def test_pi_capture_termination_metadata_is_backward_compatible_idempotent_and_conflict_safe(self):
        legacy = self.client.post(
            "/api/device/sync/session",
            json=self.pi_payload(session_id="pi-legacy-termination", seqs=(0,), ended=True),
            headers=self.headers_a,
        )
        self.assertEqual(legacy.status_code, 200)
        legacy_session = RideSession.query.filter_by(device_id=self.device_a.id, session_id="pi-legacy-termination").one()
        self.assertEqual(legacy_session.capture_status, "completed")
        self.assertIsNone(legacy_session.termination_reason)
        self.assertIsNone(legacy.json["termination_reason"])

        normal = self.client.post(
            "/api/device/sync/session",
            json=self.pi_payload(
                session_id="pi-normal-termination",
                seqs=(0,),
                ended=True,
                capture_status="completed",
                termination_reason="normal_stop",
            ),
            headers=self.headers_a,
        )
        self.assertEqual(normal.status_code, 200)
        normal_session = RideSession.query.filter_by(device_id=self.device_a.id, session_id="pi-normal-termination").one()
        self.assertEqual(normal_session.sync_status, "complete")
        self.assertEqual(normal_session.capture_status, "completed")
        self.assertEqual(normal_session.termination_reason, "normal_stop")
        self.assertEqual(normal.json["capture_status"], "completed")
        self.assertEqual(normal.json["termination_reason"], "normal_stop")

        first = self.client.post(
            "/api/device/sync/session",
            json=self.pi_payload(
                session_id="pi-interrupted-termination",
                seqs=(0,),
                capture_started_at="2026-10-02T09:00:00Z",
                capture_status="interrupted",
                termination_reason="unclean_runtime_shutdown",
            ),
            headers=self.headers_a,
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json["capture_status"], "interrupted")
        self.assertEqual(first.json["termination_reason"], "unclean_runtime_shutdown")

        final_payload = self.pi_payload(
            session_id="pi-interrupted-termination",
            seqs=(1,),
            ended=True,
            capture_started_at="2026-10-02T09:00:00Z",
            capture_ended_at="2026-10-02T09:00:00.250000Z",
            capture_status="interrupted",
            termination_reason="unclean_runtime_shutdown",
            analysis=self.pi_analysis(
                run_id="pi-run-interrupted-termination",
                overall_status="limited_data",
                evidence_window_count=1,
                minimum_windows_for_status=10,
                evidence_sufficient=False,
            ),
        )
        final = self.client.post("/api/device/sync/session", json=final_payload, headers=self.headers_a)
        self.assertEqual(final.status_code, 200)
        self.assertEqual(final.json["capture_status"], "interrupted")
        self.assertEqual(final.json["termination_reason"], "unclean_runtime_shutdown")
        self.assertEqual(final.json["analysis"]["state"], "imported")

        interrupted = RideSession.query.filter_by(device_id=self.device_a.id, session_id="pi-interrupted-termination").one()
        self.assertEqual(interrupted.sync_status, "complete")
        self.assertEqual(interrupted.capture_status, "interrupted")
        self.assertEqual(interrupted.termination_reason, "unclean_runtime_shutdown")
        self.assertEqual(interrupted.record_count, 2)
        self.assertEqual(interrupted.analysis_status, "completed")
        self.assertEqual(AnalysisResult.query.filter_by(ride_session_id=interrupted.id).one().overall_status, "limited_data")

        retry = self.client.post("/api/device/sync/session", json=final_payload, headers=self.headers_a)
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(retry.json["inserted_count"], 0)
        self.assertEqual(retry.json["duplicate_count"], 1)
        self.assertEqual(retry.json["analysis"]["state"], "duplicate")
        self.assertEqual(RideSession.query.filter_by(device_id=self.device_a.id, session_id="pi-interrupted-termination").count(), 1)
        self.assertEqual(AnalysisResult.query.filter_by(ride_session_id=interrupted.id).count(), 1)

        batches_before_conflict = SyncBatch.query.filter_by(ride_session_id=interrupted.id).count()
        conflict = self.client.post(
            "/api/device/sync/session",
            json=self.pi_payload(
                session_id="pi-interrupted-termination",
                seqs=(1,),
                ended=True,
                capture_status="completed",
                termination_reason="normal_stop",
            ),
            headers=self.headers_a,
        )
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json["error"]["code"], "PI_CAPTURE_METADATA_CONFLICT")
        db.session.refresh(interrupted)
        self.assertEqual(interrupted.capture_status, "interrupted")
        self.assertEqual(interrupted.termination_reason, "unclean_runtime_shutdown")
        self.assertEqual(SyncBatch.query.filter_by(ride_session_id=interrupted.id).count(), batches_before_conflict)

    def test_pi_capture_termination_validation_and_session_detail_rendering(self):
        invalid_status = self.pi_payload(
            session_id="pi-invalid-capture-status",
            seqs=(0,),
            capture_status="aborted",
            termination_reason="unclean_runtime_shutdown",
        )
        response = self.client.post("/api/device/sync/session", json=invalid_status, headers=self.headers_a)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json["error"]["code"], "PI_CAPTURE_STATUS_INVALID")

        invalid_reason = self.pi_payload(
            session_id="pi-invalid-termination-reason",
            seqs=(0,),
            capture_status="interrupted",
            termination_reason="power_loss",
        )
        response = self.client.post("/api/device/sync/session", json=invalid_reason, headers=self.headers_a)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json["error"]["code"], "PI_TERMINATION_REASON_INVALID")

        mismatch = self.pi_payload(
            session_id="pi-mismatched-termination",
            seqs=(0,),
            capture_status="completed",
            termination_reason="unclean_runtime_shutdown",
        )
        response = self.client.post("/api/device/sync/session", json=mismatch, headers=self.headers_a)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json["error"]["code"], "PI_CAPTURE_METADATA_MISMATCH")

        incomplete = self.pi_payload(session_id="pi-incomplete-termination", seqs=(0,), capture_status="interrupted")
        response = self.client.post("/api/device/sync/session", json=incomplete, headers=self.headers_a)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json["error"]["code"], "PI_CAPTURE_METADATA_INCOMPLETE")

        legacy = self.client.post(
            "/api/device/sync/session",
            json=self.pi_payload(session_id="pi-legacy-render", seqs=(0,), ended=True),
            headers=self.headers_a,
        )
        self.assertEqual(legacy.status_code, 200)
        interrupted_payload = self.pi_payload(
            session_id="pi-interrupted-render",
            seqs=(0,),
            ended=True,
            capture_status="interrupted",
            termination_reason="unclean_runtime_shutdown",
            analysis=self.pi_analysis(run_id="pi-run-interrupted-render", overall_status="limited_data"),
        )
        interrupted_response = self.client.post(
            "/api/device/sync/session",
            json=interrupted_payload,
            headers=self.headers_a,
        )
        self.assertEqual(interrupted_response.status_code, 200)
        legacy_session = RideSession.query.filter_by(device_id=self.device_a.id, session_id="pi-legacy-render").one()
        interrupted_session = RideSession.query.filter_by(device_id=self.device_a.id, session_id="pi-interrupted-render").one()

        self.login()
        legacy_page = self.client.get(f"/sessions/{legacy_session.id}")
        self.assertEqual(legacy_page.status_code, 200)
        self.assertIn("Capture termination unknown", legacy_page.get_data(as_text=True))
        self.assertIn("No capture termination metadata was supplied for this historical session.", legacy_page.get_data(as_text=True))

        interrupted_page = self.client.get(f"/sessions/{interrupted_session.id}")
        self.assertEqual(interrupted_page.status_code, 200)
        interrupted_html = interrupted_page.get_data(as_text=True)
        self.assertIn("Capture interrupted", interrupted_html)
        self.assertIn(
            "This capture was interrupted because the previous runtime ended uncleanly. The displayed data is the portion that was saved successfully.",
            interrupted_html,
        )
        self.assertIn("Limited data", interrupted_html)
        self.assertNotIn("electrical power loss", interrupted_html.lower())
        self.assertNotIn("ignition off", interrupted_html.lower())

        summary = self.client.get(f"/sessions/{interrupted_session.id}.json")
        self.assertEqual(summary.status_code, 200)
        self.assertEqual(summary.json["session"]["capture_status"], "interrupted")
        self.assertEqual(summary.json["session"]["termination_reason"], "unclean_runtime_shutdown")
        self.assertEqual(summary.json["session"]["capture"]["state"], "interrupted")

        translations = self.client.get("/static/js/i18n.js")
        self.assertEqual(translations.status_code, 200)
        translation_source = translations.get_data(as_text=True)
        translations.close()
        self.assertIn('"Capture interrupted": "Phiên bị gián đoạn"', translation_source)
        self.assertIn(
            '"This capture was interrupted because the previous runtime ended uncleanly. The displayed data is the portion that was saved successfully.": "Phiên thu thập bị gián đoạn do lần chạy trước kết thúc không bình thường. Dữ liệu hiển thị là phần đã được lưu thành công."',
            translation_source,
        )

    def test_pi_sync_rejects_invalid_provenance_nonfinite_and_keeps_device_scope(self):
        invalid_schema = self.pi_payload(session_id="pi-invalid", seqs=(0,))
        invalid_schema["telemetry_schema_version"] = "canonical-telemetry-v1"
        response = self.client.post("/api/device/sync/session", json=invalid_schema, headers=self.headers_a)
        self.assertEqual(response.status_code, 422)

        invalid_decoder = self.pi_payload(session_id="pi-invalid-decoder", seqs=(0,))
        invalid_decoder["decoder_id"] = "other_decoder"
        response = self.client.post("/api/device/sync/session", json=invalid_decoder, headers=self.headers_a)
        self.assertEqual(response.status_code, 422)

        nonfinite = self.client.post(
            "/api/device/sync/session",
            data=(
                '{"session_id":"pi-nan","telemetry_schema_version":"canonical-telemetry-v2",'
                '"ecu_profile_id":"honda_keihin_71_17","decoder_id":"honda_keihin_71_17",'
                '"decoder_version":"1.0.0","records":[{"seq":0,"timestamp_ms":0,"rpm":NaN,'
                '"tps_voltage":0.7,"tps_raw":37,"battery_voltage":13.5,"iat_c":31,"ect_c":75,'
                '"frame_valid":true,"checksum_valid":true}]}'
            ),
            headers=self.headers_a,
            content_type="application/json",
        )
        self.assertEqual(nonfinite.status_code, 422)

        first = self.client.post("/api/device/sync/session", json=self.pi_payload(session_id="shared-pi", seqs=(0,)), headers=self.headers_a)
        self.assertEqual(first.status_code, 200)
        other_payload = self.pi_payload(session_id="shared-pi", seqs=(0,), records=[self.pi_record(0, rpm=3500)])
        other = self.client.post("/api/device/sync/session", json=other_payload, headers=self.headers_b)
        self.assertEqual(other.status_code, 200)
        self.assertEqual(RideSession.query.filter_by(session_id="shared-pi").count(), 2)
        self.assertEqual(TelemetryRecord.query.filter_by(session_id="shared-pi").count(), 2)

    def test_pi_sync_valid_v2_fixture_is_accepted(self):
        payload = json.loads(Path("tests/fixtures/pi_sync_valid_v2.json").read_text(encoding="utf-8"))
        response = self.client.post("/api/device/sync/session", json=payload, headers=self.headers_a)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["inserted_count"], 3)
        self.assertEqual(response.json["duplicate_count"], 0)
        self.assertEqual(response.json["analysis"]["state"], "imported")
        self.assertEqual(TelemetryRecord.query.filter_by(session_id=payload["session_id"]).count(), 3)
        self.assertEqual(AnalysisResult.query.filter_by(analysis_run_id=payload["analysis"]["analysis_run_id"]).count(), 1)

    def test_pi_analysis_statuses_are_accepted_preserved_and_displayed(self):
        expected_display = {
            "ok": "Normal",
            "monitor": "Monitor",
            "attention": "Requires attention",
            "limited_data": "Limited data",
            "no_windows": "Analysis unavailable",
            "model_unavailable": "Analysis unavailable",
            "not_scored": "Analysis unavailable",
        }
        self.login()
        for index, (status, display) in enumerate(expected_display.items()):
            payload = self.pi_payload(
                session_id=f"pi-status-{status}",
                seqs=(index,),
                ended=True,
                analysis=self.pi_analysis(
                    run_id=f"pi-run-{status}",
                    overall_status=status,
                    evidence_window_count=3,
                    minimum_windows_for_status=10,
                    evidence_sufficient=status not in {"limited_data", "no_windows", "model_unavailable", "not_scored"},
                ),
            )
            response = self.client.post("/api/device/sync/session", json=payload, headers=self.headers_a)
            self.assertEqual(response.status_code, 200)

            result = AnalysisResult.query.filter_by(analysis_run_id=f"pi-run-{status}").one()
            self.assertEqual(result.overall_status, status)

            analysis_api = self.client.get(f"/api/sessions/{result.ride_session_id}/analysis")
            self.assertEqual(analysis_api.status_code, 200)
            self.assertEqual(analysis_api.json["analysis"]["external_overall_status"], status)
            self.assertEqual(analysis_api.json["analysis"]["result"], display)

    def test_post_ride_normal_signals_create_no_maintenance_findings(self):
        self.seed_v2_baseline("normal-baseline")
        session = self.post_pi_v2_session("normal-target", overall_status="ok")

        analysis = analysis_snapshot(session, session_records(session))

        self.assertEqual(analysis["baseline_status"], "established")
        self.assertEqual(analysis["overall_recommendation_severity"], "normal")
        self.assertEqual(analysis["findings"], [])

    def test_post_ride_model_anomaly_with_weak_evidence_is_monitor(self):
        self.seed_v2_baseline("weak-baseline")
        session = self.post_pi_v2_session(
            "weak-model-target",
            overall_status="monitor",
            anomaly_ratio=0.05,
            anomaly_window_count=1,
        )

        analysis = analysis_snapshot(session, session_records(session))

        self.assertEqual(analysis["baseline_status"], "established")
        self.assertEqual(analysis["overall_recommendation_severity"], "monitor")
        self.assertEqual(analysis["findings"], [])

    def test_post_ride_ect_deviation_recommends_cooling_check(self):
        self.seed_v2_baseline("ect-baseline")
        session = self.post_pi_v2_session("ect-high-target", ect_c=96.0, overall_status="ok")

        analysis = analysis_snapshot(session, session_records(session))
        finding = next(item for item in analysis["findings"] if item["signal"] == "ect_c")

        self.assertIn(finding["severity"], {"attention", "high"})
        self.assertIn(finding["recommendation_code"], {"ECT_CHECK_COOLING", "ECT_HIGH_CHECK_COOLING"})
        self.assertEqual(finding["direction"], "high")
        self.assertGreater(finding["evidence"]["baseline_p95"], 0)

    def test_post_ride_battery_abnormal_uses_controlled_rule_without_failure_diagnosis(self):
        session = self.post_pi_v2_session("battery-low-target", battery_voltage=10.9, overall_status="ok")

        analysis = analysis_snapshot(session, session_records(session))
        finding = next(item for item in analysis["findings"] if item["signal"] == "battery_voltage")

        self.assertEqual(analysis["baseline_status"], "insufficient_data")
        self.assertEqual(finding["severity"], "attention")
        self.assertEqual(finding["recommendation_code"], "BATTERY_CHECK_CHARGING")
        self.assertNotIn("bi hong", finding["recommendation"].lower())
        self.assertNotIn("phai thay", finding["recommendation"].lower())

    def test_post_ride_physical_guardrail_alone_does_not_create_high_severity(self):
        session = self.post_pi_v2_session("ect-physical-only-target", ect_c=114.0, overall_status="ok")

        analysis = analysis_snapshot(session, session_records(session))
        finding = next(item for item in analysis["findings"] if item["signal"] == "ect_c")

        self.assertEqual(analysis["baseline_status"], "insufficient_data")
        self.assertEqual(finding["severity"], "attention")
        self.assertEqual(finding["recommendation_code"], "ECT_CHECK_COOLING")
        self.assertEqual(finding["evidence"]["physical_rule"], "existing_local_screening_ect_guardrail")

    def test_post_ride_iat_baseline_deviation_creates_iat_finding(self):
        self.seed_v2_baseline("iat-baseline")
        session = self.post_pi_v2_session("iat-high-target", iat_c=45.0, overall_status="ok")

        analysis = analysis_snapshot(session, session_records(session))
        finding = next(item for item in analysis["findings"] if item["signal"] == "iat_c")

        self.assertIn(finding["severity"], {"attention", "high"})
        self.assertEqual(finding["recommendation_code"], "IAT_CHECK_INTAKE")
        self.assertEqual(finding["direction"], "high")

    def test_post_ride_limited_baseline_does_not_fake_personalized_comparison(self):
        session = self.post_pi_v2_session(
            "limited-baseline-target",
            overall_status="monitor",
            anomaly_ratio=0.05,
            anomaly_window_count=1,
        )

        analysis = analysis_snapshot(session, session_records(session))

        self.assertEqual(analysis["baseline_status"], "insufficient_data")
        self.assertIn("Đường cơ sở đang được xây dựng", analysis["baseline_message"])
        self.assertEqual(analysis["findings"], [])

    def test_post_ride_baseline_excludes_strong_anomalous_sessions(self):
        self.seed_v2_baseline("clean-baseline")
        self.post_pi_v2_session(
            "contaminated-high-session",
            ect_c=140.0,
            overall_status="high_anomaly",
            anomaly_ratio=0.8,
            anomaly_window_count=10,
        )
        session = self.post_pi_v2_session("after-contamination-target", ect_c=96.0, overall_status="ok")

        analysis = analysis_snapshot(session, session_records(session))
        ect_baseline = analysis["baseline"]["signals"]["ect_c"]
        finding = next(item for item in analysis["findings"] if item["signal"] == "ect_c")

        self.assertLess(ect_baseline["p95"], 80)
        self.assertIn(finding["severity"], {"attention", "high"})

    def test_post_ride_baseline_does_not_use_future_sessions_for_old_ride(self):
        old_session = self.post_pi_v2_session("old-target", overall_status="ok")
        old_session.started_at = utc_now() - timedelta(days=5)
        old_session.last_record_at = old_session.started_at
        db.session.commit()
        self.seed_v2_baseline("future-baseline")

        analysis = analysis_snapshot(old_session, session_records(old_session))

        self.assertEqual(analysis["baseline_status"], "insufficient_data")
        self.assertEqual(analysis["baseline"]["session_count"], 0)

    def test_post_ride_multiple_signal_findings_are_ranked_deterministically(self):
        self.seed_v2_baseline("multi-baseline")
        session = self.post_pi_v2_session(
            "multi-signal-target",
            battery_voltage=10.9,
            ect_c=96.0,
            iat_c=45.0,
            overall_status="ok",
        )

        analysis = analysis_snapshot(session, session_records(session))
        severities = [item["severity"] for item in analysis["findings"]]
        ranks = [{"normal": 0, "monitor": 1, "attention": 2, "high": 3}[severity] for severity in severities]

        self.assertLessEqual(len(analysis["findings"]), 3)
        self.assertEqual(ranks, sorted(ranks, reverse=True))
        self.assertEqual({item["signal"] for item in analysis["findings"]}, {"battery_voltage", "ect_c", "iat_c"})

    def test_out_of_order_duplicates_and_device_isolation(self):
        payload = self.batch(session_id="shared", seqs=(5, 4, 4))
        response = self.client.post("/api/logs/upload", json=payload, headers=self.headers_a)
        self.assertEqual(response.json["inserted_count"], 2)
        self.assertEqual(response.json["duplicate_count"], 1)
        self.assertEqual(response.json["accepted_sequences"]["minimum"], 4)
        self.assertEqual(response.json["accepted_sequences"]["contiguous_until"], 5)

        other = self.client.post("/api/logs/upload", json=self.batch(session_id="shared", seqs=(4,)), headers=self.headers_b)
        self.assertEqual(other.json["inserted_count"], 1)
        self.assertEqual(TelemetryRecord.query.count(), 3)

    def test_batch_validation_rejects_oversized_and_malformed_values(self):
        oversized = self.batch(seqs=tuple(range(11)))
        self.assertEqual(self.client.post("/api/logs/upload", json=oversized, headers=self.headers_a).status_code, 422)

        malformed = {"session_id": "bad-1", "records": [{"seq": 1, "rpm": "fast"}]}
        response = self.client.post("/api/logs/upload", json=malformed, headers=self.headers_a)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json["error"]["code"], "VALIDATION_ERROR")
        self.assertIn("records[0]", response.json["error"]["message"])

    def test_durable_upload_validates_preserves_and_exports_raw_frames(self):
        payload = {
            "session_id": "raw-preserve",
            "session_ended": True,
            "records": [
                {"seq": 1, "device_time_ms": 100, "raw_frame": " 02187117 "},
                {
                    "seq": 2,
                    "device_time_ms": 200,
                    "raw_frame": {
                        "hex": "0A0B",
                        "frame_type": "sample",
                        "checksum_ok": False,
                        "valid": False,
                        "decode_status": "checksum_error",
                    },
                },
            ],
        }
        response = self.client.post("/api/logs/upload", json=payload, headers=self.headers_a)
        self.assertEqual(response.status_code, 200)
        records = TelemetryRecord.query.filter_by(session_id="raw-preserve").order_by(TelemetryRecord.seq).all()
        self.assertEqual(records[0].raw_frame, "02187117")
        self.assertEqual(records[1].raw_frame["hex"], "0A0B")
        self.assertEqual(records[1].raw_frame["checksum_ok"], False)

        self.login()
        session = RideSession.query.filter_by(device_id=self.device_a.id, session_id="raw-preserve").one()
        csv_response = self.client.get(f"/sessions/{session.id}.csv")
        self.assertEqual(csv_response.status_code, 200)
        rows = list(csv.DictReader(io.StringIO(csv_response.get_data(as_text=True))))
        self.assertEqual(json.loads(rows[0]["raw_frame"]), "02187117")
        self.assertEqual(json.loads(rows[1]["raw_frame"])["decode_status"], "checksum_error")

    def test_esp_upload_stores_raw_without_local_decode(self):
        payload = {
            "session_id": "raw-owner",
            "session_ended": True,
            "sample_interval_ms": 100,
            "records": [self.canonical_raw_record(seq=0)],
        }
        response = self.client.post("/api/logs/upload", json=payload, headers=self.headers_a)
        self.assertEqual(response.status_code, 200)

        record = TelemetryRecord.query.filter_by(session_id="raw-owner").one()
        self.assertIsNone(record.rpm)
        self.assertIsNone(record.tps_voltage)
        self.assertIsNone(record.tps_raw_candidate)
        self.assertIsNone(record.battery_voltage)
        self.assertIsNone(record.iat_c)
        self.assertIsNone(record.ect_c_candidate)
        self.assertIsNone(record.map_raw)
        self.assertTrue(record.frame_valid)
        self.assertTrue(record.checksum_valid)
        self.assertFalse(record.decoder_valid)
        self.assertEqual(record.raw_frame, self.VALID_RAW_FRAME)
        self.assertEqual(record.raw_hex, self.VALID_RAW_FRAME_COMPACT)
        self.assertEqual(record.raw_representation, "legacy29_ff5")

        session = RideSession.query.filter_by(device_id=self.device_a.id, session_id="raw-owner").one()
        self.assertEqual(session.capture_status, "completed")
        self.assertEqual(session.analysis_status, "not_requested")
        self.assertEqual(session.raw_frame_count, 1)
        self.assertEqual(session.valid_frame_count, 1)
        self.assertEqual(session.checksum_error_count, 0)
        self.assertEqual(session.sample_interval_ms, 100)
        self.assertEqual(session.source_type, "esp_raw")
        self.assertEqual(session.raw_representation, "legacy29_ff5")
        self.assertEqual(session.transport_profile_id, "honda_keihin_legacy_29")
        self.assertIsNone(session.telemetry_schema_version)
        self.assertIsNone(session.decoder_id)
        self.assertEqual(EcuProfile.query.count(), 0)
        self.assertEqual(DecoderVersion.query.count(), 0)
        self.assertEqual(RawArtifact.query.filter_by(ride_session_id=session.id).count(), 1)

    def test_live_preview_preserves_raw_but_does_not_decode_it(self):
        payload = self.live_payload(
            raw_frame=self.VALID_RAW_FRAME_COMPACT,
            rpm=9999,
            battery=42,
            tps_voltage=9.9,
        )
        response = self.client.post("/api/telemetry/live", json=payload, headers=self.headers_a)
        self.assertEqual(response.status_code, 200)

        sample = self.live_backend.get_latest("xiao-a")
        self.assertEqual(sample["rpm"], 9999)
        self.assertEqual(sample["battery"], 42)
        self.assertEqual(sample["tps_voltage"], 9.9)
        self.assertEqual(sample["raw_frame"], self.VALID_RAW_FRAME_COMPACT)
        self.assertNotIn("battery_voltage", sample)
        self.assertNotIn("ect_c_candidate", sample)
        self.assertNotIn("map_raw", sample)
        self.assertNotIn("decoder_id", sample)
        self.assertEqual(TelemetryRecord.query.count(), 0)

    def test_analyzer_failure_preserves_captured_session(self):
        self.app.config["ML_API_BASE_URL"] = "http://analyzer.example"
        with patch(
            "integrations.analyzer_client.AnalyzerClient.decode_analyze_raw_session",
            side_effect=AnalyzerClientError("network unavailable"),
        ):
            response = self.client.post(
                "/api/logs/upload",
                json={
                    "session_id": "analyzer-down",
                    "session_ended": True,
                    "records": [self.canonical_raw_record(seq=0)],
                },
                headers=self.headers_a,
            )
        self.assertEqual(response.status_code, 200)
        session = RideSession.query.filter_by(session_id="analyzer-down").one()
        self.assertEqual(session.capture_status, "completed")
        self.assertEqual(session.analysis_status, "failed")
        self.assertEqual(TelemetryRecord.query.filter_by(ride_session_id=session.id).count(), 1)
        self.assertEqual(RawArtifact.query.filter_by(ride_session_id=session.id).count(), 1)

    def test_analyzer_raw_success_and_retry_persist_canonical_v2(self):
        self.app.config["ML_API_BASE_URL"] = "http://analyzer.example"
        posted_payloads = []

        def fake_analyze(payload):
            posted_payloads.append(payload)
            run_number = len(posted_payloads)
            return self.raw_analyzer_response(payload, run_id=f"run-{run_number}")

        with patch("integrations.analyzer_client.AnalyzerClient.decode_analyze_raw_session", side_effect=fake_analyze):
            response = self.client.post(
                "/api/logs/upload",
                json={
                    "session_id": "analyzer-success",
                    "session_ended": True,
                    "records": [self.canonical_raw_record(seq=0), self.canonical_raw_record(seq=1)],
                },
                headers=self.headers_a,
            )
            self.assertEqual(response.status_code, 200)
            session = RideSession.query.filter_by(session_id="analyzer-success").one()
            self.assertEqual(session.analysis_status, "completed")
            self.assertEqual(session.analysis_run_id, "run-1")
            self.assertEqual(TelemetryRecord.query.filter_by(ride_session_id=session.id).count(), 2)

            self.login()
            rerun = self.client.post(f"/api/sessions/{session.id}/analysis/re-run")
            self.assertEqual(rerun.status_code, 200)

        session = RideSession.query.filter_by(session_id="analyzer-success").one()
        self.assertEqual(session.analysis_run_id, "run-2")
        self.assertEqual(session.telemetry_schema_version, "canonical-telemetry-v2")
        self.assertEqual(session.ecu_profile_id, "honda_keihin_71_17")
        self.assertEqual(session.decoder_id, "honda_keihin_71_17")
        self.assertEqual(session.decoder_version, "1.0.0")
        self.assertEqual(AnalysisResult.query.filter_by(ride_session_id=session.id).count(), 2)
        records = TelemetryRecord.query.filter_by(ride_session_id=session.id).order_by(TelemetryRecord.seq).all()
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0].rpm, 1500)
        self.assertEqual(records[0].tps_raw, 20)
        self.assertEqual(records[0].battery_voltage, 12.7)
        self.assertEqual(records[0].iat_c, 37)
        self.assertEqual(records[0].ect_c, 66)
        self.assertIsNone(records[0].map_raw)
        self.assertEqual(records[0].raw_frame, self.VALID_RAW_FRAME)
        self.assertEqual(posted_payloads[0]["raw_representation"], "legacy29_ff5")
        self.assertEqual(posted_payloads[0]["records"][0]["raw_hex"], self.VALID_RAW_FRAME_COMPACT)
        self.assertEqual(posted_payloads[1]["records"][1]["sequence"], 1)

    def test_analyzer_limited_data_status_persists_and_displays_neutral_result(self):
        self.app.config["ML_API_BASE_URL"] = "http://analyzer.example"

        def fake_analyze(payload):
            return self.raw_analyzer_response(
                payload,
                run_id="run-limited-data",
                health_score=42.0,
                window_count=3,
                anomaly_window_count=2,
                anomaly_ratio=2 / 3,
                overall_status="limited_data",
                evidence_window_count=3,
                minimum_windows_for_status=10,
                evidence_sufficient=False,
            )

        with patch("integrations.analyzer_client.AnalyzerClient.decode_analyze_raw_session", side_effect=fake_analyze):
            response = self.client.post(
                "/api/logs/upload",
                json={
                    "session_id": "analyzer-limited-data",
                    "session_ended": True,
                    "records": [self.canonical_raw_record(seq=0)],
                },
                headers=self.headers_a,
            )

        self.assertEqual(response.status_code, 200)
        session = RideSession.query.filter_by(session_id="analyzer-limited-data").one()
        result = AnalysisResult.query.filter_by(ride_session_id=session.id).one()
        self.assertEqual(result.overall_status, "limited_data")
        self.assertEqual(result.anomaly_window_count, 2)
        self.assertAlmostEqual(result.anomaly_ratio, 2 / 3)
        self.assertEqual(result.result_summary["evidence_window_count"], 3)
        self.assertEqual(result.result_summary["minimum_windows_for_status"], 10)
        self.assertFalse(result.result_summary["evidence_sufficient"])

        self.login()
        analysis_api = self.client.get(f"/api/sessions/{session.id}/analysis")
        self.assertEqual(analysis_api.status_code, 200)
        self.assertEqual(analysis_api.json["analysis"]["result"], "Limited data")
        self.assertEqual(analysis_api.json["analysis"]["external_overall_status"], "limited_data")

        sessions_page = self.client.get("/sessions").get_data(as_text=True)
        self.assertIn("Limited data", sessions_page)
        self.assertIn("badge limited-data", sessions_page)

    def test_analyzer_diagnostic_evidence_is_persisted_and_exposed_additively(self):
        self.app.config["ML_API_BASE_URL"] = "http://analyzer.example"
        diagnostic_evidence = {
            "schema_version": "diagnostic-evidence-v1",
            "analysis_version": "analysis-v2.4.0",
            "aggregate_state": {
                "evidence_state": "detector_disagreement",
                "coverage": "partial",
                "detector_disagreement": True,
            },
            "rca_v2": {
                "observations": [
                    {"observation": "coolant temperature pattern differed from the baseline", "support": "ect_c"},
                ],
                "symptoms": ["unusual warm-up trace"],
                "possible_causes": [],
                "recommended_checks": [
                    {"label": "Save for next service", "priority": "deferred"},
                    {"label": "Inspect coolant level", "priority": "priority"},
                    {"label": "Compare next ride", "priority": "relevant"},
                    {"label": "Compression test", "priority": "insufficient_evidence"},
                ],
            },
            "historical_evidence": {
                "recurrence": "recurrent observation",
                "trend": "possible trend",
                "historical_cutoff": "2026-09-30T00:00:00Z",
            },
            "detector_versions": {
                "contextual_ect": "detector-v1",
                "iforest": "iforest-v2",
            },
            "detectors": {
                "contextual_ect": {"status": "active", "score": 0.72, "detector_version": "detector-v1"},
                "history": {"status": "not_applicable", "detector_version": "history-v1"},
            },
            "provenance": {
                "generated_at": "2026-10-01T00:00:00Z",
                "historical_cutoff": "2026-09-30T00:00:00Z",
            },
            "maturity": "research",
        }

        def fake_analyze(payload):
            return self.raw_analyzer_response(
                payload,
                run_id="run-diagnostic-evidence",
                overall_status="ok",
                health_score=91.0,
                diagnostic_evidence=diagnostic_evidence,
            )

        with patch("integrations.analyzer_client.AnalyzerClient.decode_analyze_raw_session", side_effect=fake_analyze):
            response = self.client.post(
                "/api/logs/upload",
                json={
                    "session_id": "analyzer-diagnostic-evidence",
                    "session_ended": True,
                    "records": [self.canonical_raw_record(seq=0), self.canonical_raw_record(seq=1)],
                },
                headers=self.headers_a,
            )

        self.assertEqual(response.status_code, 200)
        session = RideSession.query.filter_by(session_id="analyzer-diagnostic-evidence").one()
        result = AnalysisResult.query.filter_by(ride_session_id=session.id).one()
        self.assertEqual(result.overall_status, "ok")
        self.assertEqual(result.health_score, 91.0)
        self.assertEqual(result.result_summary["diagnostic_evidence"], diagnostic_evidence)

        self.login()
        analysis_api = self.client.get(f"/api/sessions/{session.id}/analysis")
        self.assertEqual(analysis_api.status_code, 200)
        analysis = analysis_api.json["analysis"]
        self.assertEqual(analysis["result"], "Normal")
        self.assertEqual(analysis["health_score"], 91.0)
        self.assertEqual(analysis["diagnostic_evidence"], diagnostic_evidence)
        self.assertEqual(analysis["diagnostic_evidence_view"]["state"]["value"], "detector_disagreement")
        self.assertEqual(
            [item["priority"] for item in analysis["diagnostic_evidence_view"]["checks"]],
            ["priority", "relevant", "deferred", "insufficient_evidence"],
        )

        session_page = self.client.get(f"/sessions/{session.id}").get_data(as_text=True)
        self.assertIn("Additional diagnostic evidence", session_page)
        self.assertIn("Detector disagreement", session_page)
        self.assertIn("not_applicable", session_page)

    def test_malformed_analyzer_diagnostic_evidence_does_not_fail_analysis(self):
        self.app.config["ML_API_BASE_URL"] = "http://analyzer.example"

        def fake_analyze(payload):
            return self.raw_analyzer_response(
                payload,
                run_id="run-malformed-diagnostic-evidence",
                overall_status="minor_anomaly",
                diagnostic_evidence=["not", "an", "object"],
            )

        with patch("integrations.analyzer_client.AnalyzerClient.decode_analyze_raw_session", side_effect=fake_analyze):
            response = self.client.post(
                "/api/logs/upload",
                json={
                    "session_id": "analyzer-malformed-diagnostic-evidence",
                    "session_ended": True,
                    "records": [self.canonical_raw_record(seq=0)],
                },
                headers=self.headers_a,
            )

        self.assertEqual(response.status_code, 200)
        session = RideSession.query.filter_by(session_id="analyzer-malformed-diagnostic-evidence").one()
        result = AnalysisResult.query.filter_by(ride_session_id=session.id).one()
        self.assertEqual(result.overall_status, "minor_anomaly")
        self.assertNotIn("diagnostic_evidence", result.result_summary)
        self.assertIn("diagnostic_evidence ignored", result.result_summary["warnings"][-1])

        self.login()
        analysis_api = self.client.get(f"/api/sessions/{session.id}/analysis")
        self.assertEqual(analysis_api.status_code, 200)
        self.assertEqual(analysis_api.json["analysis"]["result"], "Minor anomaly")
        self.assertIsNone(analysis_api.json["analysis"]["diagnostic_evidence"])

    def test_analyzer_client_posts_to_raw_decode_analyze_endpoint(self):
        response = Mock()
        response.headers = {"content-type": "application/json"}
        response.json.return_value = {"canonical_session": {}, "analysis": {"analysis_run_id": "run-1"}}
        response.raise_for_status.return_value = None
        with patch("integrations.analyzer_client.requests.post", return_value=response) as post:
            result = AnalyzerClient(base_url="http://analyzer", timeout_seconds=1).decode_analyze_raw_session({"records": []})
        self.assertEqual(result["analysis"]["analysis_run_id"], "run-1")
        self.assertEqual(post.call_args.args[0], "http://analyzer/api/v1/raw/decode-analyze")

    def test_canonical_contract_fixture_matches_generated_shape(self):
        fixture = json.loads(Path("tests/fixtures/canonical_shmode_session_v1.json").read_text(encoding="utf-8"))
        self.assertEqual(fixture["telemetry_schema_version"], "canonical-telemetry-v1")
        self.assertEqual(fixture["decoder_id"], "honda_keihin_legacy_29")
        self.assertEqual(fixture["decoder_version"], "0.1.0")
        self.assertEqual(len(fixture["samples"]), 25)
        self.assertEqual([sample["sequence"] for sample in fixture["samples"]], list(range(25)))
        self.assertIn("ect_c_candidate", fixture["signal_definitions"])
        self.assertEqual(fixture["signal_definitions"]["map_raw"]["status"], "candidate")

    def test_durable_upload_rejects_malformed_raw_frame_hex(self):
        invalid_hex = self.batch(session_id="bad-hex", seqs=(1,))
        invalid_hex["records"][0]["raw_frame"] = "XYZ"
        response = self.client.post("/api/logs/upload", json=invalid_hex, headers=self.headers_a)
        self.assertEqual(response.status_code, 422)
        self.assertIn("raw_frame", response.json["error"]["message"])

        invalid_metadata = self.batch(session_id="bad-metadata-hex", seqs=(1,))
        invalid_metadata["records"][0]["raw_frame"] = {"hex": "ABC", "checksum_ok": False}
        response = self.client.post("/api/logs/upload", json=invalid_metadata, headers=self.headers_a)
        self.assertEqual(response.status_code, 422)
        self.assertIn("raw_frame.hex", response.json["error"]["message"])

        invalid_shape = self.batch(session_id="bad-shape", seqs=(1,))
        invalid_shape["records"][0]["raw_frame"] = [1, 2, 3]
        response = self.client.post("/api/logs/upload", json=invalid_shape, headers=self.headers_a)
        self.assertEqual(response.status_code, 422)

        missing_hex = self.batch(session_id="missing-hex", seqs=(1,))
        missing_hex["records"][0]["raw_frame"] = {"checksum_ok": True, "valid": True}
        response = self.client.post("/api/logs/upload", json=missing_hex, headers=self.headers_a)
        self.assertEqual(response.status_code, 422)
        self.assertIn("raw_frame metadata", response.json["error"]["message"])

        empty_hex = self.batch(session_id="empty-hex", seqs=(1,))
        empty_hex["records"][0]["raw_frame"] = " "
        response = self.client.post("/api/logs/upload", json=empty_hex, headers=self.headers_a)
        self.assertEqual(response.status_code, 422)

    def test_ownership_boundaries_and_admin_access(self):
        upload = self.client.post("/api/logs/upload", json=self.batch(session_id="private-b"), headers=self.headers_b)
        self.assertEqual(upload.status_code, 200)
        session_b = RideSession.query.filter_by(device_id=self.device_b.id).one()

        self.login("a@example.com")
        self.assertEqual(self.client.get(f"/sessions/{session_b.id}").status_code, 404)
        self.assertEqual(self.client.post(f"/api/devices/{self.device_b.id}/rotate-token").status_code, 404)
        self.assertEqual(self.client.post(f"/devices/{self.device_b.id}/disable").status_code, 404)

        self.client.post("/auth/logout")
        admin_login = self.client.post("/auth/login", json={"email": "admin@example.com", "password": "a-long-test-password"})
        self.assertEqual(admin_login.status_code, 200)
        self.assertEqual(self.client.get(f"/sessions/{session_b.id}").status_code, 200)
        self.assertEqual(self.client.post(f"/api/devices/{self.device_b.id}/rotate-token").status_code, 200)

    def test_live_post_authenticates_validates_caches_and_skips_durable_storage(self):
        self.assertEqual(self.client.post("/api/telemetry/live", json=self.live_payload()).status_code, 401)
        wrong = {"X-Device-ID": "xiao-a", "Authorization": "Bearer wrong"}
        self.assertEqual(self.client.post("/api/telemetry/live", json=self.live_payload(), headers=wrong).status_code, 401)

        response = self.client.post("/api/telemetry/live", json=self.live_payload(), headers=self.headers_a)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["ok"], True)
        self.assertEqual(response.json["device_id"], "xiao-a")
        self.assertIn("server_received_at", response.json)
        self.assertNotIn("accepted_sequences", response.json)
        self.assertNotIn("inserted_count", response.json)

        sample = self.live_backend.get_latest("xiao-a")
        self.assertIsNotNone(sample)
        self.assertEqual(sample["device_id"], "xiao-a")
        self.assertEqual(sample["raw_frame"], "02187117")
        self.assertIn("live_expires_at", sample)
        self.assertIsNone(db.session.get(Device, self.device_a.id).last_seen_at)
        self.assertEqual(TelemetryRecord.query.count(), 0)
        self.assertEqual(RideSession.query.count(), 0)
        self.assertEqual(SyncBatch.query.count(), 0)

        self.device_b.is_active = False
        db.session.commit()
        inactive = self.client.post("/api/telemetry/live", json=self.live_payload(), headers=self.headers_b)
        self.assertEqual(inactive.status_code, 403)

    def test_live_post_rejects_malformed_nonfinite_oversized_and_large_requests(self):
        malformed = self.client.post(
            "/api/telemetry/live",
            data="{",
            headers=self.headers_a,
            content_type="application/json",
        )
        self.assertEqual(malformed.status_code, 400)

        nonfinite = self.client.post(
            "/api/telemetry/live",
            data='{"session_id":"live","seq":1,"device_time_ms":1,"rpm":NaN}',
            headers=self.headers_a,
            content_type="application/json",
        )
        self.assertEqual(nonfinite.status_code, 422)

        oversized_raw = self.client.post(
            "/api/telemetry/live",
            json=self.live_payload(raw_frame="AA" * 129),
            headers=self.headers_a,
        )
        self.assertEqual(oversized_raw.status_code, 422)

        unknown = self.client.post(
            "/api/telemetry/live",
            json=self.live_payload(device_id="spoofed"),
            headers=self.headers_a,
        )
        self.assertEqual(unknown.status_code, 422)

        self.app.config["LIVE_MAX_REQUEST_BYTES"] = 64
        too_large = self.client.post("/api/telemetry/live", json=self.live_payload(seq=2), headers=self.headers_a)
        self.assertEqual(too_large.status_code, 413)

    def test_live_post_rate_limiting_returns_429(self):
        self.app.config["LIVE_RATE_LIMIT_PER_SECOND"] = 1
        self.app.config["LIVE_RATE_LIMIT_BURST"] = 2
        self.assertEqual(self.client.post("/api/telemetry/live", json=self.live_payload(seq=1), headers=self.headers_a).status_code, 200)
        self.assertEqual(self.client.post("/api/telemetry/live", json=self.live_payload(seq=2), headers=self.headers_a).status_code, 200)
        limited = self.client.post("/api/telemetry/live", json=self.live_payload(seq=3), headers=self.headers_a)
        self.assertEqual(limited.status_code, 429)
        self.assertEqual(limited.headers["Retry-After"], "1")

    def test_live_latest_authorization_status_and_no_store(self):
        self.assertEqual(self.client.get("/api/devices/xiao-a/live/latest").status_code, 401)
        self.login()

        empty = self.client.get("/api/devices/xiao-a/live/latest")
        self.assertEqual(empty.status_code, 200)
        self.assertEqual(empty.headers["Cache-Control"], "no-store")
        self.assertEqual(empty.json, {"ok": True, "online": False, "sample": None})

        self.client.post("/api/telemetry/live", json=self.live_payload(), headers=self.headers_a)
        active = self.client.get("/api/devices/xiao-a/live/latest")
        self.assertEqual(active.status_code, 200)
        self.assertEqual(active.json["online"], True)
        self.assertEqual(active.json["sample"]["device_id"], "xiao-a")

        expired = {
            "device_id": "xiao-a",
            "server_received_at": isoformat_z(utc_now() - timedelta(seconds=30)),
            "live_expires_at": isoformat_z(utc_now() - timedelta(seconds=15)),
            "session_id": "expired",
            "seq": 99,
            "device_time_ms": 99,
        }
        self.live_backend.set_latest("xiao-a", expired, ttl_seconds=15)
        stale = self.client.get("/api/devices/xiao-a/live/latest")
        self.assertEqual(stale.status_code, 200)
        self.assertEqual(stale.json, {"ok": True, "online": False, "sample": None})

    def test_live_browser_authorization_and_admin_access(self):
        self.client.post("/api/telemetry/live", json=self.live_payload(session_id="private-b"), headers=self.headers_b)
        self.assertEqual(self.client.get("/api/devices/xiao-a/live/stream").status_code, 401)

        self.login("a@example.com")
        self.assertEqual(self.client.get("/api/devices/xiao-b/live/latest").status_code, 404)
        self.assertEqual(self.client.get("/api/devices/xiao-b/live/stream").status_code, 404)
        self.assertEqual(self.client.get("/devices/xiao-b/live").status_code, 404)

        self.client.post("/auth/logout")
        self.login("admin@example.com")
        latest = self.client.get("/api/devices/xiao-b/live/latest")
        self.assertEqual(latest.status_code, 200)
        self.assertTrue(latest.json["online"])
        stream = self.client.get("/api/devices/xiao-b/live/stream", buffered=False)
        self.assertEqual(stream.status_code, 200)
        stream.close()

    def test_live_sse_stream_heartbeats_filters_and_cleans_up(self):
        self.login()
        response = self.client.get("/api/devices/xiao-a/live/stream", buffered=False)
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/event-stream", response.content_type)
        self.assertEqual(response.headers["Cache-Control"], "no-cache, no-store")
        self.assertEqual(response.headers["X-Accel-Buffering"], "no")

        stream = iter(response.response)
        self.assertEqual(next(stream).decode(), ": connected\n\n")
        self.assertEqual(next(stream).decode(), ": heartbeat\n\n")
        self.assertEqual(self.live_backend.subscriber_count("xiao-a"), 1)

        self.client.post("/api/telemetry/live", json=self.live_payload(seq=10), headers=self.headers_b)
        self.assertEqual(next(stream).decode(), ": heartbeat\n\n")

        self.client.post("/api/telemetry/live", json=self.live_payload(seq=11), headers=self.headers_a)
        event = next(stream).decode()
        self.assertIn("event: telemetry", event)
        self.assertIn('"device_id":"xiao-a"', event)
        self.assertIn('"seq":11', event)

        response.close()
        self.assertEqual(self.live_backend.subscriber_count("xiao-a"), 0)

    def test_live_preview_does_not_change_normal_log_upload_behavior(self):
        self.assertEqual(self.client.post("/api/telemetry/live", json=self.live_payload(seq=1), headers=self.headers_a).status_code, 200)
        upload = self.client.post("/api/logs/upload", json=self.batch(session_id="durable-after-live", seqs=(1, 2)), headers=self.headers_a)
        self.assertEqual(upload.status_code, 200)
        self.assertEqual(upload.json["inserted_count"], 2)
        self.assertIn("accepted_sequences", upload.json)
        self.assertEqual(TelemetryRecord.query.count(), 2)
        self.assertEqual(RideSession.query.count(), 1)
        self.assertEqual(SyncBatch.query.count(), 1)

    def test_device_control_poll_authenticates_and_registers_runtime(self):
        payload = {"boot_id": "boot-a", "live_mode": False}
        self.assertEqual(self.client.post("/api/device/control/poll", json=payload).status_code, 401)
        wrong = {"X-Device-ID": "xiao-a", "Authorization": "Bearer wrong"}
        self.assertEqual(self.client.post("/api/device/control/poll", json=payload, headers=wrong).status_code, 401)

        self.device_b.is_active = False
        db.session.commit()
        self.assertEqual(self.client.post("/api/device/control/poll", json=payload, headers=self.headers_b).status_code, 403)

        response = self.control_poll(boot_id="boot-a", live_mode=False, firmware_version="2.0.0", uptime_ms=1234)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["ok"], True)
        self.assertIsNone(response.json["command"])
        runtime = DeviceControlRuntime.query.filter_by(device_id=self.device_a.id).one()
        self.assertEqual(runtime.current_boot_id, "boot-a")
        self.assertFalse(runtime.reported_live_mode)
        self.assertEqual(runtime.firmware_version, "2.0.0")
        self.assertEqual(runtime.uptime_ms, 1234)

    def test_live_mode_enable_disable_command_lifecycle_and_ack_idempotency(self):
        self.assertEqual(self.control_poll("boot-a", False).status_code, 200)
        self.login()

        enable = self.live_control_action("enable")
        self.assertEqual(enable.status_code, 202)
        command = enable.json["command"]
        self.assertEqual(command["boot_id"], "boot-a")
        self.assertEqual(command["type"], "ENABLE_LIVE_MODE")

        delivered = self.control_poll("boot-a", False)
        self.assertEqual(delivered.json["command"], command)
        redelivered = self.control_poll("boot-a", False)
        self.assertEqual(redelivered.json["command"], command)
        stored = DeviceControlCommand.query.filter_by(command_id=command["command_id"]).one()
        self.assertEqual(stored.status, "delivered")

        ack = self.control_ack(command["command_id"], boot_id="boot-a", live_mode=True)
        self.assertEqual(ack.status_code, 200)
        self.assertEqual(ack.json["status"], "applied")
        self.assertTrue(ack.json["live_mode"])
        repeated_ack = self.control_ack(command["command_id"], boot_id="boot-a", live_mode=True)
        self.assertEqual(repeated_ack.status_code, 200)
        self.assertEqual(repeated_ack.json["status"], "applied")

        state = self.client.get("/api/devices/xiao-a/live/control")
        self.assertEqual(state.status_code, 200)
        self.assertEqual(state.json["control"]["state"], "on")

        disable = self.live_control_action("disable")
        self.assertEqual(disable.status_code, 202)
        disable_command = disable.json["command"]
        self.assertEqual(disable_command["boot_id"], "boot-a")
        self.assertEqual(disable_command["type"], "DISABLE_LIVE_MODE")
        self.assertEqual(self.control_poll("boot-a", True).json["command"], disable_command)
        disabled_ack = self.control_ack(disable_command["command_id"], boot_id="boot-a", live_mode=False)
        self.assertEqual(disabled_ack.status_code, 200)
        self.assertFalse(disabled_ack.json["live_mode"])
        state = self.client.get("/api/devices/xiao-a/live/control")
        self.assertEqual(state.json["control"]["state"], "off")

    def test_live_mode_boot_change_never_delivers_stale_enable_command(self):
        self.assertEqual(self.control_poll("boot-a", False).status_code, 200)
        self.login()
        enable = self.live_control_action("enable")
        command = enable.json["command"]
        self.assertEqual(self.control_poll("boot-a", False).json["command"], command)
        self.assertEqual(self.control_ack(command["command_id"], boot_id="boot-a", live_mode=True).status_code, 200)

        runtime = DeviceControlRuntime.query.filter_by(device_id=self.device_a.id).one()
        runtime.last_control_poll_at = utc_now() - timedelta(seconds=20)
        db.session.commit()

        boot_b = self.control_poll("boot-b", False)
        self.assertEqual(boot_b.status_code, 200)
        self.assertIsNone(boot_b.json["command"])
        runtime = DeviceControlRuntime.query.filter_by(device_id=self.device_a.id).one()
        self.assertEqual(runtime.current_boot_id, "boot-b")
        self.assertFalse(runtime.reported_live_mode)

        stale_ack = self.control_ack(command["command_id"], boot_id="boot-a", live_mode=True)
        self.assertEqual(stale_ack.status_code, 409)
        runtime = DeviceControlRuntime.query.filter_by(device_id=self.device_a.id).one()
        self.assertEqual(runtime.current_boot_id, "boot-b")
        self.assertFalse(runtime.reported_live_mode)

    def test_pending_boot_a_command_is_marked_stale_on_boot_b(self):
        self.assertEqual(self.control_poll("boot-a", False).status_code, 200)
        self.login()
        enable = self.live_control_action("enable")
        command_id = enable.json["command"]["command_id"]

        boot_b = self.control_poll("boot-b", False)
        self.assertEqual(boot_b.status_code, 200)
        self.assertIsNone(boot_b.json["command"])
        old_command = DeviceControlCommand.query.filter_by(command_id=command_id).one()
        self.assertEqual(old_command.boot_id, "boot-a")
        self.assertEqual(old_command.status, "stale")
        runtime = DeviceControlRuntime.query.filter_by(device_id=self.device_a.id).one()
        self.assertEqual(runtime.current_boot_id, "boot-b")
        self.assertFalse(runtime.reported_live_mode)

    def test_dashboard_live_control_authorization_offline_coalescing_and_latest_intent(self):
        self.login()
        offline = self.live_control_action("enable")
        self.assertEqual(offline.status_code, 409)
        self.assertEqual(offline.json["error"]["code"], "DEVICE_OFFLINE")

        self.assertEqual(self.control_poll("boot-a", False).status_code, 200)
        first = self.live_control_action("enable")
        second = self.live_control_action("enable")
        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 202)
        self.assertEqual(first.json["command"], second.json["command"])
        self.assertEqual(DeviceControlCommand.query.filter_by(device_id=self.device_a.id).count(), 1)

        disable = self.live_control_action("disable")
        self.assertEqual(disable.status_code, 202)
        self.assertEqual(disable.json["command"]["type"], "DISABLE_LIVE_MODE")
        commands = DeviceControlCommand.query.filter_by(device_id=self.device_a.id).order_by(DeviceControlCommand.created_at).all()
        self.assertEqual(len(commands), 2)
        self.assertEqual(commands[0].status, "superseded")
        self.assertEqual(commands[1].status, "pending")
        self.assertEqual(self.control_poll("boot-a", False).json["command"], disable.json["command"])

        self.assertEqual(self.live_control_action("enable", device_id="xiao-b").status_code, 404)

        runtime = DeviceControlRuntime.query.filter_by(device_id=self.device_a.id).one()
        runtime.last_control_poll_at = utc_now() - timedelta(seconds=20)
        runtime.reported_live_mode = True
        db.session.commit()
        stale = self.client.get("/api/devices/xiao-a/live/control")
        self.assertEqual(stale.json["control"]["state"], "offline")
        self.assertFalse(stale.json["control"]["reported_live_mode"])
        self.assertEqual(self.live_control_action("enable").status_code, 409)

    def test_device_control_payload_validation(self):
        bad_poll = self.client.post(
            "/api/device/control/poll",
            json={"boot_id": "bad boot", "live_mode": False},
            headers=self.headers_a,
        )
        self.assertEqual(bad_poll.status_code, 422)

        missing_live_mode = self.client.post(
            "/api/device/control/poll",
            json={"boot_id": "boot-a"},
            headers=self.headers_a,
        )
        self.assertEqual(missing_live_mode.status_code, 422)

        bad_ack = self.client.post(
            "/api/device/control/ack",
            json={"boot_id": "boot-a", "command_id": "cmd_1", "status": "done", "live_mode": True},
            headers=self.headers_a,
        )
        self.assertEqual(bad_ack.status_code, 422)

        self.assertEqual(self.control_poll("boot-a", False).status_code, 200)
        missing_command = self.control_ack("cmd_missing", boot_id="boot-a", live_mode=True)
        self.assertEqual(missing_command.status_code, 404)

    def test_overview_renders_summary_recent_diagnoses_and_events(self):
        self.upload_analysis_session("normal-overview", anomaly=False)
        self.upload_analysis_session("attention-overview", anomaly=True)
        self.login()

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Overview", html)
        self.assertIn("Total uploaded sessions", html)
        self.assertIn("Recent diagnoses", html)
        self.assertIn("attention-overview", html)
        self.assertIn("Grouped events", html)
        self.assertIn("Device synchronization", html)
        self.assertNotIn("Live Dashboard", html)
        self.assertIn(">Tool<", html)

    def test_overview_vehicle_condition_empty_normal_monitor_and_attention_states(self):
        self.login()
        empty = self.client.get("/")
        self.assertEqual(empty.status_code, 200)
        empty_html = empty.get_data(as_text=True)
        self.assertIn("Vehicle condition", empty_html)
        self.assertIn("Chưa đủ dữ liệu", empty_html)

        self.seed_v2_baseline("overview-normal-baseline")
        self.post_pi_v2_session("overview-normal-target", overall_status="ok")
        normal = self.client.get("/")
        normal_html = normal.get_data(as_text=True)
        self.assertIn("Vehicle condition", normal_html)
        self.assertIn(">Bình thường<", normal_html)
        self.assertIn("Chưa ghi nhận khuyến nghị bảo dưỡng đáng chú ý", normal_html)

        self.post_pi_v2_session(
            "overview-monitor-target",
            overall_status="monitor",
            anomaly_ratio=0.05,
            anomaly_window_count=1,
        )
        monitor = self.client.get("/")
        monitor_html = monitor.get_data(as_text=True)
        self.assertIn(">Theo dõi<", monitor_html)
        self.assertIn("lệch nhẹ đáng để theo dõi", monitor_html)

        self.post_pi_v2_session("overview-ect-target", ect_c=96.0, overall_status="ok")
        attention = self.client.get("/")
        attention_html = attention.get_data(as_text=True)
        self.assertTrue("Đáng chú ý" in attention_html or "Cao" in attention_html)
        self.assertIn("Nhiệt độ động cơ cao hơn mức thông thường của xe", attention_html)
        self.assertIn("View details", attention_html)

    def test_overview_condition_aggregates_signals_severity_recency_and_limit(self):
        def finding(signal, severity, label=None):
            return {
                "signal": signal,
                "signal_label": label or signal,
                "severity": severity,
                "direction": "high",
                "recommendation": f"{signal} recommendation",
            }

        summaries = [
            {"id": 1, "session_id": "new-ect-monitor", "overall_recommendation_severity": "monitor", "findings": [finding("ect_c", "monitor", "ECT")]},
            {"id": 2, "session_id": "battery-attention", "overall_recommendation_severity": "attention", "findings": [finding("battery_voltage", "attention", "Battery")]},
            {"id": 3, "session_id": "iat-attention-newer", "overall_recommendation_severity": "attention", "findings": [finding("iat_c", "attention", "IAT")]},
            {"id": 4, "session_id": "iat-attention-older", "overall_recommendation_severity": "attention", "findings": [finding("iat_c", "attention", "IAT")]},
            {"id": 5, "session_id": "old-ect-high", "overall_recommendation_severity": "high", "findings": [finding("ect_c", "high", "ECT")]},
            {"id": 6, "session_id": "excluded-battery-high", "overall_recommendation_severity": "high", "findings": [finding("battery_voltage", "high", "Battery")]},
        ]

        condition = overview_condition_summary(summaries)

        self.assertEqual(condition["status"], "high")
        self.assertEqual(condition["scope"], "Dựa trên 5 chuyến đi đã phân tích gần nhất")
        self.assertEqual([item["signal"] for item in condition["findings"]], ["ect_c", "battery_voltage", "iat_c"])
        self.assertEqual(condition["findings"][0]["session_id"], "old-ect-high")
        self.assertEqual(condition["findings"][1]["session_id"], "battery-attention")
        self.assertEqual(condition["findings"][2]["session_id"], "iat-attention-newer")

    def test_overview_condition_bounded_recent_selection_and_device_isolation(self):
        summaries = [
            {"id": index, "session_id": f"normal-{index}", "overall_recommendation_severity": "normal", "findings": []}
            for index in range(1, 6)
        ]
        summaries.append({
            "id": 6,
            "session_id": "too-old-high",
            "overall_recommendation_severity": "high",
            "findings": [{
                "signal": "ect_c",
                "signal_label": "ECT",
                "severity": "high",
                "direction": "high",
                "recommendation": "old recommendation",
            }],
        })
        condition = overview_condition_summary(summaries)
        self.assertEqual(condition["status"], "normal")
        self.assertEqual(condition["findings"], [])

        self.post_pi_v2_session(
            "other-device-high",
            ect_c=120.0,
            overall_status="ok",
            headers=self.headers_b,
            device=self.device_b,
        )
        self.login()
        response = self.client.get("/")
        html = response.get_data(as_text=True)
        self.assertIn("Chưa đủ dữ liệu", html)
        self.assertNotIn("Nhiệt độ động cơ cao hơn mức thông thường của xe", html)

    def test_session_filters_and_notes_search(self):
        normal = self.upload_analysis_session("filter-normal", anomaly=False)
        anomalous = self.upload_analysis_session("filter-attention", anomaly=True)
        normal.notes = "weekday commute"
        db.session.commit()
        self.login()

        response = self.client.get("/sessions?search=weekday")
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("filter-normal", html)
        self.assertNotIn("filter-attention", html)

        events = self.client.get("/sessions?has_events=1").get_data(as_text=True)
        self.assertIn("filter-attention", events)
        self.assertNotIn("filter-normal", events)

        json_response = self.client.get("/api/sessions?result=anomalous")
        self.assertEqual(json_response.status_code, 200)
        self.assertEqual(json_response.json["sessions"][0]["id"], anomalous.id)

    def test_session_list_pages_use_compact_summaries(self):
        self.upload_analysis_session("compact-summary", anomaly=True)
        self.login()

        with patch("services.analysis.session_records", side_effect=AssertionError("unexpected telemetry scan")):
            sessions_page = self.client.get("/sessions")
            self.assertEqual(sessions_page.status_code, 200)
            self.assertIn("compact-summary", sessions_page.get_data(as_text=True))

            sessions_api = self.client.get("/api/sessions")
            self.assertEqual(sessions_api.status_code, 200)
            self.assertEqual(sessions_api.json["sessions"][0]["session_id"], "compact-summary")

            analysis_page = self.client.get("/analysis")
            self.assertEqual(analysis_page.status_code, 200)
            self.assertIn("compact-summary", analysis_page.get_data(as_text=True))

            tool_page = self.client.get("/tool")
            self.assertEqual(tool_page.status_code, 200)
            self.assertIn("compact-summary", tool_page.get_data(as_text=True))

    def test_session_detail_statistics_events_and_large_downsampling(self):
        self.app.config["MAX_LOG_RECORDS"] = 300
        session = self.upload_analysis_session("detail-large", count=180, anomaly=True)
        self.login()

        page = self.client.get(f"/sessions/{session.id}")
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        self.assertIn("Analysis summary", html)
        self.assertIn("Battery voltage", html)
        self.assertNotIn("TPS voltage", html)
        self.assertIn("Anomaly timeline", html)
        self.assertIn('id="anomaly-timeline-viewport"', html)
        self.assertIn('id="anomaly-timeline-track"', html)
        self.assertNotIn('id="event-list"', html)
        self.assertNotIn("window.sessionAnalysis", html)
        disclosure = '<details class="card chart-workbench chart-disclosure" id="session-workbench"'
        self.assertIn(disclosure, html)
        disclosure_tag = html[html.index(disclosure):html.index(">", html.index(disclosure))]
        self.assertNotIn(" open", disclosure_tag)
        self.assertIn("Show charts", html)
        self.assertIn("Collapse charts", html)
        self.assertIn('id="chart-state"', html)
        self.assertIn('id="charts"', html)

        translations = self.client.get("/static/js/i18n.js")
        self.assertEqual(translations.status_code, 200)
        translation_source = translations.get_data(as_text=True)
        translations.close()
        self.assertIn('"Telemetry charts": "Biểu đồ dữ liệu"', translation_source)
        self.assertIn('"Show charts": "Hiển thị biểu đồ"', translation_source)
        self.assertIn('"Collapse charts": "Thu gọn biểu đồ"', translation_source)

        samples = self.client.get(f"/api/sessions/{session.id}/samples?max_points=100")
        self.assertEqual(samples.status_code, 200)
        self.assertTrue(samples.json["downsampled"])
        self.assertLessEqual(samples.json["sample_count"], 100)
        self.assertGreaterEqual(len(samples.json["events"]), 1)
        self.assertEqual(samples.json["session"]["event_count"], len(samples.json["events"]))
        self.assertIn("checksum_failure_count", samples.json["statistics"]["_quality"])

    def test_ai_api_unavailable_and_rerun_state(self):
        session = self.upload_analysis_session("ai-unavailable", anomaly=True)
        self.login()

        status = self.client.get("/api/analysis/status")
        self.assertEqual(status.status_code, 200)
        self.assertFalse(status.json["available"])
        self.assertEqual(status.json["message"], "ML API is not configured.")

        rerun = self.client.post(f"/api/sessions/{session.id}/analysis/re-run")
        self.assertEqual(rerun.status_code, 503)
        self.assertEqual(rerun.json["state"], "failed")
        self.assertIn("analysis", rerun.json)

    def test_ai_status_uses_stored_analysis_metadata_when_api_unavailable(self):
        session = self.upload_analysis_session("ai-stored-metadata", anomaly=False)
        result = AnalysisResult(
            ride_session_id=session.id,
            session_id=session.session_id,
            analysis_run_id="stored-run-1",
            model_version="stored-model-v1",
            feature_schema_version="stored-feature-v1",
            telemetry_schema_version="canonical-telemetry-v2",
            decoder_id="stored-decoder",
            decoder_version="1.2.3",
            overall_status="ok",
            health_score=95.0,
            anomaly_ratio=0.0,
            anomaly_window_count=0,
            result_summary={"analysis_run_id": "stored-run-1"},
        )
        db.session.add(result)
        db.session.commit()
        self.login()

        status = self.client.get("/api/analysis/status")
        self.assertEqual(status.status_code, 200)
        self.assertFalse(status.json["available"])
        self.assertTrue(status.json["stored_analysis_available"])
        self.assertEqual(status.json["latest_analysis_run_id"], "stored-run-1")
        self.assertEqual(status.json["model_version"], "stored-model-v1")
        self.assertEqual(status.json["feature_schema_version"], "stored-feature-v1")
        self.assertEqual(status.json["telemetry_schema_version"], "canonical-telemetry-v2")
        self.assertEqual(status.json["decoder_id"], "stored-decoder")
        self.assertEqual(status.json["decoder_version"], "1.2.3")

        page = self.client.get("/analysis")
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        self.assertIn("Stored analysis metadata", html)
        self.assertIn("Stored metadata", html)
        self.assertIn("stored-run-1", html)
        self.assertIn("stored-model-v1", html)
        self.assertIn("stored-feature-v1", html)
        self.assertIn("stored-decoder", html)
        self.assertNotIn("<dt>Training data</dt><dd>Not reported</dd>", html)
        self.assertNotIn("<dt>Threshold</dt><dd>Not reported</dd>", html)
        self.assertNotIn("<dt>Average latency</dt><dd>Not reported</dd>", html)

    def test_compare_removed_and_tool_playback_page(self):
        self.upload_analysis_session("tool-session", anomaly=True)
        self.login()

        self.assertEqual(self.client.get("/compare").status_code, 404)
        self.assertEqual(self.client.get("/api/compare").status_code, 404)
        home_page = self.client.get("/")
        self.assertEqual(home_page.status_code, 200)
        home_html = home_page.get_data(as_text=True)
        self.assertNotIn('id="language-select"', home_html)
        self.assertNotIn(">Compare<", home_html)

        settings_page = self.client.get("/settings")
        self.assertEqual(settings_page.status_code, 200)
        settings_html = settings_page.get_data(as_text=True)
        self.assertIn('select name="language"', settings_html)
        self.assertIn("Tiếng Việt", settings_html)
        self.assertIn("Save settings", settings_html)
        self.assertNotIn("Temperature unit", settings_html)
        self.assertNotIn("Chart sample density", settings_html)
        self.assertNotIn("Language readiness", settings_html)

        tool_page = self.client.get("/tool")
        self.assertEqual(tool_page.status_code, 200)
        tool_html = tool_page.get_data(as_text=True)
        self.assertIn("Session playback", tool_html)
        self.assertIn("Raw frame inspector", tool_html)

    def test_unit_formatting_helpers(self):
        self.assertEqual(format_duration(92), "1m 32s")
        self.assertEqual(format_duration(3661), "1h 1m 1s")
        self.assertEqual(format_number(12.345, 1, " V"), "12.3 V")

    def test_health_check_reports_database_ready(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"status": "ok", "database": "ok", "redis": "memory"})


if __name__ == "__main__":
    unittest.main()
