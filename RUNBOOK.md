# Runbook

Operational guide for deploying, verifying, and tearing down the stacks
in this repo. For what the project is and how it works, see
[README.md](README.md).

Everything below assumes the `personal` AWS profile and `us-east-1`.

## Preflight checks

Run these once per working session before deploying.

### 1. Tooling

```bash
aws --version        # AWS CLI v2
python3 --version    # 3.12+ matches the Lambda runtime
zip -v | head -1
```

### 2. Connectivity and identity

```bash
aws sso login --profile personal
aws sts get-caller-identity --profile personal
```

`get-caller-identity` must succeed and show your account ID — the
generated bucket names embed it. If you see `Token has expired`, run
the login command again.

### 3. Permissions

Quick read-only probes for each service the templates touch:

```bash
for svc in "cloudformation list-stacks" "s3api list-buckets" \
           "iam list-roles --max-items 1" "lambda list-functions --max-items 1" \
           "sqs list-queues" "kms list-keys --limit 1" \
           "secretsmanager list-secrets --max-results 1"; do
  eval "aws $svc --profile personal --region us-east-1" >/dev/null 2>&1 \
    && echo "OK   ${svc%% *}" || echo "FAIL ${svc%% *}"
done
```

Deploying also needs write access: the stacks create named IAM roles
(hence `--capabilities CAPABILITY_NAMED_IAM`), Lambda functions, S3
buckets, and — depending on the template — SQS queues, KMS keys, and
Secrets Manager secrets. An `AdministratorAccess` SSO role covers all
of it; anything narrower must allow those services' create/delete
actions plus `iam:PassRole` on the created roles.

### 4. Local checks (no AWS needed)

```bash
python3 -m unittest discover -s tests   # all handler unit tests
pip install cfn-lint && cfn-lint template*.yaml
```

Both must pass before a deploy is worth attempting. Note your IDE's
YAML plugin may flag `!Ref`/`!Sub` as "Unresolved tag" — that is a
false positive for CloudFormation short forms; trust cfn-lint.

### 5. VPC prerequisites (template-mvp.yaml only)

The MVP template places the Lambda in a VPC, which needs two subnet
IDs, a security group ID, and — critically — an S3 **gateway** endpoint.
A VPC-attached Lambda has no route to S3 without one and every S3 call
will hang until timeout.

```bash
VPC_ID=$(aws ec2 describe-vpcs --filters Name=is-default,Values=true \
  --query 'Vpcs[0].VpcId' --output text --profile personal --region us-east-1)

# Subnets and default security group to pass as parameters
aws ec2 describe-subnets --filters Name=vpc-id,Values=$VPC_ID \
  --query 'Subnets[].[SubnetId,AvailabilityZone]' --output text \
  --profile personal --region us-east-1
aws ec2 describe-security-groups \
  --filters Name=vpc-id,Values=$VPC_ID Name=group-name,Values=default \
  --query 'SecurityGroups[0].GroupId' --output text \
  --profile personal --region us-east-1

# Does an S3 gateway endpoint already exist?
aws ec2 describe-vpc-endpoints \
  --filters Name=vpc-id,Values=$VPC_ID Name=service-name,Values=com.amazonaws.us-east-1.s3 \
  --query 'VpcEndpoints[].VpcEndpointId' --output text \
  --profile personal --region us-east-1
```

If the last command prints nothing, create the endpoint (free, unlike
a NAT gateway):

```bash
RT_ID=$(aws ec2 describe-route-tables \
  --filters Name=vpc-id,Values=$VPC_ID Name=association.main,Values=true \
  --query 'RouteTables[0].RouteTableId' --output text \
  --profile personal --region us-east-1)

aws ec2 create-vpc-endpoint --vpc-id $VPC_ID \
  --service-name com.amazonaws.us-east-1.s3 \
  --route-table-ids $RT_ID --profile personal --region us-east-1
```

Avoid `us-east-1e` subnets; Lambda does not support that AZ.

## Deploy

Create an artifacts bucket once (shared by all variants):

```bash
aws s3 mb s3://YOUR-NAME-cfn-artifacts-dev \
  --profile personal \
  --region us-east-1
```

### Full pipeline (template.yaml)

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

### MVP (template-mvp.yaml)

Uses the subnet and security group IDs from preflight step 5.

```bash
cd src/handlers
zip ../../lambda-mvp-package.zip process_dump_mvp.py
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
      LambdaSubnetIds=subnet-XXXX,subnet-YYYY \
      LambdaSecurityGroupIds=sg-XXXX \
      TechnicalOwner=you@example.com \
  --capabilities CAPABILITY_NAMED_IAM \
  --profile personal \
  --region us-east-1
```

