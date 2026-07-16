"""Read synthetic LMS CSV dumps from S3 and deliver them to a mock API."""

import csv
import io
import json
import logging
import os
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import unquote_plus
from urllib.request import Request, urlopen

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")
API_URL = os.environ["API_URL"]


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


def send_to_api(payload: dict) -> int:
    request = Request(
        API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            # A real receiver can use this to make whole-file retries safe.
            "Idempotency-Key": payload["externalId"],
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=10) as response:
            return response.status
    except HTTPError as error:
        response_body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"API rejected {payload.get('externalId')}: "
            f"HTTP {error.code} {response_body}"
        ) from error
    except URLError as error:
        raise RuntimeError(f"API request failed: {error.reason}") from error


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
            status = send_to_api(payload)
            logger.info("Sent row=%s id=%s status=%s", row_number, payload["externalId"], status)
            sent += 1
        except Exception as error:
            raise RuntimeError(f"Failed source row {row_number} in s3://{bucket}/{key}: {error}") from error

    write_checkpoint(bucket, table_name, key, sent)
    return {"key": key, "status": "SUCCESS", "records_sent": sent}


def handler(event, context):
    failures = []
    results = []

    for record in event.get("Records", []):
        message_id = record.get("messageId", "unknown")
        try:
            body = json.loads(record["body"])
            for s3_record in body.get("Records", []):
                bucket = s3_record["s3"]["bucket"]["name"]
                key = unquote_plus(s3_record["s3"]["object"]["key"])
                results.append(process_s3_object(bucket, key))
        except Exception:
            logger.exception("Failed SQS message %s", message_id)
            failures.append({"itemIdentifier": message_id})

    return {"batchItemFailures": failures, "results": results}
