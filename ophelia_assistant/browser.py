from __future__ import annotations

import re
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator

from playwright.sync_api import (
    Browser,
    Error as PlaywrightError,
    Locator,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

from .config import app_data_dir
from .gmail_utils import RECIPIENT_LABEL_RE, SUBJECT_LABEL_RE, gmail_new_message_url
from .operation import check_cancel


class BrowserAutomationError(RuntimeError):
    pass


ProgressCallback = Callable[[int, str], None]


def _notify(progress: ProgressCallback | None, percent: int, detail: str) -> None:
    if progress is not None:
        progress(percent, detail)


@contextmanager
def connected_browser(cdp_url: str) -> Iterator[tuple[Playwright, Browser]]:
    playwright = sync_playwright().start()
    try:
        browser = playwright.chromium.connect_over_cdp(cdp_url)
        yield playwright, browser
    finally:
        # Stopping Playwright disconnects automation while leaving the user's
        # Leave the selected browser profile and open Gmail draft running.
        playwright.stop()


def _context(browser: Browser):
    return browser.contexts[0] if browser.contexts else browser.new_context()


def _matching_page(browser: Browser, pattern: str) -> Page | None:
    for page in _context(browser).pages:
        if pattern in page.url:
            return page
    return None


def _compose_page(browser: Browser) -> Page | None:
    """Gmail may open Compose in a separate tab; find that tab if present."""
    for page in _context(browser).pages:
        url = page.url or ""
        if "view=cm" in url or "compose" in url or "to=" in url:
            return page
    return None


def _gmail_page(browser: Browser) -> Page:
    page = _matching_page(browser, "mail.google.com") or _context(browser).new_page()
    if "mail.google.com" not in page.url:
        page.goto("https://mail.google.com/mail/u/0/#inbox", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(800)
    if "accounts.google.com" in page.url:
        raise BrowserAutomationError("此浏览器窗口尚未登录 Gmail")
    return page


def _visible_last(
    locator: Locator,
    timeout_ms: int = 15000,
    cancel_event: threading.Event | None = None,
) -> Locator | None:
    elapsed = 0
    while elapsed <= timeout_ms:
        check_cancel(cancel_event)
        for index in range(locator.count() - 1, -1, -1):
            candidate = locator.nth(index)
            if candidate.is_visible():
                return candidate
        time.sleep(0.25)
        elapsed += 250
    return None


def _visible_largest(
    locator: Locator,
    timeout_ms: int = 15000,
    cancel_event: threading.Event | None = None,
) -> Locator | None:
    """Return the largest visible match, which is Gmail's message body fallback."""
    elapsed = 0
    while elapsed <= timeout_ms:
        check_cancel(cancel_event)
        largest: Locator | None = None
        largest_area = -1.0
        for index in range(locator.count()):
            candidate = locator.nth(index)
            if not candidate.is_visible():
                continue
            box = candidate.bounding_box()
            if not box:
                continue
            area = box["width"] * box["height"]
            if area > largest_area:
                largest = candidate
                largest_area = area
        if largest is not None:
            return largest
        time.sleep(0.25)
        elapsed += 250
    return None


def _visible_inputs_by_position(
    scope: Locator,
    timeout_ms: int = 3000,
    cancel_event: threading.Event | None = None,
) -> list[Locator]:
    """Find visible compose inputs from top to bottom for geometry fallback."""
    elapsed = 0
    while elapsed <= timeout_ms:
        check_cancel(cancel_event)
        found: list[tuple[float, float, Locator]] = []
        locator = scope.locator(
            'input:not([type="hidden"]):not([disabled]), textarea:not([disabled])'
        )
        for index in range(locator.count()):
            candidate = locator.nth(index)
            if not candidate.is_visible():
                continue
            box = candidate.bounding_box()
            if not box or box["width"] < 20 or box["height"] < 10:
                continue
            found.append((box["y"], box["x"], candidate))
        if found:
            found.sort(key=lambda item: (item[0], item[1]))
            return [item[2] for item in found]
        time.sleep(0.25)
        elapsed += 250
    return []


def _compose_scope(page: Page, cancel_event: threading.Event | None = None) -> Locator:
    dialogs = page.locator(
        'div[role="dialog"], div[aria-label*="New Message"], '
        'div[aria-label*="新邮件"], div[aria-label*="新郵件"]'
    )
    dialog = _visible_last(dialogs, 5000, cancel_event)
    return dialog if dialog is not None else page.locator("body")


def _replace_compose_body(
    page: Page,
    body_box: Locator,
    body: str,
    cancel_event: threading.Event | None = None,
) -> None:
    """Focus Gmail's contenteditable body and type like a real keyboard.

    Recent Gmail layouts expose the body as contenteditable but can keep it in
    a transient state where Playwright's Locator.fill() waits until timeout.
    Keyboard input after DOM focus works with both English and Chinese layouts.
    """
    check_cancel(cancel_event)
    marker = next((line.strip() for line in body.splitlines() if line.strip()), body.strip())
    try:
        body_box.evaluate("element => element.focus()")
    except PlaywrightError as exc:
        raise BrowserAutomationError("正文输入区已找到，但无法获得输入焦点") from exc
    page.keyboard.press("Control+A")
    page.keyboard.press("Backspace")
    page.keyboard.insert_text(body)
    page.wait_for_timeout(200)
    check_cancel(cancel_event)

    # Read through DOM evaluation instead of Locator.inner_text(). Gmail may
    # visually accept all text while inner_text still waits for actionability
    # and eventually reports a false timeout.
    try:
        current_text = body_box.evaluate(
            "element => element.innerText || element.textContent || ''"
        )
    except PlaywrightError:
        # Keyboard insertion completed without error. If Gmail is temporarily
        # replacing the editor node, keep the visible draft and avoid a false popup.
        return
    if marker and marker in str(current_text):
        return

    # Fallback for layouts that ignore synthetic keyboard input until an edit
    # command is issued. This still edits the draft only and never sends it.
    try:
        body_box.evaluate(
            """(element, value) => {
                element.focus();
                const selection = window.getSelection();
                const range = document.createRange();
                range.selectNodeContents(element);
                selection.removeAllRanges();
                selection.addRange(range);
                return document.execCommand('insertText', false, value);
            }""",
            body,
        )
        page.wait_for_timeout(200)
        current_text = body_box.evaluate(
            "element => element.innerText || element.textContent || ''"
        )
    except PlaywrightError:
        # The first keyboard strategy already completed; a DOM replacement
        # during secondary verification must not turn success into an error.
        return
    if not marker or marker not in str(current_text):
        raise BrowserAutomationError("正文输入区已找到，但写入后未检测到邮件内容")


def _input_has_value(control: Locator, expected: str) -> bool:
    try:
        actual = control.input_value(timeout=1000).strip()
    except PlaywrightTimeoutError:
        actual = ""
    return actual == expected.strip()


def _replace_text_control(
    page: Page,
    control: Locator,
    value: str,
    field_name: str,
    cancel_event: threading.Event | None = None,
) -> None:
    """Fill a compose field fast, falling back to focused keyboard input."""
    check_cancel(cancel_event)
    try:
        control.fill(value, timeout=2000)
    except PlaywrightTimeoutError:
        # Gmail can keep compose inputs in a transient state where Playwright
        # waits on actionability; focused keyboard input avoids that gate.
        pass
    if _input_has_value(control, value):
        check_cancel(cancel_event)
        return

    try:
        control.evaluate("element => element.focus()")
        page.keyboard.press("Control+A")
        page.keyboard.press("Backspace")
        page.keyboard.insert_text(value)
    except PlaywrightError as exc:
        raise BrowserAutomationError(f"{field_name}输入框已找到，但无法写入内容") from exc
    if not _input_has_value(control, value):
        raise BrowserAutomationError(f"{field_name}输入框已找到，但页面回读内容不一致")
    check_cancel(cancel_event)


def _control_hint(control: Locator) -> str:
    values = []
    for attribute in ("name", "aria-label", "placeholder", "role", "peoplekit-id"):
        try:
            values.append(control.get_attribute(attribute, timeout=300) or "")
        except PlaywrightTimeoutError:
            values.append("")
    return " ".join(values).lower()


def _looks_like_subject(control: Locator) -> bool:
    hint = _control_hint(control)
    return any(token in hint for token in ("subject", "主题", "主旨"))


def _looks_like_recipient(control: Locator) -> bool:
    hint = _control_hint(control)
    return any(token in hint for token in ("recipient", "peoplekit", "收件人", "收件者")) or bool(
        re.search(r"(^|\s)to($|\s)", hint)
    )


def _recipient_control(
    scope: Locator,
    cancel_event: threading.Event | None = None,
) -> Locator | None:
    selectors = (
        'input[name="to"], textarea[name="to"], input[peoplekit-id], '
        'input[aria-label*="recipient" i], textarea[aria-label*="recipient" i], '
        'input[aria-label*="收件人"], input[aria-label*="收件者"], '
        'textarea[aria-label*="收件人"], textarea[aria-label*="收件者"], '
        'input[placeholder*="Recipient" i], input[placeholder*="收件人"], '
        'input[placeholder*="收件者"]'
    )
    control = _visible_last(scope.locator(selectors), 2000, cancel_event)
    if control is not None:
        return control
    prompt = _visible_last(scope.get_by_text(RECIPIENT_LABEL_RE, exact=True), 1200, cancel_event)
    if prompt is not None:
        prompt.click()
        control = _visible_last(scope.locator(selectors), 1200, cancel_event)
        if control is not None:
            return control
    # Gmail variants may expose only a generic combobox after To receives focus.
    inputs = _visible_inputs_by_position(scope, 1500, cancel_event)
    for candidate in inputs:
        if not _looks_like_subject(candidate):
            return candidate
    return None


def _subject_control(
    scope: Locator,
    cancel_event: threading.Event | None = None,
) -> Locator | None:
    selectors = (
        'input[name="subjectbox"], input[placeholder="Subject" i], '
        'input[aria-label="Subject" i], input[aria-label*="主题"], '
        'input[aria-label*="主旨"], input[placeholder*="主题"], '
        'input[placeholder*="主旨"]'
    )
    control = _visible_last(scope.locator(selectors), 2000, cancel_event)
    if control is not None:
        return control
    prompt = _visible_last(scope.get_by_text(SUBJECT_LABEL_RE, exact=True), 1200, cancel_event)
    if prompt is not None:
        prompt.click()
        control = _visible_last(scope.locator(selectors), 1200, cancel_event)
        if control is not None:
            return control
    # Subject is the lower of the compact visible input rows in the compose box.
    inputs = _visible_inputs_by_position(scope, 1500, cancel_event)
    for candidate in reversed(inputs):
        if _looks_like_subject(candidate):
            return candidate
    non_recipient = [candidate for candidate in inputs if not _looks_like_recipient(candidate)]
    return non_recipient[-1] if len(inputs) >= 2 and non_recipient else None


def _fill_open_compose(
    page: Page,
    recipient: str,
    subject: str,
    body: str,
    progress: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> int:
    check_cancel(cancel_event)
    dialog = _visible_last(
        page.locator(
            'div[role="dialog"], div[aria-label*="New Message"], '
            'div[aria-label*="新邮件"], div[aria-label*="新郵件"]'
        ),
        5000,
        cancel_event,
    )
    if dialog is None and "view=cm" not in page.url and "compose" not in page.url:
        raise BrowserAutomationError(
            "Gmail 写信窗口未出现，可能在新标签页打开，或 Gmail 页面仍在加载"
        )
    scope = dialog if dialog is not None else page.locator("body")
    _notify(progress, 0, "已打开写信窗口，正在定位收件人")

    to_box = _recipient_control(scope, cancel_event)
    if to_box is None:
        raise BrowserAutomationError("已打开写信窗口，多种策略均找不到 To / 收件人输入框")
    _replace_text_control(page, to_box, recipient, "收件人", cancel_event)
    to_box.press("Enter")
    _notify(progress, 33, "收件人已填写并验证")

    subject_box = _subject_control(scope, cancel_event)
    if subject_box is None:
        raise BrowserAutomationError("收件人已填写，多种策略均找不到 Subject / 主题输入框")
    _replace_text_control(page, subject_box, subject, "主题", cancel_event)
    _notify(progress, 67, "收件人和主题已填写并验证")

    body_box = _visible_largest(scope.locator(
        'div[contenteditable="true"][aria-label*="Message Body" i], '
        'div[contenteditable="true"][aria-label*="邮件正文"], '
        'div[contenteditable="true"][aria-label*="郵件正文"], '
        'div[contenteditable="true"][aria-label*="郵件內文"], '
        'div[contenteditable="true"][aria-label*="正文"]'
    ), 4000, cancel_event)
    if body_box is None:
        # The body is the large third editable area. Choosing the largest visible
        # contenteditable avoids confusing it with compact recipient controls.
        body_box = _visible_largest(scope.locator(
            'div[contenteditable="true"][aria-multiline="true"], '
            'div[contenteditable="true"][role="textbox"], '
            'div[contenteditable="true"]'
        ), 3000, cancel_event)
    if body_box is not None:
        _replace_compose_body(page, body_box, body, cancel_event)
    else:
        body_prompt = _visible_last(scope.get_by_text(
            re.compile(r"Press\s*/\s*for Help me write|撰写邮件|撰寫郵件", re.I)
        ), 3000, cancel_event)
        if body_prompt is None:
            raise BrowserAutomationError("已填写收件人和主题，但找不到正文大输入框（第三个输入区）")
        body_prompt.click()
        page.keyboard.insert_text(body)
        page.wait_for_timeout(200)
        try:
            scope_text = scope.evaluate(
                "element => element.innerText || element.textContent || ''"
            )
        except PlaywrightError:
            scope_text = None
        if scope_text is not None and body.splitlines()[0].strip() not in str(scope_text):
            raise BrowserAutomationError("正文区域已点击，但页面回读未检测到邮件内容")
    _notify(progress, 100, "收件人、主题和正文均已验证")
    return 100


def _open_compose_by_button(
    browser: Browser,
    page: Page,
    cancel_event: threading.Event | None = None,
) -> Page:
    check_cancel(cancel_event)
    if "mail.google.com" not in page.url:
        page.goto("https://mail.google.com/mail/u/0/#inbox", wait_until="domcontentloaded", timeout=60000)
    if "accounts.google.com" in page.url:
        raise BrowserAutomationError("此浏览器窗口尚未登录 Gmail")
    compose = _visible_last(page.locator(
        'div[gh="cm"], div[role="button"][aria-label*="Compose" i], '
        'div[role="button"][aria-label*="撰写"], div[role="button"][aria-label*="撰寫"]'
    ), 8000, cancel_event)
    if compose is None:
        compose = _visible_last(page.get_by_text(
            re.compile(r"^(Compose|撰写|寫郵件|撰寫)$", re.I), exact=True
        ), 3000, cancel_event)
    if compose is None:
        page.goto("https://mail.google.com/mail/u/0/#inbox", wait_until="domcontentloaded", timeout=60000)
        compose = _visible_last(page.locator('div[gh="cm"]'), 12000, cancel_event)
    if compose is None:
        raise BrowserAutomationError("Gmail 已打开，但找不到左侧 Compose 写信按钮")
    compose.click()
    check_cancel(cancel_event)
    # Gmail may open Compose in a new tab. Prefer that tab for filling.
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        check_cancel(cancel_event)
        compose_page = _compose_page(browser)
        if compose_page is not None and compose_page != page:
            return compose_page
        time.sleep(0.25)
    return page


def _save_failure_screenshot(page: Page, label: str) -> str:
    try:
        shots_dir = app_data_dir() / "screenshots"
        shots_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        path = shots_dir / f"{label}_{stamp}.png"
        page.screenshot(path=str(path), full_page=True)
        return str(path)
    except Exception:
        return ""


def save_failure_screenshot(
    browser: Browser,
    task_id: int,
    profile_no: int,
    stage: str,
) -> str:
    """Save a task-scoped failure screenshot with id/window/stage in the name."""
    try:
        page = _compose_page(browser) or _gmail_page(browser)
    except BrowserAutomationError:
        return ""
    label = f"task_{task_id}_window_{profile_no}_{stage}"
    return _save_failure_screenshot(page, label)


def _send_button_selectors() -> tuple[str, ...]:
    """Multiple Gmail Send button strategies; used only inside compose scope."""
    return (
        'div[role="button"][aria-label^="Send" i]',
        'div[role="button"][aria-label^="发送" i]',
        'div[role="button"][aria-label^="寄出" i]',
        'div[role="button"][aria-label^="送出" i]',
        'div[role="button"][data-tooltip^="Send" i]',
        'div[role="button"][data-tooltip^="发送" i]',
        'div[role="button"][data-tooltip^="寄出" i]',
        'div[role="button"][data-tooltip*="send" i]',
        'button[aria-label^="Send" i]',
        'button[aria-label^="发送" i]',
        'button[aria-label^="寄出" i]',
        '[gh="cm"] [role="button"][aria-label*="Send" i]',
    )


SUCCESS_PROMPT_RE = re.compile(
    r"Message sent|邮件已发送|已发送|已寄出|寄出", re.I
)
FAILURE_PROMPT_RE = re.compile(
    r"发送失败|无法发送|Error sending|Did not send|出错了|发生错误", re.I
)


def _prepare_gmail_draft_on_page(
    browser: Browser,
    page: Page,
    recipient: str,
    subject: str,
    body: str,
    progress: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> int:
    check_cancel(cancel_event)
    if not recipient.strip() or "@" not in recipient:
        raise BrowserAutomationError("收件邮箱无效")
    if not subject.strip() or not body.strip():
        raise BrowserAutomationError("邮件主题或正文为空")
    _notify(progress, 0, "正在打开 Gmail 写信窗口")
    try:
        page = _open_compose_by_button(browser, page, cancel_event)
    except (PlaywrightTimeoutError, BrowserAutomationError):
        try:
            page.goto(gmail_new_message_url(), wait_until="domcontentloaded", timeout=60000)
            if "accounts.google.com" in page.url:
                raise BrowserAutomationError("此浏览器窗口尚未登录 Gmail")
        except (PlaywrightTimeoutError, BrowserAutomationError) as exc:
            raise BrowserAutomationError(f"无法打开 Gmail 写信窗口：{exc}") from exc
    try:
        accuracy = _fill_open_compose(
            page, recipient, subject, body, progress, cancel_event
        )
    except (PlaywrightTimeoutError, BrowserAutomationError) as exc:
        # Do not open another compose window after partial input. Keeping the
        # existing draft prevents duplicate blank drafts when only body entry fails.
        raise BrowserAutomationError(f"Gmail 写信窗口已打开，但自动填写失败：{exc}") from exc
    page.bring_to_front()
    check_cancel(cancel_event)
    return accuracy


def prepare_gmail_draft(
    browser: Browser,
    recipient: str,
    subject: str,
    body: str,
    progress: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> int:
    check_cancel(cancel_event)
    page = _gmail_page(browser)
    try:
        return _prepare_gmail_draft_on_page(
            browser, page, recipient, subject, body, progress, cancel_event
        )
    except Exception as exc:
        shot = _save_failure_screenshot(page, "gmail_draft")
        if shot:
            raise type(exc)(f"{exc}\n失败截图：{shot}") from exc
        raise


def verify_draft_fields(
    browser: Browser,
    recipient: str,
    subject: str,
    body: str,
) -> bool:
    """Re-read the open compose controls and compare full field values."""
    def _emails(value: str) -> set[str]:
        return set(re.findall(r"[\w.+-]+@[\w.-]+", str(value or "").casefold()))

    def _norm(value: str) -> str:
        return " ".join(str(value or "").split()).casefold()

    try:
        page = _compose_page(browser) or _gmail_page(browser)
        scope = _compose_scope(page)
        recipient_value = scope.locator(
            'input[name="to"], textarea[name="to"]'
        ).last.input_value()
        subject_value = scope.locator(
            'input[name="subjectbox"], input[name="subject"]'
        ).last.input_value()
        body_value = scope.locator(
            'div[contenteditable="true"][role="textbox"], '
            'div[aria-label*="Message Body" i], '
            'div[aria-label*="邮件正文" i], '
            'div[aria-label*="郵件正文" i]'
        ).last.inner_text()
    except (PlaywrightError, BrowserAutomationError):
        return False
    return (
        bool(recipient_value)
        and _emails(recipient) == _emails(recipient_value)
        and _norm(subject_value) == _norm(subject)
        and _norm(body_value) == _norm(body)
    )


def click_gmail_send(
    browser: Browser,
    cancel_event: threading.Event | None = None,
) -> None:
    """Click the Gmail compose Send button inside the active compose only."""
    page = _compose_page(browser) or _gmail_page(browser)
    scope = _compose_scope(page, cancel_event)
    deadline = time.monotonic() + 12
    last_error = ""
    while time.monotonic() < deadline:
        check_cancel(cancel_event)
        try:
            locators = [scope.locator(selector) for selector in _send_button_selectors()]
            locators.append(
                scope.get_by_role(
                    "button",
                    name=re.compile(r"^(Send|发送|寄出|送出|寄送)$", re.I),
                )
            )
            for locator in locators:
                for index in range(locator.count() - 1, -1, -1):
                    candidate = locator.nth(index)
                    try:
                        if candidate.is_visible() and candidate.is_enabled():
                            candidate.click()
                            return
                    except PlaywrightError as exc:
                        last_error = str(exc)
        except PlaywrightError as exc:
            last_error = str(exc)
        time.sleep(0.3)
    raise BrowserAutomationError(
        f"找不到可用的 Gmail 发送按钮，未自动发送（{last_error or '按钮不可见'}）"
    )


def wait_for_gmail_send(
    browser: Browser,
    timeout_ms: int = 300_000,
    cancel_event: threading.Event | None = None,
) -> None:
    """Wait until Gmail shows a send success toast; fail on explicit errors."""
    page = _compose_page(browser) or _gmail_page(browser)
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        check_cancel(cancel_event)
        try:
            alerts = page.locator('[role="alert"]').all_inner_texts()
        except PlaywrightError:
            alerts = []
        for alert in alerts:
            alert_text = str(alert)
            if FAILURE_PROMPT_RE.search(alert_text):
                raise BrowserAutomationError(
                    f"Gmail 提示发送失败：{alert_text[:200]}"
                )
            if SUCCESS_PROMPT_RE.search(alert_text):
                return
        time.sleep(1)
    raise BrowserAutomationError(
        f"等待 Gmail 发送确认超时（{timeout_ms // 1000} 秒），请检查该窗口是否已发送"
    )
