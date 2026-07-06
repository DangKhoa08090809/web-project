import unittest

from app import create_app
from extensions import db
from models import Device, RideSession, TelemetryRecord, User


class DashboardTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app({
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite://",
            "WTF_CSRF_ENABLED": False,
            "SECRET_KEY": "test-only-secret",
            "MAX_LOG_RECORDS": 10,
        })
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        user = User(full_name="Test Admin", email="admin@example.com", role="admin")
        user.set_password("a-long-test-password")
        db.session.add(user)
        db.session.flush()
        device = Device(
            user_id=user.id, device_id="xiao-test-01", device_name="Test reader",
            vehicle_name="Test car", ecu_type="CAN", token_hash="",
        )
        device.set_token("device-secret")
        db.session.add(device)
        db.session.commit()
        self.client = self.app.test_client()
        self.headers = {"X-Device-ID": "xiao-test-01", "Authorization": "Bearer device-secret"}

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def login(self):
        return self.client.post("/auth/login", json={
            "email": "admin@example.com", "password": "a-long-test-password",
        })

    def test_browser_and_user_api_require_login(self):
        self.assertEqual(self.client.get("/").status_code, 302)
        response = self.client.get("/api/devices")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json["error"], "User authentication required")
        self.assertEqual(self.login().status_code, 200)
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_device_authentication_is_required(self):
        payload = {"session_id": "ride-1", "seq": 1, "timestamp": "2026-07-06T14:00:00Z"}
        self.assertEqual(self.client.post("/api/telemetry", json=payload).status_code, 401)
        wrong = {"X-Device-ID": "xiao-test-01", "Authorization": "Bearer wrong"}
        self.assertEqual(self.client.post("/api/telemetry", json=payload, headers=wrong).status_code, 401)

    def test_live_telemetry_and_duplicate_retry(self):
        payload = {
            "session_id": "ride-1", "seq": 1, "timestamp": "2026-07-06T14:00:00Z",
            "rpm": 2200, "tps": 12.5, "ect": 87, "battery": 13.9,
        }
        first = self.client.post("/api/telemetry", json=payload, headers=self.headers)
        retry = self.client.post("/api/telemetry", json=payload, headers=self.headers)
        self.assertEqual(first.status_code, 201)
        self.assertEqual(first.json["inserted"], 1)
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(retry.json["skipped"], 1)
        self.assertIsNotNone(Device.query.one().last_seen_at)
        self.assertEqual(TelemetryRecord.query.count(), 1)
        self.assertIsNone(RideSession.query.one().ended_at)

    def test_offline_batch_deduplicates_and_updates_session(self):
        payload = {"session_id": "offline-1", "records": [
            {"seq": 1, "timestamp": "2026-07-06T14:00:00Z", "rpm": 1000},
            {"seq": 2, "timestamp": "2026-07-06T14:00:01Z", "rpm": 1100},
            {"seq": 2, "timestamp": "2026-07-06T14:00:01Z", "rpm": 1100},
        ]}
        first = self.client.post("/api/logs/upload", json=payload, headers=self.headers)
        retry = self.client.post("/api/logs/upload", json=payload, headers=self.headers)
        self.assertEqual(first.json["inserted"], 2)
        self.assertEqual(first.json["skipped"], 1)
        self.assertEqual(retry.json["inserted"], 0)
        self.assertEqual(retry.json["skipped"], 3)
        self.assertEqual(RideSession.query.one().record_count, 2)
        self.assertIsNotNone(RideSession.query.one().ended_at)
        self.assertEqual(TelemetryRecord.query.count(), 2)

    def test_batch_validation_reports_record_index(self):
        payload = {"session_id": "bad-1", "records": [{"seq": -1, "timestamp": "nope"}]}
        response = self.client.post("/api/logs/upload", json=payload, headers=self.headers)
        self.assertEqual(response.status_code, 400)
        self.assertIn("records[0]", response.json["error"])

        mismatch = {"session_id": "one", "records": [
            {"session_id": "two", "seq": 1, "timestamp": "2026-07-06T14:00:00Z"},
        ]}
        response = self.client.post("/api/logs/upload", json=mismatch, headers=self.headers)
        self.assertEqual(response.status_code, 400)
        self.assertIn("top-level", response.json["error"])


if __name__ == "__main__":
    unittest.main()
