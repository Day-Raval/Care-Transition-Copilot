from __future__ import annotations

import importlib.util
import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

_producer = None
_producer_lock = threading.Lock()


def kafka_enabled() -> bool:
    return os.getenv("KAFKA_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}


def _topic(name: str, default: str) -> str:
    return os.getenv(name, default).strip()


def audit_events_topic() -> str:
    return _topic("KAFKA_TOPIC_AUDIT_EVENTS", "care-transition.audit-events")


def discharge_episodes_topic() -> str:
    return _topic("KAFKA_TOPIC_EPISODES", "care-transition.discharge-episodes")


def dlq_topic() -> str:
    return _topic("KAFKA_TOPIC_DLQ", "care-transition.dlq")


def consumer_group_id() -> str:
    return os.getenv("KAFKA_CONSUMER_GROUP_ID", "care-transition-copilot-consumers").strip()


def _bootstrap_servers() -> str:
    return os.getenv("KAFKA_BOOTSTRAP_SERVERS", "").strip()


def _client_available() -> bool:
    return importlib.util.find_spec("confluent_kafka") is not None


def _delivery_report(err, msg) -> None:
    if err is not None:
        logger.error("Kafka delivery failed topic=%s error=%s", getattr(msg, "topic", lambda: "unknown")(), err)


def _producer_config() -> dict[str, Any]:
    return {
        "bootstrap.servers": _bootstrap_servers(),
        "client.id": os.getenv("KAFKA_CLIENT_ID", "care-transition-copilot"),
        "enable.idempotence": True,
        "acks": "all",
        "message.timeout.ms": int(os.getenv("KAFKA_MESSAGE_TIMEOUT_MS", "5000")),
    }


def consumer_config(group_id: str | None = None) -> dict[str, Any]:
    return {
        "bootstrap.servers": _bootstrap_servers(),
        "group.id": group_id or consumer_group_id(),
        "auto.offset.reset": os.getenv("KAFKA_AUTO_OFFSET_RESET", "earliest"),
        "enable.auto.commit": os.getenv("KAFKA_ENABLE_AUTO_COMMIT", "false").strip().lower() in {"1", "true", "yes", "on"},
        "max.poll.interval.ms": int(os.getenv("KAFKA_MAX_POLL_INTERVAL_MS", "300000")),
        "session.timeout.ms": int(os.getenv("KAFKA_SESSION_TIMEOUT_MS", "45000")),
    }


def _get_producer():
    global _producer
    if not kafka_enabled():
        return None
    if not _bootstrap_servers():
        raise RuntimeError("KAFKA_ENABLED=true requires KAFKA_BOOTSTRAP_SERVERS")
    if _producer is None:
        with _producer_lock:
            if _producer is None:
                try:
                    from confluent_kafka import Producer
                except ImportError as exc:
                    raise RuntimeError("KAFKA_ENABLED=true requires the confluent-kafka package") from exc
                _producer = Producer(_producer_config())
    return _producer


def kafka_dependency_report() -> dict[str, Any]:
    enabled = kafka_enabled()
    bootstrap_configured = bool(_bootstrap_servers())
    client_available = _client_available()
    ready = not enabled or (bootstrap_configured and client_available)
    missing = []
    if enabled and not bootstrap_configured:
        missing.append("KAFKA_BOOTSTRAP_SERVERS")
    if enabled and not client_available:
        missing.append("confluent-kafka")
    return {
        "enabled": enabled,
        "ready": ready,
        "bootstrap_servers": bootstrap_configured,
        "client_available": client_available,
        "missing": missing,
    }


def publish_event(topic: str, event_type: str, payload: dict[str, Any], key: str | None = None) -> bool:
    try:
        producer = _get_producer()
    except RuntimeError as exc:
        logger.error("Kafka publish skipped: %s", exc)
        return False
    if producer is None:
        return False
    envelope = {
        "schema_version": 1,
        "event_id": uuid.uuid4().hex,
        "event_type": event_type,
        "occurred_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "care-transition-copilot",
        "payload": payload,
    }
    try:
        producer.produce(
            topic,
            key=key,
            value=json.dumps(envelope, ensure_ascii=True, default=str).encode("utf-8"),
            callback=_delivery_report,
        )
        producer.poll(0)
    except Exception as exc:
        logger.error("Kafka publish failed topic=%s event_type=%s error=%s", topic, event_type, exc)
        return False
    return True


def publish_audit_event(event_type: str, payload: dict[str, Any]) -> bool:
    key = payload.get("patient_id") or payload.get("request_id")
    return publish_event(audit_events_topic(), event_type, payload, key=key)


def publish_discharge_episode(payload: dict[str, Any]) -> bool:
    key = payload.get("encounter_id") or payload.get("patient_id")
    return publish_event(discharge_episodes_topic(), "discharge_episode_exported", payload, key=key)


def publish_to_dlq(
    original_topic: str,
    raw_payload: Any,
    error: str,
    error_type: str = "processing_error",
    retry_count: int = 0,
    key: str | None = None,
) -> bool:
    dlq_payload = {
        "original_topic": original_topic,
        "original_payload": raw_payload,
        "error": str(error),
        "error_type": error_type,
        "retry_count": retry_count,
        "failed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    return publish_event(dlq_topic(), "dead_letter_event", dlq_payload, key=key)


def flush_events(timeout: float = 5.0) -> None:
    if _producer is not None:
        _producer.flush(timeout)
