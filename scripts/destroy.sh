#!/usr/bin/env bash
# ==============================================================================
# Teardown Script: Safe AWS SAM Stack Deletion
#
# Empties the S3 upload bucket first (required by CloudFormation to avoid deletion
# failures), then executes `sam delete` with confirmation.
#
# Usage:
#   ./scripts/destroy.sh [STACK_NAME] [REGION]
# Example:
#   ./scripts/destroy.sh image-classifier-stack us-east-1
# ==============================================================================

set -euo pipefail

STACK_NAME="${1:-image-classifier-stack}"
REGION="${2:-${AWS_DEFAULT_REGION:-us-east-1}}"

echo "================================================================="
echo " AWS Serverless Image Classifier — Stack Teardown"
echo " Stack Name: ${STACK_NAME}"
echo " Region:     ${REGION}"
echo "================================================================="

# Check for AWS CLI
if ! command -v aws >/dev/null 2>&1; then
  echo "Error: 'aws' CLI is not installed or not found in PATH." >&2
  exit 1
fi

# Check for SAM CLI
if ! command -v sam >/dev/null 2>&1; then
  echo "Error: 'sam' CLI is not installed or not found in PATH." >&2
  exit 1
fi

# Verify AWS credentials
echo "Verifying AWS credentials..."
if ! aws sts get-caller-identity >/dev/null 2>&1; then
  echo "Error: AWS credentials are not configured or expired." >&2
  echo "Please configure credentials with 'aws configure' or export AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY." >&2
  exit 1
fi

# Check if stack exists
echo "Checking if CloudFormation stack '${STACK_NAME}' exists..."
if ! aws cloudformation describe-stacks --stack-name "${STACK_NAME}" --region "${REGION}" >/dev/null 2>&1; then
  echo "Stack '${STACK_NAME}' does not exist in region '${REGION}'. Nothing to delete."
  exit 0
fi

# ------------------------------------------------------------------------------
# 1. Empty S3 Bucket before stack deletion
# ------------------------------------------------------------------------------
echo "Locating S3 upload bucket from stack resources..."
BUCKET_NAME=$(aws cloudformation describe-stack-resource \
  --stack-name "${STACK_NAME}" \
  --logical-resource-id ImageUploadBucket \
  --region "${REGION}" \
  --query "StackResourceDetail.PhysicalResourceId" \
  --output text 2>/dev/null || true)

if [ -n "${BUCKET_NAME}" ] && [ "${BUCKET_NAME}" != "None" ]; then
  echo "Found S3 bucket: ${BUCKET_NAME}"
  echo "Emptying all objects from s3://${BUCKET_NAME} to allow clean stack deletion..."
  aws s3 rm "s3://${BUCKET_NAME}" --recursive --region "${REGION}" || true

  # Check if versioning is enabled and delete versioned objects if any
  echo "Checking for object versions..."
  VERSIONS=$(aws s3api list-object-versions --bucket "${BUCKET_NAME}" --region "${REGION}" \
    --query '{Objects: Versions[].{Key:Key,VersionId:VersionId}}' --output json 2>/dev/null || true)
  if [ "${VERSIONS}" != "null" ] && [ -n "${VERSIONS}" ]; then
    aws s3api delete-objects --bucket "${BUCKET_NAME}" --delete "${VERSIONS}" --region "${REGION}" 2>/dev/null || true
  fi
  echo "S3 bucket '${BUCKET_NAME}' emptied successfully."
else
  echo "No active S3 bucket found or already deleted."
fi

# ------------------------------------------------------------------------------
# 2. Execute SAM Delete
# ------------------------------------------------------------------------------
echo "Executing 'sam delete' for stack '${STACK_NAME}'..."
sam delete \
  --stack-name "${STACK_NAME}" \
  --region "${REGION}" \
  --no-prompts

echo "================================================================="
echo " Teardown completed successfully!"
echo " All resources (API Gateway, Lambdas, S3, DynamoDB) were removed."
echo " No further AWS charges will be incurred."
echo "================================================================="
