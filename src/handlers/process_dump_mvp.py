"""MVP: read synthetic LMS CSV dumps from S3 and checkpoint the mapped rows.

S3 invokes this handler directly (no SQS). Rows are validated and mapped
to the same Cornerstone-like contract as the full pipeline, but the
payloads are logged instead of posted to an API.
"""

import csv
import io
import json
import logging
from datetime import datetime, timezone
from urllib.parse import unquote_plus

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")


def extract_table_name(s3_key: str) -> str:
    """Extract the domain from incoming/<table_name>/<filename>."""
    parts = s3_key.split("/")
    if len(parts) < 3 or parts[0] != "incoming" or not parts[1]:
        raise ValueError(f"Unexpected S3 key format: {s3_key}")
    return parts[1]


def read_csv_from_s3(bucket: str, key: str) -> list[dict]:
    response = s3.get_object(Bucket=bucket, Key=key)
    csv_text = response["Body"].read().decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(csv_text)))


def build_api_payload(row: dict) -> dict:
    """Map a synthetic activity row to a Cornerstone-like contract."""
    required = ("activity_code", "title", "subject_id")
    missing = [name for name in required if not (row.get(name) or "").strip()]
    if missing:
        raise ValueError(f"Missing required CSV fields: {', '.join(missing)}")

    hours = (row.get("training_hours") or "").strip()
    active = (row.get("active") or "true").strip().lower()
    if active not in {"true", "false"}:
        raise ValueError("active must be true or false")

    return {
        "externalId": f"DT{row['activity_code'].strip()}",
        "title": row["title"].strip(),
        "subjectId": f"DT_{row['subject_id'].strip()}",
        "trainingHours": float(hours) if hours else None,
        "active": active == "true",
    }


def checkpoint_key(table_name: str) -> str:
    return f"checkpoints/{table_name}/latest.json"


def get_checkpoint(bucket: str, table_name: str) -> dict | None:
    try:
        response = s3.get_object(Bucket=bucket, Key=checkpoint_key(table_name))
    except ClientError as error:
        if error.response["Error"]["Code"] in {"NoSuchKey", "404"}:
            return None
        raise
    return json.loads(response["Body"].read())


def write_checkpoint(bucket: str, table_name: str, source_key: str, count: int) -> None:
    checkpoint = {
        "table_name": table_name,
        "source_key": source_key,
        "processed_at": datetime.now(timezone.utc).isoformat(),
        "records_sent": count,
        "status": "SUCCESS",
    }
    s3.put_object(
        Bucket=bucket,
        Key=checkpoint_key(table_name),
        Body=json.dumps(checkpoint, indent=2).encode("utf-8"),
        ContentType="application/json",
    )


def process_s3_object(bucket: str, key: str) -> dict:
    table_name = extract_table_name(key)
    previous = get_checkpoint(bucket, table_name)
    if previous and previous.get("source_key") == key:
        logger.info("Already completed; skipping duplicate event for %s", key)
        return {"key": key, "status": "SKIPPED", "records_sent": 0}

    rows = read_csv_from_s3(bucket, key)
    sent = 0
    for row_number, row in enumerate(rows, start=2):
        try:
            payload = build_api_payload(row)
            logger.info("Mapped row=%s payload=%s", row_number, json.dumps(payload))
            sent += 1
        except Exception as error:
            raise RuntimeError(f"Failed source row {row_number} in s3://{bucket}/{key}: {error}") from error

    write_checkpoint(bucket, table_name, key, sent)
    return {"key": key, "status": "SUCCESS", "records_sent": sent}


def handler(event, context):
    results = []
    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        key = unquote_plus(record["s3"]["object"]["key"])
        results.append(process_s3_object(bucket, key))
    return {"results": results}
