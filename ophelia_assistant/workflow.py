from __future__ import annotations

import json
import hashlib
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from .batch import group_tasks_by_window
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
from .config import MAX_CONCURRENT_TASKS, Settings
from .database import Database, now_iso
from .diagnostics import (
    recent_trail,
    trace_execution,
    write_error_report,
)
from .mail_content import city_only, has_city, render_email, salutation_name
from .morelogin import create_browser_provider
from .operation import OperationCancelledError, check_cancel
from .timing import StageTimer


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
    "stale_toast": "旧发送提示未消失",
    "resolve_sender": "解析发件人失败",
    "render": "本地渲染失败",
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
        values = self._render_local_for_row(
            dict(task),
            name_override=name,
            location_override=location,
            sender_name_override=sender_name,
        )
        check_cancel(cancel_event)
        self.db.update_task(task_id, **values)

    def _render_local_for_row(
        self,
        task,
        name_override: str = "",
        location_override: str = "",
        sender_name_override: str = "",
        template_index: dict[str, dict] | None = None,
    ) -> dict:
        """Render one task locally and return all DB fields for it."""
        clean_name = " ".join(
            str(
                name_override
                or _row_value(task, "name_override")
                or ""
            ).strip().split()
        )
        raw_location = str(
            location_override
            or _row_value(task, "location_override")
            or _row_value(task, "location")
            or ""
        )
        clean_location = city_only(raw_location)
        clean_sender = " ".join(
            str(
                sender_name_override
                or _row_value(task, "sender_name_override")
                or ""
            ).strip().split()
        )
        hidden = set(self.settings.hidden_system_variables)
        if not clean_name and "first_name" not in hidden:
            raise ValueError("请填写联系人名字")
        if not has_city(clean_location) and "location" not in hidden:
            raise ValueError("请填写有效的城市或城市地区")
        contact_name = salutation_name(clean_name)
        task_custom: dict[str, str] = {}
        raw_custom = _row_value(task, "custom_variables")
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
        resolved_sender, sender_source = self._resolve_sender(
            task, template_index
        )
        subject, body = self._render_email_for_task(
            task,
            contact_name,
            clean_location,
            custom_variables,
            resolved_sender=resolved_sender,
            template_index=template_index,
        )
        return {
            "name_override": clean_name,
            "location_override": clean_location,
            "location": clean_location,
            "location_source": "manual",
            "sender_name_override": clean_sender,
            "resolved_sender_name": resolved_sender,
            "sender_name_source": sender_source,
            "render_context_hash": self._render_context_hash(
                task, template_index
            ),
            "subject": subject,
            "body": body,
            "source_urls": "[]",
            "review_reason": "",
            "status": "generated",
            "generated_at": now_iso(),
            "last_error": "",
            "failure_stage": "",
            "stage_timings_json": "{}",
            "verify_result_json": "{}",
        }

    def generate_local_batch(
        self,
        task_ids: list[int],
        cancel_event: threading.Event | None = None,
        progress: Callable[[int, str], None] | None = None,
        task_status: Callable[[str, str, str, str], None] | None = None,
    ) -> dict:
        """Render many emails in memory, then persist them in one transaction."""
        ids = list(dict.fromkeys(int(task_id) for task_id in task_ids))
        if not ids:
            return {"total": 0, "success": 0, "failed": 0, "completed": 0}
        rows = [dict(row) for row in self.db.get_tasks(ids)]
        template_index = {
            str(item.get("name") or ""): item
            for item in self.settings.saved_templates
        }
        total = len(rows)
        updates: dict[int, dict] = {}
        success = 0
        failed = 0
        if progress is not None:
            progress(0, f"正在生成 0/{total}")
        for row in rows:
            check_cancel(cancel_event)
            task_id = int(row["id"])
            email = str(row.get("recipient_email") or "")
            if task_status is not None:
                task_status("", email, "生成中", "处理中")
            try:
                values = self._render_local_for_row(
                    row,
                    template_index=template_index,
                )
                values["attempts"] = int(row.get("attempts") or 0)
                updates[task_id] = values
                success += 1
            except Exception as exc:
                logging.getLogger("niuma-mail").warning(
                    "任务 %s 本地渲染失败：%s", task_id, exc
                )
                updates[task_id] = {
                    "status": "failed",
                    "last_error": str(exc),
                    "failure_stage": "render",
                    "last_failed_at": now_iso(),
                    "attempts": int(row.get("attempts") or 0) + 1,
                }
                failed += 1
            done = success + failed
            if progress is not None:
                percent = int(done * 100 / total) if total else 100
                progress(
                    percent,
                    f"正在生成 {done}/{total}：{email}",
                )
        self.db.update_tasks_batch(updates)
        if progress is not None:
            progress(100, f"生成完成：成功 {success} · 失败 {failed}")
        return {
            "total": total,
            "success": success,
            "failed": failed,
            "completed": success + failed,
        }

    def _draft_template(
        self,
        task,
        template_index: dict[str, dict] | None = None,
    ) -> tuple[dict | None, dict]:
        """Return (window-bound template, window binding) for a task."""
        profile_no = _row_value(task, "profile_no", "0")
        binding = self._active_window_binding(profile_no)
        if template_index is None:
            template_index = {
                str(item.get("name") or ""): item
                for item in self.settings.saved_templates
            }
        template = None
        template_name = binding.get("template_name")
        if template_name:
            template = template_index.get(str(template_name))
            if template is None:
                raise ValueError(
                    f"模板已删除/失效：{template_name}；请在窗口绑定中重新选择模板"
                )
        if template is None and getattr(self.settings, "active_template_name", ""):
            template = template_index.get(
                str(self.settings.active_template_name or "")
            )
        return template, binding

    def _active_window_binding(self, profile_no) -> dict:
        """Return a binding only for windows present in the current sequence."""
        try:
            number = int(profile_no)
        except (TypeError, ValueError):
            return {}
        if number <= 0:
            return {}
        active = {int(window) for window in self.settings.window_sequence}
        if number not in active:
            return {}
        binding = (self.settings.window_bindings or {}).get(str(number))
        return binding if isinstance(binding, dict) else {}

    def _render_email_for_task(
        self,
        task,
        contact_name: str,
        location: str,
        custom_variables: dict[str, str],
        resolved_sender: str | None = None,
        template_index: dict[str, dict] | None = None,
    ) -> tuple[str, str]:
        """Render with the window-bound template first, then the active template."""
        template, binding = self._draft_template(task, template_index)
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

    def _resolve_sender(
        self,
        task,
        template_index: dict[str, dict] | None = None,
    ) -> tuple[str, str]:
        """Priority: task > locked window > template > default."""
        task_sender = _row_value(task, "sender_name_override").strip()
        if task_sender:
            return task_sender, "task"
        profile = str(_row_value(task, "profile_no", "0") or "")
        binding = self._active_window_binding(profile)
        window_sender = str(binding.get("sender_name") or "").strip()
        if binding.get("locked") and window_sender:
            return window_sender, "window"
        template, _binding = self._draft_template(task, template_index)
        template_sender = ""
        if template:
            template_sender = str(template.get("sender_name") or "").strip()
        if template_sender:
            return template_sender, "template"
        default_sender = str(getattr(self.settings, "sender_name", "") or "").strip()
        return default_sender, "default"

    def _render_context_hash(
        self,
        task,
        template_index: dict[str, dict] | None = None,
    ) -> str:
        """Hash everything that affects rendered subject/body/sender."""
        profile = str(_row_value(task, "profile_no", "0") or "")
        template, _binding = self._draft_template(task, template_index)
        template_payload: dict = {}
        if template:
            template_payload = {
                "name": template.get("name"),
                "subject": template.get("subject_template"),
                "body": template.get("body_template"),
                "signature": template.get("signature"),
                "sender": template.get("sender_name"),
            }
        sender, source = self._resolve_sender(task, template_index)
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
        timer = StageTimer()
        timer.begin("get_window")
        try:
            conn = self.browser_provider.start_profile(task["profile_no"])
        except OperationCancelledError:
            self._mark_cancelled(task_id)
            raise
        except Exception as exc:
            timer.stop_all()
            self._record_failure(task_id, exc, "connect", timer=timer)
            raise
        timer.stop("get_window")
        check_cancel(cancel_event)
        timer.begin("connect")
        try:
            with connected_browser(conn.cdp_url) as (_, browser):
                timer.stop("connect")
                return self.open_draft_on_browser(
                    task_id,
                    browser,
                    progress=progress,
                    cancel_event=cancel_event,
                    timer=timer,
                )
        finally:
            timer.stop_all()

    def open_draft_on_browser(
        self,
        task_id: int,
        browser,
        progress: Callable[[int, str], None] | None = None,
        cancel_event: threading.Event | None = None,
        timer: StageTimer | None = None,
    ) -> int:
        """Fill the compose using an already connected browser window."""
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
        subject, body = str(task["subject"] or ""), str(task["body"] or "")
        try:
            accuracy = prepare_gmail_draft(
                browser,
                task["recipient_email"],
                subject,
                body,
                progress,
                cancel_event,
                timer,
            )
        except OperationCancelledError:
            self._mark_cancelled(task_id)
            raise
        except Exception as exc:
            stage = _draft_failure_stage(str(exc))
            shot = save_failure_screenshot(
                browser, task_id, task["profile_no"], stage
            )
            self._record_failure(
                task_id,
                exc,
                stage,
                timer=timer,
                screenshot_path=shot,
            )
            raise
        check_cancel(cancel_event)
        self.db.update_task(
            task_id,
            status="drafted",
            drafted_at=now_iso(),
            last_error="",
            failure_stage="",
            stage_timings_json=timer.to_json() if timer else "{}",
        )
        if timer is not None:
            logging.getLogger("niuma-mail").info(
                timer.summary(task["profile_no"])
            )
        trace_execution(
            task_id, "filled", "Gmail 草稿填写完成",
            profile_no=task["profile_no"],
            extra={"stage_timings": timer.snapshot() if timer else {}},
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
        timer = StageTimer()
        timer.begin("get_window")
        try:
            conn = self.browser_provider.start_profile(task["profile_no"])
        except OperationCancelledError:
            self._mark_cancelled(task_id)
            raise
        except Exception as exc:
            timer.stop_all()
            self._record_failure(task_id, exc, "connect", timer=timer)
            raise
        timer.stop("get_window")
        check_cancel(cancel_event)
        timer.begin("connect")
        try:
            with connected_browser(conn.cdp_url) as (_, browser):
                timer.stop("connect")
                return self.open_and_send_on_browser(
                    task_id,
                    browser,
                    progress=progress,
                    cancel_event=cancel_event,
                    timer=timer,
                )
        finally:
            timer.stop_all()

    def open_and_send_on_browser(
        self,
        task_id: int,
        browser,
        progress: Callable[[int, str], None] | None = None,
        cancel_event: threading.Event | None = None,
        timer: StageTimer | None = None,
    ) -> int:
        """Fill, verify and send using an already connected browser window."""
        check_cancel(cancel_event)
        task = self._sendable_task(task_id)
        if int(task["profile_no"] or 0) <= 0:
            raise ValueError("请在邮件预览下方选择并确认浏览器窗口")
        subject, body = str(task["subject"] or ""), str(task["body"] or "")
        try:
            resolved_sender, sender_source = self._resolve_sender(task)
        except Exception as exc:
            self._record_failure(
                task_id,
                exc,
                "resolve_sender",
                timer=timer,
            )
            raise
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
            accuracy = prepare_gmail_draft(
                browser,
                task["recipient_email"],
                subject,
                body,
                progress,
                cancel_event,
                timer,
            )
        except OperationCancelledError:
            self._mark_cancelled(task_id)
            raise
        except Exception as exc:
            stage = _draft_failure_stage(str(exc))
            shot = save_failure_screenshot(
                browser, task_id, task["profile_no"], stage
            )
            self._record_failure(
                task_id,
                exc,
                stage,
                timer=timer,
                screenshot_path=shot,
            )
            raise
        self.db.update_task(task_id, status="drafted", drafted_at=now_iso())
        trace_execution(
            task_id, "filled", "Gmail 草稿填写完成",
            profile_no=task["profile_no"],
        )
        verify_result: dict = {}
        try:
            self.db.update_task(
                task_id,
                status="validating",
                validation_at=now_iso(),
            )
            verify_result = verify_draft_fields(
                browser,
                task["recipient_email"],
                subject,
                body,
                timer,
            )
            if not verify_result.get("ok"):
                raise BrowserAutomationError(
                    self._verify_failure_text(verify_result)
                )
            trace_execution(
                task_id, "verified", "Gmail 字段完整校验通过",
                profile_no=task["profile_no"],
            )
        except OperationCancelledError:
            self._mark_cancelled(task_id)
            raise
        except Exception as exc:
            self._record_failure(
                task_id,
                exc,
                "validate",
                timer=timer,
                verify_result=verify_result,
            )
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
                self._record_failure(
                    task_id,
                    exc,
                    "stale_toast",
                    timer=timer,
                    verify_result=verify_result,
                )
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
                click_gmail_send(browser, cancel_event=cancel_event, timer=timer)
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
                shot = save_failure_screenshot(
                    browser, task_id, task["profile_no"], stage
                )
                if stage == "send_button":
                    self._record_failure(
                        task_id,
                        exc,
                        stage,
                        timer=timer,
                        verify_result=verify_result,
                        screenshot_path=shot,
                    )
                else:
                    # Click outcome is unknown: never allow auto-retry.
                    self.db.update_task(
                        task_id,
                        send_clicked_at=now_iso(),
                    )
                    self._mark_needs_review(
                        task_id,
                        exc,
                        stage,
                        timer=timer,
                        verify_result=verify_result,
                        screenshot_path=shot,
                    )
                raise
            trace_execution(
                task_id, "send_clicked", "已点击 Gmail 发送按钮",
                profile_no=task["profile_no"],
            )
            # In-flight sends must finish: do not cancel while waiting.
            try:
                wait_for_gmail_send(browser, timer=timer)
            except BrowserAutomationError as exc:
                stage = (
                    "wait_send"
                    if "超时" in str(exc)
                    else "send_failed"
                )
                shot = save_failure_screenshot(
                    browser, task_id, task["profile_no"], stage
                )
                if stage == "wait_send":
                    self._mark_needs_review(
                        task_id,
                        exc,
                        stage,
                        timer=timer,
                        verify_result=verify_result,
                        screenshot_path=shot,
                    )
                else:
                    self._record_failure(
                        task_id,
                        exc,
                        stage,
                        timer=timer,
                        verify_result=verify_result,
                        screenshot_path=shot,
                    )
                raise
            except Exception as exc:
                shot = save_failure_screenshot(
                    browser, task_id, task["profile_no"], "wait_send"
                )
                self._mark_needs_review(
                    task_id,
                    exc,
                    "wait_send",
                    timer=timer,
                    verify_result=verify_result,
                    screenshot_path=shot,
                )
                raise
        else:
            wait_for_gmail_send(
                browser,
                cancel_event=cancel_event,
                timer=timer,
            )
        self.db.update_task(
            task_id,
            status="sent",
            sent_at=now_iso(),
            drafted_at=now_iso(),
            sent_method="confirmed",
            needs_manual_review=0,
            last_error="",
            failure_stage="",
            stage_timings_json=timer.to_json() if timer else "{}",
            verify_result_json=json.dumps(
                verify_result,
                ensure_ascii=False,
                default=str,
            ),
        )
        if timer is not None:
            logging.getLogger("niuma-mail").info(
                timer.summary(task["profile_no"])
            )
        trace_execution(
            task_id, "sent", "Gmail 已确认发送成功",
            profile_no=task["profile_no"],
            extra={"stage_timings": timer.snapshot() if timer else {}},
        )
        return accuracy

    @staticmethod
    def _verify_failure_text(verify_result: dict) -> str:
        reasons: list[str] = []
        if verify_result.get("unreadable"):
            reasons.append("无法读取字段")
        recipient = verify_result.get("recipient") or {}
        if not recipient.get("ok"):
            reasons.append(
                "收件人不一致：期望 {}，实际 {}".format(
                    recipient.get("expected_display") or "无",
                    recipient.get("actual_display") or "无",
                )
            )
        subject = verify_result.get("subject") or {}
        if not subject.get("ok"):
            reasons.append("主题不一致")
        body = verify_result.get("body") or {}
        if not body.get("ok"):
            reasons.append("正文不一致")
        return "草稿校验未通过：" + "；".join(reasons)

    def _mark_needs_review(
        self,
        task_id: int,
        exc: Exception,
        stage: str,
        timer: StageTimer | None = None,
        verify_result: dict | None = None,
        screenshot_path: str = "",
    ) -> None:
        logging.getLogger("niuma-mail").warning(
            "任务 %s 结果不明确（%s）：%s", task_id, stage, exc
        )
        try:
            row = self.db.get_task(task_id)
            profile = int(row["profile_no"]) if row is not None else None
            if timer is not None:
                logging.getLogger("niuma-mail").info(
                    timer.summary(profile or "")
                )
            context = {
                "发送尝试开始时间": (
                    _row_value(row, "send_attempt_started_at")
                    if row is not None
                    else ""
                ),
                "点击Send时间": (
                    _row_value(row, "send_clicked_at")
                    if row is not None
                    else ""
                ),
                "各阶段耗时": (
                    timer.summary(profile) if timer is not None else ""
                ),
                "收件人校验结果": (
                    (verify_result or {}).get("recipient")
                    if verify_result
                    else {}
                ),
                "主题校验结果": (
                    (verify_result or {}).get("subject")
                    if verify_result
                    else {}
                ),
                "正文校验摘要": (
                    (verify_result or {}).get("body", {}).get("summary", "")
                    if verify_result
                    else ""
                ),
            }
            trace_execution(
                task_id,
                stage,
                str(exc),
                profile_no=profile,
                extra={
                    "exception": type(exc).__name__,
                    "screenshot": screenshot_path,
                    **context,
                },
            )
            self.db.update_task(
                task_id,
                status="needs_review",
                needs_manual_review=1,
                last_error=str(exc),
                failure_stage=stage,
                last_failed_at=now_iso(),
                stage_timings_json=timer.to_json() if timer else "{}",
                verify_result_json=(
                    json.dumps(verify_result, ensure_ascii=False, default=str)
                    if verify_result
                    else "{}"
                ),
                failure_screenshot=screenshot_path,
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

    def run_draft_batch(
        self,
        task_ids: list[int],
        progress: Callable[[int, str], None] | None = None,
        task_status: Callable[[str, str, str, str], None] | None = None,
        cancel_event: threading.Event | None = None,
        on_summary: Callable[[dict], None] | None = None,
    ) -> dict:
        """Fill drafts for a batch, reusing one connection per window."""
        return self._run_window_batch(
            task_ids,
            auto_send=False,
            progress=progress,
            task_status=task_status,
            cancel_event=cancel_event,
            on_summary=on_summary,
        )

    def run_send_batch(
        self,
        task_ids: list[int],
        progress: Callable[[int, str], None] | None = None,
        task_status: Callable[[str, str, str, str], None] | None = None,
        cancel_event: threading.Event | None = None,
        on_summary: Callable[[dict], None] | None = None,
    ) -> dict:
        """Fill, verify and send a batch, reusing one connection per window."""
        return self._run_window_batch(
            task_ids,
            auto_send=True,
            progress=progress,
            task_status=task_status,
            cancel_event=cancel_event,
            on_summary=on_summary,
        )

    def _run_window_batch(
        self,
        task_ids: list[int],
        *,
        auto_send: bool,
        progress: Callable[[int, str], None] | None = None,
        task_status: Callable[[str, str, str, str], None] | None = None,
        cancel_event: threading.Event | None = None,
        on_summary: Callable[[dict], None] | None = None,
    ) -> dict:
        """Different windows run in parallel; one window runs strictly serially."""
        ids = list(dict.fromkeys(int(task_id) for task_id in task_ids))
        if not ids:
            return {
                "completed": 0,
                "sent": 0,
                "failed": 0,
                "needs_review": 0,
                "waiting": 0,
            }
        rows = [dict(row) for row in self.db.get_tasks(ids)]
        by_window, unassigned = group_tasks_by_window(rows)
        if unassigned:
            raise RuntimeError(
                "部分任务未绑定浏览器窗口，已取消执行："
                + ",".join(str(task_id) for task_id in unassigned[:8])
            )
        groups = list(by_window.items())
        total = len(ids)
        result = {
            "completed": 0,
            "sent": 0,
            "drafted": 0,
            "failed": 0,
            "needs_review": 0,
            "waiting": 0,
        }
        lock = threading.Lock()

        def emit_summary() -> None:
            if on_summary is None:
                return
            with lock:
                on_summary(dict(result))

        def task_progress(profile, email):
            def inner(percent: int, text: str) -> None:
                if task_status is not None:
                    task_status(str(profile), email, text, "处理中")
                if progress is not None:
                    progress(percent, text)
            return inner

        def emit_final(task_id: int, default_label: str) -> None:
            if task_status is None:
                return
            row = self.db.get_task(task_id)
            email = row["recipient_email"] if row is not None else ""
            profile = row["profile_no"] if row is not None else ""
            status = row["status"] if row is not None else ""
            label = {
                "sent": "发送成功",
                "failed": "发送失败",
                "needs_review": "需要人工确认",
                "cancelled": "已取消",
                "generated": "生成成功",
                "drafted": "草稿完成",
            }.get(status, default_label)
            task_status(str(profile), email, "完成", label)

        def mark_connect_failed(
            profile: int,
            group: list[int],
            exc: BaseException,
            window_timer: StageTimer,
        ) -> None:
            for task_id in group:
                try:
                    self._record_failure(
                        task_id,
                        exc,
                        "connect",
                        timer=window_timer,
                    )
                except Exception:
                    pass
            with lock:
                result["failed"] += len(group)
                result["completed"] += len(group)
            if progress is not None:
                progress(
                    int(result["completed"] * 100 / total),
                    f"窗口{profile}连接失败：{exc}",
                )
            for task_id in group:
                emit_final(task_id, "失败")

        def run_window(profile: int, group: list[int]) -> None:
            window_timer = StageTimer()
            window_timer.begin("get_window")
            try:
                conn = self.browser_provider.start_profile(profile)
            except OperationCancelledError:
                raise
            except Exception as exc:
                window_timer.stop_all()
                mark_connect_failed(profile, group, exc, window_timer)
                return
            window_timer.stop("get_window")
            check_cancel(cancel_event)
            window_timer.begin("connect")
            try:
                with connected_browser(conn.cdp_url) as (_playwright, browser):
                    window_timer.stop("connect")
                    for task_id in group:
                        check_cancel(cancel_event)
                        row = self.db.get_task(task_id)
                        email = row["recipient_email"] if row is not None else ""
                        if task_status is not None:
                            task_status(
                                str(profile),
                                email,
                                "连接浏览器",
                                "处理中",
                            )
                        task_timer = StageTimer()
                        task_timer.inherit(window_timer)
                        try:
                            if auto_send:
                                self.open_and_send_on_browser(
                                    task_id,
                                    browser,
                                    progress=task_progress(profile, email),
                                    cancel_event=cancel_event,
                                    timer=task_timer,
                                )
                            else:
                                self.open_draft_on_browser(
                                    task_id,
                                    browser,
                                    progress=task_progress(profile, email),
                                    cancel_event=cancel_event,
                                    timer=task_timer,
                                )
                        except OperationCancelledError:
                            raise
                        except Exception:
                            row = self.db.get_task(task_id)
                            status = row["status"] if row is not None else ""
                            with lock:
                                if status == "needs_review":
                                    result["needs_review"] += 1
                                else:
                                    result["failed"] += 1
                                result["completed"] += 1
                                current = result["completed"]
                            emit_summary()
                            if progress is not None:
                                progress(
                                    int(current * 100 / total),
                                    f"已完成 {current}/{total} · "
                                    f"成功 {result['sent']} · "
                                    f"失败 {result['failed']}",
                                )
                            emit_final(task_id, "失败")
                            continue
                        row = self.db.get_task(task_id)
                        status = row["status"] if row is not None else ""
                        with lock:
                            if status == "sent":
                                result["sent"] += 1
                            elif status == "drafted" and not auto_send:
                                result["drafted"] += 1
                            elif status == "needs_review":
                                result["needs_review"] += 1
                            elif status == "failed":
                                result["failed"] += 1
                            result["completed"] += 1
                            current = result["completed"]
                        emit_summary()
                        if progress is not None:
                            progress(
                                int(current * 100 / total),
                                f"已完成 {current}/{total} · "
                                f"成功 {result['sent']} · "
                                f"失败 {result['failed']}",
                            )
                        emit_final(task_id, "完成")
            except OperationCancelledError:
                raise
            except Exception as exc:
                mark_connect_failed(profile, group, exc, window_timer)

        if progress is not None:
            progress(0, f"等待窗口：{', '.join(str(window) for window in by_window)}")
        max_workers = min(MAX_CONCURRENT_TASKS, len(groups))
        with ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="niuma-window-batch",
        ) as executor:
            futures = [
                executor.submit(run_window, profile, group)
                for profile, group in groups
            ]
            for future in as_completed(futures):
                try:
                    future.result()
                except OperationCancelledError:
                    for pending in futures:
                        pending.cancel()
                    raise
        emit_summary()
        return result

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

    def _record_failure(
        self,
        task_id: int,
        exc: Exception,
        stage: str = "",
        timer: StageTimer | None = None,
        verify_result: dict | None = None,
        screenshot_path: str = "",
    ) -> None:
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
            if timer is not None:
                logging.getLogger("niuma-mail").info(
                    timer.summary(profile or "")
                )
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
                    "使用的模板": (
                        (self.settings.window_bindings or {})
                        .get(str(profile), {})
                        .get("template_name", "")
                    ),
                    "发送尝试开始时间": _row_value(
                        row, "send_attempt_started_at"
                    ),
                    "点击Send时间": _row_value(row, "send_clicked_at"),
                    "各阶段耗时": (
                        timer.summary(profile) if timer is not None else ""
                    ),
                    "收件人校验结果": (
                        (verify_result or {}).get("recipient")
                        if verify_result
                        else {}
                    ),
                    "主题校验结果": (
                        (verify_result or {}).get("subject")
                        if verify_result
                        else {}
                    ),
                    "正文校验摘要": (
                        (verify_result or {}).get("body", {}).get("summary", "")
                        if verify_result
                        else ""
                    ),
                }
            trace_execution(
                task_id,
                stage or "unknown",
                str(exc),
                profile_no=profile,
                extra={
                    "exception": type(exc).__name__,
                    "screenshot": screenshot_path,
                    "stage_timings": (
                        timer.snapshot() if timer is not None else {}
                    ),
                    **context,
                },
            )
            try:
                write_error_report(
                    exc,
                    task_id=task_id,
                    stage=stage,
                    profile_no=profile,
                    settings=self.settings,
                    extra_trail=recent_trail(task_id, limit=30),
                    context=context,
                    screenshot_path=screenshot_path,
                )
            except Exception:
                # Task state must still be persisted even if the report file
                # cannot be written (e.g. read-only diagnostics directory).
                pass
            attempts = int(row["attempts"] or 0) if row is not None else 0
            self.db.update_task(
                task_id,
                status="failed",
                last_error=str(exc),
                failure_stage=stage,
                last_failed_at=now_iso(),
                browser_type=self.settings.browser_provider,
                attempts=attempts + 1,
                stage_timings_json=timer.to_json() if timer else "{}",
                verify_result_json=(
                    json.dumps(verify_result, ensure_ascii=False, default=str)
                    if verify_result
                    else "{}"
                ),
                failure_screenshot=screenshot_path,
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
