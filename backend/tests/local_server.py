"""Local HTTP dev server to test API endpoints with curl/Postman."""

import base64
import json
import os
import sys
from decimal import Decimal
from http.server import HTTPServer, BaseHTTPRequestHandler

# Set mock AWS env vars for local testing
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("BUCKET_NAME", "local-test-bucket")
os.environ.setdefault("TABLE_NAME", "ClassificationResults")

import boto3
from moto import mock_aws  # type: ignore

# Setup moto
mock = mock_aws()
mock.start()

s3 = boto3.client("s3", region_name="us-east-1")
s3.create_bucket(Bucket="local-test-bucket")

dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
dynamodb.create_table(
    TableName="ClassificationResults",
    KeySchema=[{"AttributeName": "imageId", "KeyType": "HASH"}],
    AttributeDefinitions=[{"AttributeName": "imageId", "AttributeType": "S"}],
    BillingMode="PAY_PER_REQUEST",
)
table = dynamodb.Table("ClassificationResults")

# Pre-populate a test result
table.put_item(
    Item={
        "imageId": "sample-uuid-cat",
        "label": "Golden Retriever",
        "confidence": Decimal("98.7"),
        "s3Key": "sample-uuid-cat",
        "timestamp": "2026-09-22T08:00:00Z",
    }
)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api", "upload"))
import app as upload_app  # type: ignore
upload_app.s3_client = s3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api", "get_result"))
import importlib.util
spec = importlib.util.spec_from_file_location(
    "get_result_app",
    os.path.join(os.path.dirname(__file__), "..", "api", "get_result", "app.py")
)
get_result_app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(get_result_app)
get_result_app.dynamodb_resource = dynamodb


class LocalApiHandler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.end_headers()

    def do_POST(self):
        if self.path == "/upload":
            content_len = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_len).decode("utf-8")
            event = {
                "httpMethod": "POST",
                "path": "/upload",
                "headers": dict(self.headers),
                "body": body,
            }
            res = upload_app.lambda_handler(event, None)
            self.send_response(res["statusCode"])
            for k, v in res.get("headers", {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(res["body"].encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        if self.path.startswith("/results/"):
            image_id = self.path[len("/results/"):]
            event = {
                "httpMethod": "GET",
                "path": self.path,
                "pathParameters": {"imageId": image_id},
                "headers": dict(self.headers),
            }
            res = get_result_app.lambda_handler(event, None)
            self.send_response(res["statusCode"])
            for k, v in res.get("headers", {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(res["body"].encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()


if __name__ == "__main__":
    server = HTTPServer(("127.0.0.1", 3000), LocalApiHandler)
    print("Local API Server listening on http://127.0.0.1:3000")
    sys.stdout.flush()
    server.serve_forever()
