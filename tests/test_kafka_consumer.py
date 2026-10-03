"""
Unit tests for Kafka Consumer Infrastructure, Real-Time Intake Gateway,
Idempotency Tracking, and Dead-Letter Queue (DLQ) Routing.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.data_services.idempotency import IdempotencyTracker
from src.data_services.consumer import KafkaEventConsumer
from src.ingestion.hl7_intake import (
    parse_hl7_adt_message,
    parse_hl7_timestamp,
    process_realtime_intake,
)


class TestKafkaConsumerInfrastructure(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_idempotency.sqlite3"
        self.tracker = IdempotencyTracker(sqlite_path=self.db_path, max_memory_entries=100)

    def tearDown(self):
        self.temp_dir.cleanup()

    # -------------------------------------------------------------
    # 1. Idempotency Tracker Tests
    # -------------------------------------------------------------
    def test_idempotency_tracker_in_memory_and_sqlite(self):
        event_id = "evt-12345"

        # Initially not processed
        self.assertFalse(self.tracker.is_processed(event_id))

        # Mark processed
        marked = self.tracker.mark_processed(event_id, topic="care-transition.discharge-episodes", details={"foo": "bar"})
        self.assertTrue(marked)

        # Subsequent check returns True
        self.assertTrue(self.tracker.is_processed(event_id))

        # New tracker pointing to same SQLite DB confirms persistent deduplication
        fresh_tracker = IdempotencyTracker(sqlite_path=self.db_path)
        self.assertTrue(fresh_tracker.is_processed(event_id))

        # Different event returns False
        self.assertFalse(fresh_tracker.is_processed("evt-other"))

    def test_idempotency_tracker_cache_eviction(self):
        # Tracker with small capacity
        small_tracker = IdempotencyTracker(sqlite_path=self.db_path, max_memory_entries=2)
        small_tracker.mark_processed("e1")
        small_tracker.mark_processed("e2")
        small_tracker.mark_processed("e3")  # Evicts e1 from in-memory cache

        # e1 should still be recognized from SQLite storage fallback
        self.assertTrue(small_tracker.is_processed("e1"))

    # -------------------------------------------------------------
    # 2. HL7v2 ADT^A03 Intake Gateway Tests
    # -------------------------------------------------------------
    def test_parse_hl7_timestamp(self):
        # HL7 YYYYMMDDHHMMSS
        iso_ts = parse_hl7_timestamp("20260821143000")
        self.assertEqual(iso_ts, "2026-08-21T14:30:00+00:00")

        # HL7 YYYYMMDD
        iso_date = parse_hl7_timestamp("20260821")
        self.assertEqual(iso_date, "2026-08-21T00:00:00+00:00")

        # ISO format passthrough
        self.assertEqual(parse_hl7_timestamp("2026-08-21T14:30:00"), "2026-08-21T14:30:00")

    def test_parse_hl7_adt_message_valid(self):
        raw_msg = (
            "MSH|^~\\&|EPIC|GENHOSP|COPILOT|COPILOT|20260821143000||ADT^A03|MSG00001|P|2.5\n"
            "PID|1||88213^^^MRN||Whitaker^James^^^^||19580312|M\n"
            "PV1|1|I|CARD^204^1||||123456^Alvarez^Maria|||CARD|||||||||88213-VISIT9|||||||||||||||||||||||||20260815080000|20260821143000\n"
            "DG1|1||I50.9^Congestive Heart Failure^ICD10\n"
        )
        parsed = parse_hl7_adt_message(raw_msg)

        self.assertEqual(parsed["event_type"], "ADT^A03")
        self.assertEqual(parsed["patient_id"], "88213")
        self.assertEqual(parsed["patient_name"], "James Whitaker")
        self.assertEqual(parsed["encounter_id"], "88213-VISIT9")
        self.assertEqual(parsed["discharge_ts"], "2026-08-21T14:30:00+00:00")
        self.assertEqual(parsed["admit_ts"], "2026-08-15T08:00:00+00:00")
        self.assertEqual(parsed["attending_provider"], "123456 Alvarez Maria")
        self.assertEqual(parsed["service_line"], "CARD")
        self.assertEqual(len(parsed["diagnoses"]), 1)
        self.assertEqual(parsed["diagnoses"][0]["code"], "I50.9")
        self.assertEqual(parsed["diagnoses"][0]["description"], "Congestive Heart Failure")

    def test_parse_hl7_adt_message_malformed(self):
        with self.assertRaises(ValueError):
            parse_hl7_adt_message("")

        with self.assertRaises(ValueError):
            parse_hl7_adt_message("PID|1||12345\nPV1|1|I\n")  # Missing MSH

    @patch("src.ingestion.hl7_intake.publish_discharge_episode", return_value=True)
    @patch("src.ingestion.hl7_intake.publish_audit_event", return_value=True)
    def test_process_realtime_intake_pipeline(self, mock_audit, mock_pub):
        raw_msg = (
            "MSH|^~\\&|EPIC|GENHOSP|COPILOT|COPILOT|20260821143000||ADT^A03|MSG00001|P|2.5\n"
            "PID|1||TEST-PATIENT-99||Smith^Jane^^^^||19550101|F\n"
            "PV1|1|I|CARD^204^1||||1234^Doc|||CARD|||||||||TEST-ENC-1|||||||||||||||||||||||||20260818080000|20260821143000\n"
            "DG1|1||I50.9^Heart Failure^ICD10\n"
        )
        res = process_realtime_intake(raw_msg, trigger_precompute=False)

        self.assertEqual(res["status"], "success")
        self.assertEqual(res["patient_id"], "TEST-PATIENT-99")
        self.assertEqual(res["encounter_id"], "TEST-ENC-1")
        self.assertIn("risk_score", res)
        self.assertIn("risk_category", res)
        self.assertTrue(res["published_to_kafka"])
        mock_pub.assert_called_once()
        mock_audit.assert_called_once()

    # -------------------------------------------------------------
    # 3. Kafka Consumer & DLQ Routing Tests
    # -------------------------------------------------------------
    @patch("src.data_services.consumer.publish_to_dlq")
    def test_consumer_malformed_json_routes_to_dlq(self, mock_dlq):
        consumer = KafkaEventConsumer(topics=["care-transition.discharge-episodes"])
        mock_msg = MagicMock()
        mock_msg.topic.return_value = "care-transition.discharge-episodes"
        mock_msg.value.return_value = b"NOT_VALID_JSON{{"
        mock_msg.key.return_value = b"key-1"

        consumer._handle_raw_message(mock_msg)

        mock_dlq.assert_called_once()
        args, kwargs = mock_dlq.call_args
        self.assertEqual(kwargs.get("original_topic"), "care-transition.discharge-episodes")
        self.assertEqual(kwargs.get("error_type"), "malformed_json")
        self.assertEqual(consumer.stats["dlq_count"], 1)

    @patch("src.data_services.consumer.publish_to_dlq")
    def test_consumer_invalid_envelope_routes_to_dlq(self, mock_dlq):
        consumer = KafkaEventConsumer(topics=["care-transition.discharge-episodes"])
        mock_msg = MagicMock()
        mock_msg.topic.return_value = "care-transition.discharge-episodes"
        # Missing event_id and payload
        mock_msg.value.return_value = json.dumps({"schema_version": 1}).encode("utf-8")
        mock_msg.key.return_value = b"key-2"

        consumer._handle_raw_message(mock_msg)

        mock_dlq.assert_called_once()
        args, kwargs = mock_dlq.call_args
        self.assertEqual(kwargs.get("error_type"), "invalid_envelope_schema")
        self.assertEqual(consumer.stats["dlq_count"], 1)

    @patch("src.data_services.consumer.publish_to_dlq")
    def test_consumer_idempotency_skips_duplicates(self, mock_dlq):
        consumer = KafkaEventConsumer(topics=["care-transition.discharge-episodes"])
        consumer._idempotency = self.tracker

        event_id = "duplicate-event-999"
        valid_envelope = {
            "schema_version": 1,
            "event_id": event_id,
            "event_type": "discharge_episode_exported",
            "occurred_at": "2026-08-21T14:30:00Z",
            "payload": {
                "patient_id": "P-100",
                "discharge_ts": "2026-08-21T14:30:00Z",
                "risk_category": "low",
                "risk_score": 0.5,
            },
        }

        mock_msg = MagicMock()
        mock_msg.topic.return_value = "care-transition.discharge-episodes"
        mock_msg.value.return_value = json.dumps(valid_envelope).encode("utf-8")
        mock_msg.key.return_value = b"P-100"

        # First consumption -> processed
        consumer._handle_raw_message(mock_msg)
        self.assertEqual(consumer.stats["processed_count"], 1)
        self.assertEqual(consumer.stats["duplicate_count"], 0)
        self.assertTrue(self.tracker.is_processed(event_id))

        # Second consumption of exact same event_id -> recognized as duplicate and skipped
        consumer._handle_raw_message(mock_msg)
        self.assertEqual(consumer.stats["processed_count"], 1)
        self.assertEqual(consumer.stats["duplicate_count"], 1)
        mock_dlq.assert_not_called()

    @patch("src.data_services.consumer.publish_to_dlq")
    def test_consumer_retry_and_dlq_on_handler_failure(self, mock_dlq):
        consumer = KafkaEventConsumer(topics=["care-transition.discharge-episodes"], max_retries=2)
        consumer._idempotency = self.tracker

        # Missing required patient_id triggers exception in dispatch
        bad_envelope = {
            "schema_version": 1,
            "event_id": "failing-event-1",
            "event_type": "discharge_episode_exported",
            "occurred_at": "2026-08-21T14:30:00Z",
            "payload": {
                "discharge_ts": "2026-08-21T14:30:00Z",
                # missing patient_id
            },
        }

        mock_msg = MagicMock()
        mock_msg.topic.return_value = "care-transition.discharge-episodes"
        mock_msg.value.return_value = json.dumps(bad_envelope).encode("utf-8")
        mock_msg.key.return_value = None

        consumer._handle_raw_message(mock_msg)

        mock_dlq.assert_called_once()
        args, kwargs = mock_dlq.call_args
        self.assertEqual(kwargs.get("error_type"), "processing_retry_exhausted")
        self.assertEqual(kwargs.get("retry_count"), 2)
        self.assertEqual(consumer.stats["dlq_count"], 1)


if __name__ == "__main__":
    unittest.main()
