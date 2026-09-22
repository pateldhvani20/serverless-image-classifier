"""Unit tests for the classify_image Lambda function.

Uses **moto** to mock S3 and DynamoDB, and ``unittest.mock.MagicMock`` for
the Rekognition client (moto's Rekognition support does not return
configurable label data).
"""

import json
import os
import sys
from decimal import Decimal
from unittest.mock import MagicMock

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws  # type: ignore

# ---------------------------------------------------------------------------
# Path setup & env bootstrap — env vars MUST be set before importing ``app``
# because it creates boto3 clients at module level.
# ---------------------------------------------------------------------------
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")

_CLASSIFY_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), os.pardir, "classify_image")
)
sys.path.insert(0, _CLASSIFY_DIR)

import app as classify_module  # noqa: E402  # type: ignore

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
BUCKET = "test-image-uploads"
TABLE = "ClassificationResults"
REGION = "us-east-1"

MOCK_LABELS = [
    {"Name": "Cat", "Confidence": 98.5, "Instances": [], "Parents": []},
    {"Name": "Animal", "Confidence": 95.2, "Instances": [], "Parents": [{"Name": "Cat"}]},
    {"Name": "Pet", "Confidence": 92.1, "Instances": [], "Parents": [{"Name": "Animal"}]},
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _s3_event(bucket: str, key: str) -> dict:
    """Build a minimal S3 ``ObjectCreated`` event payload."""
    return {
        "Records": [
            {
                "s3": {
                    "bucket": {"name": bucket},
                    "object": {"key": key},
                }
            }
        ]
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _env(monkeypatch):
    """Inject required environment variables for every test."""
    monkeypatch.setenv("TABLE_NAME", TABLE)
    monkeypatch.setenv("BUCKET_NAME", BUCKET)
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")


@pytest.fixture()
def aws():
    """Stand up mocked S3 + DynamoDB (via moto) and a Rekognition stub.

    Module-level clients in ``app`` are replaced for the duration of each
    test and restored afterwards.
    """
    with mock_aws():
        # ---- S3 --------------------------------------------------------
        s3 = boto3.client("s3", region_name=REGION)
        s3.create_bucket(Bucket=BUCKET)
        s3.put_object(Bucket=BUCKET, Key="images/cat.jpg", Body=b"\xff\xd8fake")
        s3.put_object(Bucket=BUCKET, Key="data/report.csv", Body=b"col1,col2")

        # ---- DynamoDB --------------------------------------------------
        dynamodb = boto3.resource("dynamodb", region_name=REGION)
        dynamodb.create_table(
            TableName=TABLE,
            KeySchema=[{"AttributeName": "imageId", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "imageId", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        table = dynamodb.Table(TABLE)

        # ---- Rekognition (MagicMock) -----------------------------------
        mock_rek = MagicMock()
        mock_rek.detect_labels.return_value = {"Labels": list(MOCK_LABELS)}

        # Swap module-level clients so the handler uses our mocks
        orig_dynamo = classify_module.dynamodb_resource
        orig_rek = classify_module.rekognition_client

        classify_module.dynamodb_resource = dynamodb
        classify_module.rekognition_client = mock_rek

        yield {
            "s3": s3,
            "dynamodb": dynamodb,
            "table": table,
            "rekognition": mock_rek,
        }

        # Restore originals
        classify_module.dynamodb_resource = orig_dynamo
        classify_module.rekognition_client = orig_rek


# ===================================================================
# Happy path
# ===================================================================
class TestSuccessfulClassification:
    """Supported image uploaded → Rekognition labels → DynamoDB write."""

    def test_returns_200(self, aws):
        result = classify_module.lambda_handler(
            _s3_event(BUCKET, "images/cat.jpg"), None,
        )
        assert result["statusCode"] == 200

    def test_top_label_stored_in_dynamodb(self, aws):
        classify_module.lambda_handler(
            _s3_event(BUCKET, "images/cat.jpg"), None,
        )
        items = aws["table"].scan()["Items"]
        assert len(items) == 1
        assert items[0]["label"] == "Cat"

    def test_all_required_fields_present(self, aws):
        classify_module.lambda_handler(
            _s3_event(BUCKET, "images/cat.jpg"), None,
        )
        item = aws["table"].scan()["Items"][0]
        required = {"imageId", "label", "confidence", "s3Key", "timestamp"}
        assert required.issubset(item.keys())
        assert item["s3Key"] == "images/cat.jpg"
        assert item["confidence"] == Decimal("98.5")

    def test_rekognition_called_with_correct_image(self, aws):
        classify_module.lambda_handler(
            _s3_event(BUCKET, "images/cat.jpg"), None,
        )
        aws["rekognition"].detect_labels.assert_called_once_with(
            Image={"S3Object": {"Bucket": BUCKET, "Name": "images/cat.jpg"}},
            MaxLabels=10,
            MinConfidence=70.0,
        )

    def test_response_body_contains_result(self, aws):
        result = classify_module.lambda_handler(
            _s3_event(BUCKET, "images/cat.jpg"), None,
        )
        body = json.loads(result["body"])
        assert body["results"][0]["label"] == "Cat"
        assert body["results"][0]["confidence"] == 98.5


# ===================================================================
# Unsupported file type
# ===================================================================
class TestUnsupportedFileType:
    """Non-image files should be skipped without raising."""

    def test_csv_returns_200_with_error_detail(self, aws):
        result = classify_module.lambda_handler(
            _s3_event(BUCKET, "data/report.csv"), None,
        )
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert "error" in body["results"][0]
        assert "Unsupported" in body["results"][0]["error"]

    def test_no_dynamodb_write(self, aws):
        classify_module.lambda_handler(
            _s3_event(BUCKET, "data/report.csv"), None,
        )
        assert aws["table"].scan()["Items"] == []

    def test_rekognition_not_called(self, aws):
        classify_module.lambda_handler(
            _s3_event(BUCKET, "data/report.csv"), None,
        )
        aws["rekognition"].detect_labels.assert_not_called()


# ===================================================================
# Rekognition failure
# ===================================================================
class TestRekognitionFailure:
    """AWS service errors should propagate so Lambda retries the batch."""

    def test_client_error_raises(self, aws):
        aws["rekognition"].detect_labels.side_effect = ClientError(
            {"Error": {"Code": "InvalidImageFormatException",
                       "Message": "Bad image"}},
            "DetectLabels",
        )
        with pytest.raises(ClientError) as exc_info:
            classify_module.lambda_handler(
                _s3_event(BUCKET, "images/cat.jpg"), None,
            )
        assert exc_info.value.response["Error"]["Code"] == "InvalidImageFormatException"

    def test_no_dynamodb_write_on_failure(self, aws):
        aws["rekognition"].detect_labels.side_effect = ClientError(
            {"Error": {"Code": "InternalServerError",
                       "Message": "Service unavailable"}},
            "DetectLabels",
        )
        with pytest.raises(ClientError):
            classify_module.lambda_handler(
                _s3_event(BUCKET, "images/cat.jpg"), None,
            )
        assert aws["table"].scan()["Items"] == []
