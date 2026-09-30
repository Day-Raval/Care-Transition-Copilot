"""
Notification stub for post-decision patient/clinician communication.

Mirrors the Kafka producer pattern in kafka_events.py: safe to call from
the request path with no external service configured. Two delivery paths:

- Twilio SMS, when TWILIO_ACCOUNT_SID/TWILIO_AUTH_TOKEN/TWILIO_FROM_NUMBER
  are all set and the optional `twilio` package is installed.
- A local "patient portal" stub otherwise — records what would have been
  sent without any external dependency, so local test checks and demos
  don't require a Twilio account.

Delivery never raises back to the caller; failures are recorded on the
returned record so an approve/reject request never fails because a
notification could not be delivered.
"""

from __future__ import annotations

import importlib.util
import logging
import os
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


def notifications_enabled() -> bool:
    return os.getenv("NOTIFICATIONS_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}


def _twilio_configured() -> bool:
    return bool(
        os.getenv("TWILIO_ACCOUNT_SID")
        and os.getenv("TWILIO_AUTH_TOKEN")
        and os.getenv("TWILIO_FROM_NUMBER")
    )


def _twilio_client_available() -> bool:
    return importlib.util.find_spec("twilio") is not None


def notification_channel() -> str:
    return "twilio_sms" if _twilio_configured() else "portal_stub"


def notification_dependency_report() -> dict[str, Any]:
    enabled = notifications_enabled()
    twilio_configured = _twilio_configured()
    twilio_client_available = _twilio_client_available()
    # The portal stub has no external dependency, so it's always "ready".
    ready = not enabled or not twilio_configured or twilio_client_available
    missing = []
    if enabled and twilio_configured and not twilio_client_available:
        missing.append("twilio package (pip install twilio)")
    return {
        "ready": ready,
        "enabled": enabled,
        "channel": notification_channel(),
        "missing": missing,
    }


def _send_via_twilio(to_number: str, message: str) -> dict[str, Any]:
    from twilio.rest import Client  # optional dependency, imported lazily

    client = Client(os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"])
    sms = client.messages.create(to=to_number, from_=os.environ["TWILIO_FROM_NUMBER"], body=message)
    return {"status": "sent", "provider_message_id": sms.sid}


def _send_via_portal_stub(patient_id: str, message: str) -> dict[str, Any]:
    logger.info("Patient portal stub notification queued for patient_id=%s: %s", patient_id, message)
    return {"status": "queued"}


def send_care_plan_notification(
    patient_id: str,
    discharge_ts: str,
    message: str,
    to_number: str | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "patient_id": patient_id,
        "discharge_ts": discharge_ts,
        "channel": notification_channel(),
        "message": message,
    }
    if not notifications_enabled():
        record["status"] = "disabled"
        return record

    try:
        if _twilio_configured() and to_number:
            record.update(_send_via_twilio(to_number, message))
        else:
            record.update(_send_via_portal_stub(patient_id, message))
    except Exception as exc:
        logger.error("Notification delivery failed patient_id=%s error=%s", patient_id, exc)
        record["status"] = "failed"
        record["error"] = str(exc)
    return record
