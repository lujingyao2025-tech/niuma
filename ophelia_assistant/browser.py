from __future__ import annotations

import hashlib
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
from .timing import StageTimer


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
        page.goto(
            "https://mail.google.com/mail/u/0/#inbox",
            wait_until="domcontentloaded",
            timeout=60000,
        )
    if "accounts.google.com" in page.url:
        raise BrowserAutomationError("此浏览器窗口尚未登录 Gmail")
    return page


def _visible_last(
    locator: Locator,
    timeout_ms: int = 15000,
    cancel_event: threading.Event | None = None,
) -> Locator | None:
    """Return the last visible match using state waits instead of fixed sleeps."""
    elapsed = 0
    try:
        locator.last.wait_for(
            state="visible",
            timeout=min(2000, timeout_ms),
        )
    except (PlaywrightTimeoutError, PlaywrightError):
        pass
    while elapsed <= timeout_ms:
        check_cancel(cancel_event)
        for index in range(locator.count() - 1, -1, -1):
            candidate = locator.nth(index)
            try:
                if candidate.is_visible():
                    return candidate
            except PlaywrightError:
                continue
        remaining = timeout_ms - elapsed
        if remaining <= 0:
            break
        try:
            locator.last.wait_for(
                state="visible",
                timeout=min(1000, remaining),
            )
        except (PlaywrightTimeoutError, PlaywrightError):
            pass
        elapsed += min(1000, remaining)
    return None


def _visible_largest(
    locator: Locator,
    timeout_ms: int = 15000,
    cancel_event: threading.Event | None = None,
) -> Locator | None:
    """Return the largest visible match, which is Gmail's message body fallback."""
    try:
        locator.first.wait_for(
            state="visible",
            timeout=min(1500, timeout_ms),
        )
    except (PlaywrightTimeoutError, PlaywrightError):
        pass
    elapsed = 0
    while elapsed <= timeout_ms:
        check_cancel(cancel_event)
        largest: Locator | None = None
        largest_area = -1.0
        for index in range(locator.count()):
            candidate = locator.nth(index)
            try:
                visible = candidate.is_visible()
            except PlaywrightError:
                continue
            if not visible:
                continue
            try:
                box = candidate.bounding_box()
            except PlaywrightError:
                continue
            if not box:
                continue
            area = box["width"] * box["height"]
            if area > largest_area:
                largest = candidate
                largest_area = area
        if largest is not None:
            return largest
        remaining = timeout_ms - elapsed
        if remaining <= 0:
            break
        try:
            locator.first.wait_for(
                state="visible",
                timeout=min(1000, remaining),
            )
        except (PlaywrightTimeoutError, PlaywrightError):
            pass
        elapsed += min(1000, remaining)
    return None


def _visible_inputs_by_position(
    scope: Locator,
    timeout_ms: int = 3000,
    cancel_event: threading.Event | None = None,
) -> list[Locator]:
    """Find visible compose inputs from top to bottom for geometry fallback."""
    locator = scope.locator(
        'input:not([type="hidden"]):not([disabled]), textarea:not([disabled])'
    )
    try:
        locator.first.wait_for(
            state="visible",
            timeout=min(1200, timeout_ms),
        )
    except (PlaywrightTimeoutError, PlaywrightError):
        pass
    elapsed = 0
    while elapsed <= timeout_ms:
        check_cancel(cancel_event)
        found: list[tuple[float, float, Locator]] = []
        for index in range(locator.count()):
            candidate = locator.nth(index)
            try:
                visible = candidate.is_visible()
            except PlaywrightError:
                continue
            if not visible:
                continue
            try:
                box = candidate.bounding_box()
            except PlaywrightError:
                continue
            if not box or box["width"] < 20 or box["height"] < 10:
                continue
            found.append((box["y"], box["x"], candidate))
        if found:
            found.sort(key=lambda item: (item[0], item[1]))
            return [item[2] for item in found]
        remaining = timeout_ms - elapsed
        if remaining <= 0:
            break
        try:
            locator.first.wait_for(
                state="visible",
                timeout=min(1000, remaining),
            )
        except (PlaywrightTimeoutError, PlaywrightError):
            pass
        elapsed += min(1000, remaining)
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
    check_cancel(cancel_event)

    # Read through DOM evaluation instead of Locator.inner_text(). Gmail may
    # visually accept all text while inner_text still waits for actionability
    # and eventually reports a false timeout.
    current_text = ""
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        check_cancel(cancel_event)
        try:
            current_text = body_box.evaluate(
                "element => element.innerText || element.textContent || ''"
            )
        except PlaywrightError:
            current_text = ""
        if marker and marker in str(current_text):
            break
        page.wait_for_timeout(100)
    check_cancel(cancel_event)
    try:
        current_text = body_box.evaluate(
            "element => element.innerText || element.textContent || ''"
        )
    except PlaywrightError:
        current_text = ""
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
        deadline = time.monotonic() + 2
        current_text = ""
        while time.monotonic() < deadline:
            check_cancel(cancel_event)
            try:
                current_text = body_box.evaluate(
                    "element => element.innerText || element.textContent || ''"
                )
            except PlaywrightError:
                current_text = ""
            if marker and marker in str(current_text):
                break
            page.wait_for_timeout(100)
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


