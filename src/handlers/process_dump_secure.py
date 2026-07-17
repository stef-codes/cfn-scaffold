"""Secure MVP: S3 -> Lambda processing with a Secrets Manager-backed API key.

Same CSV processing as process_dump_mvp, plus retrieval of a synthetic
API key from Secrets Manager at first invocation. The key is where a real
delivery integration would authenticate; this variant only proves the
retrieval and never logs the secret value.
"""

import json
import logging
import os
from urllib.parse import unquote_plus

import boto3

from process_dump_mvp import process_s3_object

logger = logging.getLogger()
logger.setLevel(logging.INFO)

secretsmanager = boto3.client("secretsmanager")
SECRET_ARN = os.environ["SECRET_ARN"]

_api_key: str | None = None


def get_api_key() -> str:
    """Fetch and cache the synthetic API key for the container's lifetime."""
    global _api_key
    if _api_key is None:
        response = secretsmanager.get_secret_value(SecretId=SECRET_ARN)
        _api_key = json.loads(response["SecretString"])["api_key"]
        logger.info(
            "Loaded synthetic API key from %s (version %s)",
            SECRET_ARN,
            response["VersionId"],
        )
    return _api_key


def handler(event, context):
    if not get_api_key():
        raise RuntimeError("Secret retrieved but api_key is empty")

    results = []
    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        key = unquote_plus(record["s3"]["object"]["key"])
        results.append(process_s3_object(bucket, key))
    return {"results": results}
