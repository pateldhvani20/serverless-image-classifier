"""Get Result Lambda function for GET /results/{imageId}.

Reads classification results from DynamoDB by imageId. Returns the classification
result as JSON (HTTP 200), or HTTP 202 with status "processing" if not yet available.
"""

import json
import logging
import os
from decimal import Decimal

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

TABLE_NAME = os.environ.get("TABLE_NAME")
dynamodb_resource = boto3.resource("dynamodb")

CORS_HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token",
    "Access-Control-Allow-Methods": "GET,OPTIONS",
}


def _decimal_to_serializable(obj):
    """Recursively convert DynamoDB Decimal instances to float or int."""
    if isinstance(obj, list):
        return [_decimal_to_serializable(item) for item in obj]
    if isinstance(obj, dict):
        return {key: _decimal_to_serializable(val) for key, val in obj.items()}
    if isinstance(obj, Decimal):
        return int(obj) if obj % 1 == 0 else float(obj)
    return obj


def lambda_handler(event, context):
    """Handle GET /results/{imageId} requests."""
    logger.info(json.dumps({"event": "get_result_invocation_start"}))

    http_method = event.get("httpMethod", "").upper()
    if http_method == "OPTIONS":
        return {
            "statusCode": 200,
            "headers": CORS_HEADERS,
            "body": json.dumps({"message": "OK"}),
        }

    path_parameters = event.get("pathParameters") or {}
    image_id = path_parameters.get("imageId")

    if not image_id:
        return {
            "statusCode": 400,
            "headers": CORS_HEADERS,
            "body": json.dumps({"error": "Missing 'imageId' path parameter."}),
        }

    table_name = TABLE_NAME or os.environ.get("TABLE_NAME")
    if not table_name:
        logger.error("TABLE_NAME environment variable is not configured.")
        return {
            "statusCode": 500,
            "headers": CORS_HEADERS,
            "body": json.dumps({"error": "Table name is not configured."}),
        }

    try:
        table = dynamodb_resource.Table(table_name)
        response = table.get_item(Key={"imageId": image_id})
        item = response.get("Item")

        # Fallback check if stored with an extension
        if not item and "." not in image_id:
            for ext in (".jpg", ".jpeg", ".png"):
                fallback_res = table.get_item(Key={"imageId": f"{image_id}{ext}"})
                if "Item" in fallback_res:
                    item = fallback_res["Item"]
                    break

        if item:
            serialized_item = _decimal_to_serializable(item)
            logger.info(json.dumps({"event": "result_found", "imageId": image_id}))
            return {
                "statusCode": 200,
                "headers": CORS_HEADERS,
                "body": json.dumps(serialized_item),
            }

        # Not yet classified or in-flight
        logger.info(json.dumps({"event": "result_processing", "imageId": image_id}))
        return {
            "statusCode": 202,
            "headers": CORS_HEADERS,
            "body": json.dumps({
                "status": "processing",
                "message": "Image classification is still processing.",
                "imageId": image_id,
            }),
        }

    except ClientError as exc:
        logger.error(json.dumps({
            "event": "dynamodb_read_error",
            "imageId": image_id,
            "code": exc.response["Error"]["Code"],
            "message": exc.response["Error"]["Message"],
        }))
        return {
            "statusCode": 500,
            "headers": CORS_HEADERS,
            "body": json.dumps({"error": "Failed to retrieve classification result."}),
        }
    except Exception as exc:
        logger.error(json.dumps({"event": "unexpected_error", "error": str(exc)}))
        return {
            "statusCode": 500,
            "headers": CORS_HEADERS,
            "body": json.dumps({"error": "Internal server error."}),
        }
