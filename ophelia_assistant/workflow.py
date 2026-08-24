from __future__ import annotations

import json
import threading
from typing import Callable

from .browser import (
    BrowserAutomationError,
    connected_browser,
    prepare_gmail_draft,
    verify_draft_fields,
    wait_for_gmail_send,
)
from .config import Settings
from .database import Database, now_iso
from .mail_content import city_only, has_city, render_email, salutation_name
from .morelogin import create_browser_provider
from .operation import check_cancel


class Workflow:
    """Generate email content locally and place it in a Gmail draft."""

    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self.browser_provider = create_browser_provider(settings)

    def generate_local(
        self, task_id: int, cancel_event: threading.Event | None = None
    ) -> None:
        check_cancel(cancel_event)
        task = self._task(task_id)
        name = str(
            task["name_override"]
            or " ".join(
                value for value in (task["first_name"], task["last_name"]) if value
            )
        ).strip()
        location = city_only(task["location_override"] or task["location"])
        self._save_local_email(task_id, name, location, cancel_event)

    def apply_manual_profile(self, task_id: int, name: str, location: str) -> None:
        task = self._task(task_id)
        clean_name = name.strip() or str(
            task["name_override"]
            or " ".join(
                value for value in (task["first_name"], task["last_name"]) if value
            )
        ).strip()
        clean_location = city_only(
            location.strip() or str(task["location_override"] or task["location"] or "")
        )
        self._save_local_email(task_id, clean_name, clean_location)

    def _save_local_email(
        self,
        task_id: int,
        name: str,
        location: str,
        cancel_event: threading.Event | None = None,
    ) -> None:
        task = self._task(task_id)
        clean_name = " ".join(str(name or "").strip().split())
        clean_location = city_only(location)
        hidden = set(self.settings.hidden_system_variables)
        if not clean_name and "first_name" not in hidden:
            raise ValueError("请填写联系人名字")
        if not has_city(clean_location) and "location" not in hidden:
            raise ValueError("请填写有效的城市或城市地区")
        contact_name = salutation_name(clean_name)
        task_custom: dict[str, str] = {}
        try:
            raw_custom = task["custom_variables"] or ""
        except (KeyError, IndexError):
            raw_custom = ""
        if raw_custom:
            try:
                parsed_custom = json.loads(raw_custom)
            except ValueError:
                parsed_custom = None
            if isinstance(parsed_custom, dict):
                task_custom = {
                    str(key): str(value)
                    for key, value in parsed_custom.items()
                    if str(value)
                }
        custom_variables = dict(self.settings.custom_variables)
        custom_variables.update(task_custom)
        subject, body = render_email(
            contact_name,
            clean_location,
            self.settings.sender_name,
            self.settings.subject_template,
            self.settings.body_template,
            custom_variables,
        )
        check_cancel(cancel_event)
        self.db.update_task(
            task_id,
            name_override=clean_name,
            location_override=clean_location,
            location=clean_location,
            location_source="manual",
            subject=subject,
            body=body,
            source_urls="[]",
            review_reason="",
            status="ready",
            generated_at=now_iso(),
            last_error="",
        )

    def open_draft(
        self,
        task_id: int,
        progress: Callable[[int, str], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> int:
        check_cancel(cancel_event)
        task = self._sendable_task(task_id)
        if int(task["profile_no"] or 0) <= 0:
            raise ValueError("请在邮件预览下方选择并确认浏览器窗口")
        conn = self.browser_provider.start_profile(task["profile_no"])
        check_cancel(cancel_event)
        with connected_browser(conn.cdp_url) as (_, browser):
            try:
                accuracy = prepare_gmail_draft(
                    browser,
                    task["recipient_email"],
                    task["subject"],
                    task["body"],
                    progress,
                    cancel_event,
                )
            except Exception as exc:
                self._record_failure(task_id, exc)
                raise
        check_cancel(cancel_event)
        self.db.update_task(
            task_id,
            status="drafted",
            drafted_at=now_iso(),
            last_error="",
        )
        return accuracy

    def open_draft_wait_send(
        self,
        task_id: int,
        progress: Callable[[int, str], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> int:
        """Fill the Gmail draft, verify it, then wait until the user sends it."""
        check_cancel(cancel_event)
        task = self._sendable_task(task_id)
        if int(task["profile_no"] or 0) <= 0:
            raise ValueError("请在邮件预览下方选择并确认浏览器窗口")
        conn = self.browser_provider.start_profile(task["profile_no"])
        check_cancel(cancel_event)
        with connected_browser(conn.cdp_url) as (_, browser):
            try:
                accuracy = prepare_gmail_draft(
                    browser,
                    task["recipient_email"],
                    task["subject"],
                    task["body"],
                    progress,
                    cancel_event,
                )
                if not verify_draft_fields(
                    browser,
                    task["recipient_email"],
                    task["subject"],
                    task["body"],
                ):
                    raise BrowserAutomationError(
                        "草稿校验未通过：Gmail 中的收件人/主题/正文与任务不一致，请手动核对"
                    )
                wait_for_gmail_send(browser, cancel_event=cancel_event)
            except Exception as exc:
                self._record_failure(task_id, exc)
                raise
        self.db.update_task(
            task_id,
            status="sent",
            sent_at=now_iso(),
            drafted_at=now_iso(),
            last_error="",
        )
        return accuracy

    def _record_failure(self, task_id: int, exc: Exception) -> None:
        try:
            row = self.db.get_task(task_id)
            attempts = int(row["attempts"] or 0) if row is not None else 0
            self.db.update_task(
                task_id,
                last_error=str(exc),
                attempts=attempts + 1,
            )
        except Exception:
            pass

    def _task(self, task_id: int):
        task = self.db.get_task(task_id)
        if task is None:
            raise ValueError("任务不存在")
        return task

    def _sendable_task(self, task_id: int):
        task = self._task(task_id)
        if not task["subject"] or not task["body"]:
            raise ValueError("请先在本地生成邮件预览")
        if task["status"] in {"sent", "replied"}:
            raise ValueError("该任务已经标记为已发送")
        return task
