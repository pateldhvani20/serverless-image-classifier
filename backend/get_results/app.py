"""Stub handler for retrieving classification results."""

import json
import os


def lambda_handler(event, context):
    """Handles GET /results/{imageId}. Will query DynamoDB for results.

    Environment variables:
        TABLE_NAME: DynamoDB table to read results from.
    """
    table_name = os.environ.get("TABLE_NAME")
    image_id = event.get("pathParameters", {}).get("imageId", "")

    # TODO: Query DynamoDB for the classification result by imageId
    # TODO: Return 404 if not found

    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps({
            "imageId": image_id,
            "message": "Results stub",
        }),
    }
