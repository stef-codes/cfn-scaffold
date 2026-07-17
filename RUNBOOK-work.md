# Work runbook

How to mimic this project at work with [template-work.yaml](template-work.yaml),
shrunk to a work MVP that uses infrastructure the cloud team already
controls:

```text
Upload test CSV
      ↓
Work S3 bucket
      ↓
Lambda
      ↓
Mock endpoint or logging
      ↓
Checkpoint/error file in S3
```

SQS, KMS, Secrets Manager, and a custom VPC are deliberately left out
until the basic deployment works. Add them only when the actual
requirements call for them (see the last section).

The template creates **no IAM resources**: the Lambda execution role
comes from the cloud team as a parameter, so the deploy needs no
`--capabilities` flag at all.

## 1. Replace the personal AWS settings

Instead of `--profile personal` / `us-east-1`, use the work profile and
the region the architect gives you:

```bash
export AWS_PROFILE=your-work-profile
export AWS_REGION=us-west-2
```

Then authenticate and verify your identity:

```bash
aws sso login --profile "$AWS_PROFILE"

aws sts get-caller-identity --profile "$AWS_PROFILE"
```

If the work environment provides credentials without a named profile,
omit `--profile`. With `AWS_PROFILE` and `AWS_REGION` exported, the
remaining commands in this runbook need no explicit flags — they are
shown with the variables for clarity.

## 2. Ask for existing infrastructure values

Do not create a new VPC, subnets, endpoint, KMS key, or IAM role until
the cloud team confirms you're allowed to. Ask the architect for:

```text
AWS account:
Region:
Work SSO profile/permission set:
TechnicalOwner tag key:
TechnicalOwner tag value:
Deployment role ARN:
Lambda execution role ARN:
Existing artifacts bucket:
Existing inbound/test bucket:
Bucket naming standard (template default: <project>-<env>-incoming-<account>):
VPC required?:
If yes, subnet IDs:
If yes, security group ID:
Approved outbound route to Cornerstone:
```

The biggest question is whether you may create IAM roles. The personal
templates use `--capabilities CAPABILITY_NAMED_IAM` because they create
named roles; many work environments prohibit developers from doing
that. `template-work.yaml` instead takes the role as a parameter:

```yaml
Parameters:
  LambdaExecutionRoleArn:
    Type: String

Resources:
  ProcessFunction:
    Type: AWS::Lambda::Function
    Properties:
      Role: !Ref LambdaExecutionRoleArn
```

### What the execution role must include

Hand this to whoever creates the role. Trust policy: assumable by
`lambda.amazonaws.com`. Permissions: CloudWatch Logs write access
(`AWSLambdaBasicExecutionRole` or equivalent) plus, for the bucket the
stack creates:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ReadIncoming",
      "Effect": "Allow",
      "Action": "s3:GetObject",
      "Resource": "arn:aws:s3:::BUCKET-NAME/incoming/*"
    },
    {
      "Sid": "WriteCheckpoints",
      "Effect": "Allow",
      "Action": "s3:PutObject",
      "Resource": "arn:aws:s3:::BUCKET-NAME/checkpoints/*"
    },
    {
      "Sid": "DetectMissingCheckpoints",
      "Effect": "Allow",
      "Action": "s3:ListBucket",
      "Resource": "arn:aws:s3:::BUCKET-NAME",
      "Condition": { "StringLike": { "s3:prefix": "checkpoints/*" } }
    }
  ]
}
```

The `ListBucket` statement is not optional: without it, S3 returns 403
instead of 404 for a missing checkpoint and the handler's first-run
detection breaks (found the hard way — see RUNBOOK.md troubleshooting).

## 3. Confirm the tagging standard

The template applies a `TechnicalOwner` tag to the bucket and the
Lambda, with the value from the `TechnicalOwner` parameter (the
execution role belongs to the cloud team, so they tag it). Ask for the
exact required value — and the exact required **key**. If the
organization's standard is `technical-owner`, `technical_owner`, or
`Technical-Owner`, edit the `Key:` literals in the template to match
their spelling and capitalization exactly; the key cannot be
parameterized.

## 4. Run read-only permission checks

Don't assume you need (or have) `AdministratorAccess` — you shouldn't
need it for this project. Start with broad probes:

```bash
aws cloudformation list-stacks --profile "$AWS_PROFILE" --region "$AWS_REGION"

aws s3api list-buckets --profile "$AWS_PROFILE"

aws lambda list-functions --max-items 5 \
  --profile "$AWS_PROFILE" --region "$AWS_REGION"
```

An individual command failing doesn't necessarily mean the project
cannot work. For example, the organization may block
`s3:ListAllMyBuckets` while allowing access to one designated bucket.
Test the exact resources where possible:

```bash
aws s3api head-bucket --bucket YOUR-WORK-BUCKET --profile "$AWS_PROFILE"

