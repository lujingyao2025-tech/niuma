import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ophelia_assistant.config import Settings
from ophelia_assistant.diagnostics import (
    redact_settings,
    trace_execution,
    write_error_report,
)


class DiagnosticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self._patch = mock.patch(
            "ophelia_assistant.config.app_data_dir",
            return_value=Path(self._temp.name),
        )
        self._patch.start()

    def tearDown(self) -> None:
        self._patch.stop()
        self._temp.cleanup()

    def test_redact_settings_hides_api_key(self) -> None:
        settings = Settings()
        settings.adspower_api_key = "super-secret"
        snapshot = redact_settings(settings)
        self.assertEqual(snapshot["adspower_api_key"], "<redacted>")
        self.assertNotIn("super-secret", str(snapshot))

    def test_error_report_contains_traceback_and_trail(self) -> None:
        trace_execution(7, "start", "开始")
        trace_execution(7, "sent", "发送成功")
        try:
            raise RuntimeError("测试故障")
        except RuntimeError as exc:
            path = write_error_report(
                exc,
                task_id=7,
                stage="send_failed",
                profile_no=3,
                settings=Settings(),
                extra_trail=None,
            )
        text = path.read_text(encoding="utf-8")
        self.assertIn("RuntimeError", text)
        self.assertIn("测试故障", text)
        self.assertIn("send_failed", text)
        self.assertTrue(path.name.startswith("error_"))

    def test_execution_trail_is_append_only(self) -> None:
        trace_execution(9, "fill_recipient", "填写收件人")
        from ophelia_assistant.diagnostics import recent_trail

        trail = recent_trail(task_id=9, limit=50)
        self.assertTrue(any(item["stage"] == "fill_recipient" for item in trail))


if __name__ == "__main__":
    unittest.main()
