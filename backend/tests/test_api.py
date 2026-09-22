"""Unit tests for the upload and get-result API Lambda functions.

Uses moto to mock S3 and DynamoDB services.
"""

import base64
import importlib.util
import json
import os
import sys
from decimal import Decimal
from unittest.mock import MagicMock

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

# Environment setup
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")

_UPLOAD_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), os.pardir, "api", "upload")
)
_GET_RESULT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), os.pardir, "api", "get_result")
)

# Load upload module under unique module name to avoid sys.modules collision
upload_spec = importlib.util.spec_from_file_location(
    "api_upload_app", os.path.join(_UPLOAD_DIR, "app.py")
)
upload_app = importlib.util.module_from_spec(upload_spec)
upload_spec.loader.exec_module(upload_app)

# Load get_result module under unique module name
get_result_spec = importlib.util.spec_from_file_location(
    "api_get_result_app", os.path.join(_GET_RESULT_DIR, "app.py")
)
get_result_app = importlib.util.module_from_spec(get_result_spec)
get_result_spec.loader.exec_module(get_result_app)

BUCKET = "test-image-uploads"
TABLE = "ClassificationResults"
REGION = "us-east-1"

# 1x1 transparent PNG image
SAMPLE_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\rIDATx\x9cc\x00\x01\x00\x00\x05"
    b"\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)
SAMPLE_PNG_B64 = base64.b64encode(SAMPLE_PNG_BYTES).decode("utf-8")

# Minimal JPEG header
SAMPLE_JPEG_BYTES = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xff\xdb"
SAMPLE_JPEG_B64 = base64.b64encode(SAMPLE_JPEG_BYTES).decode("utf-8")


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    """Set environment variables for all API tests."""
    monkeypatch.setenv("BUCKET_NAME", BUCKET)
    monkeypatch.setenv("TABLE_NAME", TABLE)
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")


@pytest.fixture()
def aws_env():
    """Stand up mocked S3 and DynamoDB."""
    with mock_aws():
        s3 = boto3.client("s3", region_name=REGION)
        s3.create_bucket(Bucket=BUCKET)

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

        # Wire mocked clients into the modules
        orig_s3 = upload_app.s3_client
        orig_dynamo = get_result_app.dynamodb_resource

        upload_app.s3_client = s3
        get_result_app.dynamodb_resource = dynamodb

        yield {"s3": s3, "dynamodb": dynamodb, "table": table}

        upload_app.s3_client = orig_s3
        get_result_app.dynamodb_resource = orig_dynamo


# ===================================================================
# POST /upload Tests
# ===================================================================
class TestUploadHandler:
    """Tests for the upload Lambda function."""

    def test_upload_json_body_success(self, aws_env):
        event = {
            "httpMethod": "POST",
            "body": json.dumps({"image": SAMPLE_JPEG_B64}),
        }
        response = upload_app.lambda_handler(event, None)
        assert response["statusCode"] == 200
        assert "Access-Control-Allow-Origin" in response["headers"]

        body = json.loads(response["body"])
        assert "imageId" in body
        image_id = body["imageId"]

        # Check S3 object was created
        obj = aws_env["s3"].get_object(Bucket=BUCKET, Key=image_id)
        assert obj["Body"].read() == SAMPLE_JPEG_BYTES
        assert obj["ContentType"] == "image/jpeg"

    def test_upload_data_uri_png(self, aws_env):
        data_uri = f"data:image/png;base64,{SAMPLE_PNG_B64}"
        event = {
            "httpMethod": "POST",
            "body": json.dumps({"image": data_uri}),
        }
        response = upload_app.lambda_handler(event, None)
        assert response["statusCode"] == 200

        body = json.loads(response["body"])
        image_id = body["imageId"]

        obj = aws_env["s3"].get_object(Bucket=BUCKET, Key=image_id)
        assert obj["Body"].read() == SAMPLE_PNG_BYTES
        assert obj["ContentType"] == "image/png"

    def test_upload_raw_base64_string(self, aws_env):
        event = {
            "httpMethod": "POST",
            "body": SAMPLE_JPEG_B64,
        }
        response = upload_app.lambda_handler(event, None)
        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert "imageId" in body

    def test_upload_is_base64_encoded(self, aws_env):
        event = {
            "httpMethod": "POST",
            "isBase64Encoded": True,
            "body": SAMPLE_JPEG_B64,
        }
        response = upload_app.lambda_handler(event, None)
        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert "imageId" in body

    def test_upload_empty_body_returns_400(self, aws_env):
        event = {"httpMethod": "POST", "body": ""}
        response = upload_app.lambda_handler(event, None)
        assert response["statusCode"] == 400
        assert "Access-Control-Allow-Origin" in response["headers"]
        body = json.loads(response["body"])
        assert "error" in body

    def test_upload_invalid_base64_returns_400(self, aws_env):
        event = {
            "httpMethod": "POST",
            "body": json.dumps({"image": "not-valid-base64!@#$%"}),
        }
        response = upload_app.lambda_handler(event, None)
        assert response["statusCode"] == 400
        body = json.loads(response["body"])
        assert "error" in body

    def test_upload_options_preflight(self, aws_env):
        event = {"httpMethod": "OPTIONS"}
        response = upload_app.lambda_handler(event, None)
        assert response["statusCode"] == 200
        assert response["headers"]["Access-Control-Allow-Origin"] == "*"

    def test_upload_s3_error_returns_500(self, aws_env, monkeypatch):
        def _mock_put_object(*args, **kwargs):
            raise ClientError(
                {"Error": {"Code": "InternalError", "Message": "S3 down"}},
                "PutObject",
            )

        monkeypatch.setattr(upload_app.s3_client, "put_object", _mock_put_object)
        event = {
            "httpMethod": "POST",
            "body": json.dumps({"image": SAMPLE_JPEG_B64}),
        }
        response = upload_app.lambda_handler(event, None)
        assert response["statusCode"] == 500
        body = json.loads(response["body"])
        assert "error" in body


