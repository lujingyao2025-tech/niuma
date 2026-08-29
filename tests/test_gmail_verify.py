from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:
    import playwright  # noqa: F401
    import requests  # noqa: F401
except ImportError as exc:
    raise unittest.SkipTest(f"missing dependency: {exc.name}")

from ophelia_assistant import browser as browser_module
from ophelia_assistant.browser import BrowserAutomationError
from ophelia_assistant.config import Settings
from ophelia_assistant.database import Database
from ophelia_assistant.workflow import Workflow


class FakeControl:
    def __init__(self, text: str = "", attrs: dict | None = None) -> None:
        self.text = text
        self.attrs = dict(attrs or {})

    def is_visible(self) -> bool:
        return True

    def is_enabled(self) -> bool:
        return True

    def input_value(self, timeout: int = 0) -> str:
        return self.text

    def inner_text(self, timeout: int = 0) -> str:
        return self.text

    def text_content(self, timeout: int = 0) -> str:
        return self.text

    def get_attribute(self, name: str, timeout: int = 0) -> str | None:
        return self.attrs.get(name)

    def click(self, timeout: int = 0) -> None:
        return None

    def evaluate(self, _expression: str, *args):
        return ""

    def bounding_box(self):
        return {"x": 0, "y": 0, "width": 200, "height": 100}

    def wait_for(self, state: str = "visible", timeout: int = 0) -> None:
        return None


class FakeComposeProvider:
    def __init__(
        self,
        recipient_input: str = "",
        subject: str = "",
        body: str = "",
        chips: list[FakeControl] | None = None,
    ) -> None:
        self.recipient_input = recipient_input
        self.subject = subject
        self.body = body
        self.chips = chips or []

    def matches(self, selector: str) -> list[FakeControl]:
        if (
            "role=\"chip\"" in selector
            or "data-hovercard-id" in selector
            or "data-email" in selector
            or "[email]" in selector
        ):
            return list(self.chips)
        if (
            'name="to"' in selector
            or "peoplekit" in selector
            or "收件人" in selector
        ):
            return [FakeControl(self.recipient_input)]
        if (
            "subjectbox" in selector
            or 'name="subject"' in selector
            or 'aria-label="Subject"' in selector
            or "主题" in selector
        ):
            return [FakeControl(self.subject)]
        if "contenteditable" in selector:
            return [FakeControl(self.body)]
        if 'role="dialog"' in selector:
            return [FakeControl("")]
        return []


class FakePage:
    def __init__(self, provider: FakeComposeProvider) -> None:
        self.provider = provider
        self.url = "https://mail.google.com/mail/u/0/#inbox"

    def locator(self, selector: str):
        return FakeLocator(self.provider, selector)


class FakeLocator:
    def __init__(self, provider: FakeComposeProvider, selector: str) -> None:
        self.provider = provider
        self.selector = selector

    def locator(self, selector: str):
        return FakeLocator(self.provider, selector)

    def count(self) -> int:
        return len(self.provider.matches(self.selector))

    def nth(self, index: int) -> FakeControl:
        return self.provider.matches(self.selector)[index]

    @property
    def last(self):
        count = self.count()
        if count:
            return self.nth(count - 1)
        return self

    @property
    def first(self):
        if self.count():
            return self.nth(0)
        return self

    def wait_for(self, state: str = "visible", timeout: int = 0) -> None:
        return None

    def is_visible(self) -> bool:
        return self.count() > 0

    def input_value(self, timeout: int = 0) -> str:
        values = self.provider.matches(self.selector)
        return values[0].input_value(timeout) if values else ""

    def inner_text(self, timeout: int = 0) -> str:
        values = self.provider.matches(self.selector)
        return values[0].inner_text(timeout) if values else ""

    def text_content(self, timeout: int = 0) -> str:
        values = self.provider.matches(self.selector)
        return values[0].text_content(timeout) if values else ""

    def get_attribute(self, name: str, timeout: int = 0) -> str | None:
        values = self.provider.matches(self.selector)
        return values[0].get_attribute(name, timeout) if values else None

    def all_inner_texts(self) -> list[str]:
        return [
            control.inner_text()
            for control in self.provider.matches(self.selector)
        ]