### Secrets Manager variant (template-mvp-secure.yaml)

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

## Verify a deployment

These checks work for any of the three stacks — set `STACK` accordingly
(`pipeline-prototype-dev`, `pipeline-mvp-dev`, or `pipeline-secure-dev`).

```bash
STACK=pipeline-mvp-dev
PROFILE="--profile personal --region us-east-1"

# 1. Stack landed cleanly
aws cloudformation describe-stacks --stack-name $STACK \
  --query 'Stacks[0].StackStatus' --output text $PROFILE
# expect: CREATE_COMPLETE or UPDATE_COMPLETE

BUCKET=$(aws cloudformation describe-stacks --stack-name $STACK \
  --query "Stacks[0].Outputs[?OutputKey=='IncomingBucketName'].OutputValue" \
  --output text $PROFILE)
FUNC=$(aws cloudformation describe-stacks --stack-name $STACK \
  --query "Stacks[0].Outputs[?OutputKey=='ProcessFunctionName'].OutputValue" \
  --output text $PROFILE)

# 2. The function exists and is wired to the right role
aws lambda get-function-configuration --function-name $FUNC \
  --query '[Role,Handler,State]' $PROFILE

# 3. The role's permissions are what the template says
ROLE=$(basename $(aws lambda get-function-configuration \
  --function-name $FUNC --query Role --output text $PROFILE))
aws iam list-attached-role-policies --role-name $ROLE $PROFILE
aws iam list-role-policies --role-name $ROLE $PROFILE
aws iam get-role-policy --role-name $ROLE \
  --policy-name process-permissions $PROFILE   # inline-policy stacks only

# 4. S3 -> Lambda (or S3 -> SQS) event wiring is in place
aws s3api get-bucket-notification-configuration --bucket $BUCKET $PROFILE

# 5. S3 is allowed to invoke the function (MVP stacks)
aws lambda get-policy --function-name $FUNC --query Policy \
  --output text $PROFILE | python3 -m json.tool
```

Then run the end-to-end smoke test — upload, checkpoint, logs:

```bash
aws s3 cp events/sample_activities.csv \
  s3://$BUCKET/incoming/activities/activities_$(date +%Y%m%d).csv --profile personal

sleep 15
aws s3 cp s3://$BUCKET/checkpoints/activities/latest.json - --profile personal
aws logs tail /aws/lambda/$FUNC --since 5m $PROFILE
```

Success is a checkpoint JSON with `"status": "SUCCESS"` and mapped
payloads in the logs. Re-uploading the same key logs
`Already completed; skipping duplicate event` — that is the
duplicate-event guard working, not a failure. For the secrets variant,
the first invocation also logs
`Loaded synthetic API key from <arn> (version ...)`.

## Troubleshooting

- **`Token has expired` on any command** — SSO session lapsed; run
  `aws sso login --profile personal`.
- **`AccessDenied ... s3:ListBucket` in the Lambda logs on the first
  file** — the role is missing the `DetectMissingCheckpoints`
  statement. Without `s3:ListBucket`, S3 returns 403 instead of 404
  for a missing checkpoint and first-run detection breaks.
- **Lambda hangs ~120s then times out on S3 calls
  (template-mvp.yaml)** — the VPC has no S3 gateway endpoint; see the
  preflight section.
- **`BucketAlreadyExists` during deploy** — the `ProjectName` +
  `Environment` combination collides with another stack (bucket names
  are global); override `ProjectName`.
- **Stack stuck in `ROLLBACK_COMPLETE` after a failed first create** —
  CloudFormation cannot update from that state; delete the stack and
  deploy again.
- **Upload succeeds but the Lambda never fires** — check step 4/5
  of the verification section; the object must match both the
  `incoming/` prefix and `.csv` suffix filters.
- **Checkpoint missing after a failed run** — expected by design: the
  checkpoint is only written when every row succeeds. Check the logs
  for the failing row, fix the CSV, re-upload.

## Cleanup

Empty the generated incoming bucket before deleting a stack because
CloudFormation cannot delete a non-empty versioned bucket. Repeat per
stack you deployed:

```bash
aws s3 rm s3://YOUR-INCOMING-BUCKET --recursive --profile personal

aws cloudformation delete-stack \
  --stack-name pipeline-prototype-dev \
  --profile personal
```

Not part of any stack, so remove separately if you are done with the
project entirely:

- the artifacts bucket (`aws s3 rb s3://YOUR-NAME-cfn-artifacts-dev --force`)
- the S3 gateway VPC endpoint, if you created one in preflight
  (`aws ec2 delete-vpc-endpoints --vpc-endpoint-ids vpce-...`)
