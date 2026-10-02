"""
Background Precomputation Manager for Care Transition Copilot.

Enables proactive asynchronous generation of draft care plans for high/medium-risk
patients upon discharge or on-demand, caching results in memory/Redis and persisting
to the care_plans table/JSONL. Drops clinician UI review load time from 10-15s to <10ms.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable

logger = logging.getLogger(__name__)


class PrecomputeManager:
    def __init__(self):
        self._lock = threading.Lock()
        self._active_events: dict[tuple[str, str], threading.Event] = {}
        self._worker_thread: threading.Thread | None = None
        self._status = {
            "status": "idle",  # "idle" | "running"
            "total": 0,
            "completed": 0,
            "failed": 0,
            "skipped": 0,
            "in_progress_patient_id": None,
            "current_index": 0,
            "started_at": None,
            "last_completed_at": None,
            "errors": [],
        }

    def get_status(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._status)

    def is_running(self) -> bool:
        with self._lock:
            return self._status["status"] == "running"

    def get_patient_event(self, patient_id: str, discharge_ts: str) -> tuple[threading.Event, bool]:
        """
        Returns (event, is_creator).
        If is_creator is True, caller is responsible for generating and finishing the event.
        If is_creator is False, another caller or worker is currently generating it; caller can wait.
        """
        key = (str(patient_id), str(discharge_ts))
        with self._lock:
            if key in self._active_events:
                return self._active_events[key], False
            evt = threading.Event()
            self._active_events[key] = evt
            return evt, True

    def finish_patient_event(self, patient_id: str, discharge_ts: str) -> None:
        key = (str(patient_id), str(discharge_ts))
        with self._lock:
            evt = self._active_events.pop(key, None)
            if evt:
                evt.set()

    def start_background_precompute(
        self,
        get_candidates_fn: Callable[[], list[dict[str, Any]]],
        generate_fn: Callable[[str, str], Any],
        is_cached_fn: Callable[[str, str], bool],
        categories: list[str] | None = None,
        limit: int = 10,
        force: bool = False,
    ) -> dict[str, Any]:
        """
        Launches background thread to precompute assessments for target queue patients.
        """
        with self._lock:
            if self._status["status"] == "running":
                return {
                    "started": False,
                    "message": "Precomputation task is already running",
                    "status": dict(self._status),
                }

            self._status["status"] = "running"
            self._status["total"] = 0
            self._status["completed"] = 0
            self._status["failed"] = 0
            self._status["skipped"] = 0
            self._status["in_progress_patient_id"] = None
            self._status["current_index"] = 0
            self._status["started_at"] = datetime.now(timezone.utc).isoformat()
            self._status["errors"] = []

        def worker():
            try:
                candidates = get_candidates_fn()
                if categories:
                    norm_cats = {c.strip().lower() for c in categories if c.strip()}
                    candidates = [c for c in candidates if str(c.get("risk_category", "")).lower() in norm_cats]

                target_candidates = candidates[:limit]

                with self._lock:
                    self._status["total"] = len(target_candidates)

                logger.info(
                    "Background precompute starting for %d patients (categories=%s, force=%s)",
                    len(target_candidates),
                    categories,
                    force,
                )

                for idx, c in enumerate(target_candidates, 1):
                    p_id = str(c["patient_id"])
                    d_ts = str(c["discharge_ts"])
                    p_name = c.get("patient_name", p_id)
                    p_cat = c.get("risk_category", "unknown")

                    with self._lock:
                        self._status["current_index"] = idx
                        self._status["in_progress_patient_id"] = p_id

                    if not force and is_cached_fn(p_id, d_ts):
                        with self._lock:
                            self._status["skipped"] += 1
                        logger.debug("Precompute skipped already-cached patient %s (%s)", p_id, p_name)
                        continue

                    evt, is_creator = self.get_patient_event(p_id, d_ts)
                    if not is_creator:
                        # Another caller or thread is already handling this
                        evt.wait(timeout=120)
                        with self._lock:
                            self._status["completed"] += 1
                        continue

                    try:
                        t0 = time.monotonic()
                        generate_fn(p_id, d_ts)
                        duration = time.monotonic() - t0
                        with self._lock:
                            self._status["completed"] += 1
                            self._status["last_completed_at"] = datetime.now(timezone.utc).isoformat()
                        logger.info(
                            "Precomputed assessment for patient %s (%s, %s risk) in %.2fs [%d/%d]",
                            p_id,
                            p_name,
                            p_cat,
                            duration,
                            idx,
                            len(target_candidates),
                        )
                        # Modest delay between LLM calls to avoid burst rate-limits
                        time.sleep(0.3)
                    except Exception as exc:
                        logger.exception("Precompute failed for patient %s: %s", p_id, exc)
                        with self._lock:
                            self._status["failed"] += 1
                            self._status["errors"].append({
                                "patient_id": p_id,
                                "discharge_ts": d_ts,
                                "error": str(exc),
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            })
                    finally:
                        self.finish_patient_event(p_id, d_ts)

            except Exception as exc:
                logger.exception("Unexpected error in background precompute worker: %s", exc)
                with self._lock:
                    self._status["errors"].append({
                        "error": str(exc),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })
            finally:
                with self._lock:
                    self._status["status"] = "idle"
                    self._status["in_progress_patient_id"] = None
                logger.info("Background precompute finished: %s", self.get_status())

        self._worker_thread = threading.Thread(target=worker, daemon=True, name="care-plan-precompute-worker")
        self._worker_thread.start()

        return {
            "started": True,
            "message": "Precomputation task launched in background",
            "status": self.get_status(),
        }


precompute_manager = PrecomputeManager()
