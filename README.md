# Pipeline Prototype -- Native CloudFormation (No SAM)

Personal-account prototype using **pure CloudFormation**, no SAM transform,
no SAM CLI. Every resource -- including IAM roles and policies -- is
written out explicitly, matching the style you'd use for the government
project. **No real or CUI data belongs in this repo or this AWS account.**
Use synthetic data only.

## What's here

- `template.yaml` -- native CloudFormation: KMS key, S3 bucket, FIFO SQS +
  DLQ, DynamoDB snapshot table, explicit IAM roles/policies for the Lambda
  and the state machine, Lambda function + event source mapping, Step
  Functions state machine (definition inlined via `Fn::Sub`), CloudWatch
  alarms.
- `src/handlers/process_dump.py` -- same intake Lambda logic as before:
  looks up `latest_successful_snapshot`, starts a Step Functions execution.
- `events/sqs_s3_event.json` -- sample event for manual/local testing.

## Key differences from the SAM version

- **No `AWS::Serverless::*` shorthand.** `AWS::Lambda::Function`,
  `AWS::IAM::Role`, `AWS::Lambda::EventSourceMapping`, and
  `AWS::StepFunctions::StateMachine` are all spelled out.
- **IAM is explicit and scoped.** Each role's policy lists exact actions
  and resources instead of relying on SAM's `Policies:` templates
  (`DynamoDBReadPolicy`, etc.) which generate broader managed policies
  behind the scenes.
- **Circular dependency avoided deliberately.** The Lambda's IAM policy
  needs `states:StartExecution` on the state machine, but the state
  machine doesn't exist yet when the role is created. Rather than
  `!GetAtt PipelineStateMachine.Arn` (which would create a cycle), the
  policy is scoped to a `Fn::Sub`'d ARN built from the predictable naming
  pattern. Worth remembering, this is a normal pattern in CFN -- you'll
  hit it again.
- **Lambda code isn't inline in the template.** It's uploaded to S3 first,
  then referenced by bucket/key. This is what `aws cloudformation package`
  automates for you below.

## Prerequisites

- AWS CLI v2, SSO profile configured
- Python 3.12 (matching the Lambda runtime, for local logic testing)
- An S3 bucket to hold deployment artifacts (see step 1)
- No SAM CLI, no Docker required

## Deploy steps

### 1. Create (or reuse) an artifacts bucket

CloudFormation needs somewhere to stage the zipped Lambda code before it
can reference it in the template.

```bash
aws s3 mb s3://YOUR-NAME-cfn-artifacts-dev --profile personal-pipeline --region us-east-1
```

### 2. Package -- zips Lambda code, uploads to S3, rewrites the template

```bash
cd cfn-scaffold

aws cloudformation package \
  --template-file template.yaml \
  --s3-bucket YOUR-NAME-cfn-artifacts-dev \
  --output-template-file packaged.yaml \
  --profile personal-pipeline
```

This is a **CLI feature, not SAM** -- it walks the template, finds
`Code:` blocks pointing at local paths, zips them, uploads to S3, and
replaces the local path with the real `S3Bucket`/`S3Key`. Since our
template already expects `LambdaCodeS3Bucket`/`LambdaCodeS3Key` as
parameters rather than a local path, you have two options:

**Option A (recommended for learning raw CFN):** skip `package` and zip +
upload manually, then pass the bucket/key as parameters:

```bash
cd src/handlers
zip -r ../../lambda-package.zip process_dump.py
cd ../..

aws s3 cp lambda-package.zip s3://YOUR-NAME-cfn-artifacts-dev/lambda-package.zip \
  --profile personal-pipeline

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

`CAPABILITY_NAMED_IAM` is required because the template creates named IAM
roles -- CloudFormation wants explicit acknowledgment before it'll create
IAM resources on your behalf. You'll use this same flag on the government
project.

**Option B:** switch `Code:` in `template.yaml` to a local path
(`CodeUri: src/handlers/`) and use `aws cloudformation package` to handle
zipping/uploading automatically -- closer to the SAM build experience but
still pure CloudFormation under the hood.

### 3. Watch the deploy

```bash
aws cloudformation describe-stack-events \
  --stack-name pipeline-prototype-dev \
  --profile personal-pipeline \
  --max-items 20
```

Or watch it in the CloudFormation console -- easier to read the event
timeline visually while you're still getting a feel for how resources
come up.

## Testing without SAM local

You lose `sam local invoke`. Two fallbacks:

**A. Test the Lambda logic directly, no AWS calls involved for parsing:**
```bash
cd src/handlers
python3 -c "
import json
from process_dump import extract_table_name
print(extract_table_name('incoming/students/dump.csv'))
"
```
Useful for the pure-logic pieces (`extract_table_name`, event parsing).
Anything hitting DynamoDB/Step Functions needs real credentials, so this
only covers part of the function.

**B. After deploying, invoke the real Lambda with the sample event:**
```bash
aws lambda invoke \
  --function-name pipeline-prototype-dev-intake \
  --payload file://events/sqs_s3_event.json \
  --cli-binary-format raw-in-base64-out \
  response.json \
  --profile personal-pipeline

cat response.json
```
This is the realistic gov-environment testing loop: deploy, invoke,
check CloudWatch Logs, iterate. Slower than local Docker-based testing,
but it's what you'll actually be doing day to day.

```bash
aws logs tail /aws/lambda/pipeline-prototype-dev-intake --follow --profile personal-pipeline
```

## Updating after code changes

Re-run steps 1 (if needed) and 2 -- re-zip, re-upload (same key is fine,
S3 versioning on the artifacts bucket is optional but handy), then
`aws cloudformation deploy` again with the same parameters. CloudFormation
only updates what changed.

## Cleanup

```bash
aws cloudformation delete-stack --stack-name pipeline-prototype-dev --profile personal-pipeline
```

Note: the S3 artifacts bucket and the `IncomingBucket` (if it has objects
in it) won't delete automatically if non-empty -- empty them first if you
want a full teardown:
```bash
aws s3 rm s3://YOUR-NAME-cfn-artifacts-dev --recursive --profile personal-pipeline
```

## What's intentionally out of scope (pilot)

- DataSync on-prem -> S3 path
- Secrets Manager integration
- Real validation/transform logic (`ValidateInput`, `FullLoad`, `DeltaLoad`
  are `Pass` states)
- Nested stacks / cross-stack references -- everything's in one template
  for now, deliberately, to keep the CFN learning curve manageable before
  splitting into the multi-stack structure a production deployment would use
