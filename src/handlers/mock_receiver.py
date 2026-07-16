"""A controllable HTTP receiver that imitates a small LMS API surface."""

import json
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body),
    }


def handler(event, context):
    try:
        payload = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return response(400, {"error": "Request body must be valid JSON"})

    required = ("externalId", "title", "subjectId")
    missing = [field for field in required if not payload.get(field)]
    if missing:
        return response(400, {"error": "Missing required fields", "fields": missing})

    # Synthetic titles provide deterministic ways to exercise retry behavior.
    title = payload["title"].upper()
    if "THROTTLE" in title:
        return response(429, {"error": "Synthetic throttling response"})
    if "FAIL" in title:
        return response(500, {"error": "Synthetic server failure"})

    logger.info("Accepted synthetic record id=%s", payload["externalId"])
    return response(201, {"status": "created", "id": payload["externalId"]})
