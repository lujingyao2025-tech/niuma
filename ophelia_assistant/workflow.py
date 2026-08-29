from __future__ import annotations

import json
import hashlib
import logging
import re
import threading
from typing import Callable

from .browser import (
    BrowserAutomationError,
    click_gmail_send,
    connected_browser,
    gmail_alert_baseline,
    prepare_gmail_draft,
    save_failure_screenshot,
    verify_draft_fields,
    wait_for_gmail_alerts_clear,
    wait_for_gmail_send,
)
from .config import Settings
from .database import Database, now_iso
from .diagnostics import (
    recent_trail,
    trace_execution,
    write_error_report,
)
from .mail_content import city_only, has_city, render_email, salutation_name
from .morelogin import create_browser_provider
from .operation import OperationCancelledError, check_cancel


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

STAGE_LABELS = {
    "connect": "无法连接浏览器窗口",
    "login": "浏览器窗口未登录Gmail",
    "compose": "找不到Compose按钮",
    "fill_recipient": "收件人填写失败",
    "fill_subject": "主题填写失败",
    "fill_body": "正文填写失败",
    "validate": "Gmail字段校验不一致",
    "send_button": "找不到Send按钮",
    "click_send": "点击Send失败",
    "wait_send": "等待发送成功提示超时",
    "send_failed": "Gmail提示发送失败",
    "network": "网络或浏览器连接中断",
    "cancelled": "用户取消任务",
}

SENDER_SOURCE_LABELS = {
    "task": "任务指定",
    "window": "窗口锁定",
    "template": "模板",
    "default": "软件默认",
}


def _row_value(row, key: str, default: str = "") -> str:
    """Read a database row field that may be a sqlite3.Row or a dict."""
    try:
        return str(row[key] or default)
    except (KeyError, IndexError, TypeError):
        return default


def _draft_failure_stage(message: str) -> str:
    text = str(message or "")
    if "登录" in text:
        return "login"
    if "写信" in text or "Compose" in text:
        return "compose"
    if "收件" in text or "To" in text:
        return "fill_recipient"
    if "主题" in text or "Subject" in text:
        return "fill_subject"
    if "正文" in text or "Message Body" in text:
        return "fill_body"
    if "连接" in text or "网络" in text or "timeout" in text.lower():
        return "network"
    return "compose"