# ===================================================================
# GET /results/{imageId} Tests
# ===================================================================
class TestGetResultHandler:
    """Tests for the get-result Lambda function."""

    def test_get_result_found_returns_200(self, aws_env):
        aws_env["table"].put_item(
            Item={
                "imageId": "cat-uuid-1234",
                "label": "Cat",
                "confidence": Decimal("98.5"),
                "s3Key": "cat-uuid-1234",
                "timestamp": "2026-09-22T08:00:00Z",
            }
        )

        event = {
            "httpMethod": "GET",
            "pathParameters": {"imageId": "cat-uuid-1234"},
        }
        response = get_result_app.lambda_handler(event, None)
        assert response["statusCode"] == 200
        assert "Access-Control-Allow-Origin" in response["headers"]

        body = json.loads(response["body"])
        assert body["imageId"] == "cat-uuid-1234"
        assert body["label"] == "Cat"
        assert body["confidence"] == 98.5  # Properly serialized Decimal to float

    def test_get_result_fallback_with_extension(self, aws_env):
        aws_env["table"].put_item(
            Item={
                "imageId": "cat-uuid-5678.jpg",
                "label": "Persian Cat",
                "confidence": Decimal("95.0"),
                "s3Key": "cat-uuid-5678.jpg",
                "timestamp": "2026-09-22T08:00:00Z",
            }
        )

        event = {
            "httpMethod": "GET",
            "pathParameters": {"imageId": "cat-uuid-5678"},
        }
        response = get_result_app.lambda_handler(event, None)
        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["label"] == "Persian Cat"

    def test_get_result_not_found_returns_202_processing(self, aws_env):
        event = {
            "httpMethod": "GET",
            "pathParameters": {"imageId": "non-existent-uuid"},
        }
        response = get_result_app.lambda_handler(event, None)
        assert response["statusCode"] == 202
        assert "Access-Control-Allow-Origin" in response["headers"]

        body = json.loads(response["body"])
        assert body["status"] == "processing"
        assert body["imageId"] == "non-existent-uuid"

    def test_get_result_missing_imageId_returns_400(self, aws_env):
        event = {"httpMethod": "GET", "pathParameters": {}}
        response = get_result_app.lambda_handler(event, None)
        assert response["statusCode"] == 400
        body = json.loads(response["body"])
        assert "error" in body

    def test_get_result_options_preflight(self, aws_env):
        event = {"httpMethod": "OPTIONS"}
        response = get_result_app.lambda_handler(event, None)
        assert response["statusCode"] == 200
        assert response["headers"]["Access-Control-Allow-Origin"] == "*"

    def test_get_result_dynamo_error_returns_500(self, aws_env, monkeypatch):
        mock_table = MagicMock()
        mock_table.get_item.side_effect = ClientError(
            {"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "Throttled"}},
            "GetItem",
        )
        monkeypatch.setattr(get_result_app.dynamodb_resource, "Table", lambda name: mock_table)

        event = {
            "httpMethod": "GET",
            "pathParameters": {"imageId": "some-id"},
        }
        response = get_result_app.lambda_handler(event, None)
        assert response["statusCode"] == 500
        body = json.loads(response["body"])
        assert "error" in body
