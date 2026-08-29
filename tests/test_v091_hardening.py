from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import ophelia_assistant.config as config_module
import ophelia_assistant.trial as trial_module
from ophelia_assistant.config import Settings, normalize_window_sequence
from ophelia_assistant.trial import check_trial

try:
    import PySide6  # noqa: F401
    PYSIDE_AVAILABLE = True
    from PySide6.QtWidgets import QMessageBox, QTableWidgetItem
except ImportError:
    PYSIDE_AVAILABLE = False
    QMessageBox = None
    QTableWidgetItem = None


class SettingsStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self._patch_dir = mock.patch(
            "ophelia_assistant.config.app_data_dir",
            return_value=Path(self._temp.name),
        )
        self._patch_dir.start()

    def tearDown(self) -> None:
        self._patch_dir.stop()
        self._temp.cleanup()

    def _settings_file(self) -> Path:
        return Path(self._temp.name) / "settings.json"

    def test_window_sequence_persists_after_reload(self) -> None:
        settings = Settings()
        settings.save_window_sequence([199, 198, 197, 196])
        reloaded = Settings.load()
        self.assertEqual(reloaded.window_sequence, [199, 198, 197, 196])

    def test_window_sequence_save_ignores_api_key_encryption(self) -> None:
        settings = Settings()
        settings.adspower_api_key = "plain-key"
        settings._api_key_dirty = True
        with mock.patch.object(
            config_module,
            "_protect_secret",
            side_effect=RuntimeError("must not be called"),
        ):
            settings.save_window_sequence([199, 198, 197, 196])
        reloaded = Settings.load()
        self.assertEqual(reloaded.window_sequence, [199, 198, 197, 196])

    def test_save_failure_preserves_original_file(self) -> None:
        settings = Settings()
        settings.save_window_sequence([1])
        original = self._settings_file().read_text(encoding="utf-8")
        with mock.patch.object(
            config_module,
            "_atomic_write_json",
            side_effect=RuntimeError("disk full"),
        ):
            with self.assertRaisesRegex(RuntimeError, "disk full"):
                settings.save_window_sequence([2, 3])
        self.assertEqual(
            self._settings_file().read_text(encoding="utf-8"),
            original,
        )
        self.assertEqual(settings.window_sequence, [1])
        self.assertFalse(
            Path(self._temp.name, "settings.json.tmp").exists()
        )

    def test_sequence_write_failure_keeps_old_memory_state(self) -> None:
        settings = Settings()
        settings.save_window_sequence([199, 198])
        with mock.patch.object(
            config_module,
            "_atomic_write_json",
            side_effect=RuntimeError("disk full"),
        ):
            with self.assertRaisesRegex(RuntimeError, "disk full"):
                settings.save_window_sequence([197, 196])
        self.assertEqual(settings.window_sequence, [199, 198])
        data = json.loads(self._settings_file().read_text(encoding="utf-8"))
        self.assertEqual(data["window_sequence"], [199, 198])

    def test_merge_bindings_failure_keeps_old_memory_state(self) -> None:
        settings = Settings()
        settings.merge_window_bindings(
            {"1": {"sender_name": "Old", "locked": False}}
        )
        with mock.patch.object(
            config_module,
            "_atomic_write_json",
            side_effect=RuntimeError("disk full"),
        ):
            with self.assertRaisesRegex(RuntimeError, "disk full"):
                settings.merge_window_bindings(
                    {"2": {"sender_name": "New", "locked": True}}
                )
        self.assertNotIn("2", settings.window_bindings)
        self.assertIn("1", settings.window_bindings)
        data = json.loads(self._settings_file().read_text(encoding="utf-8"))
        self.assertNotIn("2", data["window_bindings"])

    def test_prune_failure_keeps_history_in_memory_and_disk(self) -> None:
        settings = Settings()
        settings.replace_window_bindings(
            {
                "1": {"sender_name": "Current", "locked": True},
                "9": {"sender_name": "History", "locked": False},
            }
        )
        with mock.patch.object(
            config_module,
            "_atomic_write_json",
            side_effect=RuntimeError("disk full"),
        ):
            with self.assertRaisesRegex(RuntimeError, "disk full"):
                settings.prune_window_bindings({"1"})
        self.assertIn("9", settings.window_bindings)
        data = json.loads(self._settings_file().read_text(encoding="utf-8"))
        self.assertIn("9", data["window_bindings"])

    def test_failed_save_keeps_last_good_assignment_data(self) -> None:
        from ophelia_assistant.config import resolve_task_windows_balanced

        settings = Settings()
        settings.save_window_sequence([199, 198])
        with mock.patch.object(
            config_module,
            "_atomic_write_json",
            side_effect=RuntimeError("disk full"),
        ):
            with self.assertRaisesRegex(RuntimeError, "disk full"):
                settings.save_window_sequence([197, 196])
        resolved = resolve_task_windows_balanced(
            [0, 0],
            settings.window_sequence,
        )
        self.assertEqual(resolved, [199, 198])

    def test_duplicate_and_empty_window_numbers_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "重复"):
            normalize_window_sequence([199, 199])
        with self.assertRaisesRegex(ValueError, "不能为空"):
            normalize_window_sequence([199, ""])

    def test_partial_save_preserves_unknown_fields_and_api_key(self) -> None:
        path = self._settings_file()
        path.write_text(
            json.dumps(
                {
                    "window_sequence": [1],
                    "adspower_api_key": "enc:v1:existing",
                    "future_field": "keep-me",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        settings = Settings.load()
        settings.save_fields({"window_sequence": [5]})
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["window_sequence"], [5])
        self.assertEqual(data["adspower_api_key"], "enc:v1:existing")
        self.assertEqual(data["future_field"], "keep-me")

    def test_partial_save_preserves_unsaved_api_key_state(self) -> None:
        path = self._settings_file()
        path.write_text(
            json.dumps(
                {
                    "window_sequence": [199],
                    "adspower_api_key": "enc:v1:old",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        settings = Settings.load()
        settings.adspower_api_key = "new-unsaved-key"
        settings._api_key_dirty = True
        old_persisted = settings._persisted_api_key

        settings.save_window_sequence([198, 197])
        settings.merge_window_bindings(
            {"198": {"sender_name": "Anna", "locked": True}}
        )

        self.assertEqual(settings.adspower_api_key, "new-unsaved-key")
        self.assertTrue(settings._api_key_dirty)
        self.assertEqual(settings._persisted_api_key, old_persisted)
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["adspower_api_key"], "enc:v1:old")
        self.assertEqual(data["window_sequence"], [198, 197])

        with mock.patch.object(
            config_module,
            "_atomic_write_json",
            side_effect=RuntimeError("disk full"),
        ):
            with self.assertRaisesRegex(RuntimeError, "disk full"):
                settings.save_window_sequence([196])
        self.assertEqual(settings.adspower_api_key, "new-unsaved-key")
        self.assertTrue(settings._api_key_dirty)
        self.assertEqual(settings._persisted_api_key, old_persisted)

    def test_schema_metadata_is_written_and_migrates_once(self) -> None:
        path = self._settings_file()
        path.write_text(
            json.dumps({"window_sequence": [199, 198]}, ensure_ascii=False),
            encoding="utf-8",
        )
        loaded = Settings.load()
        self.assertEqual(loaded._app_version, "0.91.0")
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(data["app_version"], "0.91.0")
        self.assertTrue(data["migration_id"])
        self.assertTrue(data["updated_at"])
        Settings.load()
        migration_log = Path(self._temp.name) / "migrations.jsonl"
        entries = [
            json.loads(line)
            for line in migration_log.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        settings_entries = [
            entry for entry in entries if entry.get("kind") == "settings"
        ]
        self.assertEqual(len(settings_entries), 1)

    def test_single_bad_field_does_not_reset_all_settings(self) -> None:
        path = self._settings_file()
        path.write_text(
            json.dumps(
                {
                    "sender_name": "Keep Me",
                    "window_sequence": "not-a-list",
                    "theme_mode": "dark",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        settings = Settings.load()
        self.assertEqual(settings.sender_name, "Keep Me")
        self.assertEqual(settings.theme_mode, "dark")
        self.assertEqual(settings.window_sequence, [])
        self.assertTrue(getattr(settings, "_load_problems", []))


class LicenseGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._old_env: dict[str, str | None] = {}
        for key in (
            "NIUMA_MAIL_TRIAL_DIR",
            "NIUMA_MAIL_MACHINE_ID",
            "NIUMA_MAIL_DISABLE_REGISTRY",
        ):
            self._old_env[key] = os.environ.get(key)
        os.environ["NIUMA_MAIL_TRIAL_DIR"] = self._tmp.name
        os.environ["NIUMA_MAIL_MACHINE_ID"] = "TEST-MACHINE-LICENSE"
        os.environ["NIUMA_MAIL_DISABLE_REGISTRY"] = "1"

    def tearDown(self) -> None:
        for key, value in self._old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._tmp.cleanup()

    def _write_v2_record(
        self,
        path: Path,
        authorized_until: int,
        last_seen_at: int | None = None,
    ) -> None:
        payload = {
            "version": 2,
            "machine": trial_module._machine_digest().hex(),
            "started_at": int(time.time()) - 7 * 86400,
            "last_seen_at": int(time.time() - 60) if last_seen_at is None else last_seen_at,
            "authorized_until": authorized_until,
            "grant_type": "admin",
            "redeemed_codes": {
                "0" * 64: int(time.time()) + 86400,
            },
        }
        path.write_text(
            trial_module._signed_record(payload),
            encoding="utf-8",
        )

    def test_version_change_does_not_reset_or_extend_authorization(self) -> None:
        now = int(time.time())
        expires_at = now + 7 * 86400
        trial_module._save_state_v3(
            now - 100,
            now - 50,
            expires_at,
            "trial",
            {},
            grant_source="trial",
            change_reason="test",
            granted_at=now - 100,
        )
        trial_module._journal_append(
            {
                "time": now,
                "event": "trial_creation",
                "new_until": expires_at,
                "reason": "test",
            }
        )
        status = check_trial(now=now)
        self.assertEqual(status.expires_at, expires_at)
        self.assertFalse(status.suspicious)

    def test_anomalous_legacy_record_is_detected_and_not_written_back(self) -> None:
        primary = Path(self._tmp.name) / "weekly_authorization.dat"
        authorized_until = int(time.time()) + 46 * 86400
        self._write_v2_record(primary, authorized_until)
        original = primary.read_text(encoding="utf-8")
        status = check_trial()
        self.assertTrue(status.suspicious)
        self.assertIn("异常", status.reason)
        self.assertEqual(primary.read_text(encoding="utf-8"), original)
        second = check_trial()
        self.assertTrue(second.suspicious)
        self.assertEqual(primary.read_text(encoding="utf-8"), original)

    def test_invalid_state_is_reported_and_not_reset(self) -> None:
        primary = Path(self._tmp.name) / "weekly_authorization.dat"
        primary.write_text("not-json", encoding="utf-8")
        status = check_trial()
        self.assertFalse(status.active)
        self.assertIn("无效", status.reason)
        self.assertEqual(primary.read_text(encoding="utf-8"), "not-json")

    def test_legacy_migration_runs_only_once(self) -> None:
        legacy_dir = Path(self._tmp.name) / "legacy"
        legacy_dir.mkdir()
        legacy_path = legacy_dir / "weekly_authorization.dat"
        self._write_v2_record(legacy_path, int(time.time()) + 86400)
        with mock.patch.object(
            trial_module,
            "_legacy_state_candidates",
            return_value=[legacy_path],
        ):
            status = check_trial()
            self.assertTrue(status.active)
            self.assertEqual(
                status.source,
                "migration",
            )
            primary = Path(self._tmp.name) / "weekly_authorization.dat"
            self.assertTrue(primary.exists())
            check_trial()
        migration_log = Path(self._tmp.name) / "migrations.jsonl"
        entries = [
            json.loads(line)
            for line in migration_log.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        license_migrations = [
            entry for entry in entries if entry.get("kind") == "license"
        ]
        self.assertEqual(len(license_migrations), 1)

    def test_multiple_legacy_dirs_pick_latest_and_log_source(self) -> None:
        legacy_dir = Path(self._tmp.name) / "legacy"
        legacy_dir.mkdir()
        older_path = legacy_dir / "older.dat"
        newer_path = legacy_dir / "newer.dat"
        now = int(time.time())
        self._write_v2_record(older_path, now + 86400, last_seen_at=now - 120)
        self._write_v2_record(newer_path, now + 86400, last_seen_at=now - 30)
        with mock.patch.object(
            trial_module,
            "_legacy_state_candidates",
            return_value=[older_path, newer_path],
        ):
            status = check_trial()
        self.assertTrue(status.active)
        self.assertEqual(status.source, "migration")
        migration_log = Path(self._tmp.name) / "migrations.jsonl"
        entries = [
            json.loads(line)
            for line in migration_log.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        license_migrations = [
            entry for entry in entries if entry.get("kind") == "license"
        ]
        self.assertEqual(len(license_migrations), 1)
        self.assertEqual(license_migrations[0]["from_path"], str(newer_path))


@unittest.skipUnless(PYSIDE_AVAILABLE, "PySide6 not installed")
class StudioWindowSyncTests(unittest.TestCase):
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

    def _setup_windows(self) -> None:
        self.window.settings.window_sequence = [199, 198, 197, 196]
        self.window.settings.window_bindings = {
            str(number): {
                "template_name": "",
                "sender_name": f"Sender {number}",
                "locked": False,
            }
            for number in range(169, 200)
        }
        self.window.settings.window_bindings["198"]["sender_name"] = "Tom"
        self.window.settings.window_bindings["197"]["sender_name"] = "Anna"
        self.window.settings.save_window_sequence([199, 198, 197, 196])

    def test_bindings_table_only_shows_sequence_windows(self) -> None:
        self._setup_windows()
        self.window.pages[0].window_panel.load()
        panel = self.window.pages[0].window_panel
        self.assertEqual(panel.bindings_table.rowCount(), 4)
        shown = [
            panel.bindings_table.item(row, 0).text()
            for row in range(panel.bindings_table.rowCount())
        ]
        self.assertEqual(shown, ["199", "198", "197", "196"])
        self.assertNotIn("195", shown)

    def test_delete_row_does_not_shift_bindings_by_row(self) -> None:
        self._setup_windows()
        self.window.pages[0].window_panel.load()
        panel = self.window.pages[0].window_panel
        panel.sequence_table.selectRow(1)
        panel.delete_row()
        panel.save_sequence()
        self.assertEqual(
            self.window.settings.window_sequence,
            [199, 197, 196],
        )
        self.assertEqual(
            self.window.settings.window_bindings["197"]["sender_name"],
            "Anna",
        )
        shown = [
            panel.bindings_table.item(row, 0).text()
            for row in range(panel.bindings_table.rowCount())
        ]
        self.assertEqual(shown, ["199", "197", "196"])

    def test_reopened_history_window_restores_binding_by_number(self) -> None:
        self._setup_windows()
        self.window.settings.save_window_sequence([199, 197, 196])
        self.window.settings.window_bindings["198"]["sender_name"] = "Tom"
        self.window.settings.save_window_bindings(
            self.window.settings.window_bindings
        )
        self.window.settings.save_window_sequence([199, 197, 196, 198])
        panel = self.window.pages[0].window_panel
        panel.load()
        for row in range(panel.bindings_table.rowCount()):
            if panel.bindings_table.item(row, 0).text() == "198":
                sender = panel.bindings_table.cellWidget(row, 2)
                self.assertEqual(sender.text(), "Tom")
                break
        else:
            self.fail("198 binding row missing")

    def test_save_bindings_failure_keeps_ui_content(self) -> None:
        self.window.settings.save_window_sequence([199])
        panel = self.window.pages[0].window_panel
        panel.load()
        sender = panel.bindings_table.cellWidget(0, 2)
        sender.setText("New Sender")
        with mock.patch.object(
            self.window.settings,
            "merge_window_bindings",
            side_effect=RuntimeError("write blocked"),
        ), mock.patch.object(self.window, "show_error") as show_error:
            panel.save_bindings()
        self.assertEqual(sender.text(), "New Sender")
        show_error.assert_called_once()
        self.assertIn("窗口绑定保存失败", show_error.call_args[0][0])

    def test_api_key_encryption_failure_does_not_block_bindings(self) -> None:
        self.window.settings.save_window_sequence([199, 198, 197, 196])
        self.window.settings.adspower_api_key = "plain-key"
        self.window.settings._api_key_dirty = True
        panel = self.window.pages[0].window_panel
        panel.load()
        with mock.patch.object(
            config_module,
            "_protect_secret",
            side_effect=RuntimeError("must not be called"),
        ):
            panel.save_bindings()
        self.assertIn("共 4 个窗口", self.window.status_label.text())

    def test_save_bindings_success_message_count(self) -> None:
        self.window.settings.save_window_sequence([199, 198, 197, 196])
        panel = self.window.pages[0].window_panel
        panel.load()
        panel.save_bindings()
        self.assertIn("共 4 个窗口", self.window.status_label.text())

    def test_save_bindings_button_preserves_closed_window_history(self) -> None:
        self._setup_windows()
        panel = self.window.pages[0].window_panel
        panel.load()
        panel.sequence_table.selectRow(1)
        panel.delete_row()
        panel.save_sequence()
        self.assertEqual(
            self.window.settings.window_sequence,
            [199, 197, 196],
        )
        self.assertIn(
            "198",
            self.window.settings.window_bindings,
        )
        panel.save_bindings()
        self.assertIn(
            "198",
            self.window.settings.window_bindings,
        )
        self.assertEqual(
            self.window.settings.window_bindings["198"]["sender_name"],
            "Tom",
        )
        panel.add_row()
        new_row = panel.sequence_table.rowCount() - 1
        panel.sequence_table.setItem(
            new_row,
            1,
            QTableWidgetItem("198"),
        )
        panel.save_sequence()
        for row in range(panel.bindings_table.rowCount()):
            if panel.bindings_table.item(row, 0).text() == "198":
                sender = panel.bindings_table.cellWidget(row, 2)
                self.assertEqual(sender.text(), "Tom")
                break
        else:
            self.fail("198 binding row missing after reopening")

    def test_unsaved_binding_edits_survive_sequence_changes(self) -> None:
        self.window.settings.save_window_sequence([199, 198, 197, 196])
        panel = self.window.pages[0].window_panel
        panel.load()
        sender = panel.bindings_table.cellWidget(0, 2)
        sender.setText("Unsaved Sender")
        lock = panel.bindings_table.cellWidget(0, 3)
        lock.setChecked(True)

        panel.sequence_table.selectRow(0)
        panel.move_row(1)
        self.assertEqual(self._sender_for_window(panel, "199"), "Unsaved Sender")
        self.assertTrue(self._lock_for_window(panel, "199"))

        panel.sequence_table.selectRow(2)
        panel.delete_row()
        self.assertEqual(self._sender_for_window(panel, "199"), "Unsaved Sender")

        panel.add_row()
        last = panel.sequence_table.rowCount() - 1
        panel.sequence_table.setItem(last, 1, QTableWidgetItem("200"))
        self.assertEqual(self._sender_for_window(panel, "199"), "Unsaved Sender")

    @staticmethod
    def _sender_for_window(panel, window: str) -> str:
        for row in range(panel.bindings_table.rowCount()):
            if panel.bindings_table.item(row, 0).text() == window:
                return panel.bindings_table.cellWidget(row, 2).text()
        raise AssertionError(f"window {window} missing from bindings table")

    @staticmethod
    def _lock_for_window(panel, window: str) -> bool:
        for row in range(panel.bindings_table.rowCount()):
            if panel.bindings_table.item(row, 0).text() == window:
                return bool(
                    panel.bindings_table.cellWidget(row, 3).isChecked()
                )
        raise AssertionError(f"window {window} missing from bindings table")

    def test_prune_button_removes_history_only(self) -> None:
        self.window.settings.save_window_sequence([199])
        self.window.settings.merge_window_bindings(
            {
                "199": {"sender_name": "Current", "locked": True},
                "195": {"sender_name": "History", "locked": False},
            }
        )
        panel = self.window.pages[0].window_panel
        panel.load()
        with mock.patch(
            "PySide6.QtWidgets.QMessageBox.question",
            return_value=QMessageBox.Yes,
        ):
            panel.prune_invalid_bindings()
        self.assertNotIn("195", self.window.settings.window_bindings)
        self.assertIn("199", self.window.settings.window_bindings)
        self.assertIn("已清理 1 个无效绑定", self.window.status_label.text())

    def test_both_save_buttons_ignore_api_key_encryption_failure(self) -> None:
        self.window.settings.save_window_sequence([199, 198])
        self.window.settings.adspower_api_key = "plain-key"
        self.window.settings._api_key_dirty = True
        panel = self.window.pages[0].window_panel
        panel.load()
        with mock.patch.object(
            config_module,
            "_protect_secret",
            side_effect=RuntimeError("must not be called"),
        ):
            panel.save_sequence()
            self.assertIn(
                "窗口顺序已保存",
                self.window.status_label.text(),
            )
            panel.save_bindings()
        self.assertIn("共 2 个窗口", self.window.status_label.text())

    def test_closed_window_does_not_participate_in_assignment(self) -> None:
        from ophelia_assistant.batch import assign_windows

        assignments = assign_windows(
            [
                {
                    "id": 1,
                    "profile_no": 195,
                    "profile_locked": 0,
                    "window_assignment_type": "manual",
                }
            ],
            [199, 198, 197, 196],
        )
        self.assertTrue(assignments[0]["waiting"])
        self.assertEqual(assignments[0]["conflict"], "window_not_open")


if __name__ == "__main__":
    unittest.main()
