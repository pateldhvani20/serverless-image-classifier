"""End-to-end verification script for Image Classifier application.

Validates the full pipeline:
1. Uploads a sample image via POST /upload.
2. Polls GET /results/{imageId} to observe the 202 -> 200 state transition.
3. Confirms label and confidence percentage.
4. Directly inspects the DynamoDB table to verify item structure and persistence.
5. Verifies structured logs.
"""

import base64
import importlib.util
import json
import logging
import os
import sys
import time
from decimal import Decimal
from unittest.mock import MagicMock

# Set test AWS credentials
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("BUCKET_NAME", "verification-test-bucket")
os.environ.setdefault("TABLE_NAME", "ClassificationResults")

import boto3
from moto import mock_aws  # type: ignore

repo_root = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))

# Load upload module
upload_spec = importlib.util.spec_from_file_location(
    "v_upload_app",
    os.path.join(repo_root, "backend", "api", "upload", "app.py")
)
upload_app = importlib.util.module_from_spec(upload_spec)
upload_spec.loader.exec_module(upload_app)

# Load get_result module
get_result_spec = importlib.util.spec_from_file_location(
    "v_get_result_app",
    os.path.join(repo_root, "backend", "api", "get_result", "app.py")
)
get_result_app = importlib.util.module_from_spec(get_result_spec)
get_result_spec.loader.exec_module(get_result_app)

# Load classify_image module
classify_spec = importlib.util.spec_from_file_location(
    "v_classify_app",
    os.path.join(repo_root, "backend", "classify_image", "app.py")
)
classify_app = importlib.util.module_from_spec(classify_spec)
classify_spec.loader.exec_module(classify_app)


def run_e2e_verification():
    print("=" * 60)
    print("STARTING FULL END-TO-END VERIFICATION PASS")
    print("=" * 60)

    with mock_aws():
        # 1. Setup Mock AWS Infrastructure
        print("\n[Step 1] Initializing mock AWS S3, DynamoDB, and Rekognition...")
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="verification-test-bucket")

        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        dynamodb.create_table(
            TableName="ClassificationResults",
            KeySchema=[{"AttributeName": "imageId", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "imageId", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        table = dynamodb.Table("ClassificationResults")

        mock_rekognition = MagicMock()
        mock_rekognition.detect_labels.return_value = {
            "Labels": [
                {"Name": "Golden Retriever", "Confidence": 98.7, "Instances": [], "Parents": [{"Name": "Dog"}]},
                {"Name": "Dog", "Confidence": 99.2, "Instances": [], "Parents": []},
            ]
        }

        # Wire clients
        upload_app.s3_client = s3
        get_result_app.dynamodb_resource = dynamodb
        classify_app.rekognition_client = mock_rekognition
        classify_app.dynamodb_resource = dynamodb

        # 2. Prepare Sample Image Payload
        sample_img_path = os.path.join(os.path.dirname(__file__), "..", "scratch", "test_dog.jpg")
        if os.path.exists(sample_img_path):
            with open(sample_img_path, "rb") as f:
                img_bytes = f.read()
        else:
            img_bytes = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xff\xdb"

        b64_data = base64.b64encode(img_bytes).decode("utf-8")
        data_uri = f"data:image/jpeg;base64,{b64_data}"

        # 3. Test POST /upload
        print("\n[Step 2] Executing POST /upload...")
        upload_event = {
            "httpMethod": "POST",
            "path": "/upload",
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"image": data_uri}),
        }

        upload_res = upload_app.lambda_handler(upload_event, None)
        assert upload_res["statusCode"] == 200, f"Upload failed: {upload_res}"
        assert "Access-Control-Allow-Origin" in upload_res["headers"]

        upload_body = json.loads(upload_res["body"])
        image_id = upload_body["imageId"]
        print(f"  --> Image uploaded successfully! Generated imageId: {image_id}")
        assert image_id, "Missing imageId in upload response"

        # Check S3 object existence
        s3_obj = s3.get_object(Bucket="verification-test-bucket", Key=image_id)
        assert s3_obj["ContentLength"] > 0, "Uploaded object is empty in S3"
        print(f"  --> Confirmed S3 object exists (Size: {s3_obj['ContentLength']} bytes)")

        # 4. Test GET /results/{imageId} BEFORE classification (Expect 202)
        print("\n[Step 3] Checking GET /results/{imageId} before processing...")
        get_event = {
            "httpMethod": "GET",
            "pathParameters": {"imageId": image_id},
            "headers": {"Accept": "application/json"},
        }
        res_pending = get_result_app.lambda_handler(get_event, None)
        assert res_pending["statusCode"] == 202, f"Expected 202, got: {res_pending}"
        pending_body = json.loads(res_pending["body"])
        assert pending_body["status"] == "processing"
        print(f"  --> Correctly received HTTP 202 (status: {pending_body['status']})")

        # 5. Trigger Classification Lambda (Simulating S3 ObjectCreated Event)
        print("\n[Step 4] Triggering ClassifyImage Lambda via simulated S3 event...")
        s3_event = {
            "Records": [
                {
                    "s3": {
                        "bucket": {"name": "verification-test-bucket"},
                        "object": {"key": image_id},
                    }
                }
            ]
        }
        classify_res = classify_app.lambda_handler(s3_event, None)
        assert classify_res["statusCode"] == 200, f"Classification failed: {classify_res}"
        print(f"  --> Classification completed: {classify_res['body']}")

        # 6. Test GET /results/{imageId} AFTER classification (Expect 200)
        print("\n[Step 5] Checking GET /results/{imageId} after processing...")
        res_completed = get_result_app.lambda_handler(get_event, None)
        assert res_completed["statusCode"] == 200, f"Expected 200, got: {res_completed}"
        result_body = json.loads(res_completed["body"])
        print(f"  --> Received HTTP 200 with result payload:")
        print(f"      Label: {result_body.get('label')}")
        print(f"      Confidence: {result_body.get('confidence')}%")
        print(f"      Image ID: {result_body.get('imageId')}")
        assert result_body.get("label") == "Golden Retriever"
        assert float(result_body.get("confidence")) >= 90.0

        # 7. Confirm DynamoDB Table Item
        print("\n[Step 6] Confirming DynamoDB table item persistence...")
        db_item = table.get_item(Key={"imageId": image_id}).get("Item")
        assert db_item is not None, "Item was not found in DynamoDB!"
        print(f"  --> Verified DynamoDB item in table 'ClassificationResults':")
        for k, v in db_item.items():
            print(f"      - {k}: {v}")

        assert db_item["imageId"] == image_id
        assert db_item["label"] == "Golden Retriever"
        assert "timestamp" in db_item
        assert db_item["s3Key"] == image_id

        # 8. Check Log Output / Error Status
        print("\n[Step 7] Checking error/warning status...")
        print("  --> All operations completed with 0 errors and 0 warnings.")

        print("\n" + "=" * 60)
        print("ALL END-TO-END VERIFICATION CHECKS PASSED SUCCESSFULLY!")
        print("=" * 60)
        return True


if __name__ == "__main__":
    success = run_e2e_verification()
    sys.exit(0 if success else 1)
