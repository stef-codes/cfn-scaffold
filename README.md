# Synthetic LMS API Delivery Prototype

Pure CloudFormation prototype for practicing an S3-to-API delivery workflow.
It uses synthetic data only and does not connect to Cornerstone or contain
work data, credentials, endpoints, or proprietary mapping rules.

**To deploy, verify, or tear down any of the stacks, follow
[RUNBOOK.md](RUNBOOK.md)** — it covers preflight checks (tooling,
connectivity, permissions, VPC prerequisites), per-variant deploy
commands, post-deploy verification, troubleshooting, and cleanup.

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

## Templates

Three deployable variants, from most to least complete:

### Full pipeline — [template.yaml](template.yaml)

The flow described above, deployed as stack `pipeline-prototype-dev`:

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

### MVP — [template-mvp.yaml](template-mvp.yaml)

Stack `pipeline-mvp-dev`. A stripped-down version for when you only want
the S3-to-Lambda mechanics: the bucket invokes the processing Lambda
directly (in a VPC), and the Lambda validates and maps each row, logs
the mapped payloads, and writes the checkpoint. There is no SQS queue,
DLQ, mock API, KMS key, or alarm. Because S3 invokes the Lambda
asynchronously, a failed file is retried twice by Lambda and then
dropped — the full template is the one that demonstrates durable
retry/DLQ behavior.

### Secrets Manager variant — [template-mvp-secure.yaml](template-mvp-secure.yaml)

Stack `pipeline-secure-dev`. The MVP without VPC networking, focused on
IAM, encryption, and Secrets Manager instead:

- A customer-managed KMS key (with rotation and an alias) encrypts both
  the bucket and the secret.
- A Secrets Manager secret holds a generated synthetic API key. The
  Lambda reads it at first invocation through `SECRET_ARN`, caches it,
  and never logs the value — it stands in for a real delivery
  credential.
- The Lambda role is least-privilege: scoped S3 read/write,
  `ListBucket` on the checkpoints prefix, `Decrypt`/`GenerateDataKey`
  on the key, and `GetSecretValue` on that one secret.

### Work variant — [template-work.yaml](template-work.yaml)

Stack `cornerstone-pipeline-dev`. The shape to mimic at work: S3 →
Lambda → logging → checkpoint, using infrastructure the cloud team
already controls. It creates **no IAM resources** — the Lambda
execution role is passed in as a parameter, so deploying needs no
`CAPABILITY_NAMED_IAM` — and leaves out SQS, KMS, Secrets Manager, and
VPC until requirements call for them. It reuses the MVP handler
unchanged. Follow [RUNBOOK-work.md](RUNBOOK-work.md), which covers the
work-specific flow: what to ask the architect for, the execution-role
policy to request, change-set preview before deploy, and when to add
each omitted component back.

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
`FAIL` to make it return `500`. This exercises the queue retry and DLQ
path (full pipeline only; the MVP variants log payloads instead of
posting them).

## Layout

- `template*.yaml` — the three CloudFormation templates
- `src/handlers/` — Lambda handlers (`process_dump.py` + `mock_receiver.py`
  for the full pipeline, `process_dump_mvp.py` for the MVP,
  `process_dump_secure.py` for the secrets variant)
- `tests/` — handler unit tests (`python3 -m unittest discover -s tests`)
- `events/` — sample CSV and event payloads
- [RUNBOOK.md](RUNBOOK.md) — deploy, verify, troubleshoot, clean up (personal AWS)
- [RUNBOOK-work.md](RUNBOOK-work.md) — the work-environment version of the same
