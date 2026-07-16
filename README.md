# Synthetic LMS API Delivery Prototype

Pure CloudFormation prototype for practicing an S3-to-API delivery workflow.
It uses synthetic data only and does not connect to Cornerstone or contain
work data, credentials, endpoints, or proprietary mapping rules.

## What it does

1. A synthetic CSV is uploaded under `incoming/<domain>/`.
2. S3 sends an object-created event to a standard SQS queue.
3. the processing Lambda reads the CSV directly from S3.
4. Each row is validated and mapped to a Cornerstone-like JSON contract.
5. Each payload is posted to the included mock HTTP API.
6. After every row succeeds, Lambda writes
   `checkpoints/<domain>/latest.json` to S3.
7. A failed SQS message is retried and moves to the DLQ after five receives.

The checkpoint is never written when a row fails. S3 versioning preserves
the history of each overwritten `latest.json` checkpoint.

## Resources

- KMS key
- Versioned, encrypted S3 bucket
- Standard SQS ingest queue and DLQ
- Processing Lambda
- API Gateway HTTP API and mock receiver Lambda
- DLQ depth alarm
- Explicit least-privilege IAM roles

The queue is intentionally standard: S3 event notifications cannot target
an SQS FIFO queue directly. A production design that truly needs FIFO would
route S3 events through EventBridge.

## Synthetic CSV contract

```csv
activity_code,title,subject_id,training_hours,active
10027,Defensive Driving Fundamentals,SAFETY,2.5,true
10028,Work Zone Safety,CONSTRUCTION,3,true
```

The processing Lambda maps a row to:

```json
{
  "externalId": "DT10027",
  "title": "Defensive Driving Fundamentals",
  "subjectId": "DT_SAFETY",
  "trainingHours": 2.5,
  "active": true
}
```

Use `THROTTLE` in a synthetic title to make the mock API return `429`, or
`FAIL` to make it return `500`. This exercises the queue retry and DLQ path.

## Deploy

Prerequisites: AWS CLI v2 and a configured personal AWS profile.

Create an artifacts bucket once:

```bash
aws s3 mb s3://YOUR-NAME-cfn-artifacts-dev \
  --profile personal-pipeline \
  --region us-east-1
```

Package both Lambda handlers in the same ZIP:

```bash
cd src/handlers
zip -r ../../lambda-package.zip process_dump.py mock_receiver.py
cd ../..

aws s3 cp lambda-package.zip \
  s3://YOUR-NAME-cfn-artifacts-dev/lambda-package.zip \
  --profile personal-pipeline
```

Deploy:

```bash
aws cloudformation deploy \
  --template-file template.yaml \
  --stack-name pipeline-prototype-dev \
  --parameter-overrides \
      LambdaCodeS3Bucket=YOUR-NAME-cfn-artifacts-dev \
      LambdaCodeS3Key=lambda-package.zip \
  --capabilities CAPABILITY_NAMED_IAM \
  --profile personal-pipeline \
  --region us-east-1
```

Get the generated incoming bucket name:

```bash
aws cloudformation describe-stacks \
  --stack-name pipeline-prototype-dev \
  --query "Stacks[0].Outputs[?OutputKey=='IncomingBucketName'].OutputValue" \
  --output text \
  --profile personal-pipeline
```

Upload the sample:

```bash
aws s3 cp events/sample_activities.csv \
  s3://YOUR-INCOMING-BUCKET/incoming/activities/activities_20260716.csv \
  --profile personal-pipeline
```

Inspect the checkpoint after processing:

```bash
aws s3 cp \
  s3://YOUR-INCOMING-BUCKET/checkpoints/activities/latest.json - \
  --profile personal-pipeline
```

Tail processing logs:

```bash
aws logs tail /aws/lambda/pipeline-prototype-dev-process-dump \
  --follow \
  --profile personal-pipeline
```

## Cleanup

Empty the generated incoming bucket before deleting the stack because
CloudFormation cannot delete a non-empty versioned bucket.

```bash
aws s3 rm s3://YOUR-INCOMING-BUCKET --recursive --profile personal-pipeline

aws cloudformation delete-stack \
  --stack-name pipeline-prototype-dev \
  --profile personal-pipeline
```

The separately created artifacts bucket is not part of the stack.
