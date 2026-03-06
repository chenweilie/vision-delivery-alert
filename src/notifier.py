"""
notifier.py — Multi-Channel Notification Service
Sends delivery alerts via Email (SMTP), Slack Webhook, and Generic HTTP Webhooks.
Each channel is independent — failures in one do not block others.
"""

import json
import logging
import smtplib
import urllib.request
import urllib.error
from datetime import datetime
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

from config import AppConfig
from detection_logic import DeliveryEvent

logger = logging.getLogger("notifier")


class NotificationResult:
    def __init__(self):
        self.channels_attempted: list[str] = []
        self.channels_succeeded: list[str] = []
        self.channels_failed: list[str] = []

    def record(self, channel: str, success: bool):
        self.channels_attempted.append(channel)
        if success:
            self.channels_succeeded.append(channel)
        else:
            self.channels_failed.append(channel)

    @property
    def any_success(self) -> bool:
        return bool(self.channels_succeeded)

    def __repr__(self):
        return (
            f"NotificationResult("
            f"ok={self.channels_succeeded}, "
            f"fail={self.channels_failed})"
        )


class NotificationService:
    """
    Dispatches delivery alerts across configured channels.
    Designed to be fault-tolerant: each channel is tried independently.
    """

    def __init__(self, config: AppConfig):
        self.config = config
        logger.info(
            f"NotificationService init — "
            f"email={config.notification.email_enabled}, "
            f"slack={config.notification.slack_enabled}, "
            f"webhook={config.notification.webhook_enabled}"
        )

    def send_alert(self, event: DeliveryEvent) -> NotificationResult:
        """
        Dispatch alert for a confirmed delivery event.
        Returns NotificationResult with per-channel status.
        """
        result = NotificationResult()
        cfg = self.config.notification

        if cfg.email_enabled:
            ok = self._send_email(event)
            result.record("email", ok)

        if cfg.slack_enabled and cfg.slack_webhook_url:
            ok = self._send_slack(event)
            result.record("slack", ok)

        if cfg.webhook_enabled and cfg.webhook_url:
            ok = self._send_webhook(event)
            result.record("webhook", ok)

        if not result.channels_attempted:
            logger.warning("No notification channels configured!")

        logger.info(f"Alert dispatch complete: {result}")
        return result

    # ── Email ──────────────────────────────────────────────────────────────

    def _send_email(self, event: DeliveryEvent) -> bool:
        """Send HTML email with attached frame image via SMTP."""
        cfg = self.config.notification
        try:
            msg = MIMEMultipart("related")
            msg["Subject"] = f"📦 Delivery Detected — {self._fmt_time(event.timestamp)}"
            msg["From"] = cfg.sender_email or cfg.smtp_user
            msg["To"] = ", ".join(cfg.recipient_emails)

            # HTML body
            top_labels = ", ".join(
                f"{l['name']} ({l['confidence']:.0f}%)"
                for l in event.labels[:5]
            )
            html = f"""
<html><body style="font-family: Arial, sans-serif; background:#f4f4f4; padding:20px;">
  <div style="max-width:600px; background:white; border-radius:8px; overflow:hidden; box-shadow:0 2px 8px rgba(0,0,0,0.1);">
    <div style="background:#FF9900; padding:16px 24px;">
      <h2 style="color:white; margin:0;">📦 Delivery Alert</h2>
    </div>
    <div style="padding:24px;">
      <p><strong>🕒 Time:</strong> {self._fmt_time(event.timestamp)}</p>
      <p><strong>👤 Person Confidence:</strong> {event.person_confidence:.1f}%</p>
      <p><strong>📦 Package Confidence:</strong> {event.package_confidence:.1f}%</p>
      <p><strong>🔍 Detected Labels:</strong> {top_labels}</p>
      <p><strong>🔄 State Transition:</strong> {event.state_transition}</p>
      {"<img src='cid:frame_image' style='max-width:100%;border-radius:4px;margin-top:12px;' />" if event.frame_path else ""}
    </div>
    <div style="background:#f9f9f9; padding:12px 24px; font-size:12px; color:#888;">
      Amazon Rekognition Delivery Monitor — Automated Alert
    </div>
  </div>
</body></html>"""
            msg.attach(MIMEText(html, "html"))

            # Attach frame image if available
            if event.frame_path and Path(event.frame_path).exists():
                with open(event.frame_path, "rb") as f:
                    img = MIMEImage(f.read())
                img.add_header("Content-ID", "<frame_image>")
                img.add_header("Content-Disposition", "inline", filename="frame.jpg")
                msg.attach(img)

            with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port) as smtp:
                smtp.ehlo()
                smtp.starttls()
                smtp.login(cfg.smtp_user, cfg.smtp_password)
                smtp.send_message(msg)

            logger.info(f"Email sent to {cfg.recipient_emails}")
            return True
        except Exception as e:
            logger.error(f"Email failed: {e}")
            return False

    # ── Slack ──────────────────────────────────────────────────────────────

    def _send_slack(self, event: DeliveryEvent) -> bool:
        """Send a rich Slack message via Incoming Webhook."""
        top_labels = "\n".join(
            f"• {l['name']}: {l['confidence']:.0f}%"
            for l in event.labels[:6]
        )
        payload = {
            "text": "📦 *Delivery Detected!*",
            "attachments": [
                {
                    "color": "#FF9900",
                    "blocks": [
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": (
                                    f"*📦 Package Delivery Detected*\n"
                                    f"*Time:* {self._fmt_time(event.timestamp)}\n"
                                    f"*Person Confidence:* {event.person_confidence:.1f}%\n"
                                    f"*Package Confidence:* {event.package_confidence:.1f}%"
                                ),
                            },
                        },
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f"*Detected Labels:*\n{top_labels}",
                            },
                        },
                        {
                            "type": "context",
                            "elements": [
                                {
                                    "type": "mrkdwn",
                                    "text": f"Transition: `{event.state_transition}` | ID: `{event.event_id[:8]}`",
                                }
                            ],
                        },
                    ],
                }
            ],
        }
        return self._post_json(self.config.notification.slack_webhook_url, payload, "Slack")

    # ── Generic Webhook ────────────────────────────────────────────────────

    def _send_webhook(self, event: DeliveryEvent) -> bool:
        """POST delivery event payload to a generic HTTP webhook."""
        payload = {
            "event": "delivery_detected",
            "event_id": event.event_id,
            "timestamp": event.timestamp.isoformat() + "Z",
            "person_confidence": event.person_confidence,
            "package_confidence": event.package_confidence,
            "labels": event.labels[:10],
            "state_transition": event.state_transition,
            "frame_path": event.frame_path,
        }
        return self._post_json(self.config.notification.webhook_url, payload, "Webhook")

    # ── Helpers ────────────────────────────────────────────────────────────

    def _post_json(self, url: str, payload: dict, channel: str) -> bool:
        """POST JSON payload to a URL."""
        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                logger.info(f"{channel} webhook OK (HTTP {resp.status})")
                return True
        except urllib.error.HTTPError as e:
            logger.error(f"{channel} webhook HTTP error: {e.code} — {e.read()}")
            return False
        except Exception as e:
            logger.error(f"{channel} webhook failed: {e}")
            return False

    @staticmethod
    def _fmt_time(dt: datetime) -> str:
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
