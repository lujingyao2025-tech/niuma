from __future__ import annotations

import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

try:
    import playwright  # noqa: F401
    import requests  # noqa: F401
except ImportError as exc:
    raise unittest.SkipTest(f"missing dependency: {exc.name}")

from ophelia_assistant.config import Settings
from ophelia_assistant.database import Database
from ophelia_assistant.operation import OperationCancelledError
from ophelia_assistant.timing import StageTimer
from ophelia_assistant.workflow import Workflow


class BatchGenerationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self._patch = mock.patch(
            "ophelia_assistant.database.app_data_dir",
            return_value=Path(self._temp.name),
        )
        self._patch.start()
        self.db = Database()
        self.workflow = Workflow(self.db, Settings())
        self.task_ids = [
            self.db.add_local_task(
                f"User {index}",
                "Seattle",
                f"user{index}@example.com",
            )
            for index in range(5)
        ]

    def tearDown(self) -> None:
        self._patch.stop()
        self._temp.cleanup()

    def test_batch_generation_uses_single_transaction_and_progress_0_to_100(self) -> None:
        calls: list[dict] = []
        original = self.db.update_tasks_batch

        def tracking(updates):
            calls.append(updates)
            return original(updates)

        self.db.update_tasks_batch = tracking
        progress_values: list[int] = []
        result = self.workflow.generate_local_batch(
            self.task_ids,
            progress=lambda value, _text: progress_values.append(value),
        )
        self.assertEqual(result["success"], 5)
        self.assertEqual(result["failed"], 0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(calls[0]), 5)
        self.assertEqual(progress_values[0], 0)
        self.assertEqual(progress_values[-1], 100)
        for task_id in self.task_ids:
            self.assertEqual(self.db.get_task(task_id)["status"], "generated")

    def test_batch_generation_never_calls_single_update_task(self) -> None:
        def forbidden(*_args, **_kwargs):
            raise AssertionError("批量生成不得逐封提交数据库")

        self.db.update_task = forbidden
        result = self.workflow.generate_local_batch(self.task_ids)
        self.assertEqual(result["completed"], 5)

    def test_missing_template_binding_fails_instead_of_silent_fallback(self) -> None:
        self.db.update_task(self.task_ids[0], profile_no=7)
        self.workflow.settings.window_bindings = {
            "7": {"template_name": "已删除模板"}
        }
        self.workflow.settings.window_sequence = [7]
        result = self.workflow.generate_local_batch([self.task_ids[0]])
        self.assertEqual(result["failed"], 1)
        row = self.db.get_task(self.task_ids[0])
        self.assertEqual(row["status"], "failed")
        self.assertIn("模板已删除/失效", row["last_error"])


class BatchBrowserReuseTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self._patch = mock.patch(
            "ophelia_assistant.database.app_data_dir",
            return_value=Path(self._temp.name),
        )
        self._patch.start()
        self.db = Database()
        self.workflow = Workflow(self.db, Settings())

    def tearDown(self) -> None:
        self._patch.stop()
        self._temp.cleanup()

    def _tasks_on(self, *profiles: int) -> list[int]:
        ids: list[int] = []
        for index, profile in enumerate(profiles):
            task_id = self.db.add_local_task(
                f"User {index}",
                "Seattle",
                f"user{index}@example.com",
            )
            self.db.update_task(
                task_id,
                profile_no=profile,
                subject=f"Subject {index}",
                body=f"Body {index}",
                status="generated",
            )
            ids.append(task_id)
        return ids

    def test_same_window_reuses_one_connection_and_runs_serially(self) -> None:
        ids = self._tasks_on(7, 7)
        provider = mock.Mock()
        provider.start_profile.return_value = SimpleNamespace(
            cdp_url="ws://127.0.0.1:9222"
        )
        self.workflow.browser_provider = provider
        connection_urls: list[str] = []
        captured_timers: list[StageTimer] = []

        @contextmanager
        def fake_connected(url):
            connection_urls.append(url)
            time.sleep(0.05)
            yield None, object()

        order: list[int] = []
        with mock.patch(
            "ophelia_assistant.workflow.connected_browser", fake_connected
        ), mock.patch.object(
            self.workflow,
            "open_draft_on_browser",
            side_effect=lambda task_id, *_args, **_kwargs: (
                order.append(task_id),
                captured_timers.append(_kwargs.get("timer")),
            ),
        ):
            result = self.workflow.run_draft_batch(ids)
        self.assertEqual(provider.start_profile.call_count, 1)
        self.assertEqual(connection_urls, ["ws://127.0.0.1:9222"])
        self.assertEqual(order, ids)
        self.assertEqual(result["completed"], 2)
        for timer in captured_timers:
            self.assertGreater(timer.duration("connect"), 0)

    def test_different_windows_run_in_parallel(self) -> None:
        ids = self._tasks_on(7, 9)
        provider = mock.Mock()
        provider.start_profile.side_effect = lambda profile: SimpleNamespace(
            cdp_url=f"ws://127.0.0.1:{profile}"
        )
        self.workflow.browser_provider = provider
        barrier = threading.Barrier(2)
        started: list[int] = []

        def fake_fill(task_id, *_args, **_kwargs):
            row = self.db.get_task(task_id)
            started.append(int(row["profile_no"]))
            barrier.wait(timeout=2)
            return 100

        @contextmanager
        def fake_connected(_url):
            yield None, object()

        with mock.patch(
            "ophelia_assistant.workflow.connected_browser", fake_connected
        ), mock.patch.object(
            self.workflow,
            "open_draft_on_browser",
            side_effect=fake_fill,
        ):
            result = self.workflow.run_draft_batch(ids)
        self.assertEqual(sorted(started), [7, 9])
        self.assertEqual(result["completed"], 2)

    def test_cancel_stops_window_threads_safely(self) -> None:
        ids = self._tasks_on(7)
        cancel_event = threading.Event()

        def blocking_fill(*_args, **_kwargs):
            while not cancel_event.is_set():
                time.sleep(0.01)
            raise OperationCancelledError("停止")

        self.workflow.browser_provider = mock.Mock()
        self.workflow.browser_provider.start_profile.return_value = (
            SimpleNamespace(cdp_url="ws://127.0.0.1:9222")
        )

        @contextmanager
        def fake_connected(_url):
            yield None, object()

        captured: list[BaseException] = []

        def run():
            try:
                with mock.patch(
                    "ophelia_assistant.workflow.connected_browser",
                    fake_connected,
                ), mock.patch.object(
                    self.workflow,
                    "open_draft_on_browser",
                    side_effect=blocking_fill,
                ):
                    self.workflow.run_draft_batch(
                        ids,
                        cancel_event=cancel_event,
                    )
            except BaseException as exc:  # pragma: no cover - test capture
                captured.append(exc)

        thread = threading.Thread(target=run)
        thread.start()
        time.sleep(0.2)
        cancel_event.set()
        thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertTrue(
            any(
                isinstance(exc, OperationCancelledError)
                for exc in captured
            )
        )

    def test_window_connect_failure_marks_group_failed_retryable(self) -> None:
        ids = self._tasks_on(7, 7)
        provider = mock.Mock()
        provider.start_profile.side_effect = RuntimeError("CDP 连接失败")
        self.workflow.browser_provider = provider
        result = self.workflow.run_draft_batch(ids)
        self.assertEqual(result["completed"], 2)
        self.assertEqual(result["failed"], 2)
        for task_id in ids:
            row = self.db.get_task(task_id)
            self.assertEqual(row["status"], "failed")
            self.assertEqual(row["failure_stage"], "connect")

    def test_stage_timings_are_written_to_trace(self) -> None:
        task_id = self._tasks_on(7)[0]
        traced: list[tuple] = []

        def fake_trace(*args, **kwargs):
            traced.append((args, kwargs))

        def fake_prepare(
            _browser,
            _recipient,
            _subject,
            _body,
            _progress,
            _cancel_event,
            timer,
        ):
            timer.begin("open_gmail")
            timer.stop("open_gmail")
            timer.begin("fill_recipient")
            timer.stop("fill_recipient")
            return 100

        with mock.patch(
            "ophelia_assistant.workflow.prepare_gmail_draft",
            side_effect=fake_prepare,
        ), mock.patch(
            "ophelia_assistant.workflow.save_failure_screenshot",
            return_value="",
        ), mock.patch(
            "ophelia_assistant.workflow.trace_execution",
            side_effect=fake_trace,
        ):
            self.workflow.open_draft_on_browser(
                task_id,
                object(),
                timer=StageTimer(),
            )
        filled = [
            (args, kwargs)
            for args, kwargs in traced
            if args[1] == "filled"
        ]
        self.assertTrue(filled)
        extra = filled[0][1]["extra"]
        self.assertIn("fill_recipient", extra["stage_timings"])
        self.assertIn("open_gmail", extra["stage_timings"])


if __name__ == "__main__":
    unittest.main()