class GmailVerificationTests(unittest.TestCase):
    def _verify(self, provider: FakeComposeProvider, *expected) -> dict:
        page = FakePage(provider)
        with mock.patch.object(
            browser_module, "_compose_page", return_value=page
        ), mock.patch.object(
            browser_module,
            "_compose_scope",
            return_value=page.locator("scope"),
        ):
            return browser_module.verify_draft_fields(
                mock.Mock(),
                *expected,
            )

    def test_recipient_in_input_box_passes(self) -> None:
        result = self._verify(
            FakeComposeProvider(
                recipient_input="bettyhsu707@gmail.com",
                subject="Hello",
                body="Body",
            ),
            "bettyhsu707@gmail.com",
            "Hello",
            "Body",
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["recipient"]["ok"])

    def test_recipient_chip_passes(self) -> None:
        chip = FakeControl(
            "bettyhsu707@gmail.com",
            {"data-hovercard-id": "bettyhsu707@gmail.com"},
        )
        result = self._verify(
            FakeComposeProvider(subject="Hello", body="Body", chips=[chip]),
            "bettyhsu707@gmail.com",
            "Hello",
            "Body",
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["recipient"]["ok"])

    def test_recipient_from_data_hovercard_id_passes(self) -> None:
        chip = FakeControl(
            "Betty",
            {"data-hovercard-id": "mailto:bettyhsu707@gmail.com"},
        )
        result = self._verify(
            FakeComposeProvider(subject="Hi", body="Body", chips=[chip]),
            "bettyhsu707@gmail.com",
            "Hi",
            "Body",
        )
        self.assertTrue(result["ok"])

    def test_recipient_from_aria_label_passes(self) -> None:
        chip = FakeControl(
            "Betty",
            {"aria-label": "Remove Betty bettyhsu707@gmail.com"},
        )
        result = self._verify(
            FakeComposeProvider(subject="Hi", body="Body", chips=[chip]),
            "bettyhsu707@gmail.com",
            "Hi",
            "Body",
        )
        self.assertTrue(result["ok"])

    def test_multiple_recipients_compare_as_set(self) -> None:
        chips = [
            FakeControl("a@example.com"),
            FakeControl("b@example.com", {"data-email": "b@example.com"}),
        ]
        result = self._verify(
            FakeComposeProvider(subject="Hi", body="Body", chips=chips),
            "a@example.com, b@example.com",
            "Hi",
            "Body",
        )
        self.assertTrue(result["ok"])
        self.assertEqual(
            result["recipient"]["actual"],
            ["a@example.com", "b@example.com"],
        )

    def test_subject_mismatch_blocks_send(self) -> None:
        result = self._verify(
            FakeComposeProvider(
                recipient_input="a@example.com",
                subject="Wrong",
                body="Body",
            ),
            "a@example.com",
            "Right",
            "Body",
        )
        self.assertFalse(result["ok"])
        self.assertFalse(result["subject"]["ok"])
        self.assertTrue(result["recipient"]["ok"])

    def test_body_whitespace_and_nbsp_differences_pass(self) -> None:
        result = self._verify(
            FakeComposeProvider(
                recipient_input="a@example.com",
                subject="Hi",
                body="Hi\u00a0 Bob  \n\n  Alice",
            ),
            "a@example.com",
            "Hi",
            "Hi   Bob\n\nAlice",
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["body"]["ok"])

    def test_missing_body_blocks_send(self) -> None:
        result = self._verify(
            FakeComposeProvider(
                recipient_input="a@example.com",
                subject="Hi",
            ),
            "a@example.com",
            "Hi",
            "Body",
        )
        self.assertFalse(result["ok"])
        self.assertFalse(result["body"]["ok"])
        self.assertTrue(result["unreadable"])

    def test_verify_result_is_structured(self) -> None:
        result = self._verify(
            FakeComposeProvider(
                recipient_input="a@example.com",
                subject="Hi",
                body="Body",
            ),
            "a@example.com",
            "Hi",
            "Body",
        )
        for key in ("recipient", "subject", "body"):
            self.assertIn("ok", result[key])
        self.assertIn("expected_hash", result["body"])
        self.assertIn("actual_hash", result["body"])
        self.assertIn("summary", result["body"])


class ValidationSendGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self._patch = mock.patch(
            "ophelia_assistant.database.app_data_dir",
            return_value=Path(self._temp.name),
        )
        self._patch.start()
        self.db = Database()
        self.workflow = Workflow(self.db, Settings())
        self.task_id = self.db.add_local_task(
            "Alex", "Seattle", "alex@example.com"
        )
        self.db.update_task(
            self.task_id,
            profile_no=7,
            subject="Hello",
            body="Body",
            status="generated",
        )

    def tearDown(self) -> None:
        self._patch.stop()
        self._temp.cleanup()

    def test_validation_failure_never_clicks_send_and_is_retryable(self) -> None:
        with mock.patch(
            "ophelia_assistant.workflow.prepare_gmail_draft",
            return_value=100,
        ), mock.patch(
            "ophelia_assistant.workflow.verify_draft_fields",
            return_value={
                "ok": False,
                "unreadable": False,
                "recipient": {
                    "ok": False,
                    "expected_display": "alex@example.com",
                    "actual_display": "other@example.com",
                },
                "subject": {"ok": True},
                "body": {"ok": True},
            },
        ), mock.patch(
            "ophelia_assistant.workflow.click_gmail_send"
        ) as click_send, mock.patch(
            "ophelia_assistant.workflow.save_failure_screenshot",
            return_value="",
        ):
            with self.assertRaisesRegex(
                BrowserAutomationError, "收件人不一致"
            ):
                self.workflow.open_and_send_on_browser(
                    self.task_id,
                    mock.Mock(),
                )
        click_send.assert_not_called()
        row = self.db.get_task(self.task_id)
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["failure_stage"], "validate")
        self.assertFalse(row["send_clicked_at"])


if __name__ == "__main__":
    unittest.main()
