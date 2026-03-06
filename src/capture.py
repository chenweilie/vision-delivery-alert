"""
capture.py — Image Capture Service
Handles frame acquisition from multiple source types:
  - Local webcam (OpenCV)
  - IP camera via RTSP/HTTP (OpenCV / requests)
  - Static image file (testing/demo mode)
"""

import io
import logging
import time
from pathlib import Path
from typing import Optional
from datetime import datetime

logger = logging.getLogger("capture")


class CaptureError(Exception):
    """Raised when frame capture fails unrecoverably."""
    pass


class ImageCapture:
    """
    Unified image capture interface.
    Returns raw JPEG bytes suitable for sending directly to Rekognition.
    """

    def __init__(self, source: str, save_dir: Optional[str] = None,
                 width: int = 1280, height: int = 720,
                 reconnect_attempts: int = 5):
        self.source = source
        self.save_dir = Path(save_dir) if save_dir else None
        self.width = width
        self.height = height
        self.reconnect_attempts = reconnect_attempts
        self._cap = None  # OpenCV VideoCapture

        if self.save_dir:
            self.save_dir.mkdir(parents=True, exist_ok=True)

        self._mode = self._detect_mode()
        logger.info(f"ImageCapture init → mode={self._mode} source={self.source}")

    def _detect_mode(self) -> str:
        """Identify how to interpret the source string."""
        if Path(self.source).exists():
            return "static_image"
        if self.source.startswith(("rtsp://", "http://", "https://")):
            return "ip_camera"
        # Assume webcam index (e.g., "0", "1")
        return "webcam"

    def _open_cv_capture(self):
        """Open (or reopen) an OpenCV VideoCapture."""
        try:
            import cv2
        except ImportError:
            raise CaptureError("opencv-python is required. Run: pip install opencv-python")

        source = self.source if self._mode == "ip_camera" else int(self.source)
        self._cap = cv2.VideoCapture(source)
        if self._mode != "ip_camera":
            self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)

        if not self._cap.isOpened():
            raise CaptureError(f"Cannot open video source: {self.source}")
        logger.info(f"VideoCapture opened ({self._mode})")

    def capture_frame(self) -> tuple[bytes, str]:
        """
        Capture a single frame and return (jpeg_bytes, saved_path_or_empty).
        Handles reconnection on failure.
        """
        if self._mode == "static_image":
            return self._capture_static()

        # OpenCV-based capture
        if self._cap is None or not self._cap.isOpened():
            self._open_cv_capture()

        import cv2
        for attempt in range(self.reconnect_attempts):
            ret, frame = self._cap.read()
            if ret:
                return self._encode_frame(frame)
            logger.warning(f"Frame read failed (attempt {attempt + 1}/{self.reconnect_attempts})")
            self._cap.release()
            time.sleep(2)
            self._open_cv_capture()

        raise CaptureError(f"Failed to capture frame after {self.reconnect_attempts} attempts")

    def _encode_frame(self, frame) -> tuple[bytes, str]:
        """Encode OpenCV frame to JPEG bytes and optionally save to disk."""
        import cv2
        _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
        jpeg_bytes = buf.tobytes()
        saved_path = ""
        if self.save_dir:
            ts = datetime.utcnow().strftime("%Y-%m-%dT%H-%M-%S")
            saved_path = str(self.save_dir / f"{ts}.jpg")
            with open(saved_path, "wb") as f:
                f.write(jpeg_bytes)
        return jpeg_bytes, saved_path

    def _capture_static(self) -> tuple[bytes, str]:
        """Return raw bytes from a static image file."""
        with open(self.source, "rb") as f:
            data = f.read()
        logger.debug(f"Static image loaded: {self.source} ({len(data)} bytes)")
        return data, self.source

    def release(self):
        """Release camera resources."""
        if self._cap and self._cap.isOpened():
            self._cap.release()
            logger.info("VideoCapture released")
