"""
monitor.py — Main Orchestration Loop
The central coordinator that ties together capture → detect → decide → alert.
Run this directly for continuous monitoring mode.

Usage:
    python monitor.py [--config config.yaml] [--source 0] [--demo]
"""

import argparse
import logging
import signal
import sys
import time
from pathlib import Path
from typing import Optional

# Allow running from src/ directory
sys.path.insert(0, str(Path(__file__).parent))

from capture import ImageCapture, CaptureError
from config import load_config, AppConfig
from detection_logic import DeliveryDetector
from logger import EventLogger, setup_logging
from notifier import NotificationService
from rekognition import RekognitionClient, RekognitionError


class DeliveryMonitor:
    """
    Top-level orchestrator.
    Runs a continuous monitoring loop: Capture → Detect → Decide → Alert → Log.
    """

    def __init__(self, config: AppConfig):
        self.config = config
        self.running = False

        # Initialize subsystems
        self.logger = setup_logging(config)
        self.event_logger = EventLogger(config)
        self.capture = ImageCapture(
            source=config.camera.source,
            save_dir=config.logging.frame_dir if config.logging.save_frames else None,
            width=config.camera.resolution_width,
            height=config.camera.resolution_height,
            reconnect_attempts=config.camera.reconnect_attempts,
        )
        self.rekognition = RekognitionClient(
            region=config.rekognition.region,
            max_labels=config.rekognition.max_labels,
            min_confidence=config.rekognition.min_confidence,
            aws_access_key_id=config.aws_access_key_id,
            aws_secret_access_key=config.aws_secret_access_key,
        )
        self.detector = DeliveryDetector(config)
        self.notifier = NotificationService(config)

        self.logger.info("=" * 60)
        self.logger.info("Amazon Rekognition Delivery Monitor")
        self.logger.info(f"  Camera source : {config.camera.source}")
        self.logger.info(f"  Capture interval: {config.camera.capture_interval}s")
        self.logger.info(f"  AWS Region    : {config.rekognition.region}")
        self.logger.info(f"  Person min confidence: {config.rekognition.person_confidence}%")
        self.logger.info(f"  Package min confidence: {config.rekognition.package_confidence}%")
        self.logger.info(f"  Confirm frames: {config.detection.confirmation_frames}")
        self.logger.info(f"  Cooldown: {config.detection.cooldown_minutes}min")
        self.logger.info("=" * 60)

    def run(self):
        """Main monitoring loop. Runs until interrupted."""
        self.running = True
        scan_number = 0

        while self.running:
            scan_start = time.perf_counter()
            scan_number += 1

            try:
                # ── Step 1: Capture ────────────────────────────────────────
                self.logger.debug(f"[Scan #{scan_number}] Capturing frame...")
                jpeg_bytes, frame_path = self.capture.capture_frame()

                # ── Step 2: Rekognition ────────────────────────────────────
                result = self.rekognition.detect_labels_from_bytes(jpeg_bytes)
                self.logger.debug(f"[Scan #{scan_number}] {result}")

                # ── Step 3: Detection Logic ────────────────────────────────
                delivery_event = self.detector.process_frame(result, frame_path or None)
                processing_ms = int((time.perf_counter() - scan_start) * 1000)

                # ── Step 4: Alert (if event confirmed) ─────────────────────
                alert_channels: list[str] = []
                alert_sent = False
                if delivery_event and delivery_event.should_alert:
                    notif_result = self.notifier.send_alert(delivery_event)
                    alert_sent = notif_result.any_success
                    alert_channels = notif_result.channels_succeeded

                # ── Step 5: Log ────────────────────────────────────────────
                is_delivery = delivery_event is not None
                self.event_logger.log_event(
                    labels=result.to_dict(),
                    frame_path=frame_path or None,
                    delivery_detected=is_delivery,
                    alert_sent=alert_sent,
                    alert_channels=alert_channels,
                    state_transition=self.detector.state.name,
                    processing_time_ms=processing_ms,
                    person_confidence=self.detector.ctx.last_person_confidence,
                    package_confidence=self.detector.ctx.last_package_confidence,
                )

                # ── Step 6: Status update ──────────────────────────────────
                if scan_number % 10 == 0:
                    stats = self.detector.stats
                    self.logger.info(
                        f"[Health] scans={stats['total_scans']} "
                        f"deliveries={stats['deliveries_confirmed']} "
                        f"state={stats['current_state']} "
                        f"proc={processing_ms}ms"
                    )

            except CaptureError as e:
                self.logger.error(f"Capture error: {e}")
                time.sleep(5)
                continue
            except RekognitionError as e:
                self.logger.error(f"Rekognition error: {e}")
                time.sleep(10)
                continue
            except Exception as e:
                self.logger.exception(f"Unexpected error in scan loop: {e}")
                time.sleep(5)
                continue

            # ── Throttle to capture_interval ──────────────────────────────
            elapsed = time.perf_counter() - scan_start
            sleep_time = max(0, self.config.camera.capture_interval - elapsed)
            time.sleep(sleep_time)

    def shutdown(self, sig=None, frame=None):
        """Graceful shutdown handler."""
        self.logger.info("Shutdown signal received. Stopping monitor...")
        self.running = False
        self.capture.release()
        stats = self.event_logger.get_stats()
        self.logger.info(f"Final stats: {stats}")
        self.logger.info("Monitor stopped.")


def main():
    parser = argparse.ArgumentParser(
        description="Amazon Rekognition Delivery Monitor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run with webcam
  python monitor.py

  # Run with IP camera
  python monitor.py --source rtsp://192.168.1.100:554/stream

  # Run in demo mode (static test image)
  python monitor.py --demo --source test_images/delivery.jpg

  # Custom config file
  python monitor.py --config /etc/delivery-monitor/config.yaml
        """,
    )
    parser.add_argument("--config", default=None, help="Path to config.yaml")
    parser.add_argument("--source", default=None, help="Camera source override")
    parser.add_argument("--demo", action="store_true", help="Demo mode (reduced sleep intervals)")
    args = parser.parse_args()

    config = load_config(args.config)

    if args.source:
        config.camera.source = args.source
    if args.demo:
        config.camera.capture_interval = 1  # faster in demo mode

    monitor = DeliveryMonitor(config)

    # Register graceful shutdown
    signal.signal(signal.SIGINT, monitor.shutdown)
    signal.signal(signal.SIGTERM, monitor.shutdown)

    monitor.run()


if __name__ == "__main__":
    main()