def _locator_input_values(locator: Locator) -> list[str]:
    values: list[str] = []
    try:
        count = locator.count()
    except PlaywrightError:
        return values
    for index in range(count):
        candidate = locator.nth(index)
        try:
            if not candidate.is_visible():
                continue
            value = str(candidate.input_value(timeout=800) or "").strip()
        except (PlaywrightError, PlaywrightTimeoutError):
            continue
        if value:
            values.append(value)
    return values


def _locator_texts(locator: Locator) -> list[str]:
    values: list[str] = []
    try:
        count = locator.count()
    except PlaywrightError:
        return values
    for index in range(count):
        candidate = locator.nth(index)
        try:
            if not candidate.is_visible():
                continue
            text = str(candidate.inner_text(timeout=800) or "").strip()
            if not text:
                text = str(candidate.text_content(timeout=800) or "").strip()
        except (PlaywrightError, PlaywrightTimeoutError):
            continue
        if text:
            values.append(text)
    return values


def _locator_attributes(locator: Locator, names: tuple[str, ...]) -> list[str]:
    values: list[str] = []
    try:
        count = locator.count()
    except PlaywrightError:
        return values
    for index in range(count):
        candidate = locator.nth(index)
        try:
            if not candidate.is_visible():
                continue
            for name in names:
                value = str(candidate.get_attribute(name, timeout=500) or "").strip()
                if value:
                    values.append(value)
        except (PlaywrightError, PlaywrightTimeoutError):
            continue
    return values


def _read_draft_fields(
    page: Page,
    scope: Locator | None = None,
) -> dict:
    """Read recipient chips/inputs, subject and body using primary selectors."""
    scope = scope or _compose_scope(page)
    recipient_sources: list[str] = []
    recipient_sources.extend(
        _locator_input_values(
            scope.locator(
                'input[name="to"], textarea[name="to"], '
                'input[peoplekit-id], input[aria-label*="recipient" i], '
                'input[aria-label*="收件人"], input[aria-label*="收件者"]'
            )
        )
    )
    chip_locator = scope.locator(
        'div[role="chip"], div[role="button"][data-hovercard-id], '
        '[data-hovercard-id], [data-email], [email]'
    )
    recipient_sources.extend(_locator_texts(chip_locator))
    recipient_sources.extend(
        _locator_attributes(
            chip_locator,
            (
                "data-hovercard-id",
                "data-email",
                "email",
                "aria-label",
            ),
        )
    )
    recipient_emails = sorted(
        {
            email.casefold()
            for value in recipient_sources
            for email in re.findall(r"[\w.+-]+@[\w.-]+", str(value or ""))
        }
    )

    subject_values = _locator_input_values(
        scope.locator(
            'input[name="subjectbox"], input[name="subject"], '
            'input[aria-label="Subject" i], input[aria-label*="主题"], '
            'input[aria-label*="主旨"]'
        )
    )
    subject = " ".join(subject_values).strip() if subject_values else ""

    body_selectors = (
        'div[contenteditable="true"][aria-label*="Message Body" i], '
        'div[contenteditable="true"][aria-label*="邮件正文"], '
        'div[contenteditable="true"][aria-label*="郵件正文"], '
        'div[contenteditable="true"][role="textbox"]'
    )
    body_values = _locator_texts(scope.locator(body_selectors))
    body = "\n".join(body_values).strip() if body_values else ""

    return {
        "recipients": recipient_emails,
        "subject": subject,
        "body": body,
        "recipient_read": bool(recipient_sources),
        "subject_read": bool(subject_values),
        "body_read": bool(body_values),
        "compose_found": bool(recipient_sources or subject_values or body_values),
    }


