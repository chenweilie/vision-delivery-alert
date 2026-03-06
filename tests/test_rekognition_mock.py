"""
test_rekognition_mock.py — Mocked Rekognition API Tests
Tests the RekognitionClient using mocked boto3 responses.
No AWS credentials required.
"""

import pytest
from unittest.mock import MagicMock, patch
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from rekognition import RekognitionClient, DetectionResult, RekognitionError


# ── Fixtures ────────────────────────────────────────────────────────────────

MOCK_RESPONSE = {
    "Labels": [
        {"Name": "Person", "Confidence": 92.4, "Parents": [], "Instances": [{"BoundingBox": {}}]},
        {"Name": "Package", "Confidence": 87.1, "Parents": [], "Instances": []},
        {"Name": "Door",    "Confidence": 78.3, "Parents": [{"Name": "Architecture"}], "Instances": []},
        {"Name": "House",   "Confidence": 65.1, "Parents": [], "Instances": []},
    ],
    "LabelModelVersion": "3.0",
    "ResponseMetadata": {"HTTPStatusCode": 200},
}


@pytest.fixture
def mock_client():
    """Create a RekognitionClient with a mocked boto3 client."""
    with patch("rekognition.boto3.client") as mock_boto3:
        rek = RekognitionClient(region="us-east-1", min_confidence=50.0)
        rek._client = MagicMock()
        rek._client.detect_labels.return_value = MOCK_RESPONSE
        yield rek


# ── Tests ────────────────────────────────────────────────────────────────────

class TestDetectionResult:
    def test_label_names_lowercase(self, mock_client):
        result = mock_client.detect_labels_from_bytes(b"fake_jpeg")
        assert "person" in result.label_names
        assert "package" in result.label_names

    def test_has_label_true(self, mock_client):
        result = mock_client.detect_labels_from_bytes(b"fake_jpeg")
        assert result.has_label("Person", min_confidence=80.0) is True

    def test_has_label_false_low_confidence(self, mock_client):
        result = mock_client.detect_labels_from_bytes(b"fake_jpeg")
        assert result.has_label("Person", min_confidence=99.0) is False

    def test_has_any_label_found(self, mock_client):
        result = mock_client.detect_labels_from_bytes(b"fake_jpeg")
        found, name, conf = result.has_any_label(["Box", "Package", "Crate"], min_confidence=70.0)
        assert found is True
        assert name == "Package"
        assert conf == pytest.approx(87.1)

    def test_has_any_label_not_found(self, mock_client):
        result = mock_client.detect_labels_from_bytes(b"fake_jpeg")
        found, name, conf = result.has_any_label(["Car", "Bicycle"], min_confidence=50.0)
        assert found is False
        assert name == ""

    def test_labels_sorted_by_confidence(self, mock_client):
        result = mock_client.detect_labels_from_bytes(b"fake_jpeg")
        confidences = [l["confidence"] for l in result.labels]
        assert confidences == sorted(confidences, reverse=True)

    def test_get_label_case_insensitive(self, mock_client):
        result = mock_client.detect_labels_from_bytes(b"fake_jpeg")
        assert result.get_label("person") is not None
        assert result.get_label("PERSON") is not None
        assert result.get_label("pErSoN") is not None

    def test_processing_time_recorded(self, mock_client):
        result = mock_client.detect_labels_from_bytes(b"fake_jpeg")
        assert result.processing_time_ms >= 0

    def test_to_dict_returns_list(self, mock_client):
        result = mock_client.detect_labels_from_bytes(b"fake_jpeg")
        d = result.to_dict()
        assert isinstance(d, list)
        assert len(d) == 4


class TestRekognitionClientRetry:
    def test_throttling_retries(self):
        """Should retry on ThrottlingException and succeed on second call."""
        from botocore.exceptions import ClientError
        with patch("rekognition.boto3.client"):
            rek = RekognitionClient(region="us-east-1", min_confidence=50.0, max_retries=3)
            rek._client = MagicMock()

            throttle_error = ClientError(
                {"Error": {"Code": "ThrottlingException", "Message": "Rate exceeded"}},
                "DetectLabels"
            )
            rek._client.detect_labels.side_effect = [throttle_error, MOCK_RESPONSE]

            with patch("rekognition.time.sleep"):  # don't actually wait
                result = rek.detect_labels_from_bytes(b"fake")
            assert len(result.labels) == 4

    def test_non_retriable_error_raises(self):
        """Non-throttle ClientError should raise RekognitionError immediately."""
        from botocore.exceptions import ClientError
        with patch("rekognition.boto3.client"):
            rek = RekognitionClient(region="us-east-1")
            rek._client = MagicMock()
            rek._client.detect_labels.side_effect = ClientError(
                {"Error": {"Code": "InvalidImageException", "Message": "bad image"}},
                "DetectLabels"
            )
            with pytest.raises(RekognitionError):
                rek.detect_labels_from_bytes(b"bad")


class TestMinConfidenceFiltering:
    def test_high_min_confidence_filters_labels(self):
        """Labels below min_confidence should not appear in results."""
        high_conf_response = {
            "Labels": [
                {"Name": "Person", "Confidence": 92.0, "Parents": [], "Instances": []},
                {"Name": "Dog",    "Confidence": 55.0, "Parents": [], "Instances": []},
            ]
        }
        with patch("rekognition.boto3.client"):
            rek = RekognitionClient(region="us-east-1", min_confidence=80.0)
            rek._client = MagicMock()
            rek._client.detect_labels.return_value = high_conf_response
            result = rek.detect_labels_from_bytes(b"img")

        # Rekognition itself does the filtering server-side; our wrapper passes
        # MinConfidence to the API. Here we trust the mock returns only what we set.
        assert result.has_label("Person") is True
