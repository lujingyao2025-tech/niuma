import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:
    import playwright  # noqa: F401
    import requests  # noqa: F401
except ImportError:
    raise unittest.SkipTest("playwright/requests not installed")

try:
    import PySide6  # noqa: F401
    PYSIDE_AVAILABLE = True
except ImportError:
    PYSIDE_AVAILABLE = False

from ophelia_assistant.batch import assign_windows, group_tasks_by_window
from ophelia_assistant.config import MAX_CONTACT_ROWS, Settings
from ophelia_assistant.database import Database
from ophelia_assistant.workflow import Workflow


class LimitTests(unittest.TestCase):
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

    def test_import_and_persist_300(self) -> None:
        contacts = [
            (f"User {index}", "Seattle", f"user{index}@example.com")
            for index in range(300)
        ]
        task_ids = self.db.add_local_tasks(contacts, [None] * 300)
        self.assertEqual(len(task_ids), 300)
        self.assertGreaterEqual(MAX_CONTACT_ROWS, 300)
        reopened = Database()
        self.assertEqual(len(reopened.list_tasks()), 300)


class WindowAssignmentTests(unittest.TestCase):
    def test_descending_one_window_per_email(self) -> None:
        tasks = [{"id": i, "profile_no": 0, "profile_locked": 0} for i in range(6)]
        result = assign_windows(tasks, [30, 28, 25, 17])
        assigned = [item for item in result if item["profile_no"] is not None]
        self.assertEqual(
            [item["profile_no"] for item in assigned],
            [30, 28, 25, 17],
        )
        self.assertEqual(sum(1 for item in result if item["waiting"]), 2)

    def test_manual_locked_wins_and_not_overwritten(self) -> None:
        tasks = [
            {"id": 1, "profile_no": 17, "profile_locked": 1},
            {"id": 2, "profile_no": 0, "profile_locked": 0},
        ]
        result = assign_windows(tasks, [30, 17])
        by_id = {item["task_id"]: item for item in result}
        self.assertEqual(by_id[1]["profile_no"], 17)
        self.assertEqual(by_id[2]["profile_no"], 30)

    def test_manual_conflict_and_not_open(self) -> None:
        tasks = [
            {"id": 1, "profile_no": 25, "profile_locked": 0},
            {"id": 2, "profile_no": 25, "profile_locked": 0},
            {"id": 3, "profile_no": 99, "profile_locked": 1},
        ]
        result = assign_windows(tasks, [25])
        by_id = {item["task_id"]: item for item in result}
        self.assertEqual(by_id[1]["profile_no"], 25)
        self.assertIsNone(by_id[2]["profile_no"])
        self.assertEqual(by_id[2]["conflict"], "window_conflict")
        self.assertIsNone(by_id[3]["profile_no"])
        self.assertEqual(by_id[3]["conflict"], "window_not_open")

    def test_sqlite_rows_never_call_get(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE t (id INTEGER, profile_no INTEGER)")
        conn.execute("INSERT INTO t VALUES (1, 7)")
        conn.execute("INSERT INTO t VALUES (2, 0)")
        rows = conn.execute("SELECT * FROM t").fetchall()
        by_window, unassigned = group_tasks_by_window(rows)
        self.assertEqual(by_window, {7: [1]})
        self.assertEqual(unassigned, [2])

    def test_auto_assigned_task_is_rematched_each_run(self) -> None:
        tasks = [
            {
                "id": 1,
                "profile_no": 30,
                "profile_locked": 0,
                "window_assignment_type": "auto",
            }
        ]
        result = assign_windows(tasks, [29])
        self.assertEqual(result[0]["profile_no"], 29)
        self.assertEqual(result[0]["type"], "auto")

    def test_manual_pre_reserve_then_auto_in_original_order(self) -> None:
        tasks = [
            {"id": 1, "profile_no": 0, "profile_locked": 0},
            {"id": 2, "profile_no": 28, "profile_locked": 0},
            {"id": 3, "profile_no": 0, "profile_locked": 0},
        ]
        result = assign_windows(tasks, [30, 28])
        self.assertEqual([item["task_id"] for item in result], [1, 2, 3])
        self.assertEqual(result[1]["profile_no"], 28)
        self.assertEqual(result[0]["profile_no"], 30)
        self.assertIsNone(result[2]["profile_no"])

    def test_saved_window_order_is_preserved(self) -> None:
        tasks = [
            {"id": 1, "profile_no": 0, "profile_locked": 0},
            {"id": 2, "profile_no": 0, "profile_locked": 0},
            {"id": 3, "profile_no": 0, "profile_locked": 0},
        ]
        result = assign_windows(tasks, [197, 199, 196, 198])
        self.assertEqual(
            [item["profile_no"] for item in result],
            [197, 199, 196],
        )

    def test_thirty_windows_all_assigned(self) -> None:
        tasks = [
            {"id": index, "profile_no": 0, "profile_locked": 0}
            for index in range(1, 31)
        ]
        result = assign_windows(tasks, list(range(30, 0, -1)))
        assigned = [item["profile_no"] for item in result]
        self.assertEqual(len(assigned), 30)
        self.assertEqual(len(set(assigned)), 30)
        self.assertEqual(assigned, list(range(30, 0, -1)))


class SenderPriorityTests(unittest.TestCase):
    def _workflow(self, **settings_overrides):
        settings = Settings()
        for key, value in settings_overrides.items():
            setattr(settings, key, value)
        return Workflow(db=None, settings=settings)

    def _task(self, profile=0, sender="", resolved="", locked_binding=None):
        binding = {}
        if locked_binding is not None:
            binding = {
                str(profile): {
                    "sender_name": locked_binding,
                    "locked": True,
                }
            }
        task = {
            "id": 1,
            "profile_no": profile,
            "sender_name_override": sender,
            "resolved_sender_name": resolved,
        }
        workflow = self._workflow(
            window_bindings=binding,
            window_sequence=[profile] if profile > 0 else [],
        )
        workflow.settings.saved_templates = [
            {
                "name": "T",
                "sender_name": "Template Sender",
            }
        ]
        return workflow, task

    def test_task_wins(self) -> None:
        workflow, task = self._task(profile=7, sender="Task Sender", locked_binding="Window Sender")
        name, source = workflow._resolve_sender(task)
        self.assertEqual(name, "Task Sender")
        self.assertEqual(source, "task")

    def test_locked_window_beats_template(self) -> None:
        workflow, task = self._task(profile=7, locked_binding="Window Sender")
        workflow.settings.window_bindings["7"]["template_name"] = "T"
        name, source = workflow._resolve_sender(task)
        self.assertEqual(name, "Window Sender")
        self.assertEqual(source, "window")

    def test_template_beats_default(self) -> None:
        workflow, task = self._task(profile=0)
        workflow.settings.saved_templates = [{"name": "T", "sender_name": "Template Sender"}]
        workflow.settings.active_template_name = "T"
        name, source = workflow._resolve_sender(task)
        self.assertEqual(name, "Template Sender")
        self.assertEqual(source, "template")

    def test_default_when_template_empty(self) -> None:
        workflow, task = self._task(profile=0)
        workflow.settings.saved_templates = [{"name": "T", "sender_name": ""}]
        workflow.settings.sender_name = "Anna Lee"
        name, source = workflow._resolve_sender(task)
        self.assertEqual(name, "Anna Lee")
        self.assertEqual(source, "default")


class SentMethodStatsTests(unittest.TestCase):
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

    def test_confirmed_and_manual_sent_are_separate(self) -> None:
        confirmed = self.db.add_local_task("A", "Seattle", "a@example.com")
        manual = self.db.add_local_task("B", "Seattle", "b@example.com")
        self.db.update_task(
            confirmed,
            status="sent",
            sent_method="confirmed",
            sent_at="2026-08-29T00:00:00+00:00",
        )
        self.db.mark_sent([manual])
        stats = self.db.stats()
        self.assertEqual(stats["sent"], 1)
        self.assertEqual(stats["sent_manual"], 1)


class RunningWindowTests(unittest.TestCase):
    @staticmethod
    def _open_port() -> int:
        import socket
        import threading

        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(("127.0.0.1", 0))
        port = server.getsockname()[1]

        def serve():
            server.listen(1)
            try:
                conn, _ = server.accept()
                conn.close()
            except OSError:
                pass
            server.close()

        threading.Thread(target=serve, daemon=True).start()
        return port

    def test_morelogin_filters_by_running_status(self) -> None:
        from ophelia_assistant.morelogin import MoreLoginClient

        payload = {
            "code": 0,
            "data": {
                "list": [
                    {"uniqueId": 196, "name": "A", "status": "running", "cdpUrl": "ws://127.0.0.1:1"},
                    {"uniqueId": 197, "name": "B", "status": "stopped", "cdpUrl": "ws://127.0.0.1:2"},
                    {"uniqueId": 198, "name": "C", "running": 1, "wsEndpoint": "ws://127.0.0.1:3"},
                ]
            },
        }
        with mock.patch(
            "ophelia_assistant.morelogin.requests.get",
            return_value=mock.Mock(
                json=lambda: payload,
                ok=True,
                status_code=200,
            ),
        ):
            windows = MoreLoginClient().list_running_windows(
                verify_connection=False
            )
        self.assertEqual([number for number, _ in windows], ["196", "198"])

    def test_adspower_filters_running(self) -> None:
        from ophelia_assistant.morelogin import AdsPowerClient

        client = AdsPowerClient("http://127.0.0.1", "")
        client._profiles = [
            {"serial_number": 1, "name": "A", "status": "Active", "ws": "ws://127.0.0.1:1"},
            {"serial_number": 2, "name": "B", "status": "offline", "ws": "ws://127.0.0.1:2"},
        ]
        windows = client.list_running_windows(verify_connection=False)
        self.assertEqual([number for number, _ in windows], ["1"])

    def test_bitbrowser_filters_running(self) -> None:
        from ophelia_assistant.morelogin import BitBrowserClient

        client = BitBrowserClient("http://127.0.0.1")
        client._profiles = [
            {"seq": 196, "name": "A", "status": "online", "debug_port": 9222},
            {"seq": 197, "name": "B", "status": "offline", "debug_port": 9223},
        ]
        windows = client.list_running_windows(verify_connection=False)
        self.assertEqual([number for number, _ in windows], ["196"])

    def test_bitbrowser_pure_port_verified_with_real_connection(self) -> None:
        from ophelia_assistant.morelogin import BitBrowserClient

        open_port = self._open_port()
        client = BitBrowserClient("http://127.0.0.1")
        client._profiles = [
            {"seq": 196, "name": "A", "status": "online", "debug_port": open_port},
            {"seq": 197, "name": "B", "status": "online", "debug_port": 1},
        ]
        windows = client.list_running_windows(verify_connection=True)
        self.assertEqual([number for number, _ in windows], ["196"])

    def test_morelogin_verified_connection(self) -> None:
        from ophelia_assistant.morelogin import MoreLoginClient

        open_port = self._open_port()
        payload = {
            "code": 0,
            "data": {
                "list": [
                    {
                        "uniqueId": 196,
                        "name": "A",
                        "status": "running",
                        "cdpUrl": f"ws://127.0.0.1:{open_port}",
                    },
                    {
                        "uniqueId": 197,
                        "name": "B",
                        "status": "running",
                        "cdpUrl": "ws://127.0.0.1:1",
                    },
                ]
            },
        }
        with mock.patch(
            "ophelia_assistant.morelogin.requests.get",
            return_value=mock.Mock(
                json=lambda: payload,
                ok=True,
                status_code=200,
            ),
        ):
            windows = MoreLoginClient().list_running_windows(
                verify_connection=True
            )
        self.assertEqual([number for number, _ in windows], ["196"])

    def test_bitbrowser_status_one_without_cdp_is_recognized(self) -> None:
        from ophelia_assistant.morelogin import BitBrowserClient

        client = BitBrowserClient("http://127.0.0.1")
        client._profiles = [
            {"seq": 196, "name": "A", "status": 1},
            {"seq": 197, "name": "B", "status": 0},
            {"seq": 198, "name": "C", "status": 2},
        ]
        windows = client.list_running_windows(verify_connection=True)
        self.assertEqual([number for number, _ in windows], ["196"])


class ConfirmActionsTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        self._temp = tempfile.TemporaryDirectory()
        os.environ["NIUMA_MAIL_TRIAL_DIR"] = self._temp.name
        os.environ["NIUMA_MAIL_DISABLE_REGISTRY"] = "1"
        self._patch = mock.patch(
            "ophelia_assistant.database.app_data_dir",
            return_value=Path(self._temp.name),
        )
        self._patch.start()
        self._patch_config = mock.patch(
            "ophelia_assistant.config.app_data_dir",
            return_value=Path(self._temp.name),
        )
        self._patch_config.start()
        self.db = Database()

    def tearDown(self) -> None:
        self._patch_config.stop()
        self._patch.stop()
        os.environ.pop("NIUMA_MAIL_TRIAL_DIR", None)
        os.environ.pop("NIUMA_MAIL_DISABLE_REGISTRY", None)
        self._temp.cleanup()

    def test_generated_task_cannot_be_confirmed_sent(self) -> None:
        from PySide6.QtWidgets import QApplication, QMessageBox

        from ophelia_assistant.studio.main_window import MainWindow

        app = QApplication.instance() or QApplication([])
        task_id = self.db.add_local_task("A", "Seattle", "a@example.com")
        self.db.update_task(task_id, status="generated")
        window = MainWindow()
        window.show()
        app.processEvents()
        with mock.patch.object(
            QMessageBox, "question", return_value=QMessageBox.Yes
        ), mock.patch.object(
            QMessageBox, "information", return_value=QMessageBox.Ok
        ):
            window.confirm_tasks_sent()
        app.processEvents()
        self.assertEqual(self.db.get_task(task_id)["status"], "generated")
        window._closing = True
        window.close()


@unittest.skipUnless(PYSIDE_AVAILABLE, "PySide6 not installed")
class AsyncCompatibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        self._temp = tempfile.TemporaryDirectory()
        os.environ["NIUMA_MAIL_TRIAL_DIR"] = self._temp.name
        os.environ["NIUMA_MAIL_DISABLE_REGISTRY"] = "1"
        self._patch_db = mock.patch(
            "ophelia_assistant.database.app_data_dir",
            return_value=Path(self._temp.name),
        )
        self._patch_cfg = mock.patch(
            "ophelia_assistant.config.app_data_dir",
            return_value=Path(self._temp.name),
        )
        self._patch_db.start()
        self._patch_cfg.start()
        self.db = Database()

    def tearDown(self) -> None:
        self._patch_cfg.stop()
        self._patch_db.stop()
        os.environ.pop("NIUMA_MAIL_TRIAL_DIR", None)
        os.environ.pop("NIUMA_MAIL_DISABLE_REGISTRY", None)
        self._temp.cleanup()

    def _window(self):
        from PySide6.QtWidgets import QApplication

        from ophelia_assistant.studio.main_window import MainWindow

        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        window.show()
        app.processEvents()
        return window, app

    def test_run_async_supports_two_arg_function(self) -> None:
        import time

        window, app = self._window()
        results: list = []

        def on_done(result):
            results.append(result)

        window.run_async(
            lambda cancel_event, progress: "ok",
            on_done=on_done,
        )
        deadline = time.time() + 5
        while not results and time.time() < deadline:
            app.processEvents()
            time.sleep(0.05)
        self.assertEqual(results, ["ok"])
        window._closing = True
        window.close()

    def test_auto_fill_windows_uses_running_windows(self) -> None:
        import time

        window, app = self._window()
        calls: list = []

        class FakeProvider:
            def list_running_windows(self, verify_connection=True):
                calls.append(verify_connection)
                return [("196", "A")]

        with mock.patch(
            "ophelia_assistant.studio.main_window.create_browser_provider",
            return_value=FakeProvider(),
        ):
            window.auto_fill_windows()
            deadline = time.time() + 5
            while window.settings.window_sequence != [196] and time.time() < deadline:
                app.processEvents()
                time.sleep(0.05)
        self.assertEqual(window.settings.window_sequence, [196])
        self.assertEqual(calls, [True])
        window._closing = True
        window.close()

    def test_check_update_async_signature(self) -> None:
        import time

        from PySide6.QtWidgets import QMessageBox

        window, app = self._window()
        window.settings.update_url = "http://127.0.0.1:1/update.json"
        window.settings.save()
        with mock.patch(
            "requests.get",
            side_effect=RuntimeError("network"),
        ), mock.patch.object(
            QMessageBox, "critical", return_value=QMessageBox.Ok
        ):
            window.check_update()
            deadline = time.time() + 5
            while window._busy and time.time() < deadline:
                app.processEvents()
                time.sleep(0.05)
        self.assertFalse(window._busy)
        window._closing = True
        window.close()


class CrashRecoveryTests(unittest.TestCase):
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

    def test_sending_recovers_to_needs_review(self) -> None:
        task_id = self.db.add_local_task("A", "Seattle", "a@example.com")
        self.db.update_task(
            task_id,
            status="sending",
            send_clicked_at="2026-08-29T00:00:00+00:00",
        )
        reopened = Database()
        row = reopened.get_task(task_id)
        self.assertEqual(row["status"], "needs_review")
        self.assertEqual(row["needs_manual_review"], 1)

    def test_sending_without_click_recovers_to_generated(self) -> None:
        task_id = self.db.add_local_task("B", "Seattle", "b@example.com")
        self.db.update_task(
            task_id,
            status="sending",
            send_attempt_started_at="2026-08-29T00:00:00+00:00",
        )
        reopened = Database()
        row = reopened.get_task(task_id)
        self.assertEqual(row["status"], "generated")

    def test_sender_re_resolve_regenerates_body(self) -> None:
        settings = Settings()
        settings.active_template_name = "T"
        settings.saved_templates = [
            {
                "name": "T",
                "subject_template": "Hello {first_name}",
                "body_template": "Hi {first_name}, from {sender_name}",
                "sender_name": "Template Sender",
                "signature": "",
                "custom_variables": {},
            }
        ]
        settings.window_bindings = {
            "7": {
                "template_name": "T",
                "sender_name": "Window Sender",
                "locked": True,
            }
        }
        settings.window_sequence = [7]
        workflow = Workflow(db=self.db, settings=settings)
        task_id = self.db.add_local_task("Alex", "Seattle", "alex@example.com")
        workflow.generate_local(task_id)
        row = self.db.get_task(task_id)
        self.assertEqual(row["resolved_sender_name"], "Template Sender")
        self.assertIn("Template Sender", row["body"])
        self.db.update_task(task_id, profile_no=7)
        workflow.generate_local(task_id)
        row = self.db.get_task(task_id)
        self.assertEqual(row["resolved_sender_name"], "Window Sender")
        self.assertEqual(row["sender_name_source"], "window")
        self.assertIn("Window Sender", row["body"])


class ToastDedupeTests(unittest.TestCase):
    class FakeLocator:
        def __init__(self, holder):
            self.holder = holder

        def all_inner_texts(self):
            return list(self.holder.get("texts", []))

        def evaluate_all(self, _expression):
            return [dict(node) for node in self.holder.get("nodes", [])]

    class FakePage:
        def __init__(self, holder):
            self.holder = holder

        def locator(self, _selector):
            return ToastDedupeTests.FakeLocator(self.holder)

    def test_same_text_new_toast_is_not_filtered_after_old_clears(self) -> None:
        import threading
        import time

        from ophelia_assistant import browser as browser_module

        holder = {"texts": ["Message sent"]}
        page = self.FakePage(holder)
        with mock.patch.object(
            browser_module, "_compose_page", return_value=page
        ):
            def clear_after():
                time.sleep(0.3)
                holder["texts"] = []

            thread = threading.Thread(target=clear_after)
            thread.start()
            browser_module.wait_for_gmail_alerts_clear(
                mock.Mock(), baseline=("Message sent",), timeout_ms=3000
            )
            thread.join()
            # Old toast gone; a fresh same-text toast must be accepted later.
            holder["texts"] = ["Message sent"]
            self.assertEqual(page.locator(None).all_inner_texts(), ["Message sent"])

    def test_old_alert_never_clears_blocks_send(self) -> None:
        from ophelia_assistant import browser as browser_module
        from ophelia_assistant.browser import BrowserAutomationError

        holder = {"texts": ["Message sent"]}
        page = self.FakePage(holder)
        with mock.patch.object(
            browser_module, "_compose_page", return_value=page
        ):
            with self.assertRaises(BrowserAutomationError):
                browser_module.wait_for_gmail_alerts_clear(
                    mock.Mock(), baseline=("Message sent",), timeout_ms=200
                )

    def test_unrelated_alert_does_not_block_send(self) -> None:
        from ophelia_assistant import browser as browser_module

        holder = {"texts": ["Connection lost. Retrying."]}
        page = self.FakePage(holder)
        with mock.patch.object(
            browser_module, "_compose_page", return_value=page
        ):
            browser_module.wait_for_gmail_alerts_clear(
                mock.Mock(),
                baseline=("Connection lost. Retrying.",),
                timeout_ms=500,
            )

    def test_new_same_text_toast_node_accepted_with_old_node_lingering(
        self,
    ) -> None:
        import threading
        import time

        from ophelia_assistant import browser as browser_module

        holder = {
            "nodes": [
                {"id": "node:old", "text": "Message sent"},
            ]
        }
        page = self.FakePage(holder)
        with mock.patch.object(
            browser_module, "_compose_page", return_value=page
        ):
            def show_new():
                time.sleep(0.2)
                holder["nodes"] = [
                    {"id": "node:old", "text": "Message sent"},
                    {"id": "node:new", "text": "Message sent"},
                ]

            thread = threading.Thread(target=show_new)
            thread.start()
            browser_module.wait_for_gmail_send(
                mock.Mock(),
                baseline=("node:old",),
                timeout_ms=3000,
            )
            thread.join()


class ComposeBodyFillTests(unittest.TestCase):
    class _FakeBody:
        def __init__(self, holder):
            self.holder = holder

        def fill(self, value, timeout=None):
            if self.holder.get("fill_raises"):
                from playwright.sync_api import (
                    TimeoutError as PlaywrightTimeoutError,
                )

                raise PlaywrightTimeoutError("actionability stall")
            self.holder["text"] = value

        def evaluate(self, expression, arg=None, timeout=None):
            if "execCommand" in expression:
                self.holder["text"] = arg or ""
                return True
            if "innerText" in expression or "textContent" in expression:
                return self.holder.get("text", "")
            return None

    class _FakeKeyboard:
        def __init__(self, holder):
            self.holder = holder

        def press(self, key):
            self.holder.setdefault("keys", []).append(key)

        def insert_text(self, value):
            self.holder["text"] = value

    class _FakePage:
        def __init__(self, holder):
            self.holder = holder
            self.keyboard = ComposeBodyFillTests._FakeKeyboard(holder)

    def test_body_fill_uses_fast_fill_path(self) -> None:
        from ophelia_assistant import browser as browser_module

        holder = {"text": "", "fill_raises": False}
        page = self._FakePage(holder)
        body = self._FakeBody(holder)
        browser_module._replace_compose_body(
            page,
            body,
            "Hello\nWorld",
            None,
        )
        self.assertEqual(holder["text"], "Hello\nWorld")
        self.assertEqual(holder.get("keys", []), [])

    def test_body_fill_falls_back_to_keyboard_with_bounded_timeout(self) -> None:
        from ophelia_assistant import browser as browser_module

        holder = {"text": "", "fill_raises": True}
        page = self._FakePage(holder)
        body = self._FakeBody(holder)
        browser_module._replace_compose_body(
            page,
            body,
            "Hello\nWorld",
            None,
        )
        self.assertEqual(holder["text"], "Hello\nWorld")
        self.assertIn("Control+A", holder["keys"])
        self.assertIn("Backspace", holder["keys"])


class SendBlockerTests(unittest.TestCase):
    def test_send_clicked_blocks_automatic_retry(self) -> None:
        workflow = Workflow(db=None, settings=Settings())
        task = {
            "id": 1,
            "profile_no": 7,
            "recipient_email": "alex@example.com",
            "subject": "Hello",
            "body": "Hi",
            "status": "needs_review",
            "send_clicked_at": "2026-08-29T00:00:00+00:00",
        }
        with mock.patch.object(workflow, "_task", return_value=task):
            with self.assertRaisesRegex(ValueError, "结果不明确"):
                workflow._sendable_task(1)

    def test_mark_needs_review_is_not_failed(self) -> None:
        class FakeDatabase:
            def __init__(self):
                self.updated = {}

            def get_task(self, _task_id):
                return None

            def update_task(self, _task_id, **values):
                self.updated = values

        database = FakeDatabase()
        workflow = Workflow(db=database, settings=Settings())
        workflow._mark_needs_review(
            1, RuntimeError("点击后连接中断"), "click_send"
        )
        self.assertEqual(database.updated["status"], "needs_review")
        self.assertEqual(database.updated["needs_manual_review"], 1)

    def test_send_button_not_found_allows_retry(self) -> None:
        workflow = Workflow(db=None, settings=Settings())
        task = {
            "id": 1,
            "profile_no": 7,
            "recipient_email": "alex@example.com",
            "subject": "Hello",
            "body": "Hi",
            "status": "failed",
            "send_attempt_started_at": "2026-08-29T00:00:00+00:00",
            "send_clicked_at": "",
        }
        with mock.patch.object(workflow, "_task", return_value=task):
            workflow._sendable_task(1)


class RenderContextHashTests(unittest.TestCase):
    def test_template_change_triggers_new_hash_even_with_same_sender(self) -> None:
        settings = Settings()
        settings.active_template_name = "T1"
        settings.saved_templates = [
            {
                "name": "T1",
                "subject_template": "Hello {first_name}",
                "body_template": "Body A",
                "sender_name": "Anna Lee",
                "signature": "",
                "custom_variables": {},
            },
            {
                "name": "T2",
                "subject_template": "Hi {first_name}",
                "body_template": "Body B",
                "sender_name": "Anna Lee",
                "signature": "",
                "custom_variables": {},
            },
        ]
        workflow = Workflow(db=None, settings=settings)
        task = {
            "id": 1,
            "profile_no": 0,
            "sender_name_override": "",
        }
        hash_one = workflow._render_context_hash(task)
        settings.active_template_name = "T2"
        hash_two = workflow._render_context_hash(task)
        self.assertNotEqual(hash_one, hash_two)


@unittest.skipUnless(PYSIDE_AVAILABLE, "PySide6 not installed")
class CloseFlowTests(AsyncCompatibilityTests):
    class _Event:
        def __init__(self):
            self.ignored = False
            self.accepted = False

        def ignore(self):
            self.ignored = True

        def accept(self):
            self.accepted = True

    def test_cancel_choice_keeps_everything(self) -> None:
        window, app = self._window()
        event = self._Event()
        with mock.patch.object(
            window, "_close_choice", return_value="cancel"
        ):
            window.closeEvent(event)
        app.processEvents()
        self.assertTrue(event.ignored)
        self.assertFalse(window._closing)
        window._closing = True
        window.close()

    def test_background_choice_minimizes(self) -> None:
        window, app = self._window()
        event = self._Event()
        with mock.patch.object(
            window, "_close_choice", return_value="background"
        ), mock.patch.object(window, "showMinimized") as minimized:
            window.closeEvent(event)
        app.processEvents()
        minimized.assert_called_once()
        self.assertTrue(event.ignored)
        window._closing = True
        window.close()

    def test_exit_choice_finishes_close_after_cancel(self) -> None:
        window, app = self._window()
        event = self._Event()
        with mock.patch.object(
            window, "_close_choice", return_value="exit"
        ), mock.patch.object(window, "_finish_close") as finish:
            window.closeEvent(event)
        app.processEvents()
        finish.assert_called_once()
        self.assertTrue(window._closing)
        window._closing = True
        window.close()


@unittest.skipUnless(PYSIDE_AVAILABLE, "PySide6 not installed")
class WindowBindingPanelTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        self._temp = tempfile.TemporaryDirectory()
        os.environ["NIUMA_MAIL_TRIAL_DIR"] = self._temp.name
        os.environ["NIUMA_MAIL_DISABLE_REGISTRY"] = "1"
        self._patch_db = mock.patch(
            "ophelia_assistant.database.app_data_dir",
            return_value=Path(self._temp.name),
        )
        self._patch_cfg = mock.patch(
            "ophelia_assistant.config.app_data_dir",
            return_value=Path(self._temp.name),
        )
        self._patch_db.start()
        self._patch_cfg.start()

    def tearDown(self) -> None:
        self._patch_cfg.stop()
        self._patch_db.stop()
        os.environ.pop("NIUMA_MAIL_TRIAL_DIR", None)
        os.environ.pop("NIUMA_MAIL_DISABLE_REGISTRY", None)
        self._temp.cleanup()

    def test_bindings_generated_for_saved_sequence_and_persist(self) -> None:
        from PySide6.QtWidgets import QApplication, QCheckBox, QLineEdit

        from ophelia_assistant.config import Settings
        from ophelia_assistant.studio.main_window import MainWindow

        settings = Settings()
        settings.window_sequence = [199, 198, 197, 196]
        settings.window_bindings = {}
        settings.save()
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        window.show()
        app.processEvents()
        panel = window.pages[0].window_panel
        self.assertEqual(panel.bindings_table.columnCount(), 4)
        self.assertEqual(panel.bindings_table.rowCount(), 4)
        sender = panel.bindings_table.cellWidget(0, 2)
        lock = panel.bindings_table.cellWidget(0, 3)
        self.assertIsInstance(sender, QLineEdit)
        self.assertIsInstance(lock, QCheckBox)
        sender.setText("Anna Lee")
        lock.setChecked(True)
        panel.save_bindings()
        reloaded = Settings.load()
        self.assertEqual(
            reloaded.window_bindings["199"]["sender_name"],
            "Anna Lee",
        )
        self.assertTrue(reloaded.window_bindings["199"]["locked"])
        self.assertEqual(len(reloaded.window_bindings), 4)
        window._closing = True
        window.close()

    def test_bindings_generated_for_twenty_windows(self) -> None:
        from PySide6.QtWidgets import QApplication

        from ophelia_assistant.config import Settings
        from ophelia_assistant.studio.main_window import MainWindow

        settings = Settings()
        settings.window_sequence = list(range(180, 200))
        settings.window_bindings = {}
        settings.save()
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        window.show()
        app.processEvents()
        panel = window.pages[0].window_panel
        self.assertEqual(panel.bindings_table.rowCount(), 20)
        self.assertEqual(panel.bindings_table.columnCount(), 4)
        window._closing = True
        window.close()


if __name__ == "__main__":
    unittest.main()
