"""
logger.py — Structured Event Logger
Logs detection events to SQLite database and JSON files with full context.
"""

import json
import logging
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from config import AppConfig


def setup_logging(config: AppConfig) -> logging.Logger:
    """Configure application-level logging."""
    log_dir = Path(config.logging.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    log_level = getattr(logging, config.logging.log_level.upper(), logging.INFO)
    log_file = log_dir / f"monitor_{datetime.now().strftime('%Y%m%d')}.log"

    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_file),
        ],
    )
    return logging.getLogger("delivery_monitor")


class EventLogger:
    """
    Persists detection events to both SQLite (for querying) and JSON lines
    (for easy export and demo purposes).
    """

    def __init__(self, config: AppConfig):
        self.config = config
        self.log_dir = Path(config.logging.log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        self.db_path = self.log_dir / config.logging.db_path
        self.jsonl_path = self.log_dir / "events.jsonl"

        self._init_db()
        self.logger = logging.getLogger("event_logger")

    def _init_db(self):
        """Initialize SQLite schema."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS detection_events (
                    event_id        TEXT PRIMARY KEY,
                    timestamp       TEXT NOT NULL,
                    frame_path      TEXT,
                    labels_json     TEXT,
                    delivery_detected INTEGER DEFAULT 0,
                    alert_sent      INTEGER DEFAULT 0,
                    alert_channels  TEXT,
                    state_transition TEXT,
                    processing_time_ms INTEGER,
                    person_confidence REAL,
                    package_confidence REAL,
                    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_timestamp
                ON detection_events(timestamp)
            """)
            conn.commit()

    def log_event(
        self,
        labels: list[dict],
        frame_path: Optional[str],
        delivery_detected: bool,
        alert_sent: bool,
        alert_channels: list[str],
        state_transition: str,
        processing_time_ms: int,
        person_confidence: float = 0.0,
        package_confidence: float = 0.0,
    ) -> str:
        """Log a detection event. Returns the event_id."""
        event_id = str(uuid.uuid4())
        timestamp = datetime.utcnow().isoformat() + "Z"

        event = {
            "event_id": event_id,
            "timestamp": timestamp,
            "frame_path": frame_path,
            "labels": labels,
            "delivery_detected": delivery_detected,
            "alert_sent": alert_sent,
            "alert_channels": alert_channels,
            "state_transition": state_transition,
            "processing_time_ms": processing_time_ms,
            "person_confidence": person_confidence,
            "package_confidence": package_confidence,
        }

        # Write to SQLite
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    INSERT INTO detection_events VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                """, (
                    event_id,
                    timestamp,
                    frame_path,
                    json.dumps(labels),
                    int(delivery_detected),
                    int(alert_sent),
                    json.dumps(alert_channels),
                    state_transition,
                    processing_time_ms,
                    person_confidence,
                    package_confidence,
                ))
                conn.commit()
        except sqlite3.Error as e:
            self.logger.error(f"DB write failed: {e}")

        # Append to JSONL for easy grep/demo
        try:
            with open(self.jsonl_path, "a") as f:
                f.write(json.dumps(event) + "\n")
        except IOError as e:
            self.logger.error(f"JSONL write failed: {e}")

        log_msg = (
            f"[{state_transition}] delivery={delivery_detected} "
            f"alert={alert_sent} proc={processing_time_ms}ms "
            f"labels={[l['name'] for l in labels[:5]]}"
        )
        if delivery_detected:
            self.logger.warning(f"🚨 DELIVERY EVENT — {log_msg}")
        else:
            self.logger.info(f"👁  Detection scan — {log_msg}")

        return event_id

    def get_recent_events(self, limit: int = 50) -> list[dict]:
        """Retrieve most recent events (for dashboard)."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT * FROM detection_events
                ORDER BY timestamp DESC LIMIT ?
            """, (limit,)).fetchall()

        events = []
        for row in rows:
            e = dict(row)
            e["labels"] = json.loads(e.get("labels_json", "[]"))
            e["alert_channels"] = json.loads(e.get("alert_channels", "[]"))
            events.append(e)
        return events

    def get_stats(self) -> dict[str, Any]:
        """Return summary statistics for the dashboard."""
        with sqlite3.connect(self.db_path) as conn:
            total = conn.execute("SELECT COUNT(*) FROM detection_events").fetchone()[0]
            deliveries = conn.execute(
                "SELECT COUNT(*) FROM detection_events WHERE delivery_detected=1"
            ).fetchone()[0]
            alerts = conn.execute(
                "SELECT COUNT(*) FROM detection_events WHERE alert_sent=1"
            ).fetchone()[0]
            avg_proc = conn.execute(
                "SELECT AVG(processing_time_ms) FROM detection_events"
            ).fetchone()[0] or 0

        return {
            "total_scans": total,
            "deliveries_detected": deliveries,
            "alerts_sent": alerts,
            "avg_processing_time_ms": round(avg_proc, 1),
            "false_positive_estimate": max(0, deliveries - alerts),
        }
