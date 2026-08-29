import unittest
import sqlite3
import threading

try:
    import playwright  # noqa: F401
except ImportError:
    raise unittest.SkipTest("playwright not installed")

from ophelia_assistant.config import Settings, resolve_task_windows_balanced
from ophelia_assistant.workflow import Workflow


class WorkflowTemplateBindingTests(unittest.TestCase):
    def _task(self, profile_no: int = 0) -> dict:
        return {
            "id": 1,
            "profile_no": profile_no,
            "name_override": "",
            "first_name": "Alex",
            "last_name": "Walker",
            "location": "Seattle",
            "location_override": "",
            "custom_variables": "{}",
            "subject": "old subject",
            "body": "old body",
        }

    def test_window_bound_template_wins_over_global(self) -> None:
        settings = Settings()
        settings.saved_templates = [
            {
                "name": "话术A",
                "subject_template": "A {first_name}",
                "body_template": "Hello {sender_name}",
                "sender_name": "窗口发件人",
                "signature": "A 签名",
                "custom_variables": {},
            }
        ]
        settings.window_bindings = {
            "7": {"template_name": "话术A", "sender_name": "窗口发件人"}
        }
        settings.window_sequence = [7]
        workflow = Workflow(db=None, settings=settings)
        subject, body = workflow._render_email_for_task(
            self._task(profile_no=7), "Alex", "Seattle", {}
        )
        self.assertEqual(subject, "A Alex")
        self.assertIn("窗口发件人", body)
        self.assertIn("A 签名", body)

    def test_global_template_used_without_binding(self) -> None:
        settings = Settings()
        settings.subject_template = "G {first_name}"
        settings.body_template = "G {location}"
        workflow = Workflow(db=None, settings=settings)
        subject, body = workflow._render_email_for_task(
            self._task(), "Alex", "Seattle", {}
        )
        self.assertEqual(subject, "G Alex")
        self.assertIn("Seattle", body)

    def test_task_sender_override_wins_over_window_binding(self) -> None:
        settings = Settings()
        settings.saved_templates = [
            {
                "name": "话术A",
                "subject_template": "A {first_name}",
                "body_template": "Hello {sender_name}",
                "sender_name": "模板发件人",
                "signature": "",
                "custom_variables": {},
            }
        ]
        settings.window_bindings = {
            "7": {"template_name": "话术A", "sender_name": "窗口发件人"}
        }
        settings.window_sequence = [7]
        workflow = Workflow(db=None, settings=settings)
        task = self._task(profile_no=7)
        task["sender_name_override"] = "任务发件人"
        _subject, body = workflow._render_email_for_task(
            task, "Alex", "Seattle", {}
        )
        self.assertIn("任务发件人", body)
        self.assertNotIn("窗口发件人", body)

    def test_render_works_with_sqlite_row(self) -> None:
        settings = Settings()
        settings.subject_template = "G {first_name}"
        settings.body_template = "Hello {sender_name}"
        workflow = Workflow(db=None, settings=settings)
        source = self._task(profile_no=7)
        source["sender_name_override"] = "行发件人"
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        columns = list(source.keys())
        conn.execute(
            "CREATE TABLE tasks (" + ",".join(f'"{col}" TEXT' for col in columns) + ")"
        )
        conn.execute(
            "INSERT INTO tasks (" + ",".join(f'"{col}"' for col in columns) + ") VALUES ("
            + ",".join("?" for _ in columns) + ")",
            [source[col] for col in columns],
        )
        row = conn.execute("SELECT * FROM tasks").fetchone()
        conn.close()
        _subject, body = workflow._render_email_for_task(row, "Alex", "Seattle", {})
        self.assertIn("行发件人", body)

    def test_generate_local_does_not_store_cancel_event_as_sender(self) -> None:
        task = self._task()

        class FakeDatabase:
            def __init__(self):
                self.values = None

            def get_task(self, _task_id):
                return task

            def update_task(self, _task_id, **values):
                self.values = values

        database = FakeDatabase()
        workflow = Workflow(db=database, settings=Settings())
        workflow.generate_local(1, threading.Event())
        self.assertEqual(database.values["sender_name_override"], "")
        self.assertEqual(database.values["status"], "generated")


class BalancedWindowAssignmentTests(unittest.TestCase):
    def test_round_robin_when_all_windows_empty(self) -> None:
        result = resolve_task_windows_balanced(
            [0, 0, 0, 0], [198, 199, 200], {}
        )
        self.assertEqual(result, [198, 199, 200, 198])

    def test_skips_window_with_pending_emails(self) -> None:
        result = resolve_task_windows_balanced(
            [0, 0, 0], [198, 199, 200], {199: 2}
        )
        self.assertEqual(result, [198, 200, 198])
        self.assertNotIn(199, result[:2])

    def test_keeps_existing_profiles(self) -> None:
        result = resolve_task_windows_balanced(
            [199, 0], [198, 199, 200], {}
        )
        self.assertEqual(result, [199, 198])

    def test_empty_sequence_returns_none(self) -> None:
        result = resolve_task_windows_balanced([0, 0], [], {})
        self.assertEqual(result, [None, None])


if __name__ == "__main__":
    unittest.main()
