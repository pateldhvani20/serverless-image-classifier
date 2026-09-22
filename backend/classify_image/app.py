"""Stub handler for image classification via Amazon Rekognition."""

import json
import os


def lambda_handler(event, context):
    """Triggered by S3 upload events. Will classify images using Rekognition.

    Environment variables:
        TABLE_NAME: DynamoDB table for storing results.
        BUCKET_NAME: S3 bucket containing uploaded images.
    """
    table_name = os.environ.get("TABLE_NAME")
    bucket_name = os.environ.get("BUCKET_NAME")

    # TODO: Extract S3 object key from the event
    # TODO: Call Rekognition DetectLabels
    # TODO: Store results in DynamoDB

    return {
        "statusCode": 200,
        "body": json.dumps({"message": "Classification stub"}),
    }
