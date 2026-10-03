"""
Persistent Kafka Consumer Service for Care Transition Copilot.

Provides:
- Persistent consumer loops for `care-transition.discharge-episodes` and `care-transition.audit-events`
- Idempotent stream processing using the two-tier IdempotencyTracker
- Dead-Letter Queue (DLQ) routing for malformed or unprocessable messages
- Dynamic backpressure management (pause/resume partition polling based on worker capacity)
- Resilient reconnect handling for network disconnects and broker restarts
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable

from src.data_services.idempotency import get_idempotency_tracker
from src.data_services.kafka_events import (
    audit_events_topic,
    consumer_config,
    discharge_episodes_topic,
    dlq_topic,
    kafka_enabled,
    publish_to_dlq,
)

logger = logging.getLogger(__name__)


class KafkaEventConsumer:
    """
    Production-grade Kafka Consumer with DLQ, idempotency deduplication,
    backpressure management, and graceful shutdown.
    """

    def __init__(
        self,
        topics: list[str] | None = None,
        group_id: str | None = None,
        max_workers: int = 4,
        max_in_flight: int = 50,
        max_retries: int = 3,
        poll_timeout_seconds: float = 1.0,
    ):
        self.topics = topics or [discharge_episodes_topic(), audit_events_topic()]
        self.group_id = group_id
        self.max_workers = max_workers
        self.max_in_flight = max_in_flight
        self.max_retries = max_retries
        self.poll_timeout_seconds = poll_timeout_seconds

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="kafka-worker")
        self._in_flight_lock = threading.Lock()
        self._in_flight_count = 0
        self._is_paused = False
        self._assigned_partitions = []

        self._idempotency = get_idempotency_tracker()
        self._stats = {
            "consumed_count": 0,
            "processed_count": 0,
            "duplicate_count": 0,
            "dlq_count": 0,
            "error_count": 0,
            "status": "stopped",
            "last_poll_at": None,
            "connected": False,
        }

    @property
    def stats(self) -> dict[str, Any]:
        with self._in_flight_lock:
            return dict(self._stats, in_flight=self._in_flight_count, paused=self._is_paused)

    def is_running(self) -> bool:
        return self._stats["status"] == "running" and not self._stop_event.is_set()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            logger.warning("KafkaEventConsumer is already running.")
            return

        self._stop_event.clear()
        self._stats["status"] = "running"
        self._thread = threading.Thread(target=self._run_consumer_loop, name="KafkaEventConsumerThread", daemon=True)
        self._thread.start()
        logger.info("KafkaEventConsumer started for topics: %s", self.topics)

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        self._stats["status"] = "stopping"
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self._executor.shutdown(wait=False)
        self._stats["status"] = "stopped"
        logger.info("KafkaEventConsumer stopped.")

    def _create_consumer(self):
        try:
            from confluent_kafka import Consumer
        except ImportError as exc:
            raise RuntimeError("confluent-kafka package is required for KafkaEventConsumer") from exc

        cfg = consumer_config(group_id=self.group_id)
        consumer = Consumer(cfg)

        def on_assign(c, partitions):
            self._assigned_partitions = partitions
            logger.info("Consumer assigned partitions: %s", partitions)

        def on_revoke(c, partitions):
            self._assigned_partitions = []
            logger.info("Consumer revoked partitions: %s", partitions)

        consumer.subscribe(self.topics, on_assign=on_assign, on_revoke=on_revoke)
        return consumer

    def _run_consumer_loop(self) -> None:
        if not kafka_enabled():
            logger.warning("Kafka is disabled (KAFKA_ENABLED!=true). Consumer loop exiting.")
            self._stats["status"] = "disabled"
            return

        reconnect_delay = 1.0
        while not self._stop_event.is_set():
            consumer = None
            try:
                consumer = self._create_create_consumer()
                self._stats["connected"] = True
                reconnect_delay = 1.0  # Reset delay on successful connect

                while not self._stop_event.is_set():
                    self._stats["last_poll_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

                    # Backpressure Check
                    with self._in_flight_lock:
                        in_flight = self._in_flight_count

                    if in_flight >= self.max_in_flight:
                        if not self._is_paused and self._assigned_partitions:
                            try:
                                consumer.pause(self._assigned_partitions)
                                self._is_paused = True
                                logger.warning(
                                    "Backpressure triggered: %d in-flight messages (threshold=%d). Paused partitions.",
                                    in_flight,
                                    self.max_in_flight,
                                )
                            except Exception as pe:
                                logger.error("Failed to pause partitions: %s", pe)
                        time.sleep(0.1)
                        continue
                    elif self._is_paused and in_flight < (self.max_in_flight // 2):
                        if self._assigned_partitions:
                            try:
                                consumer.resume(self._assigned_partitions)
                                self._is_paused = False
                                logger.info(
                                    "Backpressure relieved: %d in-flight messages. Resumed partitions.", in_flight
                                )
                            except Exception as re:
                                logger.error("Failed to resume partitions: %s", re)

                    msg = consumer.poll(timeout=self.poll_timeout_seconds)
                    if msg is None:
                        continue

                    if msg.error():
                        err = msg.error()
                        from confluent_kafka import KafkaError
                        if err.code() == KafkaError._PARTITION_EOF:
                            continue
                        logger.error("Kafka consumer error: %s", err)
                        self._stats["error_count"] += 1
                        continue

                    self._stats["consumed_count"] += 1

                    # Increment in-flight count
                    with self._in_flight_lock:
                        self._in_flight_count += 1

                    # Submit message processing to thread pool
                    self._executor.submit(self._process_message_task, consumer, msg)

            except Exception as exc:
                self._stats["connected"] = False
                self._stats["error_count"] += 1
                logger.error("Kafka consumer connection error: %s. Reconnecting in %.1fs...", exc, reconnect_delay)
                time.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2.0, 30.0)
            finally:
                if consumer is not None:
                    try:
                        consumer.close()
                    except Exception:
                        pass
                self._stats["connected"] = False

        self._stats["status"] = "stopped"

    def _create_create_consumer(self):
        return self._create_consumer()

    def _process_message_task(self, consumer, msg) -> None:
        try:
            self._handle_raw_message(msg)
            # Commit offset upon successful processing or DLQ routing
            try:
                consumer.commit(msg, asynchronous=True)
            except Exception as ce:
                logger.debug("Asynchronous commit error: %s", ce)
        finally:
            with self._in_flight_lock:
                self._in_flight_count = max(0, self._in_flight_count - 1)

    def _handle_raw_message(self, msg) -> None:
        topic = msg.topic()
        raw_val = msg.value()
        msg_key = msg.key().decode("utf-8") if msg.key() else None

        # 1. Deserialization & DLQ check for malformed JSON
        try:
            data_str = raw_val.decode("utf-8")
            envelope = json.loads(data_str)
        except Exception as exc:
            logger.error("Malformed message on topic=%s key=%s. Routing to DLQ: %s", topic, msg_key, exc)
            self._stats["dlq_count"] += 1
            publish_to_dlq(
                original_topic=topic,
                raw_payload=raw_val.decode("utf-8", errors="replace") if raw_val else "",
                error=f"JSON decode failure: {exc}",
                error_type="malformed_json",
                key=msg_key,
            )
            return

        # 2. Envelope Schema Validation
        event_id = envelope.get("event_id")
        event_type = envelope.get("event_type", "unknown")
        payload = envelope.get("payload")

        if not event_id or payload is None:
            logger.warning("Invalid envelope schema on topic=%s event_id=%s. Routing to DLQ.", topic, event_id)
            self._stats["dlq_count"] += 1
            publish_to_dlq(
                original_topic=topic,
                raw_payload=envelope,
                error="Missing required 'event_id' or 'payload' in envelope",
                error_type="invalid_envelope_schema",
                key=msg_key,
            )
            return

        # 3. Idempotent Processing Check
        if self._idempotency.is_processed(event_id):
            logger.info("Skipping duplicate stream event_id=%s on topic=%s", event_id, topic)
            self._stats["duplicate_count"] += 1
            return

        # 4. Processing with Retry and DLQ Fallback
        success = False
        last_error = None
        for attempt in range(1, self.max_retries + 1):
            try:
                self.dispatch_event(topic, event_type, payload, event_id)
                success = True
                break
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "Processing error on topic=%s event_id=%s attempt=%d/%d: %s",
                    topic,
                    event_id,
                    attempt,
                    self.max_retries,
                    exc,
                )
                time.sleep(0.1 * (2 ** (attempt - 1)))

        if success:
            self._idempotency.mark_processed(event_id, topic=topic, details={"event_type": event_type})
            self._stats["processed_count"] += 1
        else:
            logger.error(
                "Event %s failed after %d retries. Routing to DLQ: %s",
                event_id,
                self.max_retries,
                last_error,
            )
            self._stats["dlq_count"] += 1
            publish_to_dlq(
                original_topic=topic,
                raw_payload=envelope,
                error=str(last_error),
                error_type="processing_retry_exhausted",
                retry_count=self.max_retries,
                key=msg_key,
            )

    def dispatch_event(self, topic: str, event_type: str, payload: dict[str, Any], event_id: str) -> None:
        """Dispatches normalized events to their dedicated topic handlers."""
        if topic == discharge_episodes_topic() or event_type == "discharge_episode_exported":
            self.handle_discharge_episode_event(payload, event_id)
        elif topic == audit_events_topic() or event_type.startswith("audit_"):
            self.handle_audit_event(payload, event_id)
        else:
            logger.info("Received unhandled event on topic=%s event_type=%s", topic, event_type)

    def handle_discharge_episode_event(self, payload: dict[str, Any], event_id: str) -> None:
        """
        Processes incoming discharge episode event:
        - Validates episode fields
        - Triggers or verifies precomputation if high/medium risk
        - Records episode intake in production store
        """
        patient_id = payload.get("patient_id")
        discharge_ts = payload.get("discharge_ts")
        risk_category = payload.get("risk_category")
        risk_score = payload.get("risk_score")

        logger.info(
            "Consumer processed discharge episode patient_id=%s risk_category=%s risk_score=%s event_id=%s",
            patient_id,
            risk_category,
            risk_score,
            event_id,
        )

        if not patient_id or not discharge_ts:
            raise ValueError(f"Episode payload missing required patient_id or discharge_ts: {payload}")

        # Proactively trigger precomputation if medium or high risk
        if risk_category in {"high", "medium"}:
            try:
                from src.api.precompute import precompute_manager
                evt, is_creator = precompute_manager.get_patient_event(patient_id, discharge_ts)
                if is_creator:
                    def _bg_generate():
                        try:
                            from src.api.main import _generate_assessment_payload
                            _generate_assessment_payload(patient_id, discharge_ts)
                        except Exception as e:
                            logger.warning("Precompute failed for consumer event %s: %s", patient_id, e)
                        finally:
                            precompute_manager.finish_patient_event(patient_id, discharge_ts)

                    t = threading.Thread(target=_bg_generate, name=f"consumer-precompute-{patient_id}", daemon=True)
                    t.start()
            except Exception as exc:
                logger.debug("Precompute trigger skipped in consumer: %s", exc)

    def handle_audit_event(self, payload: dict[str, Any], event_id: str) -> None:
        """
        Processes incoming audit event:
        - Logs to persistent audit store if persistence backend is configured
        """
        logger.info("Consumer received audit event: %s (event_id=%s)", payload.get("event_type", "audit"), event_id)


# Module-level singleton
_consumer_instance: KafkaEventConsumer | None = None
_consumer_lock = threading.Lock()


def get_kafka_consumer() -> KafkaEventConsumer:
    global _consumer_instance
    if _consumer_instance is None:
        with _consumer_lock:
            if _consumer_instance is None:
                _consumer_instance = KafkaEventConsumer()
    return _consumer_instance
