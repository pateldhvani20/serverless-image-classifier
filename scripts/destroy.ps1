<#
.SYNOPSIS
    Teardown Script: Safe AWS SAM Stack Deletion (PowerShell)
.DESCRIPTION
    Empties the S3 upload bucket first to prevent CloudFormation deletion errors,
    then executes `sam delete --no-prompts`.
.PARAMETER StackName
    The name of the CloudFormation stack (default: image-classifier-stack)
.PARAMETER Region
    The AWS region (default: us-east-1)
#>

[CmdletBinding()]
param(
    [string]$StackName = "image-classifier-stack",
    [string]$Region = "us-east-1"
)

$ErrorActionPreference = "Stop"

Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host " AWS Serverless Image Classifier — Stack Teardown" -ForegroundColor Cyan
Write-Host " Stack Name: $StackName" -ForegroundColor Cyan
Write-Host " Region:     $Region" -ForegroundColor Cyan
Write-Host "=================================================================" -ForegroundColor Cyan

# Check if stack exists
Write-Host "Checking if CloudFormation stack '$StackName' exists..."
try {
    $null = aws cloudformation describe-stacks --stack-name $StackName --region $Region 2>&1
} catch {
    Write-Host "Stack '$StackName' does not exist in region '$Region'. Nothing to delete." -ForegroundColor Yellow
    exit 0
}

# 1. Empty S3 Bucket before stack deletion
Write-Host "Locating S3 upload bucket from stack resources..."
$bucketName = (aws cloudformation describe-stack-resource `
    --stack-name $StackName `
    --logical-resource-id ImageUploadBucket `
    --region $Region `
    --query "StackResourceDetail.PhysicalResourceId" `
    --output text 2>$null)

if ($bucketName -and $bucketName -ne "None") {
    Write-Host "Found S3 bucket: $bucketName" -ForegroundColor Green
    Write-Host "Emptying all objects from s3://$bucketName to allow clean stack deletion..."
    aws s3 rm "s3://$bucketName" --recursive --region $Region 2>$null
    Write-Host "S3 bucket '$bucketName' emptied successfully." -ForegroundColor Green
} else {
    Write-Host "No active S3 bucket found or already empty."
}

# 2. Execute SAM Delete
Write-Host "Executing 'sam delete' for stack '$StackName'..."
sam delete --stack-name $StackName --region $Region --no-prompts

Write-Host "=================================================================" -ForegroundColor Green
Write-Host " Teardown completed successfully!" -ForegroundColor Green
Write-Host " All resources (API Gateway, Lambdas, S3, DynamoDB) were removed." -ForegroundColor Green
Write-Host " No further AWS charges will be incurred." -ForegroundColor Green
Write-Host "=================================================================" -ForegroundColor Green
