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

## Tagging

Every taggable resource carries a `TechnicalOwner` tag, set from the
`TechnicalOwner` template parameter. Override it at deploy time to point
the tag at the right contact. Resources that do not support tags
(queue policy, API route/integration, Lambda permission) are
intentionally untagged.

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
  --profile personal \
  --region us-east-1
```

Package both Lambda handlers in the same ZIP:

```bash
cd src/handlers
zip -r ../../lambda-package.zip process_dump.py mock_receiver.py
cd ../..

aws s3 cp lambda-package.zip \
  s3://YOUR-NAME-cfn-artifacts-dev/lambda-package.zip \
  --profile personal
```

Deploy:

```bash
aws cloudformation deploy \
  --template-file template.yaml \
  --stack-name pipeline-prototype-dev \
  --parameter-overrides \
      LambdaCodeS3Bucket=YOUR-NAME-cfn-artifacts-dev \
      LambdaCodeS3Key=lambda-package.zip \
      TechnicalOwner=you@example.com \
  --capabilities CAPABILITY_NAMED_IAM \
  --profile personal \
  --region us-east-1
```

Get the generated incoming bucket name:

```bash
aws cloudformation describe-stacks \
  --stack-name pipeline-prototype-dev \
  --query "Stacks[0].Outputs[?OutputKey=='IncomingBucketName'].OutputValue" \
  --output text \
  --profile personal
```

Upload the sample:

```bash
aws s3 cp events/sample_activities.csv \
  s3://YOUR-INCOMING-BUCKET/incoming/activities/activities_20260716.csv \
  --profile personal
```

Inspect the checkpoint after processing:

```bash
aws s3 cp \
  s3://YOUR-INCOMING-BUCKET/checkpoints/activities/latest.json - \
  --profile personal
```

Tail processing logs:

```bash
aws logs tail /aws/lambda/pipeline-prototype-dev-process-dump \
  --follow \
  --profile personal
```

## MVP variant (S3 + Lambda only)

[template-mvp.yaml](template-mvp.yaml) is a stripped-down version of the
pipeline for when you only want the S3-to-Lambda mechanics: the bucket
invokes the processing Lambda directly, and the Lambda validates and maps
each row, logs the mapped payloads, and writes the checkpoint. There is
no SQS queue, DLQ, mock API, KMS key, or alarm. Because S3 invokes the
Lambda asynchronously, a failed file is retried twice by Lambda and then
dropped — the full template is the one that demonstrates durable
retry/DLQ behavior.

Package and deploy it the same way, but with the MVP handler and template:

```bash
cd src/handlers
zip -r ../../lambda-mvp-package.zip process_dump_mvp.py
cd ../..

aws s3 cp lambda-mvp-package.zip \
  s3://YOUR-NAME-cfn-artifacts-dev/lambda-mvp-package.zip \
  --profile personal

aws cloudformation deploy \
  --template-file template-mvp.yaml \
  --stack-name pipeline-mvp-dev \
  --parameter-overrides \
      LambdaCodeS3Bucket=YOUR-NAME-cfn-artifacts-dev \
      LambdaCodeS3Key=lambda-mvp-package.zip \
      TechnicalOwner=you@example.com \
  --capabilities CAPABILITY_NAMED_IAM \
  --profile personal \
  --region us-east-1
```

The upload, checkpoint, and cleanup steps are the same as the full
pipeline; only the stack name (`pipeline-mvp-dev`) and generated bucket
name differ.

## Secrets Manager variant (no VPC)

[template-mvp-secure.yaml](template-mvp-secure.yaml) is the MVP without
VPC networking, focused on IAM, encryption, and Secrets Manager instead:

- A customer-managed KMS key (with rotation and an alias) encrypts both
  the bucket and the secret.
- A Secrets Manager secret holds a generated synthetic API key. The
  Lambda reads it at first invocation through `SECRET_ARN`, caches it,
  and never logs the value — it stands in for a real delivery
  credential.
- The Lambda role is least-privilege: scoped S3 read/write,
  `ListBucket` on the checkpoints prefix, `Decrypt`/`GenerateDataKey`
  on the key, and `GetSecretValue` on that one secret.

The ZIP must include both handlers because `process_dump_secure`
imports the processing logic from `process_dump_mvp`:

```bash
cd src/handlers
zip ../../lambda-secure-package.zip process_dump_secure.py process_dump_mvp.py
cd ../..

aws s3 cp lambda-secure-package.zip \
  s3://YOUR-NAME-cfn-artifacts-dev/lambda-secure-package.zip \
  --profile personal

aws cloudformation deploy \
  --template-file template-mvp-secure.yaml \
  --stack-name pipeline-secure-dev \
  --parameter-overrides \
      LambdaCodeS3Bucket=YOUR-NAME-cfn-artifacts-dev \
      LambdaCodeS3Key=lambda-secure-package.zip \
      TechnicalOwner=you@example.com \
  --capabilities CAPABILITY_NAMED_IAM \
  --profile personal \
  --region us-east-1
```

Unlike the other stacks this one is not free: the KMS key is $1/month
and the secret $0.40/month (prorated). Deleting the stack removes the
secret immediately and schedules the KMS key for deletion after its
30-day waiting period, during which it does not bill.

## Cleanup

Empty the generated incoming bucket before deleting the stack because
CloudFormation cannot delete a non-empty versioned bucket.

```bash
aws s3 rm s3://YOUR-INCOMING-BUCKET --recursive --profile personal

aws cloudformation delete-stack \
  --stack-name pipeline-prototype-dev \
  --profile personal
```

The separately created artifacts bucket is not part of the stack.
