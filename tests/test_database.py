import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ophelia_assistant.database import Database


class DatabaseUpdateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self._patch = mock.patch(
            "ophelia_assistant.database.app_data_dir",
            return_value=Path(self._temp.name),
        )
        self._patch.start()
        self.db = Database()
        self.task_id = self.db.add_local_task("Alex", "Seattle", "alex@example.com")

    def tearDown(self) -> None:
        self._patch.stop()
        self._temp.cleanup()

    def test_sender_and_sent_timestamp_are_persisted(self) -> None:
        self.db.update_task(
            self.task_id,
            sender_name_override="Anna",
            status="sent",
            sent_at="2026-08-29T00:00:00+00:00",
        )
        task = self.db.get_task(self.task_id)
        self.assertEqual(task["sender_name_override"], "Anna")
        self.assertEqual(task["status"], "sent")
        self.assertEqual(task["sent_at"], "2026-08-29T00:00:00+00:00")

    def test_unknown_field_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown_field"):
            self.db.update_task(self.task_id, unknown_field="value")

    def test_missing_task_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "任务不存在"):
            self.db.update_task(999999, status="ready")

    def test_reimport_keeps_locked_task_instead_of_creating_duplicate(self) -> None:
        self.db.lock_task_profile(self.task_id, 7)
        imported_id = self.db.add_local_task(
            "Alex Updated", "Portland", "ALEX@example.com"
        )
        tasks = self.db.list_tasks()
        self.assertEqual(imported_id, self.task_id)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["profile_no"], 7)
        self.assertEqual(tasks[0]["profile_locked"], 1)
        self.assertEqual(tasks[0]["name_override"], "Alex Updated")
        self.assertEqual(tasks[0]["location_override"], "Portland")

    def test_replied_task_also_has_sent_timestamp(self) -> None:
        self.db.mark_sent([self.task_id], replied=True)
        task = self.db.get_task(self.task_id)
        self.assertEqual(task["status"], "replied")
        self.assertIsNotNone(task["sent_at"])
        self.assertIsNotNone(task["replied_at"])

    def test_unmark_returns_to_real_previous_stage(self) -> None:
        self.db.update_task(self.task_id, status="generated")
        self.db.mark_sent([self.task_id])
        self.db.unmark_sent([self.task_id])
        self.assertEqual(self.db.get_task(self.task_id)["status"], "generated")

        self.db.update_task(
            self.task_id,
            status="drafted",
            drafted_at="2026-08-29T00:00:00+00:00",
        )
        self.db.mark_sent([self.task_id])
        self.db.unmark_sent([self.task_id])
        self.assertEqual(self.db.get_task(self.task_id)["status"], "drafted")


if __name__ == "__main__":
    unittest.main()
