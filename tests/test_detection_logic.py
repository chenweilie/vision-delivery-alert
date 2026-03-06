"""
test_detection_logic.py — Unit tests for the delivery event state machine.
Tests cover all state transitions, cooldown deduplication, and edge cases.
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from detection_logic import DeliveryDetector, DetectionState
from rekognition import DetectionResult
from config import AppConfig, RekognitionConfig, DetectionConfig


# ── Fixtures ────────────────────────────────────────────────────────────────

def make_config(confirmation_frames=2, cooldown_minutes=30, person_conf=80.0, pkg_conf=70.0):
    config = AppConfig()
    config.rekognition = RekognitionConfig(
        person_confidence=person_conf,
        package_confidence=pkg_conf,
    )
    config.detection = DetectionConfig(
        confirmation_frames=confirmation_frames,
        cooldown_minutes=cooldown_minutes,
        state_timeout_seconds=60,
    )
    return config


def make_result(labels: dict[str, float]) -> DetectionResult:
    """Helper: build a DetectionResult from {label_name: confidence} dict."""
    label_list = [
        {"name": name, "confidence": conf, "parents": [], "instances": 1}
        for name, conf in labels.items()
    ]
    return DetectionResult(label_list, processing_time_ms=500)


def make_delivery_result(person_conf=92.0, package_conf=85.0) -> DetectionResult:
    return make_result({"Person": person_conf, "Package": package_conf, "Door": 75.0})


def make_empty_result() -> DetectionResult:
    return make_result({"Car": 95.0, "Tree": 88.0})


def make_person_only_result(conf=90.0) -> DetectionResult:
    return make_result({"Person": conf})


# ── State transition tests ──────────────────────────────────────────────────

class TestIdleState:
    def test_starts_in_idle(self):
        detector = DeliveryDetector(make_config())
        assert detector.state == DetectionState.IDLE

    def test_no_person_stays_idle(self):
        detector = DeliveryDetector(make_config())
        result = make_empty_result()
        event = detector.process_frame(result)
        assert event is None
        assert detector.state == DetectionState.IDLE

    def test_person_moves_to_candidate(self):
        detector = DeliveryDetector(make_config())
        result = make_person_only_result()
        event = detector.process_frame(result)
        assert event is None  # no alert yet
        assert detector.state == DetectionState.CANDIDATE

    def test_low_confidence_person_stays_idle(self):
        detector = DeliveryDetector(make_config(person_conf=85.0))
        result = make_result({"Person": 70.0})  # below threshold
        detector.process_frame(result)
        assert detector.state == DetectionState.IDLE


class TestCandidateState:
    def test_candidate_no_package_stays_candidate(self):
        detector = DeliveryDetector(make_config())
        detector.process_frame(make_person_only_result())  # → CANDIDATE
        assert detector.state == DetectionState.CANDIDATE

        detector.process_frame(make_person_only_result())  # still no package
        assert detector.state == DetectionState.CANDIDATE

    def test_candidate_person_leaves_resets_to_idle(self):
        detector = DeliveryDetector(make_config())
        detector.process_frame(make_person_only_result())  # → CANDIDATE
        detector.process_frame(make_empty_result())        # person gone → IDLE
        assert detector.state == DetectionState.IDLE

    def test_candidate_confirmed_on_second_delivery_frame(self):
        config = make_config(confirmation_frames=2)
        detector = DeliveryDetector(config)
        detector.process_frame(make_person_only_result())   # → CANDIDATE
        detector.process_frame(make_delivery_result())      # frame 1 (+person+pkg)
        event = detector.process_frame(make_delivery_result())  # frame 2 → CONFIRMED
        assert event is not None
        assert event.should_alert is True
        assert detector.state == DetectionState.CONFIRMED

    def test_single_frame_confirmation(self):
        config = make_config(confirmation_frames=1)
        detector = DeliveryDetector(config)
        detector.process_frame(make_person_only_result())   # → CANDIDATE
        event = detector.process_frame(make_delivery_result())  # frame 1 → CONFIRMED
        assert event is not None

    def test_candidate_times_out(self):
        detector = DeliveryDetector(make_config())
        detector.process_frame(make_person_only_result())  # → CANDIDATE
        # Manually inject old timestamp to simulate timeout
        from datetime import timedelta
        detector.ctx.last_state_change = datetime.utcnow() - timedelta(seconds=120)
        detector.process_frame(make_person_only_result())  # should time out → IDLE
        assert detector.state == DetectionState.IDLE


class TestConfirmedAndCooldown:
    def _get_confirmed_detector(self):
        """Helper: advance detector to CONFIRMED state."""
        config = make_config(confirmation_frames=1)
        detector = DeliveryDetector(config)
        detector.process_frame(make_person_only_result())
        detector.process_frame(make_delivery_result())
        assert detector.state == DetectionState.CONFIRMED
        return detector

    def test_confirmed_moves_to_alerted(self):
        detector = self._get_confirmed_detector()
        detector.process_frame(make_empty_result())  # → ALERTED
        assert detector.state == DetectionState.ALERTED

    def test_alerted_moves_to_cooldown(self):
        detector = self._get_confirmed_detector()
        detector.process_frame(make_empty_result())  # → ALERTED
        detector.process_frame(make_empty_result())  # → COOLDOWN
        assert detector.state == DetectionState.COOLDOWN

    def test_cooldown_prevents_new_events(self):
        detector = self._get_confirmed_detector()
        detector.process_frame(make_empty_result())  # → ALERTED
        detector.process_frame(make_empty_result())  # → COOLDOWN

        # Even with strong delivery signal, no event during cooldown
        for _ in range(5):
            event = detector.process_frame(make_delivery_result())
            assert event is None
            assert detector.state == DetectionState.COOLDOWN

    def test_cooldown_expires_returns_to_idle(self):
        config = make_config(confirmation_frames=1, cooldown_minutes=1)
        detector = DeliveryDetector(config)
        detector.process_frame(make_person_only_result())
        detector.process_frame(make_delivery_result())
        detector.process_frame(make_empty_result())  # → ALERTED
        detector.process_frame(make_empty_result())  # → COOLDOWN

        # Simulate expired cooldown
        detector.ctx.last_alert_time = datetime.utcnow() - timedelta(minutes=2)
        detector.process_frame(make_empty_result())
        assert detector.state == DetectionState.IDLE


# ── Package label detection tests ──────────────────────────────────────────

class TestPackageLabelDetection:
    def test_box_label_detected(self):
        config = make_config(confirmation_frames=1)
        detector = DeliveryDetector(config)
        detector.process_frame(make_person_only_result())
        event = detector.process_frame(make_result({"Person": 90.0, "Box": 85.0}))
        assert event is not None

    def test_luggage_label_detected(self):
        config = make_config(confirmation_frames=1)
        detector = DeliveryDetector(config)
        detector.process_frame(make_person_only_result())
        event = detector.process_frame(make_result({"Person": 90.0, "Luggage": 82.0}))
        assert event is not None

    def test_low_confidence_package_ignored(self):
        config = make_config(confirmation_frames=1, pkg_conf=75.0)
        detector = DeliveryDetector(config)
        detector.process_frame(make_person_only_result())
        event = detector.process_frame(make_result({"Person": 90.0, "Package": 60.0}))
        assert event is None  # Package confidence below threshold


# ── Stats tests ─────────────────────────────────────────────────────────────

class TestStats:
    def test_stats_increments_on_delivery(self):
        config = make_config(confirmation_frames=1)
        detector = DeliveryDetector(config)
        assert detector.stats["deliveries_confirmed"] == 0

        detector.process_frame(make_person_only_result())
        detector.process_frame(make_delivery_result())
        assert detector.stats["deliveries_confirmed"] == 1

    def test_scan_count_increments(self):
        detector = DeliveryDetector(make_config())
        for i in range(5):
            detector.process_frame(make_empty_result())
        assert detector.stats["total_scans"] == 5
