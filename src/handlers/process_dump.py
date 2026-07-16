"""
Intake Lambda: triggered per-message from the FIFO SQS queue.

Responsibility (kept deliberately narrow for the prototype):
  1. Parse the S3 event embedded in the SQS message body.
  2. Look up latest_successful_snapshot for this table from DynamoDB.
  3. Start a Step Functions execution with enough context for the
     state machine to decide delta vs. full processing.

Does NOT do the actual transform/load -- that's the state machine's job.
This keeps the Lambda fast and makes retries cheap (SQS redrive handles
message-level failures; Step Functions handles workflow-level failures).
"""

import json
import logging
import os
from datetime import datetime, timezone

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb")
sfn = boto3.client("stepfunctions")

SNAPSHOT_TABLE = os.environ["SNAPSHOT_TABLE"]
STATE_MACHINE_ARN = os.environ["STATE_MACHINE_ARN"]


def extract_table_name(s3_key: str) -> str:
    """
    Derive table name from the S3 key.
    Expects a convention like: incoming/<table_name>/<filename>.
    Adjust this once the real drop convention from the external team
    is confirmed -- this is a placeholder assumption for the prototype.
    """
    parts = s3_key.split("/")
    if len(parts) < 2:
        raise ValueError(f"Unexpected S3 key format, cannot derive table name: {s3_key}")
    return parts[1]


def get_latest_successful_snapshot(table_name: str) -> dict | None:
    table = dynamodb.Table(SNAPSHOT_TABLE)
    response = table.get_item(Key={"table_name": table_name})
    return response.get("Item")


def handler(event, context):
    results = []

    for record in event["Records"]:
        body = json.loads(record["body"])

        # S3 -> SQS notifications wrap the actual event in a JSON body
        s3_records = body.get("Records", [])
        if not s3_records:
            logger.warning("No S3 records in message body, skipping: %s", body)
            continue

        for s3_record in s3_records:
            bucket = s3_record["s3"]["bucket"]["name"]
            key = s3_record["s3"]["object"]["key"]

            table_name = extract_table_name(key)
            snapshot = get_latest_successful_snapshot(table_name)

            # Core principle: compare against latest_successful_snapshot,
            # never against "yesterday's file" -- handles delayed or
            # re-dropped dumps correctly via catch-up delta generation.
            last_snapshot_key = snapshot.get("s3_key") if snapshot else None
            last_snapshot_ts = snapshot.get("processed_at") if snapshot else None

            execution_input = {
                "bucket": bucket,
                "key": key,
                "table_name": table_name,
                "last_successful_snapshot_key": last_snapshot_key,
                "last_successful_snapshot_ts": last_snapshot_ts,
                "received_at": datetime.now(timezone.utc).isoformat(),
            }

            response = sfn.start_execution(
                stateMachineArn=STATE_MACHINE_ARN,
                name=f"{table_name}-{context.aws_request_id}",
                input=json.dumps(execution_input),
            )

            logger.info(
                "Started execution %s for table=%s key=%s",
                response["executionArn"],
                table_name,
                key,
            )
            results.append(response["executionArn"])

    return {"started_executions": results}
