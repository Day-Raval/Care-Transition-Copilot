"""
Stream Processing Idempotency Tracker.

Ensures that duplicate messages delivered by Kafka (at-least-once guarantee)
do not cause duplicate side effects (duplicate risk scoring, duplicate
care plan precomputation, or duplicate audit records).
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import Column, Integer, MetaData, String, Table, Text, create_engine, text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

DEFAULT_MAX_MEMORY_CACHE = 10000
RESULTS_DIR = Path("results")
IDEMPOTENCY_DB_PATH = RESULTS_DIR / "idempotency.sqlite3"


class IdempotencyTracker:
    """
    Thread-safe idempotency tracker with two-tier storage:
    1. In-memory LRU cache for high-throughput duplicate suppression.
    2. Local SQLite or PostgreSQL persistence for crash resilience.
    """

    def __init__(
        self,
        max_memory_entries: int = DEFAULT_MAX_MEMORY_CACHE,
        sqlite_path: Path | str = IDEMPOTENCY_DB_PATH,
        engine: Engine | None = None,
    ):
        self._lock = threading.Lock()
        self._memory_cache: OrderedDict[str, float] = OrderedDict()
        self._max_memory_entries = max_memory_entries
        self._sqlite_path = Path(sqlite_path)
        self._engine = engine
        self._init_storage()

    def _init_storage(self) -> None:
        if self._engine is not None:
            metadata = MetaData()
            Table(
                "processed_stream_events",
                metadata,
                Column("id", Integer, primary_key=True, autoincrement=True),
                Column("event_id", String, unique=True, nullable=False, index=True),
                Column("topic", String, nullable=True),
                Column("processed_at", String, nullable=False),
                Column("details_json", Text, nullable=True),
            )
            metadata.create_all(self._engine)
        else:
            self._sqlite_path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(self._sqlite_path) as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS processed_stream_events (
                        event_id TEXT PRIMARY KEY,
                        topic TEXT,
                        processed_at TEXT NOT NULL,
                        details_json TEXT
                    )
                    """
                )
                conn.commit()

    def is_processed(self, event_id: str) -> bool:
        if not event_id:
            return False

        with self._lock:
            # 1. Fast in-memory check
            if event_id in self._memory_cache:
                self._memory_cache.move_to_end(event_id)
                return True

        # 2. Storage check
        if self._engine is not None:
            try:
                with self._engine.connect() as conn:
                    result = conn.execute(
                        text("SELECT 1 FROM processed_stream_events WHERE event_id = :event_id"),
                        {"event_id": event_id},
                    ).first()
                    if result:
                        self._add_to_memory(event_id)
                        return True
            except Exception as exc:
                logger.warning("Error checking DB idempotency for %s: %s", event_id, exc)
        else:
            try:
                with sqlite3.connect(self._sqlite_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        "SELECT 1 FROM processed_stream_events WHERE event_id = ?",
                        (event_id,),
                    )
                    if cursor.fetchone():
                        self._add_to_memory(event_id)
                        return True
            except Exception as exc:
                logger.warning("Error checking SQLite idempotency for %s: %s", event_id, exc)

        return False

    def mark_processed(
        self,
        event_id: str,
        topic: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> bool:
        if not event_id:
            return False

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        import json

        details_json = json.dumps(details or {}, ensure_ascii=True, default=str)

        with self._lock:
            self._add_to_memory(event_id)

        if self._engine is not None:
            try:
                with self._engine.begin() as conn:
                    conn.execute(
                        text(
                            """
                            INSERT INTO processed_stream_events (event_id, topic, processed_at, details_json)
                            VALUES (:event_id, :topic, :processed_at, :details_json)
                            ON CONFLICT (event_id) DO NOTHING
                            """
                        ),
                        {
                            "event_id": event_id,
                            "topic": topic,
                            "processed_at": now,
                            "details_json": details_json,
                        },
                    )
                return True
            except Exception as exc:
                logger.error("Failed to mark event %s as processed in DB: %s", event_id, exc)
                return False
        else:
            try:
                with sqlite3.connect(self._sqlite_path) as conn:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO processed_stream_events (event_id, topic, processed_at, details_json)
                        VALUES (?, ?, ?, ?)
                        """,
                        (event_id, topic, now, details_json),
                    )
                    conn.commit()
                return True
            except Exception as exc:
                logger.error("Failed to mark event %s as processed in SQLite: %s", event_id, exc)
                return False

    def _add_to_memory(self, event_id: str) -> None:
        self._memory_cache[event_id] = datetime.now(timezone.utc).timestamp()
        if len(self._memory_cache) > self._max_memory_entries:
            self._memory_cache.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._memory_cache.clear()
        if self._engine is not None:
            with self._engine.begin() as conn:
                conn.execute(text("DELETE FROM processed_stream_events"))
        elif self._sqlite_path.exists():
            with sqlite3.connect(self._sqlite_path) as conn:
                conn.execute("DELETE FROM processed_stream_events")
                conn.commit()


# Default singleton instance
_tracker: IdempotencyTracker | None = None
_tracker_lock = threading.Lock()


def get_idempotency_tracker(engine: Engine | None = None) -> IdempotencyTracker:
    global _tracker
    if _tracker is None:
        with _tracker_lock:
            if _tracker is None:
                _tracker = IdempotencyTracker(engine=engine)
    return _tracker
