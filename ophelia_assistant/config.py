from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

APP_NAME = "NiuMaMail"
MAX_CONCURRENT_TASKS = 30
MAX_WINDOW_SEQUENCE = 30
BATCH_CONTACT_ROWS = 10
MAX_CONTACT_ROWS = 100
BATCH_DRAFT_INTERVAL_SECONDS = 3
OLD_DEFAULT_SENDER_NAME = "Ophelia Carter"
DEFAULT_SENDER_NAME = "Anna Lee"
OLD_DEFAULT_SUBJECT_TEMPLATE = "A quick question about {location}"
DEFAULT_SUBJECT_TEMPLATE = "An email seeking help regarding the {location} area!"
OLD_DEFAULT_BODY_TEMPLATE = """Hi {first_name},

My name is {sender_name}. I noticed that you live in {location}, and I’m considering moving there.

Since I’m still getting to know the area, I’d really appreciate hearing about your experience living there and any recommendations you might have.

Thank you for your time!

Best regards,
{sender_name}"""
PREVIOUS_DEFAULT_BODY_TEMPLATE = """Hello Mr. {first_name},

My name is {sender_name}. I noticed that you live in {location}, and I’m considering moving there.

Since I’m still getting to know the area, I’d really appreciate hearing about your experience living there and any recommendations you might have.

Thank you for your time!

Best regards,
{sender_name}"""
DEFAULT_BODY_TEMPLATE = """Hello, Mr./Ms. {first_name}:

My name is {sender_name}.

I hope you are doing well and I apologize for bothering you. I noticed you are currently staying in {location}, and I am contacting you because I am planning a trip here with friends. If you don't mind, I would greatly appreciate it if you could share some information, especially regarding travel recommendations, restaurants, hotels, and safety.

Thank you very much for your time, and I sincerely appreciate any advice you may have.

Sincerely,
{sender_name}"""
CUSTOM_VARIABLE_KEYS = tuple(f"custom_{index}" for index in range(1, 6))


def next_custom_variable_key(existing_keys) -> str:
    """Return the first unused custom_N key, starting from custom_1."""
    used = {str(key) for key in existing_keys}
    index = 1
    while f"custom_{index}" in used:
        index += 1
    return f"custom_{index}"


def default_custom_variables() -> dict[str, str]:
    return {}


def normalize_window_sequence(values) -> list[int]:
    """Validate up to 30 unique positive browser window numbers."""
    if values is None:
        return []
    if not isinstance(values, (list, tuple)):
        raise ValueError("窗口顺序必须是编号列表")
    normalized: list[int] = []
    for raw_value in values:
        if raw_value is None or str(raw_value).strip() == "":
            continue
        try:
            number = int(str(raw_value).strip())
        except (TypeError, ValueError) as exc:
            raise ValueError(f"窗口编号无效：{raw_value}") from exc
        if number <= 0:
            raise ValueError("窗口编号必须大于 0")
        if number in normalized:
            raise ValueError(f"窗口编号 {number} 重复，请为每个顺序填写不同窗口")
        normalized.append(number)
    if len(normalized) > MAX_WINDOW_SEQUENCE:
        raise ValueError(f"最多只能设置 {MAX_WINDOW_SEQUENCE} 个窗口编号")
    return normalized


def pair_tasks_with_windows(task_ids, window_values) -> list[tuple[int, int]]:
    """Pair tasks with window numbers without changing either input order."""
    tasks = [int(task_id) for task_id in task_ids]
    windows = normalize_window_sequence(window_values)
    if len(tasks) > len(windows):
        raise ValueError(
            f"已选择 {len(tasks)} 封邮件，但窗口顺序只填写了 {len(windows)} 个编号。"
        )
    return list(zip(tasks, windows))


def resolve_task_windows(profile_values, window_values) -> list[int | None]:
    """Prefer each task's table profile; use the same-position sequence as fallback."""
    windows = normalize_window_sequence(window_values)
    resolved: list[int | None] = []
    for index, raw_profile in enumerate(profile_values):
        try:
            profile_no = int(raw_profile or 0)
        except (TypeError, ValueError):
            profile_no = 0
        if profile_no > 0:
            resolved.append(profile_no)
        elif index < len(windows):
            resolved.append(windows[index])
        else:
            resolved.append(None)
    return resolved


def app_data_dir() -> Path:
    root = Path(os.getenv("LOCALAPPDATA", Path.home() / ".ophelia-mail-assistant"))
    path = root / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class Settings:
    browser_provider: str = "morelogin"
    morelogin_url: str = "http://127.0.0.1:40000"
    adspower_url: str = "http://127.0.0.1:50325"
    adspower_api_key: str = ""
    bitbrowser_url: str = "http://127.0.0.1:54345"
    sender_name: str = DEFAULT_SENDER_NAME
    language: str = "zh"
    subject_template: str = DEFAULT_SUBJECT_TEMPLATE
    body_template: str = DEFAULT_BODY_TEMPLATE
    custom_variables: dict[str, str] = field(default_factory=default_custom_variables)
    custom_variable_keys: list[str] = field(default_factory=list)
    hidden_system_variables: list[str] = field(default_factory=list)
    saved_templates: list[dict] = field(default_factory=list)
    update_url: str = ""
    window_sequence: list[int] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.browser_provider not in {"morelogin", "adspower", "bitbrowser"}:
            self.browser_provider = "morelogin"
        if self.language not in {"zh", "en"}:
            self.language = "zh"
        raw_custom_variables = self.custom_variables if isinstance(self.custom_variables, dict) else {}
        self.custom_variables = {
            str(key): str(value) for key, value in raw_custom_variables.items()
        }
        raw_keys = self.custom_variable_keys if isinstance(self.custom_variable_keys, list) else []
        self.custom_variable_keys = [
            str(key) for key in raw_keys if str(key) in self.custom_variables
        ]
        if not isinstance(self.hidden_system_variables, list):
            self.hidden_system_variables = []
        else:
            known_system = {"first_name", "location", "sender_name"}
            self.hidden_system_variables = [
                str(key)
                for key in self.hidden_system_variables
                if str(key) in known_system
            ]
        if not isinstance(self.saved_templates, list):
            self.saved_templates = []
        else:
            self.saved_templates = [
                entry for entry in self.saved_templates if isinstance(entry, dict)
            ]
        self.window_sequence = normalize_window_sequence(self.window_sequence)

    @classmethod
    def load(cls) -> "Settings":
        path = app_data_dir() / "settings.json"
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("sender_name") == OLD_DEFAULT_SENDER_NAME:
                data["sender_name"] = DEFAULT_SENDER_NAME
            if data.get("subject_template") == OLD_DEFAULT_SUBJECT_TEMPLATE:
                data["subject_template"] = DEFAULT_SUBJECT_TEMPLATE
            if data.get("body_template") in {
                OLD_DEFAULT_BODY_TEMPLATE,
                PREVIOUS_DEFAULT_BODY_TEMPLATE,
            }:
                data["body_template"] = DEFAULT_BODY_TEMPLATE
            allowed = cls.__dataclass_fields__.keys()
            return cls(**{k: v for k, v in data.items() if k in allowed})
        except (OSError, ValueError, TypeError):
            return cls()

    def save(self) -> None:
        path = app_data_dir() / "settings.json"
        path.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
