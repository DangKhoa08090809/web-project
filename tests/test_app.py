from datetime import timedelta
import unittest

from app import create_app
from extensions import db
from models import Device, DevicePairingCode, RideSession, SyncBatch, TelemetryRecord, User, utc_now
from services.analysis import format_duration, format_number
from services.live import InMemoryLiveBackend, isoformat_z


class DashboardTestCase(unittest.TestCase):
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
                {"seq": seq, "device_time_ms": seq * 100, "rpm": 1000 + seq, "battery": 13.5}
                for seq in seqs
            ],
        }

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
                "raw_frame": {"hex": f"{seq:02X}", "checksum_ok": seq != 6},
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

    def test_session_detail_statistics_events_and_large_downsampling(self):
        self.app.config["MAX_LOG_RECORDS"] = 300
        session = self.upload_analysis_session("detail-large", count=180, anomaly=True)
        self.login()

        page = self.client.get(f"/sessions/{session.id}")
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        self.assertIn("Statistical summary", html)
        self.assertIn("Battery voltage", html)
        self.assertIn("Anomaly timeline", html)

        samples = self.client.get(f"/api/sessions/{session.id}/samples?max_points=100")
        self.assertEqual(samples.status_code, 200)
        self.assertTrue(samples.json["downsampled"])
        self.assertLessEqual(samples.json["sample_count"], 100)
        self.assertGreaterEqual(len(samples.json["events"]), 1)
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

    def test_compare_calculations_and_tool_playback_page(self):
        baseline = self.upload_analysis_session("compare-baseline", anomaly=False)
        comparison = self.upload_analysis_session("compare-attention", anomaly=True)
        self.login()

        compare = self.client.get(f"/api/compare?baseline={baseline.id}&comparison={comparison.id}")
        self.assertEqual(compare.status_code, 200)
        labels = [metric["label"] for metric in compare.json["metrics"]]
        self.assertIn("Mean RPM", labels)
        self.assertIn("Maximum anomaly score", labels)
        self.assertGreater(compare.json["comparison"]["max_anomaly_score"], compare.json["baseline"]["max_anomaly_score"])

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
