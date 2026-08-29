import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ophelia_assistant.batch import group_tasks_by_window
from ophelia_assistant.browser import (
    FAILURE_PROMPT_RE,
    SUCCESS_PROMPT_RE,
    _send_button_selectors,
)
from ophelia_assistant.config import Settings
from ophelia_assistant.database import Database
from ophelia_assistant.workflow import Workflow


class SendButtonDetectionTests(unittest.TestCase):
    def test_multiple_selectors_cover_languages_and_tooltips(self) -> None:
        selectors = _send_button_selectors()
        self.assertGreaterEqual(len(selectors), 8)
        combined = "\n".join(selectors)
        self.assertIn("aria-label^=\"Send\"", combined)
        self.assertIn("aria-label^=\"发送\"", combined)
        self.assertIn("aria-label^=\"寄出\"", combined)
        self.assertIn("data-tooltip", combined)
        self.assertIn("[gh=\"cm\"]", combined)

    def test_success_prompt_patterns(self) -> None:
        for text in ("Message sent", "已发送", "邮件已发送", "已寄出", "寄出"):
            self.assertIsNotNone(SUCCESS_PROMPT_RE.search(text), text)

    def test_failure_prompt_patterns(self) -> None:
        for text in ("发送失败", "无法发送", "Error sending", "出错了"):
            self.assertIsNotNone(FAILURE_PROMPT_RE.search(text), text)


class WindowGroupingTests(unittest.TestCase):
    def test_groups_by_window_and_reports_unassigned(self) -> None:
        tasks = [
            {"id": 1, "profile_no": 7},
            {"id": 2, "profile_no": 7},
            {"id": 3, "profile_no": 9},
            {"id": 4, "profile_no": 0},
        ]
        by_window, unassigned = group_tasks_by_window(tasks)
        self.assertEqual(by_window, {7: [1, 2], 9: [3]})
        self.assertEqual(unassigned, [4])


class SendGuardTests(unittest.TestCase):
    def _workflow(self):
        return Workflow(db=None, settings=Settings())

    def _task(self, **overrides) -> dict:
        task = {
            "id": 1,
            "profile_no": 7,
            "recipient_email": "alex@example.com",
            "subject": "Hello",
            "body": "Hi",
            "status": "generated",
        }
        task.update(overrides)
        return task

    def test_invalid_email_blocks_send(self) -> None:
        workflow = self._workflow()
        with mock.patch.object(workflow, "_task", return_value=self._task(recipient_email="not-an-email")):
            with self.assertRaisesRegex(ValueError, "格式错误"):
                workflow._sendable_task(1)

    def test_empty_content_blocks_send(self) -> None:
        workflow = self._workflow()
        with mock.patch.object(workflow, "_task", return_value=self._task(subject="")):
            with self.assertRaisesRegex(ValueError, "生成邮件预览"):
                workflow._sendable_task(1)

    def test_already_sent_blocks_duplicate(self) -> None:
        workflow = self._workflow()
        with mock.patch.object(workflow, "_task", return_value=self._task(status="sent")):
            with self.assertRaisesRegex(ValueError, "重复发送"):
                workflow._sendable_task(1)

    def test_failure_records_stage_and_failed_status(self) -> None:
        class FakeDatabase:
            def __init__(self):
                self.updated = {}
                self.rows = {
                    1: {
                        "id": 1,
                        "profile_no": 7,
                        "recipient_email": "alex@example.com",
                        "subject": "Hello",
                        "body": "Hi",
                        "status": "generated",
                        "attempts": 0,
                        "last_error": "",
                    }
                }

            def get_task(self, task_id):
                return self.rows[task_id]

            def update_task(self, task_id, **values):
                self.updated = values

        with tempfile.TemporaryDirectory() as temp:
            with mock.patch(
                "ophelia_assistant.config.app_data_dir",
                return_value=Path(temp),
            ):
                database = FakeDatabase()
                workflow = Workflow(db=database, settings=Settings())
                workflow._record_failure(1, RuntimeError("连接失败"), "connect")
                self.assertEqual(database.updated["status"], "failed")
                self.assertEqual(database.updated["failure_stage"], "connect")
                self.assertEqual(database.updated["attempts"], 1)


class StatsAndMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self._patch = mock.patch(
            "ophelia_assistant.database.app_data_dir",
            return_value=Path(self._temp.name),
        )
        self._patch.start()
        self.db = Database()

    def tearDown(self) -> None:
        self._patch.stop()
        self._temp.cleanup()

    def test_legacy_statuses_migrate(self) -> None:
        task_id = self.db.add_local_task("Alex", "Seattle", "alex@example.com")
        self.db.update_task(task_id, status="ready")
        migrated = Database()
        self.assertEqual(migrated.get_task(task_id)["status"], "generated")

    def test_stats_are_mutually_exclusive_and_persisted(self) -> None:
        campaign_id = self.db.create_campaign("统计")
        pending = self.db.add_local_task("A", "Seattle", "a@example.com", campaign_id=campaign_id)
        generated = self.db.add_local_task("B", "Seattle", "b@example.com", campaign_id=campaign_id)
        sent = self.db.add_local_task("C", "Seattle", "c@example.com", campaign_id=campaign_id)
        failed = self.db.add_local_task("D", "Seattle", "d@example.com", campaign_id=campaign_id)
        self.db.update_task(pending, status="pending")
        self.db.update_task(generated, status="generated")
        self.db.update_task(sent, status="sent")
        self.db.update_task(failed, status="failed")
        stats = self.db.stats()
        self.assertEqual(stats["total"], 4)
        self.assertEqual(stats["pending"], 1)
        self.assertEqual(stats["generated"], 1)
        self.assertEqual(stats["sent"], 1)
        self.assertEqual(stats["failed"], 1)
        self.assertEqual(
            stats["pending"] + stats["generated"] + stats["processing"]
            + stats["sent"] + stats["failed"] + stats["other"],
            stats["total"],
        )


if __name__ == "__main__":
    unittest.main()
