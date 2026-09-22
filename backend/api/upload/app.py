"""Upload Lambda function for POST /upload.

Accepts a base64-encoded image, uploads it to an S3 bucket with a generated UUID
as the key, and returns the imageId.
"""

import base64
import binascii
import json
import logging
import os
import uuid

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

BUCKET_NAME = os.environ.get("BUCKET_NAME")
s3_client = boto3.client("s3")

CORS_HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token",
    "Access-Control-Allow-Methods": "POST,OPTIONS",
}


def _detect_content_type(data: bytes) -> str:
    """Detect image MIME type from binary magic bytes."""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "image/gif"
    if data.startswith(b"RIFF") and len(data) >= 12 and data[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


def _extract_base64(event: dict) -> str:
    """Extract raw base64 string from API Gateway event."""
    body = event.get("body")
    if not body:
        raise ValueError("Request body is empty.")

    # If already base64 encoded by API Gateway
    if event.get("isBase64Encoded", False):
        return body

    # Try parsing as JSON
    if isinstance(body, str):
        body_trimmed = body.strip()
        if body_trimmed.startswith("{"):
            try:
                data = json.loads(body_trimmed)
                for key in ("image", "data", "imageData", "image_data", "content", "file"):
                    if key in data and isinstance(data[key], str):
                        return data[key]
                raise ValueError("JSON body must contain an 'image' property with base64 data.")
            except json.JSONDecodeError:
                pass
        return body_trimmed

    raise ValueError("Invalid request body format.")


def lambda_handler(event, context):
    """Handle POST /upload requests."""
    logger.info(json.dumps({"event": "upload_invocation_start"}))

    # Handle OPTIONS preflight request if routed here
    http_method = event.get("httpMethod", "").upper()
    if http_method == "OPTIONS":
        return {
            "statusCode": 200,
            "headers": CORS_HEADERS,
            "body": json.dumps({"message": "OK"}),
        }

    bucket = BUCKET_NAME or os.environ.get("BUCKET_NAME")
    if not bucket:
        logger.error("BUCKET_NAME environment variable is not configured.")
        return {
            "statusCode": 500,
            "headers": CORS_HEADERS,
            "body": json.dumps({"error": "Bucket name is not configured."}),
        }

    try:
        raw_b64 = _extract_base64(event)

        # Strip Data URI prefix if present (e.g. data:image/jpeg;base64,...)
        if "," in raw_b64 and raw_b64.startswith("data:"):
            raw_b64 = raw_b64.split(",", 1)[1]

        image_bytes = base64.b64decode(raw_b64, validate=True)
        if not image_bytes:
            raise ValueError("Decoded image content is empty.")

    except (ValueError, binascii.Error) as exc:
        logger.warning(json.dumps({"event": "invalid_payload", "error": str(exc)}))
        return {
            "statusCode": 400,
            "headers": CORS_HEADERS,
            "body": json.dumps({"error": f"Invalid image payload: {str(exc)}"}),
        }

    image_id = str(uuid.uuid4())
    content_type = _detect_content_type(image_bytes)

    try:
        s3_client.put_object(
            Bucket=bucket,
            Key=image_id,
            Body=image_bytes,
            ContentType=content_type,
        )
        logger.info(json.dumps({
            "event": "image_uploaded",
            "imageId": image_id,
            "bucket": bucket,
            "size": len(image_bytes),
            "contentType": content_type,
        }))

        return {
            "statusCode": 200,
            "headers": CORS_HEADERS,
            "body": json.dumps({
                "imageId": image_id,
                "message": "Image uploaded successfully",
            }),
        }

    except ClientError as exc:
        logger.error(json.dumps({
            "event": "s3_upload_error",
            "code": exc.response["Error"]["Code"],
            "message": exc.response["Error"]["Message"],
        }))
        return {
            "statusCode": 500,
            "headers": CORS_HEADERS,
            "body": json.dumps({"error": "Failed to upload image to storage."}),
        }
    except Exception as exc:
        logger.error(json.dumps({"event": "unexpected_error", "error": str(exc)}))
        return {
            "statusCode": 500,
            "headers": CORS_HEADERS,
            "body": json.dumps({"error": "Internal server error."}),
        }
