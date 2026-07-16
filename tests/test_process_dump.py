import importlib
import os
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("API_URL", "https://example.invalid/learning-objects")


class ProcessDumpTests(unittest.TestCase):
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
        cls.module = importlib.import_module("process_dump")

    def test_extract_table_name(self):
        self.assertEqual(
            self.module.extract_table_name("incoming/activities/dump.csv"),
            "activities",
        )

    def test_build_api_payload(self):
        payload = self.module.build_api_payload(
            {
                "activity_code": "10027",
                "title": "Defensive Driving",
                "subject_id": "SAFETY",
                "training_hours": "2.5",
                "active": "true",
            }
        )
        self.assertEqual(payload["externalId"], "DT10027")
        self.assertEqual(payload["subjectId"], "DT_SAFETY")
        self.assertEqual(payload["trainingHours"], 2.5)
        self.assertTrue(payload["active"])

    def test_checkpoint_is_written_after_all_rows_succeed(self):
        rows = [
            {
                "activity_code": "1",
                "title": "One",
                "subject_id": "TEST",
                "training_hours": "1",
                "active": "true",
            },
            {
                "activity_code": "2",
                "title": "Two",
                "subject_id": "TEST",
                "training_hours": "2",
                "active": "false",
            },
        ]
        with (
            patch.object(self.module, "get_checkpoint", return_value=None),
            patch.object(self.module, "read_csv_from_s3", return_value=rows),
            patch.object(self.module, "send_to_api", return_value=201),
            patch.object(self.module, "write_checkpoint") as write_checkpoint,
        ):
            result = self.module.process_s3_object(
                "test-bucket", "incoming/activities/dump.csv"
            )

        self.assertEqual(result["records_sent"], 2)
        write_checkpoint.assert_called_once_with(
            "test-bucket", "activities", "incoming/activities/dump.csv", 2
        )

    def test_checkpoint_is_not_written_when_api_fails(self):
        rows = [
            {
                "activity_code": "1",
                "title": "FAIL",
                "subject_id": "TEST",
                "training_hours": "1",
                "active": "true",
            }
        ]
        with (
            patch.object(self.module, "get_checkpoint", return_value=None),
            patch.object(self.module, "read_csv_from_s3", return_value=rows),
            patch.object(self.module, "send_to_api", side_effect=RuntimeError("500")),
            patch.object(self.module, "write_checkpoint") as write_checkpoint,
        ):
            with self.assertRaises(RuntimeError):
                self.module.process_s3_object(
                    "test-bucket", "incoming/activities/dump.csv"
                )

        write_checkpoint.assert_not_called()


if __name__ == "__main__":
    unittest.main()
