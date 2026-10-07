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

    def test_follow_up_status_and_reminder_persist_locally(self):
        old_follow_ups_path = production.FOLLOW_UPS_PATH
        old_reminders_path = production.REMINDERS_PATH
        with tempfile.TemporaryDirectory() as tmpdir:
            production.FOLLOW_UPS_PATH = Path(tmpdir) / "follow_ups.jsonl"
            production.REMINDERS_PATH = Path(tmpdir) / "reminders.jsonl"
            try:
                follow_up = production.save_follow_up_status(
                    patient_id="patient-1",
                    discharge_ts="2026-09-24",
                    status="contacted",
                    actor="clinician-1",
                    note="Reached patient portal.",
                )
                reminder = production.schedule_notification_reminder(
                    patient_id="patient-1",
                    discharge_ts="2026-09-24",
                    remind_at="2026-09-26T09:00",
                    message="Check scheduled follow-up.",
                    actor="clinician-1",
                )

                self.assertEqual(follow_up["status"], "contacted")
                self.assertEqual(production.latest_follow_up("patient-1", "2026-09-24")["note"], "Reached patient portal.")
                self.assertEqual(reminder["status"], "scheduled")
                self.assertEqual(production.read_reminders(limit=10)[0]["message"], "Check scheduled follow-up.")
            finally:
                production.FOLLOW_UPS_PATH = old_follow_ups_path
                production.REMINDERS_PATH = old_reminders_path


if __name__ == "__main__":
    unittest.main()
