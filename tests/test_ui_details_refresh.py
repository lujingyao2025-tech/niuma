from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

try:
    import PySide6  # noqa: F401
    import playwright  # noqa: F401
    import requests  # noqa: F401
except ImportError as exc:
    raise unittest.SkipTest(f"missing dependency: {exc.name}")


class StudioUiTests(unittest.TestCase):
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
        from PySide6.QtWidgets import QApplication

        self.app = QApplication.instance() or QApplication([])
        from ophelia_assistant.studio.main_window import MainWindow

        self.window = MainWindow()
        self.window.show()
        self.app.processEvents()

    def tearDown(self) -> None:
        self.window._closing = True
        self.window.close()
        self.app.processEvents()
        self._patch_cfg.stop()
        self._patch_db.stop()
        os.environ.pop("NIUMA_MAIL_TRIAL_DIR", None)
        os.environ.pop("NIUMA_MAIL_DISABLE_REGISTRY", None)
        self._temp.cleanup()

    def test_window_tab_hides_details_panel(self) -> None:
        with mock.patch.object(
            self.window, "refresh_window_bindings"
        ) as refresh:
            self.window.pages[0].tabs.setCurrentIndex(2)
        self.app.processEvents()
        wrapper = self.window.pages[0].details_wrapper
        self.assertFalse(wrapper._visible_override)
        self.assertFalse(wrapper.inspector.isVisible())
        refresh.assert_called_once()

    def test_contact_selection_auto_expands_details(self) -> None:
        task_id = self.window.db.add_local_task(
            "Alex", "Seattle", "alex@example.com"
        )
        self.window.on_task_selection([task_id])
        self.app.processEvents()
        wrapper = self.window.pages[0].details_wrapper
        self.assertTrue(wrapper.is_expanded())
        self.assertTrue(wrapper.inspector.isVisible())

    def test_draft_selection_auto_expands_details(self) -> None:
        task_id = self.window.db.add_local_task(
            "Alex", "Seattle", "alex@example.com"
        )
        self.window.db.update_task(
            task_id,
            status="drafted",
            drafted_at="2026-08-30T00:00:00+00:00",
        )
        self.window.refresh_tasks()
        self.app.processEvents()
        self.window.pages[0]._draft_model.select_ids([task_id])
        self.app.processEvents()
        wrapper = self.window.pages[0].details_wrapper
        self.assertTrue(wrapper.is_expanded())

    def test_contact_selection_does_not_save_settings(self) -> None:
        task_id = self.window.db.add_local_task(
            "Alex", "Seattle", "alex@example.com"
        )
        with mock.patch.object(
            self.window.settings, "save"
        ) as settings_save:
            self.window.on_task_selection([task_id])
            self.app.processEvents()
            settings_save.assert_not_called()

    def test_auto_expand_does_not_save_settings(self) -> None:
        from ophelia_assistant.studio.panels import (
            CollapsibleDetails,
            TaskInspector,
        )

        wrapper = CollapsibleDetails(
            TaskInspector(self.window),
            self.window.ui_state,
            "test_auto_expand",
        )
        with mock.patch.object(
            self.window.settings, "save"
        ) as settings_save:
            wrapper.auto_show()
            settings_save.assert_not_called()

    def test_ui_state_flush_does_not_call_protect_secret(self) -> None:
        import ophelia_assistant.config as config_module

        with mock.patch.object(
            config_module, "_protect_secret"
        ) as protect:
            self.window.ui_state.setValue("test_ui_key", 1)
            self.window.ui_state.flush()
            protect.assert_not_called()

    def test_bitbrowser_generation_with_adspower_key_does_not_save_settings(
        self,
    ) -> None:
        self.window.settings.browser_provider = "bitbrowser"
        self.window.settings.adspower_api_key = "stored-ads-key"
        task_id = self.window.db.add_local_task(
            "Alex", "Seattle", "alex@example.com"
        )
        with mock.patch.object(
            self.window.settings, "save"
        ) as settings_save:
            self.window.workflow.generate_local(task_id)
            settings_save.assert_not_called()
        self.assertEqual(
            self.window.db.get_task(task_id)["status"],
            "generated",
        )

    def test_collapse_state_and_width_persist(self) -> None:
        from ophelia_assistant.studio.panels import (
            CollapsibleDetails,
            TaskInspector,
        )
        from ophelia_assistant.studio.ui_state import UiStateStore

        first_store = UiStateStore(self.window, debounce_ms=200)

        first = CollapsibleDetails(
            TaskInspector(self.window),
            first_store,
            "test_details_state",
        )
        first.set_expanded(True)
        first.save_width(380)
        first_store.flush()
        second_store = UiStateStore(self.window, debounce_ms=200)
        self.assertTrue(
            second_store.value(
                "test_details_state_expanded",
                False,
                type=bool,
            )
        )
        self.assertEqual(
            second_store.value("test_details_state_width", 340, type=int),
            380,
        )
        second = CollapsibleDetails(
            TaskInspector(self.window),
            second_store,
            "test_details_state",
        )
        self.assertTrue(second.is_expanded())
        self.assertEqual(second.saved_width(), 380)

    def test_expanded_details_can_be_dragged_and_saves_width(self) -> None:
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QSplitter, QWidget

        from ophelia_assistant.studio.panels import (
            CollapsibleDetails,
            TaskInspector,
        )
        from ophelia_assistant.studio.ui_state import UiStateStore

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(QWidget())
        wrapper = CollapsibleDetails(
            TaskInspector(self.window),
            self.window.ui_state,
            "test_drag_width",
        )
        splitter.addWidget(wrapper)
        wrapper.splitter = splitter
        splitter.splitterMoved.connect(
            lambda _pos, _index: wrapper.save_width(wrapper.width())
        )
        splitter.setSizes([700, 340])
        splitter.resize(1040, 500)
        splitter.show()
        self.app.processEvents()
        wrapper.set_expanded(True)
        self.app.processEvents()
        self.assertEqual(wrapper.minimumWidth(), 300)
        self.assertEqual(wrapper.maximumWidth(), 420)
        self.assertGreater(wrapper.maximumWidth(), wrapper.minimumWidth())
        before = wrapper.width()
        handle = splitter.handle(1)
        center = handle.rect().center()
        with mock.patch.object(
            self.window.settings, "save"
        ) as settings_save:
            QTest.mousePress(
                handle,
                Qt.LeftButton,
                Qt.NoModifier,
                center,
            )
            QTest.mouseMove(
                handle,
                center + QPoint(60, 0),
                delay=20,
            )
            QTest.mouseRelease(
                handle,
                Qt.LeftButton,
                Qt.NoModifier,
                center + QPoint(60, 0),
            )
            self.app.processEvents()
            settings_save.assert_not_called()
        after = wrapper.width()
        self.assertNotEqual(after, before)
        self.window.ui_state.flush()
        stored = int(
            self.window.ui_state.value("test_drag_width_width", 0, type=int)
        )
        self.assertGreaterEqual(
            stored,
            300,
            f"drag did not persist width: before={before} after={after}",
        )
        self.assertLessEqual(stored, 420)
        reloaded = UiStateStore(self.window)
        self.assertEqual(
            reloaded.value("test_drag_width_width", 0, type=int),
            stored,
        )
        wrapper.set_expanded(False)

    def test_generate_selected_caps_at_100(self) -> None:
        ids = []
        for index in range(101):
            ids.append(
                self.window.db.add_local_task(
                    f"User {index}",
                    "Seattle",
                    f"user{index}@example.com",
                )
            )
        self.window.refresh_tasks()
        self.app.processEvents()
        self.window.pages[0].select_task_ids(ids)
        with mock.patch.object(self.window, "generate_tasks") as gen:
            self.window.generate_selected()
            gen.assert_called_once()
            called_ids = gen.call_args[0][0]
        self.assertEqual(len(called_ids), 100)
        self.assertIn("100", self.window.status_label.text())

    def test_global_chunk_progress_never_goes_backwards(self) -> None:
        from ophelia_assistant.studio.main_window import global_chunk_percent

        self.assertEqual(global_chunk_percent(0, 100, 300, 50), 16)
        self.assertEqual(global_chunk_percent(100, 100, 300, 0), 33)
        self.assertEqual(global_chunk_percent(100, 100, 300, 100), 66)
        self.assertEqual(global_chunk_percent(200, 100, 300, 100), 100)

    def test_new_template_appears_in_window_binding_dropdown(self) -> None:
        self.window.settings.window_sequence = [199]
        self.window.settings.save()
        self.window.save_template_library(
            "询问地区",
            "Subject",
            "Body",
            "Anna Lee",
            "",
        )
        panel = self.window.pages[0].window_panel
        combo = panel.bindings_table.cellWidget(0, 1)
        self.assertGreaterEqual(combo.findText("询问地区"), 0)

    def test_refresh_preserves_selection_sender_and_lock(self) -> None:
        self.window.settings.window_sequence = [199]
        self.window.settings.window_bindings = {
            "199": {
                "template_name": "询问地区",
                "sender_name": "Anna Lee",
                "locked": True,
            }
        }
        self.window.settings.saved_templates = [
            {
                "name": "询问地区",
                "subject_template": "S",
                "body_template": "B",
                "sender_name": "Anna Lee",
                "signature": "",
                "custom_variables": {},
            }
        ]
        self.window.settings.save()
        self.window.pages[0].window_panel.load()
        self.window.refresh_window_bindings()
        panel = self.window.pages[0].window_panel
        combo = panel.bindings_table.cellWidget(0, 1)
        sender = panel.bindings_table.cellWidget(0, 2)
        lock = panel.bindings_table.cellWidget(0, 3)
        self.assertEqual(combo.currentData(), "询问地区")
        self.assertEqual(sender.text(), "Anna Lee")
        self.assertTrue(lock.isChecked())

    def test_deleted_bound_template_shows_invalid_state(self) -> None:
        self.window.settings.window_sequence = [199]
        self.window.settings.window_bindings = {
            "199": {"template_name": "已删除模板"}
        }
        self.window.settings.save()
        self.window.pages[0].window_panel.load()
        panel = self.window.pages[0].window_panel
        combo = panel.bindings_table.cellWidget(0, 1)
        self.assertIn("模板已删除/失效", combo.currentText())
        self.assertGreaterEqual(combo.findData("已删除模板"), 0)

    def test_auto_fill_preserves_saved_order_and_appends_new_window(self) -> None:
        self.window.settings.window_sequence = [197, 199, 196, 198]
        self.window.settings.save()

        class FakeProvider:
            def list_running_windows(self, verify_connection=True):
                return [
                    ("197", "A"),
                    ("199", "B"),
                    ("196", "C"),
                    ("198", "D"),
                    ("195", "E"),
                ]

        with mock.patch(
            "ophelia_assistant.studio.main_window.create_browser_provider",
            return_value=FakeProvider(),
        ):
            self.window.auto_fill_windows()
            deadline = time.time() + 5
            while (
                self.window.settings.window_sequence
                != [197, 199, 196, 198, 195]
                and time.time() < deadline
            ):
                self.app.processEvents()
                time.sleep(0.05)
        self.assertEqual(
            self.window.settings.window_sequence,
            [197, 199, 196, 198, 195],
        )

    def test_insufficient_windows_marks_waiting_and_reports_count(self) -> None:
        self.window.settings.auto_send_confirm = False
        self.window.settings.save()
        task_ids = []
        for index in range(3):
            task_id = self.window.db.add_local_task(
                f"User {index}",
                "Seattle",
                f"user{index}@example.com",
            )
            self.window.db.update_task(
                task_id,
                subject=f"Subject {index}",
                body=f"Body {index}",
                status="generated",
            )
            task_ids.append(task_id)

        class FakeProvider:
            def list_running_windows(self):
                return [("199", "A")]

        def fake_run_batch(*_args, **_kwargs):
            return {
                "completed": 0,
                "sent": 0,
                "drafted": 0,
                "failed": 0,
                "needs_review": 0,
                "waiting": 0,
            }

        with mock.patch(
            "ophelia_assistant.studio.main_window.create_browser_provider",
            return_value=FakeProvider(),
        ), mock.patch.object(
            self.window.workflow,
            "run_send_batch",
            side_effect=fake_run_batch,
        ):
            self.window.open_selected_drafts(
                wait_send=True,
                forced_ids=task_ids,
            )
            deadline = time.time() + 5
            while (
                "2 封邮件未发送" not in self.window.status_label.text()
                and time.time() < deadline
            ):
                self.app.processEvents()
                time.sleep(0.05)
        rows = [dict(row) for row in self.window.db.get_tasks(task_ids)]
        self.assertEqual(
            sum(1 for row in rows if row["status"] == "waiting_window"),
            2,
        )
        self.assertIn("2 封邮件未发送", self.window.status_label.text())

    def test_exit_waits_twenty_seconds_before_force_prompt(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        class Event:
            def __init__(self):
                self.accepted = False
                self.ignored = False

            def accept(self):
                self.accepted = True

            def ignore(self):
                self.ignored = True

        event = Event()
        with mock.patch.object(
            self.window, "_close_choice", return_value="exit"
        ), mock.patch.object(
            self.window.thread_pool, "waitForDone", return_value=False
        ), mock.patch.object(
            self.window, "_finish_close"
        ) as finish:
            self.window.closeEvent(event)
            self.window._close_started = time.monotonic() - 21
            with mock.patch.object(
                QMessageBox, "question", return_value=QMessageBox.No
            ):
                self.window._poll_close()
            self.assertTrue(self.window._close_prompt_shown)
            finish.assert_not_called()
            self.window._close_prompt_shown = False
            with mock.patch.object(
                QMessageBox, "question", return_value=QMessageBox.Yes
            ):
                self.window._poll_close()
            finish.assert_called_once()


if __name__ == "__main__":
    unittest.main()