aws lambda get-function --function-name YOUR-TEST-FUNCTION \
  --profile "$AWS_PROFILE" --region "$AWS_REGION"
```

## 5. Validate locally before touching AWS

```bash
python3 -m unittest discover -s tests

cfn-lint template-work.yaml

aws cloudformation validate-template \
  --template-body file://template-work.yaml \
  --profile "$AWS_PROFILE" --region "$AWS_REGION"
```

`validate-template` only checks the template structure. It doesn't
prove you have permission to deploy everything.

## 6. Package the Lambda

For the first work test, the Lambda reads the CSV, validates and maps
each row, logs the mapped payloads, and writes a checkpoint — without
calling Cornerstone. That is exactly what `process_dump_mvp.py` does:

```bash
cd src/handlers
zip ../../lambda-work-package.zip process_dump_mvp.py
cd ../..
```

Upload it to the cloud team's approved artifacts bucket:

```bash
aws s3 cp lambda-work-package.zip \
  s3://YOUR-ARTIFACTS-BUCKET/lambda/lambda-work-package.zip \
  --profile "$AWS_PROFILE" --region "$AWS_REGION"
```

This upload also tests whether your current role has `s3:PutObject`.

## 7. Preview the CloudFormation deployment

Before deploying, create a change set the architect can review:

```bash
aws cloudformation deploy \
  --template-file template-work.yaml \
  --stack-name cornerstone-pipeline-dev \
  --parameter-overrides \
      LambdaCodeS3Bucket=YOUR-ARTIFACTS-BUCKET \
      LambdaCodeS3Key=lambda/lambda-work-package.zip \
      TechnicalOwner=YOUR-APPROVED-VALUE \
      LambdaExecutionRoleArn=YOUR-APPROVED-ROLE-ARN \
  --no-execute-changeset \
  --profile "$AWS_PROFILE" --region "$AWS_REGION"
```

`--no-execute-changeset` prepares the proposed changes without
deploying them; the command prints a `describe-change-set` command to
inspect exactly what would be created. No `--capabilities` flag is
needed because the template creates no IAM resources.

When approved, rerun the same command without `--no-execute-changeset`.

## 8. Smoke-test it with fake data

Use a synthetic CSV — no employee IDs, emails, or other real PII:

```bash
aws s3 cp events/sample_activities.csv \
  s3://YOUR-INBOUND-BUCKET/incoming/activities/test.csv \
  --profile "$AWS_PROFILE" --region "$AWS_REGION"
```

Then inspect the logs:

```bash
aws logs tail /aws/lambda/cornerstone-pipeline-dev-process-dump \
  --since 10m \
  --profile "$AWS_PROFILE" --region "$AWS_REGION"
```

And retrieve the checkpoint:

```bash
aws s3 cp \
  s3://YOUR-INBOUND-BUCKET/checkpoints/activities/latest.json - \
  --profile "$AWS_PROFILE" --region "$AWS_REGION"
```

Success looks the same as the personal MVP: mapped payloads in the
logs and a checkpoint with `"status": "SUCCESS"`. A failed row means
no checkpoint and the error in the logs — by design.

## What to tell the architect

> "I want to start with a tagged CloudFormation MVP: an approved S3
> bucket triggers one Lambda using a pre-approved execution role. The
> Lambda will process synthetic data and write a checkpoint to S3. I'll
> validate the template and generate a change set for review before
> deployment. Once that works, we can determine whether the production
> design needs a VPC, SQS, Secrets Manager, and outbound access to
> Cornerstone."

That gives you a real work simulation without asking the organization
to approve the entire final architecture at once. The most important
things to obtain first: the work role, exact `TechnicalOwner` tag,
approved region, artifacts bucket, Lambda execution role, and whether
a VPC is mandated.

## When to add the pieces back

Each omitted component has a concrete trigger, and this repo already
has a template demonstrating it:

- **SQS + DLQ** ([template.yaml](template.yaml)) — when a dropped file
  is unacceptable. S3's direct async invoke retries twice and gives up;
  the queue gives durable retries and a DLQ to alarm on.
- **KMS customer-managed key** ([template-mvp-secure.yaml](template-mvp-secure.yaml)) —
  when the org's data classification requires CMK encryption instead of
  SSE-S3, or auditors need key-usage logging.
- **Secrets Manager** ([template-mvp-secure.yaml](template-mvp-secure.yaml)) —
  the moment the Lambda authenticates to the real Cornerstone API.
  Never put the credential in code, env vars, or the template.
- **VPC** ([template-mvp.yaml](template-mvp.yaml)) — only if the org
  mandates it or the Lambda must reach private resources. Remember the
  S3 gateway endpoint requirement that comes with it.
