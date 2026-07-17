import importlib
import json
import os
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault(
    "SECRET_ARN", "arn:aws:secretsmanager:us-east-1:000000000000:secret:test"
)


class ProcessDumpSecureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fake_boto3 = types.ModuleType("boto3")
        fake_boto3.client = MagicMock(return_value=MagicMock())

        fake_botocore = types.ModuleType("botocore")
        fake_exceptions = types.ModuleType("botocore.exceptions")

        class ClientError(Exception):
            pass

        fake_exceptions.ClientError = ClientError
        fake_botocore.exceptions = fake_exceptions
        sys.modules["boto3"] = fake_boto3
        sys.modules["botocore"] = fake_botocore
        sys.modules["botocore.exceptions"] = fake_exceptions
        sys.path.insert(0, "src/handlers")
        cls.module = importlib.import_module("process_dump_secure")

    def setUp(self):
        self.module._api_key = None

    def test_secret_is_fetched_once_and_cached(self):
        secrets_client = MagicMock()
        secrets_client.get_secret_value.return_value = {
            "SecretString": json.dumps({"api_key": "synthetic-key"}),
            "VersionId": "v1",
        }
        event = {
            "Records": [
                {
                    "s3": {
                        "bucket": {"name": "test-bucket"},
                        "object": {"key": "incoming/activities/dump.csv"},
                    }
                }
            ]
        }
        with (
            patch.object(self.module, "secretsmanager", secrets_client),
            patch.object(
                self.module, "process_s3_object", return_value={"status": "SUCCESS"}
            ) as process,
        ):
            self.module.handler(event, None)
            self.module.handler(event, None)

        secrets_client.get_secret_value.assert_called_once()
        self.assertEqual(process.call_count, 2)

    def test_handler_fails_when_api_key_is_empty(self):
        secrets_client = MagicMock()
        secrets_client.get_secret_value.return_value = {
            "SecretString": json.dumps({"api_key": ""}),
            "VersionId": "v1",
        }
        with patch.object(self.module, "secretsmanager", secrets_client):
            with self.assertRaises(RuntimeError):
                self.module.handler({"Records": []}, None)


if __name__ == "__main__":
    unittest.main()
