import os
import tempfile
import unittest
from pathlib import Path

from src.api import production
from src.data_services import notifications


class NotificationsStubTest(unittest.TestCase):
    """Local (no-network) test checks for the Twilio/patient-portal notification stub."""

    def setUp(self):
        self._env_keys = [
            "NOTIFICATIONS_ENABLED",
            "TWILIO_ACCOUNT_SID",
            "TWILIO_AUTH_TOKEN",
            "TWILIO_FROM_NUMBER",
        ]
        self._old_env = {key: os.environ.get(key) for key in self._env_keys}

    def tearDown(self):
        for key, value in self._old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _clear_twilio_env(self):
        for key in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER"):
            os.environ.pop(key, None)

    def test_portal_stub_used_when_twilio_not_configured(self):
        self._clear_twilio_env()
        os.environ["NOTIFICATIONS_ENABLED"] = "true"

        self.assertEqual(notifications.notification_channel(), "portal_stub")

        record = notifications.send_care_plan_notification(
            patient_id="patient-1",
            discharge_ts="2026-09-24",
            message="Your care plan was approved.",
        )

        self.assertEqual(record["status"], "queued")
        self.assertEqual(record["channel"], "portal_stub")
        self.assertEqual(record["patient_id"], "patient-1")

    def test_disabled_notifications_short_circuit(self):
        self._clear_twilio_env()
        os.environ["NOTIFICATIONS_ENABLED"] = "false"

        record = notifications.send_care_plan_notification(
            patient_id="patient-1",
            discharge_ts="2026-09-24",
            message="Your care plan was approved.",
        )

        self.assertEqual(record["status"], "disabled")

    def test_twilio_channel_selected_once_credentials_present(self):
        os.environ["TWILIO_ACCOUNT_SID"] = "AC_test"
        os.environ["TWILIO_AUTH_TOKEN"] = "token_test"
        os.environ["TWILIO_FROM_NUMBER"] = "+15550000000"

        self.assertEqual(notifications.notification_channel(), "twilio_sms")

    def test_missing_to_number_falls_back_to_portal_stub_even_with_twilio_configured(self):
        os.environ["TWILIO_ACCOUNT_SID"] = "AC_test"
        os.environ["TWILIO_AUTH_TOKEN"] = "token_test"
        os.environ["TWILIO_FROM_NUMBER"] = "+15550000000"

        record = notifications.send_care_plan_notification(
            patient_id="patient-1",
            discharge_ts="2026-09-24",
            message="Your care plan was approved.",
            to_number=None,
        )

        self.assertEqual(record["status"], "queued")

    def test_dependency_report_flags_missing_twilio_package(self):
        self._clear_twilio_env()
        os.environ["TWILIO_ACCOUNT_SID"] = "AC_test"
        os.environ["TWILIO_AUTH_TOKEN"] = "token_test"
        os.environ["TWILIO_FROM_NUMBER"] = "+15550000000"

        report = notifications.notification_dependency_report()

        self.assertEqual(report["channel"], "twilio_sms")
        if not notifications._twilio_client_available():
            self.assertFalse(report["ready"])
            self.assertIn("twilio package (pip install twilio)", report["missing"])

    def test_dependency_report_ready_for_portal_stub(self):
        self._clear_twilio_env()
        report = notifications.notification_dependency_report()

        self.assertTrue(report["ready"])
        self.assertEqual(report["channel"], "portal_stub")
        self.assertEqual(report["missing"], [])


class NotificationsPersistenceTest(unittest.TestCase):
    def setUp(self):
        self._clear_twilio_env()
        os.environ["NOTIFICATIONS_ENABLED"] = "true"
        os.environ["PERSISTENCE_BACKEND"] = "local"
        self._old_results_dir = production.RESULTS_DIR
        self._old_notifications_path = production.NOTIFICATIONS_PATH

    def tearDown(self):
        production.RESULTS_DIR = self._old_results_dir
        production.NOTIFICATIONS_PATH = self._old_notifications_path
        os.environ.pop("NOTIFICATIONS_ENABLED", None)
        os.environ.pop("PERSISTENCE_BACKEND", None)

    def _clear_twilio_env(self):
        for key in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER"):
            os.environ.pop(key, None)

    def test_notify_care_plan_decision_persists_locally(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            production.RESULTS_DIR = Path(tmpdir)
            production.NOTIFICATIONS_PATH = Path(tmpdir) / "notifications.jsonl"

            record = production.notify_care_plan_decision(
                patient_id="patient-1",
                discharge_ts="2026-09-24",
                message="Your care plan was approved.",
            )

            self.assertEqual(record["status"], "queued")
            saved = production.read_notifications(limit=10)
            self.assertEqual(len(saved), 1)
            self.assertEqual(saved[0]["patient_id"], "patient-1")


if __name__ == "__main__":
    unittest.main()