class Workflow:
    """Generate email content locally, fill Gmail and optionally auto-send."""

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
        self._save_local_email(
            task_id,
            name,
            location,
            cancel_event=cancel_event,
        )

    def apply_manual_profile(
        self, task_id: int, name: str, location: str, sender_name: str = ""
    ) -> None:
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
        self._save_local_email(task_id, clean_name, clean_location, sender_name)

    def _save_local_email(
        self,
        task_id: int,
        name: str,
        location: str,
        sender_name: str = "",
        cancel_event: threading.Event | None = None,
    ) -> None:
        task = self._task(task_id)
        clean_name = " ".join(str(name or "").strip().split())
        clean_location = city_only(location)
        clean_sender = " ".join(str(sender_name or "").strip().split())
        if not clean_sender:
            clean_sender = _row_value(task, "sender_name_override").strip()
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
        resolved_sender, sender_source = self._resolve_sender(task)
        subject, body = self._render_email_for_task(
            task,
            contact_name,
            clean_location,
            custom_variables,
            resolved_sender=resolved_sender,
        )
        check_cancel(cancel_event)
        self.db.update_task(
            task_id,
            name_override=clean_name,
            location_override=clean_location,
            location=clean_location,
            location_source="manual",
            sender_name_override=clean_sender,
            resolved_sender_name=resolved_sender,
            sender_name_source=sender_source,
            render_context_hash=self._render_context_hash(task),
            subject=subject,
            body=body,
            source_urls="[]",
            review_reason="",
            status="generated",
            generated_at=now_iso(),
            last_error="",
            failure_stage="",
        )

    def _draft_template(self, task) -> tuple[dict | None, dict]:
        """Return (window-bound template, window binding) for a task."""
        binding = (self.settings.window_bindings or {}).get(
            str(task["profile_no"])
        ) or {}
        template = None
        template_name = binding.get("template_name")
        if template_name:
            template = next(
                (
                    item
                    for item in self.settings.saved_templates
                    if item.get("name") == template_name
                ),
                None,
            )
        if template is None and getattr(self.settings, "active_template_name", ""):
            template = next(
                (
                    item
                    for item in self.settings.saved_templates
                    if item.get("name") == self.settings.active_template_name
                ),
                None,
            )
        return template, binding

    def _render_email_for_task(
        self,
        task,
        contact_name: str,
        location: str,
        custom_variables: dict[str, str],
        resolved_sender: str | None = None,
    ) -> tuple[str, str]:
        """Render with the window-bound template first, then the active template."""
        template, binding = self._draft_template(task)
        task_sender = _row_value(task, "sender_name_override").strip()
        if template:
            subject_template = (
                template.get("subject_template")
                or self.settings.subject_template
            )
            body_template = template.get("body_template") or self.settings.body_template
            sender = (
                resolved_sender
                or task_sender
                or binding.get("sender_name")
                or template.get("sender_name")
                or self.settings.sender_name
            )
            signature = template.get("signature") or self.settings.signature
            custom = dict(custom_variables)
            custom.update(template.get("custom_variables") or {})
        else:
            subject_template = self.settings.subject_template
            body_template = self.settings.body_template
            sender = (
                resolved_sender
                or task_sender
                or binding.get("sender_name")
                or self.settings.sender_name
            )
            signature = self.settings.signature
            custom = dict(custom_variables)
        subject, body = render_email(
            contact_name,
            location,
            sender,
            subject_template,
            body_template,
            custom,
        )
        if signature:
            body = body.rstrip() + "\n\n" + signature.strip()
        return subject, body

    def _resolve_sender(self, task) -> tuple[str, str]:
        """Priority: task > locked window > template > default."""
        task_sender = _row_value(task, "sender_name_override").strip()
        if task_sender:
            return task_sender, "task"
        profile = str(task["profile_no"] or "")
        binding = (self.settings.window_bindings or {}).get(profile) or {}
        window_sender = str(binding.get("sender_name") or "").strip()
        if binding.get("locked") and window_sender:
            return window_sender, "window"
        template, _binding = self._draft_template(task)
        template_sender = ""
        if template:
            template_sender = str(template.get("sender_name") or "").strip()
        if template_sender:
            return template_sender, "template"
        default_sender = str(getattr(self.settings, "sender_name", "") or "").strip()
        return default_sender, "default"

    def _render_context_hash(self, task) -> str:
        """Hash everything that affects rendered subject/body/sender."""
        profile = str(task["profile_no"] or "")
        template, _binding = self._draft_template(task)
        template_payload: dict = {}
        if template:
            template_payload = {
                "name": template.get("name"),
                "subject": template.get("subject_template"),
                "body": template.get("body_template"),
                "signature": template.get("signature"),
                "sender": template.get("sender_name"),
            }
        sender, source = self._resolve_sender(task)
        payload = {
            "profile": profile,
            "template": template_payload,
            "subject_template": self.settings.subject_template,
            "body_template": self.settings.body_template,
            "signature": self.settings.signature,
            "custom_variables": self.settings.custom_variables,
            "task_sender_override": _row_value(task, "sender_name_override"),
            "resolved_sender": sender,
            "sender_source": source,
        }
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def open_draft(
        self,
        task_id: int,
        progress: Callable[[int, str], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> int:
        """Fill the Gmail compose without sending (manual confirmation mode)."""
        check_cancel(cancel_event)
        task = self._sendable_task(task_id)
        if int(task["profile_no"] or 0) <= 0:
            raise ValueError("请在邮件预览下方选择并确认浏览器窗口")
        self.db.update_task(
            task_id,
            status="filling",
            fill_started_at=now_iso(),
            browser_type=self.settings.browser_provider,
            last_error="",
            failure_stage="",
        )
        trace_execution(
            task_id, "start", "开始执行", profile_no=task["profile_no"]
        )
        conn = self.browser_provider.start_profile(task["profile_no"])
        check_cancel(cancel_event)
        subject, body = str(task["subject"] or ""), str(task["body"] or "")
        with connected_browser(conn.cdp_url) as (_, browser):
            try:
                accuracy = prepare_gmail_draft(
                    browser,
                    task["recipient_email"],
                    subject,
                    body,
                    progress,
                    cancel_event,
                )
            except OperationCancelledError:
                self._mark_cancelled(task_id)
                raise
            except Exception as exc:
                stage = _draft_failure_stage(str(exc))
                self._record_failure(task_id, exc, stage)
                save_failure_screenshot(
                    browser, task_id, task["profile_no"], stage
                )
                raise
        check_cancel(cancel_event)
        self.db.update_task(
            task_id,
            status="drafted",
            drafted_at=now_iso(),
            last_error="",
            failure_stage="",
        )
        trace_execution(
            task_id, "filled", "Gmail 草稿填写完成",
            profile_no=task["profile_no"],
        )
        return accuracy

    def open_and_send(
        self,
        task_id: int,
        progress: Callable[[int, str], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> int:
        """Fill, verify, click Send and wait for a confirmed result."""
        check_cancel(cancel_event)
        task = self._sendable_task(task_id)
        if int(task["profile_no"] or 0) <= 0:
            raise ValueError("请在邮件预览下方选择并确认浏览器窗口")
        subject, body = str(task["subject"] or ""), str(task["body"] or "")
        resolved_sender, sender_source = self._resolve_sender(task)
        self.db.update_task(
            task_id,
            status="assigned",
            assigned_at=now_iso(),
            resolved_sender_name=resolved_sender,
            sender_name_source=sender_source,
            window_assignment_type=(
                _row_value(task, "window_assignment_type") or "auto"
            ),
            browser_type=self.settings.browser_provider,
            last_error="",
            failure_stage="",
        )
        self.db.update_task(
            task_id,
            status="filling",
            fill_started_at=now_iso(),
        )
        trace_execution(
            task_id, "start", "开始自动发送", profile_no=task["profile_no"]
        )
        try:
            conn = self.browser_provider.start_profile(task["profile_no"])
        except OperationCancelledError:
            self._mark_cancelled(task_id)
            raise
        except Exception as exc:
            self._record_failure(task_id, exc, "connect")
            raise
        check_cancel(cancel_event)
        with connected_browser(conn.cdp_url) as (_, browser):
            try:
                accuracy = prepare_gmail_draft(
                    browser,
                    task["recipient_email"],
                    subject,
                    body,
                    progress,
                    cancel_event,
                )
            except OperationCancelledError:
                self._mark_cancelled(task_id)
                raise
            except Exception as exc:
                stage = _draft_failure_stage(str(exc))
                self._record_failure(task_id, exc, stage)
                save_failure_screenshot(
                    browser, task_id, task["profile_no"], stage
                )
                raise
            self.db.update_task(task_id, status="drafted", drafted_at=now_iso())
            trace_execution(
                task_id, "filled", "Gmail 草稿填写完成",
                profile_no=task["profile_no"],
            )
            try:
                self.db.update_task(
                    task_id,
                    status="validating",
                    validation_at=now_iso(),
                )
                if not verify_draft_fields(
                    browser,
                    task["recipient_email"],
                    subject,
                    body,
                ):
                    raise BrowserAutomationError(
                        "草稿校验未通过：Gmail 中的收件人/主题/正文与任务不一致，请手动核对"
                    )
                trace_execution(
                    task_id, "verified", "Gmail 字段完整校验通过",
                    profile_no=task["profile_no"],
                )
            except OperationCancelledError:
                self._mark_cancelled(task_id)
                raise
            except Exception as exc:
                self._record_failure(task_id, exc, "validate")
                save_failure_screenshot(
                    browser, task_id, task["profile_no"], "validate"
                )
                raise
            auto_send = getattr(self.settings, "auto_click_send", True)
            if auto_send:
                baseline = gmail_alert_baseline(browser)
                try:
                    wait_for_gmail_alerts_clear(
                        browser, baseline=baseline, cancel_event=cancel_event
                    )
                except OperationCancelledError:
                    self._mark_cancelled(task_id)
                    raise
                except Exception as exc:
                    self._record_failure(task_id, exc, "stale_toast")
                    save_failure_screenshot(
                        browser, task_id, task["profile_no"], "stale_toast"
                    )
                    raise
                self.db.update_task(
                    task_id,
                    status="sending",
                    send_attempt_started_at=now_iso(),
                )
                try:
                    click_gmail_send(browser, cancel_event=cancel_event)
                    self.db.update_task(
                        task_id,
                        send_clicked_at=now_iso(),
                    )
                except OperationCancelledError:
                    self._mark_cancelled(task_id)
                    raise
                except Exception as exc:
                    stage = (
                        "send_button"
                        if "发送按钮" in str(exc)
                        else "click_send"
                    )
                    if stage == "send_button":
                        self._record_failure(task_id, exc, stage)
                    else:
                        # Click outcome is unknown: never allow auto-retry.
                        self.db.update_task(
                            task_id,
                            send_clicked_at=now_iso(),
                        )
                        self._mark_needs_review(task_id, exc, stage)
                    save_failure_screenshot(
                        browser, task_id, task["profile_no"], stage
                    )
                    raise
                trace_execution(
                    task_id, "send_clicked", "已点击 Gmail 发送按钮",
                    profile_no=task["profile_no"],
                )
                # In-flight sends must finish: do not cancel while waiting.
                try:
                    wait_for_gmail_send(browser)
                except BrowserAutomationError as exc:
                    stage = (
                        "wait_send"
                        if "超时" in str(exc)
                        else "send_failed"
                    )
                    if stage == "wait_send":
                        self._mark_needs_review(task_id, exc, stage)
                    else:
                        self._record_failure(task_id, exc, stage)
                    save_failure_screenshot(
                        browser, task_id, task["profile_no"], stage
                    )
                    raise
            else:
                wait_for_gmail_send(browser, cancel_event=cancel_event)
        self.db.update_task(
            task_id,
            status="sent",
            sent_at=now_iso(),
            drafted_at=now_iso(),
            sent_method="confirmed",
            needs_manual_review=0,
            last_error="",
            failure_stage="",
        )
        trace_execution(
            task_id, "sent", "Gmail 已确认发送成功",
            profile_no=task["profile_no"],
        )
        return accuracy

    def _mark_needs_review(self, task_id: int, exc: Exception, stage: str) -> None:
        logging.getLogger("niuma-mail").warning(
            "任务 %s 结果不明确（%s）：%s", task_id, stage, exc
        )
        try:
            self.db.update_task(
                task_id,
                status="needs_review",
                needs_manual_review=1,
                last_error=str(exc),
                failure_stage=stage,
                last_failed_at=now_iso(),
            )
        except Exception:
            pass

    def open_draft_wait_send(
        self,
        task_id: int,
        progress: Callable[[int, str], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> int:
        """Backward-compatible entry: fill and send (or wait) per settings."""
        return self.open_and_send(
            task_id, progress=progress, cancel_event=cancel_event
        )

    def _mark_cancelled(self, task_id: int) -> None:
        try:
            row = self.db.get_task(task_id)
            profile = int(row["profile_no"]) if row is not None else None
            trace_execution(
                task_id, "cancelled", "用户取消任务", profile_no=profile
            )
            self.db.update_task(
                task_id,
                status="cancelled",
                last_error="用户取消任务",
                failure_stage="cancelled",
                last_failed_at=now_iso(),
            )
        except Exception:
            pass

    def _record_failure(self, task_id: int, exc: Exception, stage: str = "") -> None:
        logging.getLogger("niuma-mail").error(
            "任务 %s 执行失败（%s）：%s",
            task_id,
            stage or "unknown",
            exc,
            exc_info=True,
        )
        try:
            row = self.db.get_task(task_id)
            profile = int(row["profile_no"]) if row is not None else None
            context = {}
            if row is not None:
                context = {
                    "联系人邮箱": _row_value(row, "recipient_email"),
                    "联系人姓名": (
                        _row_value(row, "name_override")
                        or _row_value(row, "first_name")
                    ),
                    "浏览器类型": (
                        _row_value(row, "browser_type")
                        or self.settings.browser_provider
                    ),
                    "窗口编号": profile,
                    "窗口分配方式": _row_value(row, "window_assignment_type"),
                    "实际发件人姓名": _row_value(row, "resolved_sender_name"),
                    "发件人姓名来源": _row_value(row, "sender_name_source"),
                }
            trace_execution(
                task_id,
                stage or "unknown",
                str(exc),
                profile_no=profile,
                extra={"exception": type(exc).__name__, **context},
            )
            write_error_report(
                exc,
                task_id=task_id,
                stage=stage,
                profile_no=profile,
                settings=self.settings,
                extra_trail=recent_trail(task_id, limit=30),
                context=context,
            )
            attempts = int(row["attempts"] or 0) if row is not None else 0
            self.db.update_task(
                task_id,
                status="failed",
                last_error=str(exc),
                failure_stage=stage,
                last_failed_at=now_iso(),
                browser_type=self.settings.browser_provider,
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
        email = str(task["recipient_email"] or "").strip()
        if not EMAIL_RE.fullmatch(email):
            raise ValueError("收件邮箱格式错误，禁止发送")
        if not task["subject"] or not task["body"]:
            raise ValueError("请先在本地生成邮件预览")
        if task["status"] in {"sent", "replied"}:
            raise ValueError("该任务已经标记为已发送，禁止重复发送")
        if _row_value(task, "send_clicked_at"):
            raise ValueError(
                "该任务已进入发送流程且结果不明确，禁止直接重发；请先人工确认 Gmail"
            )
        return task
