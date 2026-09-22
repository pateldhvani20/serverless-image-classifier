"""Local Development and Verification Server.

Serves the frontend static files from /frontend and routes API calls
to the Lambda handlers with mocked AWS services (moto).
Simulates the asynchronous Rekognition classification with a 1.5s delay
so the 2-second client polling flow is realistically verified.
"""

import base64
import json
import os
import sys
import threading
import time
from decimal import Decimal
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse

# Set test AWS credentials
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("BUCKET_NAME", "image-classifier-local-bucket")
os.environ.setdefault("TABLE_NAME", "ClassificationResults")

import boto3
from moto import mock_aws

mock = mock_aws()
mock.start()

# Setup S3
s3 = boto3.client("s3", region_name="us-east-1")
s3.create_bucket(Bucket="image-classifier-local-bucket")

# Setup DynamoDB
dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
dynamodb.create_table(
    TableName="ClassificationResults",
    KeySchema=[{"AttributeName": "imageId", "KeyType": "HASH"}],
    AttributeDefinitions=[{"AttributeName": "imageId", "AttributeType": "S"}],
    BillingMode="PAY_PER_REQUEST",
)
table = dynamodb.Table("ClassificationResults")

# Load handlers
repo_root = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(repo_root, "backend", "api", "upload"))
import app as upload_app
upload_app.s3_client = s3

sys.path.insert(0, os.path.join(repo_root, "backend", "api", "get_result"))
import importlib.util
spec = importlib.util.spec_from_file_location("get_result_app", os.path.join(repo_root, "backend", "api", "get_result", "app.py"))
get_result_app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(get_result_app)
get_result_app.dynamodb_resource = dynamodb

frontend_dir = os.path.join(repo_root, "frontend")


def _simulate_async_classification(image_id):
    """Simulate S3-triggered Rekognition Lambda after a short delay."""
    time.sleep(1.8)  # Enough delay to verify the 202 polling state
    print(f"[Async Pipeline] Classifying image {image_id}...")
    table.put_item(
        Item={
            "imageId": image_id,
            "label": "Golden Retriever",
            "confidence": Decimal("98.7"),
            "s3Key": image_id,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    )
    print(f"[Async Pipeline] Stored classification result for {image_id}")


class DevHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=frontend_dir, **kwargs)

    def end_headers(self):
        # Enable CORS for local testing
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/upload":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")

            event = {
                "httpMethod": "POST",
                "path": "/upload",
                "headers": dict(self.headers),
                "body": body,
            }

            res = upload_app.lambda_handler(event, None)
            status_code = res.get("statusCode", 500)
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(res["body"].encode("utf-8"))

            if status_code == 200:
                body_data = json.loads(res["body"])
                image_id = body_data.get("imageId")
                if image_id:
                    # Spawn async classifier thread
                    threading.Thread(target=_simulate_async_classification, args=(image_id,), daemon=True).start()
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/results/"):
            image_id = parsed.path[len("/results/"):]
            event = {
                "httpMethod": "GET",
                "path": parsed.path,
                "pathParameters": {"imageId": image_id},
                "headers": dict(self.headers),
            }

            res = get_result_app.lambda_handler(event, None)
            self.send_response(res.get("statusCode", 500))
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(res["body"].encode("utf-8"))
        else:
            # Serve frontend files
            super().do_GET()


if __name__ == "__main__":
    port = 3000
    server = HTTPServer(("127.0.0.1", port), DevHandler)
    print(f"Dev server running at http://127.0.0.1:{port}")
    sys.stdout.flush()
    server.serve_forever()