def _existing_blank_compose(
    browser: Browser,
    page: Page,
    cancel_event: threading.Event | None = None,
) -> Page | None:
    """Reuse an already open blank compose; never overwrite a nonblank draft."""
    compose_page = _compose_page(browser)
    candidates = []
    if compose_page is not None:
        candidates.append(compose_page)
    if page.url and ("view=cm" in page.url or "compose" in page.url or "to=" in page.url):
        candidates.append(page)
    dialog = _compose_scope(page, cancel_event)
    if dialog is not None and dialog.locator('div[role="dialog"]').count() > 0:
        candidates.append(page)
    seen: set[int] = set()
    for candidate in candidates:
        if id(candidate) in seen:
            continue
        seen.add(id(candidate))
        try:
            fields = _read_draft_fields(candidate)
        except PlaywrightError:
            continue
        if (
            fields.get("compose_found")
            and not fields.get("recipients")
            and not fields.get("subject")
            and not fields.get("body")
        ):
            return candidate
    return None


def _fill_open_compose(
    page: Page,
    recipient: str,
    subject: str,
    body: str,
    progress: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
    timer: StageTimer | None = None,
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
    _notify(progress, 0, "定位收件人输入框")

    if timer is not None:
        timer.next("locate_recipient")
    to_box = _recipient_control(scope, cancel_event)
    if timer is not None:
        timer.stop("locate_recipient")
    if to_box is None:
        raise BrowserAutomationError("已打开写信窗口，多种策略均找不到 To / 收件人输入框")
    if timer is not None:
        timer.next("fill_recipient")
    _replace_text_control(page, to_box, recipient, "收件人", cancel_event)
    to_box.press("Enter")
    if timer is not None:
        timer.stop("fill_recipient")
    _notify(progress, 33, "填写收件人")

    if timer is not None:
        timer.next("fill_subject")
    subject_box = _subject_control(scope, cancel_event)
    if subject_box is None:
        raise BrowserAutomationError("收件人已填写，多种策略均找不到 Subject / 主题输入框")
    _replace_text_control(page, subject_box, subject, "主题", cancel_event)
    if timer is not None:
        timer.stop("fill_subject")
    _notify(progress, 67, "填写主题")

    if timer is not None:
        timer.next("fill_body")
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
        deadline = time.monotonic() + 2
        scope_text = ""
        while time.monotonic() < deadline:
            check_cancel(cancel_event)
            try:
                scope_text = str(
                    scope.evaluate(
                        "element => element.innerText || element.textContent || ''"
                    )
                    or ""
                )
            except PlaywrightError:
                scope_text = ""
            if body.splitlines()[0].strip() in scope_text:
                break
            page.wait_for_timeout(100)
        if body.splitlines()[0].strip() not in scope_text:
            raise BrowserAutomationError("正文区域已点击，但页面回读未检测到邮件内容")
    if timer is not None:
        timer.stop("fill_body")
    _notify(progress, 100, "填写正文")
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
        time.sleep(0.1)
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
    timer: StageTimer | None = None,
) -> int:
    check_cancel(cancel_event)
    if not recipient.strip() or "@" not in recipient:
        raise BrowserAutomationError("收件邮箱无效")
    if not subject.strip() or not body.strip():
        raise BrowserAutomationError("邮件主题或正文为空")
    _notify(progress, 0, "打开Compose")
    if timer is not None:
        timer.next("open_compose")
    reused = _existing_blank_compose(browser, page, cancel_event)
    if reused is not None:
        page = reused
    else:
        try:
            page = _open_compose_by_button(browser, page, cancel_event)
        except (PlaywrightTimeoutError, BrowserAutomationError):
            try:
                page.goto(gmail_new_message_url(), wait_until="domcontentloaded", timeout=60000)
                if "accounts.google.com" in page.url:
                    raise BrowserAutomationError("此浏览器窗口尚未登录 Gmail")
            except (PlaywrightTimeoutError, BrowserAutomationError) as exc:
                raise BrowserAutomationError(f"无法打开 Gmail 写信窗口：{exc}") from exc
    if timer is not None:
        timer.stop("open_compose")
    try:
        accuracy = _fill_open_compose(
            page, recipient, subject, body, progress, cancel_event, timer
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
    timer: StageTimer | None = None,
) -> int:
    check_cancel(cancel_event)
    _notify(progress, 0, "打开Gmail")
    if timer is not None:
        timer.begin("open_gmail")
    page = _gmail_page(browser)
    if timer is not None:
        timer.stop("open_gmail")
    try:
        return _prepare_gmail_draft_on_page(
            browser,
            page,
            recipient,
            subject,
            body,
            progress,
            cancel_event,
            timer,
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
    timer: StageTimer | None = None,
) -> dict:
    """Re-read the open compose and return a structured verification result."""
    if timer is not None:
        timer.next("validate")
    expected_emails = sorted(
        {
            email.casefold()
            for email in re.findall(
                r"[\w.+-]+@[\w.-]+",
                str(recipient or ""),
            )
        }
    )

    def _norm(value: str) -> str:
        text = str(value or "")
        text = text.replace("\u00a0", " ").replace("\u200b", "").replace("\ufeff", "")
        text = re.sub(r"[ \t\r\n]+", " ", text)
        return text.strip().casefold()

    try:
        page = _compose_page(browser) or _gmail_page(browser)
        fields = _read_draft_fields(page)
    except (PlaywrightError, BrowserAutomationError):
        fields = {
            "recipients": [],
            "subject": "",
            "body": "",
            "recipient_read": False,
            "subject_read": False,
            "body_read": False,
            "compose_found": False,
        }

    actual_emails = sorted(set(fields.get("recipients") or []))
    recipient_ok = bool(expected_emails) and set(expected_emails) == set(actual_emails)
    normalized_subject = _norm(fields.get("subject"))
    normalized_expected_subject = _norm(subject)
    subject_ok = bool(fields.get("subject_read")) and normalized_subject == normalized_expected_subject
    normalized_body = _norm(fields.get("body"))
    normalized_expected_body = _norm(body)
    body_ok = bool(fields.get("body_read")) and normalized_body == normalized_expected_body
    unreadable = not (
        fields.get("recipient_read")
        and fields.get("subject_read")
        and fields.get("body_read")
    )
    result = {
        "ok": recipient_ok and subject_ok and body_ok,
        "unreadable": unreadable,
        "recipient": {
            "ok": recipient_ok,
            "readable": bool(fields.get("recipient_read")),
            "expected": expected_emails,
            "actual": actual_emails,
            "expected_display": ", ".join(expected_emails),
            "actual_display": ", ".join(actual_emails),
        },
        "subject": {
            "ok": subject_ok,
            "readable": bool(fields.get("subject_read")),
            "expected": str(subject or ""),
            "actual": str(fields.get("subject") or ""),
            "expected_hash": hashlib.sha256(
                normalized_expected_subject.encode("utf-8")
            ).hexdigest(),
            "actual_hash": hashlib.sha256(
                normalized_subject.encode("utf-8")
            ).hexdigest(),
        },
        "body": {
            "ok": body_ok,
            "readable": bool(fields.get("body_read")),
            "expected_hash": hashlib.sha256(
                normalized_expected_body.encode("utf-8")
            ).hexdigest(),
            "actual_hash": hashlib.sha256(
                normalized_body.encode("utf-8")
            ).hexdigest(),
            "summary": str(fields.get("body") or "")[:200],
        },
    }
    if timer is not None:
        timer.stop("validate")
    return result


