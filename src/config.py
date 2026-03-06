"""
config.py — Configuration Loader
Loads settings from config.yaml and overrides with environment variables.
Follows Twelve-Factor App principles for config management.
"""

import os
import yaml
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional
from dotenv import load_dotenv

# Load .env file if it exists
load_dotenv()

ROOT_DIR = Path(__file__).parent.parent


@dataclass
class CameraConfig:
    source: str = "0"              # "0" = webcam, RTSP URL = IP cam, path = static image
    capture_interval: int = 5      # seconds between frames
    resolution_width: int = 1280
    resolution_height: int = 720
    reconnect_attempts: int = 5
    reconnect_delay: int = 3


@dataclass
class RekognitionConfig:
    region: str = "us-east-1"
    max_labels: int = 20
    min_confidence: float = 70.0
    person_confidence: float = 80.0
    package_confidence: float = 70.0
    # Labels that indicate a package
    package_labels: list = field(default_factory=lambda: [
        "Package", "Box", "Cardboard", "Luggage", "Bag", "Crate"
    ])
    # Labels that indicate a delivery context location
    location_labels: list = field(default_factory=lambda: [
        "Door", "Porch", "Entrance", "House", "Building", "Gate"
    ])


@dataclass
class DetectionConfig:
    confirmation_frames: int = 2        # consecutive positive frames to confirm event
    cooldown_minutes: int = 30          # minutes between alerts for same zone
    state_timeout_seconds: int = 60     # seconds before resetting CANDIDATE state


@dataclass
class NotificationConfig:
    # Email (SMTP or Amazon SES)
    email_enabled: bool = False
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    sender_email: str = ""
    recipient_emails: list = field(default_factory=list)

    # Slack
    slack_enabled: bool = False
    slack_webhook_url: str = ""
    slack_channel: str = "#deliveries"

    # Generic Webhook
    webhook_enabled: bool = False
    webhook_url: str = ""
    webhook_secret: str = ""


@dataclass
class LoggingConfig:
    log_dir: str = "logs"
    frame_dir: str = "frames"
    db_path: str = "events.db"
    log_level: str = "INFO"
    save_frames: bool = True


@dataclass
class AppConfig:
    camera: CameraConfig = field(default_factory=CameraConfig)
    rekognition: RekognitionConfig = field(default_factory=RekognitionConfig)
    detection: DetectionConfig = field(default_factory=DetectionConfig)
    notification: NotificationConfig = field(default_factory=NotificationConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    dashboard_port: int = 8080
    aws_access_key_id: Optional[str] = None
    aws_secret_access_key: Optional[str] = None


def load_config(config_path: Optional[str] = None) -> AppConfig:
    """Load configuration from YAML file and environment variables."""
    config = AppConfig()

    # Load YAML config
    yaml_path = config_path or os.getenv("CONFIG_PATH", str(ROOT_DIR / "config.yaml"))
    if os.path.exists(yaml_path):
        with open(yaml_path, "r") as f:
            raw = yaml.safe_load(f) or {}

        # Camera
        if cam := raw.get("camera"):
            config.camera.source = cam.get("source", config.camera.source)
            config.camera.capture_interval = cam.get("capture_interval", config.camera.capture_interval)
            config.camera.resolution_width = cam.get("resolution_width", config.camera.resolution_width)
            config.camera.resolution_height = cam.get("resolution_height", config.camera.resolution_height)

        # Rekognition
        if rek := raw.get("rekognition"):
            config.rekognition.region = rek.get("region", config.rekognition.region)
            config.rekognition.max_labels = rek.get("max_labels", config.rekognition.max_labels)
            config.rekognition.min_confidence = rek.get("min_confidence", config.rekognition.min_confidence)
            config.rekognition.person_confidence = rek.get("person_confidence", config.rekognition.person_confidence)
            config.rekognition.package_confidence = rek.get("package_confidence", config.rekognition.package_confidence)

        # Detection
        if det := raw.get("detection"):
            config.detection.confirmation_frames = det.get("confirmation_frames", config.detection.confirmation_frames)
            config.detection.cooldown_minutes = det.get("cooldown_minutes", config.detection.cooldown_minutes)

        # Notification
        if notif := raw.get("notification"):
            email = notif.get("email", {})
            config.notification.email_enabled = email.get("enabled", False)
            config.notification.smtp_host = email.get("smtp_host", config.notification.smtp_host)
            config.notification.smtp_port = email.get("smtp_port", config.notification.smtp_port)
            config.notification.recipient_emails = email.get("recipients", [])

            slack = notif.get("slack", {})
            config.notification.slack_enabled = slack.get("enabled", False)

            webhook = notif.get("webhook", {})
            config.notification.webhook_enabled = webhook.get("enabled", False)

        # Logging
        if log := raw.get("logging"):
            config.logging.log_level = log.get("level", config.logging.log_level)
            config.logging.save_frames = log.get("save_frames", config.logging.save_frames)

        config.dashboard_port = raw.get("dashboard_port", config.dashboard_port)

    # Environment variable overrides (highest priority)
    if key := os.getenv("AWS_ACCESS_KEY_ID"):
        config.aws_access_key_id = key
    if secret := os.getenv("AWS_SECRET_ACCESS_KEY"):
        config.aws_secret_access_key = secret
    if region := os.getenv("AWS_REGION"):
        config.rekognition.region = region
    if source := os.getenv("CAMERA_SOURCE"):
        config.camera.source = source
    if webhook := os.getenv("SLACK_WEBHOOK_URL"):
        config.notification.slack_webhook_url = webhook
        config.notification.slack_enabled = True
    if webhook := os.getenv("WEBHOOK_URL"):
        config.notification.webhook_url = webhook
        config.notification.webhook_enabled = True
    if smtp_pass := os.getenv("SMTP_PASSWORD"):
        config.notification.smtp_password = smtp_pass
    if smtp_user := os.getenv("SMTP_USER"):
        config.notification.smtp_user = smtp_user
        config.notification.sender_email = smtp_user
    if recipients := os.getenv("ALERT_RECIPIENTS"):
        config.notification.recipient_emails = [r.strip() for r in recipients.split(",")]
        config.notification.email_enabled = True

    return config
