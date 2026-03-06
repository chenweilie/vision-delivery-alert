"""
handler.py — AWS Lambda Entry Point
Serverless deployment variant of the delivery monitor.
Triggered by EventBridge (CloudWatch Events) on a schedule (e.g., every 30s).

Environment Variables Required:
    CAMERA_IMAGE_S3_BUCKET  — S3 bucket containing the latest camera frame
    CAMERA_IMAGE_S3_KEY     — S3 object key of the frame (e.g., "live/latest.jpg")
    SLACK_WEBHOOK_URL       — Slack Incoming Webhook URL
    ALERT_RECIPIENTS        — Comma-separated email list
    AWS_REGION              — AWS region (set automatically in Lambda)
"""

import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# Add src to path for shared modules
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from config import load_config
from rekognition import RekognitionClient, RekognitionError
from detection_logic import DeliveryDetector
from notifier import NotificationService

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Module-level detector (survives Lambda warm starts → maintains state)
_detector: DeliveryDetector | None = None
_config = None


def _get_config():
    global _config
    if _config is None:
        _config = load_config()
    return _config


def _get_detector() -> DeliveryDetector:
    """Return singleton detector (warm-start friendly)."""
    global _detector
    if _detector is None:
        _detector = DeliveryDetector(_get_config())
        logger.info("DeliveryDetector initialized (cold start)")
    return _detector


def lambda_handler(event, context):
    """
    Lambda entry point.
    Expected invocation: EventBridge scheduled rule every 5-10 seconds.

    Returns:
        dict with statusCode and detection result
    """
    start = time.perf_counter()
    config = _get_config()
    detector = _get_detector()

    # ── Source: S3 image or direct bytes in event ───────────────────────
    bucket = os.environ.get("CAMERA_IMAGE_S3_BUCKET") or event.get("bucket")
    key = os.environ.get("CAMERA_IMAGE_S3_KEY") or event.get("key", "live/latest.jpg")

    if not bucket:
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "CAMERA_IMAGE_S3_BUCKET not configured"}),
        }

    # ── Rekognition ─────────────────────────────────────────────────────
    rek_client = RekognitionClient(
        region=config.rekognition.region,
        max_labels=config.rekognition.max_labels,
        min_confidence=config.rekognition.min_confidence,
    )

    try:
        result = rek_client.detect_labels_from_s3(bucket, key)
    except RekognitionError as e:
        logger.error(f"Rekognition error: {e}")
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}

    # ── Detection Logic ──────────────────────────────────────────────────
    frame_path = f"s3://{bucket}/{key}"
    delivery_event = detector.process_frame(result, frame_path)

    processing_ms = int((time.perf_counter() - start) * 1000)

    # ── Notification ─────────────────────────────────────────────────────
    alert_sent = False
    alert_channels: list[str] = []
    if delivery_event and delivery_event.should_alert:
        notifier = NotificationService(config)
        notif_result = notifier.send_alert(delivery_event)
        alert_sent = notif_result.any_success
        alert_channels = notif_result.channels_succeeded
        logger.warning(f"DELIVERY ALERT SENT via {alert_channels}")

    response_body = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "delivery_detected": delivery_event is not None,
        "alert_sent": alert_sent,
        "alert_channels": alert_channels,
        "detector_state": detector.state.name,
        "detector_stats": detector.stats,
        "top_labels": [
            {"name": l["name"], "confidence": l["confidence"]}
            for l in result.labels[:8]
        ],
        "processing_time_ms": processing_ms,
        "s3_source": frame_path,
    }

    logger.info(json.dumps(response_body))
    return {
        "statusCode": 200,
        "body": json.dumps(response_body),
    }
