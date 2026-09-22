"""Stub handler for generating pre-signed S3 upload URLs."""

import json
import os


def lambda_handler(event, context):
    """Handles POST /upload requests. Will return a pre-signed S3 URL.

    Environment variables:
        BUCKET_NAME: S3 bucket to generate upload URLs for.
    """
    bucket_name = os.environ.get("BUCKET_NAME")

    # TODO: Parse request body for filename / content type
    # TODO: Generate a pre-signed PUT URL via S3
    # TODO: Return the URL to the client

    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps({"message": "Upload stub"}),
    }