def click_gmail_send(
    browser: Browser,
    cancel_event: threading.Event | None = None,
    timer: StageTimer | None = None,
) -> None:
    """Click the Gmail compose Send button inside the active compose only."""
    page = _compose_page(browser) or _gmail_page(browser)
    scope = _compose_scope(page, cancel_event)
    if timer is not None:
        timer.next("locate_send")
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
                try:
                    locator.last.wait_for(
                        state="visible",
                        timeout=min(
                            1200,
                            max(0, int((deadline - time.monotonic()) * 1000)),
                        ),
                    )
                except (PlaywrightTimeoutError, PlaywrightError):
                    pass
                for index in range(locator.count() - 1, -1, -1):
                    candidate = locator.nth(index)
                    try:
                        if candidate.is_visible() and candidate.is_enabled():
                            if timer is not None:
                                timer.stop("locate_send")
                                timer.next("click_send")
                            candidate.click(timeout=3000)
                            if timer is not None:
                                timer.stop("click_send")
                            return
                    except PlaywrightError as exc:
                        last_error = str(exc)
        except PlaywrightError as exc:
            last_error = str(exc)
        time.sleep(0.15)
    raise BrowserAutomationError(
        f"找不到可用的 Gmail 发送按钮，未自动发送（{last_error or '按钮不可见'}）"
    )


