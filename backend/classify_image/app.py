"""Classify images uploaded to S3 using Amazon Rekognition.

This Lambda function is triggered by S3 ``ObjectCreated`` events.  It calls
Rekognition ``DetectLabels``, extracts the top prediction, and persists the
result to a DynamoDB table.
"""

import json
import logging
import os
from datetime import datetime, timezone
from decimal import Decimal
from urllib.parse import unquote_plus

import boto3
from botocore.exceptions import ClientError

# ---------------------------------------------------------------------------
# Configuration & logging
# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

TABLE_NAME = os.environ.get("TABLE_NAME", "ClassificationResults")

SUPPORTED_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png"})

# ---------------------------------------------------------------------------
# AWS SDK clients – created once per container (warm-start reuse)
# ---------------------------------------------------------------------------
rekognition_client = boto3.client("rekognition")
dynamodb_resource = boto3.resource("dynamodb")


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------
def lambda_handler(event, context):
    """Process S3 event records, classify each image, and store results.

    Parameters
    ----------
    event : dict
        S3 notification event containing one or more ``Records``.
    context : LambdaContext
        AWS Lambda runtime context (unused in this stub).

    Returns
    -------
    dict
        HTTP-style response with ``statusCode`` and JSON ``body``.
    """
    records = event.get("Records", [])
    logger.info(json.dumps({"event": "invocation_start", "record_count": len(records)}))

    results = []

    for record in records:
        bucket = record["s3"]["bucket"]["name"]
        key = unquote_plus(record["s3"]["object"]["key"])

        logger.info(json.dumps({"event": "processing", "bucket": bucket, "key": key}))

        try:
            # --- validate ------------------------------------------------
            _validate_extension(key)

            # --- classify ------------------------------------------------
            labels = _detect_labels(bucket, key)
            top = labels[0] if labels else {"Name": "Unknown", "Confidence": 0.0}

            # --- persist -------------------------------------------------
            _store_result(
                image_id=key,
                label=top["Name"],
                confidence=top["Confidence"],
                s3_key=key,
            )

            results.append({
                "key": key,
                "label": top["Name"],
                "confidence": top["Confidence"],
            })
            logger.info(json.dumps({
                "event": "classified",
                "key": key,
                "label": top["Name"],
                "confidence": top["Confidence"],
            }))

        except ValueError as exc:
            # Unsupported file type – log a warning and continue to next record.
            logger.warning(json.dumps({
                "event": "skipped_unsupported",
                "key": key,
                "reason": str(exc),
            }))
            results.append({"key": key, "error": str(exc)})

        except ClientError as exc:
            # AWS service error – log and re-raise so Lambda can retry.
            logger.error(json.dumps({
                "event": "aws_error",
                "key": key,
                "code": exc.response["Error"]["Code"],
                "message": exc.response["Error"]["Message"],
            }))
            raise

        except Exception as exc:
            logger.error(json.dumps({
                "event": "unexpected_error",
                "key": key,
                "error": str(exc),
            }))
            raise

    return {"statusCode": 200, "body": json.dumps({"results": results})}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _validate_extension(key: str) -> None:
    """Raise ``ValueError`` if *key* has an unsupported extension."""
    ext = os.path.splitext(key)[1].lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            f"Unsupported file type '{ext}'. "
            f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )


def _detect_labels(
    bucket: str,
    key: str,
    max_labels: int = 10,
    min_confidence: float = 70.0,
) -> list:
    """Call Rekognition ``DetectLabels`` and return the label list."""
    response = rekognition_client.detect_labels(
        Image={"S3Object": {"Bucket": bucket, "Name": key}},
        MaxLabels=max_labels,
        MinConfidence=min_confidence,
    )
    return response.get("Labels", [])


def _store_result(
    *,
    image_id: str,
    label: str,
    confidence: float,
    s3_key: str,
) -> None:
    """Write a classification result to DynamoDB."""
    table = dynamodb_resource.Table(TABLE_NAME)
    table.put_item(
        Item={
            "imageId": image_id,
            "label": label,
            "confidence": Decimal(str(round(confidence, 2))),
            "s3Key": s3_key,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    )
