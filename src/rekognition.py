"""
rekognition.py — Amazon Rekognition API Wrapper
Provides a clean, testable interface over boto3's Rekognition client.
Includes retry logic, confidence filtering, and structured label output.
"""

import base64
import logging
import time
from typing import Optional

import boto3
from botocore.exceptions import ClientError, EndpointConnectionError

logger = logging.getLogger("rekognition")


class RekognitionError(Exception):
    """Raised when Rekognition API call fails."""
    pass


class DetectionResult:
    """Structured output from a single Rekognition call."""

    def __init__(self, labels: list[dict], processing_time_ms: int):
        self.labels = labels  # [{"name": str, "confidence": float, "parents": list}]
        self.processing_time_ms = processing_time_ms

    @property
    def label_names(self) -> set[str]:
        return {l["name"].lower() for l in self.labels}

    def get_label(self, name: str) -> Optional[dict]:
        """Return label dict if found (case-insensitive), else None."""
        name_lower = name.lower()
        for label in self.labels:
            if label["name"].lower() == name_lower:
                return label
        return None

    def has_label(self, name: str, min_confidence: float = 0.0) -> bool:
        """Return True if label exists at or above min_confidence."""
        label = self.get_label(name)
        if label and label["confidence"] >= min_confidence:
            return True
        return False

    def has_any_label(self, names: list[str], min_confidence: float = 0.0) -> tuple[bool, str, float]:
        """
        Return (found, matching_label_name, confidence)
        for any label in the names list meeting the confidence threshold.
        """
        for name in names:
            label = self.get_label(name)
            if label and label["confidence"] >= min_confidence:
                return True, label["name"], label["confidence"]
        return False, "", 0.0

    def to_dict(self) -> list[dict]:
        return self.labels

    def __repr__(self):
        top = sorted(self.labels, key=lambda x: x["confidence"], reverse=True)[:5]
        return f"DetectionResult(top={[(l['name'], f\"{l['confidence']:.1f}%\") for l in top]}, t={self.processing_time_ms}ms)"


class RekognitionClient:
    """
    Thin, testable wrapper around boto3 Rekognition.
    Supports image bytes (from camera) and S3 objects.
    """

    def __init__(
        self,
        region: str = "us-east-1",
        max_labels: int = 20,
        min_confidence: float = 70.0,
        aws_access_key_id: Optional[str] = None,
        aws_secret_access_key: Optional[str] = None,
        max_retries: int = 3,
    ):
        self.region = region
        self.max_labels = max_labels
        self.min_confidence = min_confidence
        self.max_retries = max_retries

        session_kwargs = {"region_name": region}
        if aws_access_key_id and aws_secret_access_key:
            session_kwargs["aws_access_key_id"] = aws_access_key_id
            session_kwargs["aws_secret_access_key"] = aws_secret_access_key

        self._client = boto3.client("rekognition", **session_kwargs)
        logger.info(f"RekognitionClient initialized (region={region}, min_confidence={min_confidence})")

    def detect_labels_from_bytes(self, image_bytes: bytes) -> DetectionResult:
        """
        Send raw JPEG bytes to Rekognition DetectLabels.
        Returns structured DetectionResult.
        """
        start = time.perf_counter()
        for attempt in range(self.max_retries):
            try:
                response = self._client.detect_labels(
                    Image={"Bytes": image_bytes},
                    MaxLabels=self.max_labels,
                    MinConfidence=self.min_confidence,
                )
                elapsed_ms = int((time.perf_counter() - start) * 1000)
                labels = self._parse_labels(response)
                logger.debug(f"Rekognition OK: {len(labels)} labels in {elapsed_ms}ms")
                return DetectionResult(labels, elapsed_ms)

            except ClientError as e:
                code = e.response["Error"]["Code"]
                if code in ("ThrottlingException", "ProvisionedThroughputExceededException"):
                    wait = 2 ** attempt
                    logger.warning(f"Rekognition throttled, retrying in {wait}s...")
                    time.sleep(wait)
                    continue
                raise RekognitionError(f"Rekognition API error: {code} — {e}") from e
            except EndpointConnectionError as e:
                raise RekognitionError(f"Cannot reach AWS endpoint: {e}") from e

        raise RekognitionError(f"Rekognition failed after {self.max_retries} retries")

    def detect_labels_from_s3(self, bucket: str, key: str) -> DetectionResult:
        """Send an S3 object reference to Rekognition."""
        start = time.perf_counter()
        response = self._client.detect_labels(
            Image={"S3Object": {"Bucket": bucket, "Name": key}},
            MaxLabels=self.max_labels,
            MinConfidence=self.min_confidence,
        )
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        labels = self._parse_labels(response)
        return DetectionResult(labels, elapsed_ms)

    def _parse_labels(self, response: dict) -> list[dict]:
        """Normalize Rekognition response into clean list of dicts."""
        labels = []
        for item in response.get("Labels", []):
            labels.append({
                "name": item["Name"],
                "confidence": round(item["Confidence"], 2),
                "parents": [p["Name"] for p in item.get("Parents", [])],
                "instances": len(item.get("Instances", [])),
            })
        # Sort by confidence descending
        return sorted(labels, key=lambda x: x["confidence"], reverse=True)