def wait_for_gmail_send(
    browser: Browser,
    timeout_ms: int = 300_000,
    cancel_event: threading.Event | None = None,
    baseline: tuple[str, ...] | None = None,
    timer: StageTimer | None = None,
) -> None:
    """Wait for a NEW send toast node; same-text old toasts are ignored."""
    if timer is not None:
        timer.next("wait_send")
    page = _compose_page(browser) or _gmail_page(browser)
    baseline_ids = {
        str(marker)
        for marker in (baseline or ())
        if str(marker).startswith("node:")
    }
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        check_cancel(cancel_event)
        try:
            nodes = _alert_nodes(page)
        except (PlaywrightTimeoutError, PlaywrightError):
            nodes = []
        for node in nodes:
            node_id = str(node.get("id") or "")
            if baseline_ids and node_id in baseline_ids:
                continue
            alert_text = str(node.get("text") or "")
            if FAILURE_PROMPT_RE.search(alert_text):
                raise BrowserAutomationError(
                    f"Gmail 提示发送失败：{alert_text[:200]}"
                )
            if SUCCESS_PROMPT_RE.search(alert_text):
                if timer is not None:
                    timer.stop("wait_send")
                return
        time.sleep(0.5)
    if timer is not None:
        timer.stop("wait_send")
    raise BrowserAutomationError(
        f"等待 Gmail 发送确认超时（{timeout_ms // 1000} 秒），请检查该窗口是否已发送"
    )


def _alert_nodes(page: Page) -> list[dict[str, str]]:
    """Return visible role=alert nodes with stable ids and inner text."""
    try:
        raw = page.locator('[role="alert"]').evaluate_all(
            """els => els.filter((el) => {
                const rect = el.getBoundingClientRect();
                const style = getComputedStyle(el);
                return rect.width > 0 && rect.height > 0 &&
                    style.visibility !== 'hidden' && style.display !== 'none';
            }).map((el) => {
                if (!el.__niumaAlertId) {
                    el.__niumaAlertId = 'node:' +
                        Math.random().toString(36).slice(2) +
                        Date.now().toString(36);
                }
                return {id: el.__niumaAlertId, text: el.innerText || ''};
            })"""
        )
        return [
            {"id": str(item.get("id") or ""), "text": str(item.get("text") or "")}
            for item in raw
            if isinstance(item, dict)
        ]
    except PlaywrightError:
        return []


def gmail_alert_baseline(browser: Browser) -> tuple[str, ...]:
    """Capture current visible role=alert node ids before clicking Send."""
    page = _compose_page(browser) or _gmail_page(browser)
    return tuple(sorted(node["id"] for node in _alert_nodes(page) if node["id"]))


def wait_for_gmail_alerts_clear(
    browser: Browser,
    baseline: tuple[str, ...] | None = None,
    timeout_ms: int = 15000,
    cancel_event: threading.Event | None = None,
) -> None:
    """Wait for pre-click send toasts to clear; unrelated alerts never block."""
    page = _compose_page(browser) or _gmail_page(browser)
    baseline_ids = {
        str(marker)
        for marker in (baseline or ())
        if str(marker).startswith("node:")
    }
    baseline_texts = {
        str(text)
        for text in (baseline or ())
        if not str(text).startswith("node:")
    }
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        check_cancel(cancel_event)
        if baseline_ids:
            current_ids = {node["id"] for node in _alert_nodes(page)}
            if not (baseline_ids & current_ids):
                return
        if baseline_texts:
            try:
                current = [
                    str(alert)
                    for alert in page.locator('[role="alert"]').all_inner_texts()
                ]
            except PlaywrightError:
                current = []
            send_texts = [
                text
                for text in current
                if SUCCESS_PROMPT_RE.search(text) or FAILURE_PROMPT_RE.search(text)
            ]
            if not any(text in baseline_texts for text in send_texts):
                return
        if not baseline_ids and not baseline_texts:
            return
        time.sleep(0.1)
    raise BrowserAutomationError(
        "旧发送提示未在限定时间内消失，已取消自动发送以避免误判发送结果"
    )
