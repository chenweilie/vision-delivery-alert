"""
detection_logic.py — Delivery Event State Machine
Implements the core business logic that determines when a delivery event
has occurred, using a multi-frame confirmation approach to reduce false positives.

State Machine:
    IDLE → CANDIDATE (person detected)
    CANDIDATE → CONFIRMED (package + person ≥ N consecutive frames)
    CONFIRMED → ALERTED (notification dispatched)
    ALERTED → COOLDOWN (dedup window active)
    COOLDOWN → IDLE (cooldown expires)
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum, auto
from typing import Optional

from rekognition import DetectionResult
from config import AppConfig

logger = logging.getLogger("detection_logic")


class DetectionState(Enum):
    IDLE = auto()
    CANDIDATE = auto()
    CONFIRMED = auto()
    ALERTED = auto()
    COOLDOWN = auto()


@dataclass
class DeliveryEvent:
    """Represents a confirmed delivery detection event."""
    event_id: str
    timestamp: datetime
    state_transition: str
    person_confidence: float
    package_confidence: float
    labels: list[dict]
    frame_path: Optional[str] = None
    should_alert: bool = True


@dataclass
class DetectionContext:
    """Internal state context carried between frames."""
    state: DetectionState = DetectionState.IDLE
    consecutive_positive_frames: int = 0
    last_state_change: datetime = field(default_factory=datetime.utcnow)
    last_alert_time: Optional[datetime] = None
    last_person_confidence: float = 0.0
    last_package_confidence: float = 0.0


class DeliveryDetector:
    """
    Stateful detector that processes frames sequentially and emits
    DeliveryEvent when a confirmed delivery is observed.
    """

    # These labels indicate a package/delivery item
    PACKAGE_LABELS = frozenset([
        "package", "box", "cardboard", "luggage", "bag",
        "crate", "container", "briefcase", "suitcase", "parcel",
    ])

    def __init__(self, config: AppConfig):
        self.config = config
        self.ctx = DetectionContext()
        self._scan_count = 0
        self._delivery_count = 0

    def process_frame(
        self,
        result: DetectionResult,
        frame_path: Optional[str] = None,
    ) -> Optional[DeliveryEvent]:
        """
        Evaluate a Rekognition result and advance the state machine.
        Returns a DeliveryEvent if alerting is required, else None.
        """
        self._scan_count += 1
        prev_state = self.ctx.state

        person_found, person_conf = self._check_person(result)
        package_found, pkg_label, pkg_conf = self._check_package(result)

        self.ctx.last_person_confidence = person_conf
        self.ctx.last_package_confidence = pkg_conf

        logger.debug(
            f"[Frame #{self._scan_count}] state={self.ctx.state.name} "
            f"person={person_found}({person_conf:.0f}%) "
            f"package={package_found}({pkg_conf:.0f}% / {pkg_label})"
        )

        event = self._advance_state(person_found, package_found, result, frame_path)

        if self.ctx.state != prev_state:
            logger.info(
                f"State transition: {prev_state.name} → {self.ctx.state.name} "
                f"(scan #{self._scan_count})"
            )

        return event

    def _check_person(self, result: DetectionResult) -> tuple[bool, float]:
        """Return (found, confidence) for Person label."""
        label = result.get_label("Person")
        if label and label["confidence"] >= self.config.rekognition.person_confidence:
            return True, label["confidence"]
        return False, 0.0

    def _check_package(self, result: DetectionResult) -> tuple[bool, str, float]:
        """
        Return (found, matching_label_name, confidence) for any package-type label.
        Checks both Rekognition output and configured package_labels.
        """
        configured = [l.lower() for l in self.config.rekognition.package_labels]
        all_package_labels = list(self.PACKAGE_LABELS | set(configured))

        # Normalize to original case for lookup
        for label_dict in result.labels:
            if label_dict["name"].lower() in all_package_labels:
                if label_dict["confidence"] >= self.config.rekognition.package_confidence:
                    return True, label_dict["name"], label_dict["confidence"]
        return False, "", 0.0

    def _advance_state(
        self,
        person_found: bool,
        package_found: bool,
        result: DetectionResult,
        frame_path: Optional[str],
    ) -> Optional[DeliveryEvent]:
        """Core state machine transition logic."""
        now = datetime.utcnow()
        cfg = self.config.detection
        state = self.ctx.state

        # ── COOLDOWN: check if window has expired ─────────────────────────
        if state == DetectionState.COOLDOWN:
            if self.ctx.last_alert_time and (
                now - self.ctx.last_alert_time > timedelta(minutes=cfg.cooldown_minutes)
            ):
                logger.info(f"Cooldown expired — returning to IDLE")
                self._transition(DetectionState.IDLE)
            return None  # Never emit events during cooldown

        # ── IDLE: look for a person ────────────────────────────────────────
        if state == DetectionState.IDLE:
            if person_found:
                self.ctx.consecutive_positive_frames = 1
                self._transition(DetectionState.CANDIDATE)
            return None

        # ── CANDIDATE: check timeout and wait for package ──────────────────
        if state == DetectionState.CANDIDATE:
            timeout = timedelta(seconds=cfg.state_timeout_seconds)
            if now - self.ctx.last_state_change > timeout:
                logger.debug("CANDIDATE state timed out → IDLE")
                self._transition(DetectionState.IDLE)
                self.ctx.consecutive_positive_frames = 0
                return None

            if person_found and package_found:
                self.ctx.consecutive_positive_frames += 1
                if self.ctx.consecutive_positive_frames >= cfg.confirmation_frames:
                    self._transition(DetectionState.CONFIRMED)
                    return self._create_event(result, frame_path)
            elif not person_found:
                # Person left the frame → reset
                self._transition(DetectionState.IDLE)
                self.ctx.consecutive_positive_frames = 0
            return None

        # ── CONFIRMED: move to ALERTED (this frame triggers it) ─────────────
        if state == DetectionState.CONFIRMED:
            self._transition(DetectionState.ALERTED)
            return None

        # ── ALERTED: move to COOLDOWN ──────────────────────────────────────
        if state == DetectionState.ALERTED:
            self.ctx.last_alert_time = now
            self._transition(DetectionState.COOLDOWN)
            return None

        return None

    def _transition(self, new_state: DetectionState):
        """Update state and timestamp."""
        self.ctx.state = new_state
        self.ctx.last_state_change = datetime.utcnow()

    def _create_event(
        self, result: DetectionResult, frame_path: Optional[str]
    ) -> DeliveryEvent:
        """Construct a DeliveryEvent from confirmed detection."""
        self._delivery_count += 1
        import uuid
        transition_str = "CANDIDATE → CONFIRMED → ALERTED"
        event = DeliveryEvent(
            event_id=str(uuid.uuid4()),
            timestamp=datetime.utcnow(),
            state_transition=transition_str,
            person_confidence=self.ctx.last_person_confidence,
            package_confidence=self.ctx.last_package_confidence,
            labels=result.to_dict(),
            frame_path=frame_path,
            should_alert=True,
        )
        logger.warning(
            f"🚚 DELIVERY CONFIRMED (event #{self._delivery_count}) — "
            f"person={event.person_confidence:.1f}% "
            f"package={event.package_confidence:.1f}%"
        )
        return event

    @property
    def state(self) -> DetectionState:
        return self.ctx.state

    @property
    def stats(self) -> dict:
        return {
            "total_scans": self._scan_count,
            "deliveries_confirmed": self._delivery_count,
            "current_state": self.ctx.state.name,
            "consecutive_positive_frames": self.ctx.consecutive_positive_frames,
            "last_alert": self.ctx.last_alert_time.isoformat() if self.ctx.last_alert_time else None,
        }
