import importlib
import sys
import types
import unittest
from unittest.mock import MagicMock, patch


class ProcessDumpMvpTests(unittest.TestCase):
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
        cls.module = importlib.import_module("process_dump_mvp")

    def test_handler_processes_direct_s3_event(self):
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
        rows = [
            {
                "activity_code": "1",
                "title": "One",
                "subject_id": "TEST",
                "training_hours": "1",
                "active": "true",
            }
        ]
        with (
            patch.object(self.module, "get_checkpoint", return_value=None),
            patch.object(self.module, "read_csv_from_s3", return_value=rows),
            patch.object(self.module, "write_checkpoint") as write_checkpoint,
        ):
            result = self.module.handler(event, None)

        self.assertEqual(result["results"][0]["records_sent"], 1)
        write_checkpoint.assert_called_once_with(
            "test-bucket", "activities", "incoming/activities/dump.csv", 1
        )

    def test_checkpoint_is_not_written_when_a_row_is_invalid(self):
        rows = [
            {
                "activity_code": "1",
                "title": "",
                "subject_id": "TEST",
                "training_hours": "1",
                "active": "true",
            }
        ]
        with (
            patch.object(self.module, "get_checkpoint", return_value=None),
            patch.object(self.module, "read_csv_from_s3", return_value=rows),
            patch.object(self.module, "write_checkpoint") as write_checkpoint,
        ):
            with self.assertRaises(RuntimeError):
                self.module.process_s3_object(
                    "test-bucket", "incoming/activities/dump.csv"
                )

        write_checkpoint.assert_not_called()

    def test_duplicate_event_is_skipped(self):
        previous = {"source_key": "incoming/activities/dump.csv"}
        with (
            patch.object(self.module, "get_checkpoint", return_value=previous),
            patch.object(self.module, "write_checkpoint") as write_checkpoint,
        ):
            result = self.module.process_s3_object(
                "test-bucket", "incoming/activities/dump.csv"
            )

        self.assertEqual(result["status"], "SKIPPED")
        write_checkpoint.assert_not_called()


if __name__ == "__main__":
    unittest.main()
