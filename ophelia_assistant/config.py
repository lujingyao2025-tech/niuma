from __future__ import annotations

import base64
import json
import logging
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from packaging.version import InvalidVersion, Version

APP_NAME = "NiuMaMail"

logger = logging.getLogger("niuma-mail")

MAX_CONCURRENT_TASKS = 30
MAX_WINDOW_SEQUENCE = 30
BATCH_CONTACT_ROWS = 10
MAX_CONTACT_ROWS = 300
MAX_GENERATE_TASKS = 100
BATCH_GENERATE_LIMIT = 100
BATCH_IMPORT_LIMIT = 100
BATCH_DRAFT_INTERVAL_SECONDS = 3

SETTINGS_SCHEMA_VERSION = 1
SETTINGS_MIGRATION_ID = "niuma-settings-schema-v1-2026-08-30"


def is_newer_version(candidate: str, current: str) -> bool:
    """Compare application versions using PEP 440 instead of string order."""
    try:
        return Version(str(candidate).strip().lstrip("v")) > Version(
            str(current).strip().lstrip("v")
        )
    except InvalidVersion:
        return False


def _dpapi_context():
    """Return ctypes handles with declared DPAPI/LocalFree signatures."""
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_byte)),
        ]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(DATA_BLOB),
        ctypes.c_wchar_p,
        ctypes.POINTER(DATA_BLOB),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(DATA_BLOB),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(DATA_BLOB),
        ctypes.POINTER(ctypes.c_wchar_p),
        ctypes.POINTER(DATA_BLOB),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(DATA_BLOB),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    return ctypes, wintypes, crypt32, kernel32, DATA_BLOB


def _protect_secret(value: str) -> str:
    """Encrypt a small secret with Windows DPAPI."""
    if not value:
        return value
    if isinstance(value, str) and value.startswith("enc:v1:"):
        return value
    if sys.platform != "win32":
        return value
    try:
        ctypes, wintypes, crypt32, kernel32, DATA_BLOB = _dpapi_context()
        encoded = value.encode("utf-16-le")
        blob_in = DATA_BLOB(
            len(encoded),
            ctypes.cast(
                ctypes.create_string_buffer(encoded),
                ctypes.POINTER(ctypes.c_byte),
            ),
        )
        blob_out = DATA_BLOB()
        if not crypt32.CryptProtectData(
            ctypes.byref(blob_in),
            None,
            None,
            None,
            None,
            0,
            ctypes.byref(blob_out),
        ):
            error_code = ctypes.get_last_error()
            error = ctypes.WinError(error_code)
            logger.error("DPAPI 加密失败，错误码 %s：%s", error_code, error)
            raise RuntimeError(
                f"无法加密 API Key（Windows 错误码 {error_code}）：{error}；"
                "已取消保存，请检查系统安全设置后重试"
            )
        try:
            raw = ctypes.string_at(blob_out.pbData, blob_out.cbData)
            return "enc:v1:" + base64.b64encode(raw).decode("ascii")
        finally:
            kernel32.LocalFree(blob_out.pbData)
    except RuntimeError:
        raise
    except Exception as exc:
        logger.exception("DPAPI 加密 API Key 异常")
        raise RuntimeError(f"无法加密 API Key，已取消保存：{exc}") from exc


def _unprotect_secret(value: str) -> str:
    if not isinstance(value, str) or not value.startswith("enc:v1:"):
        return value
    if sys.platform != "win32":
        logger.warning("非 Windows 环境无法解密 API Key，已清除")
        return ""
    try:
        ctypes, wintypes, crypt32, kernel32, DATA_BLOB = _dpapi_context()
        raw = base64.b64decode(value[len("enc:v1:"):], validate=True)
        blob_in = DATA_BLOB(
            len(raw),
            ctypes.cast(ctypes.create_string_buffer(raw), ctypes.POINTER(ctypes.c_byte)),
        )
        blob_out = DATA_BLOB()
        if not crypt32.CryptUnprotectData(
            ctypes.byref(blob_in),
            None,
            None,
            None,
            None,
            0,
            ctypes.byref(blob_out),
        ):
            error_code = ctypes.get_last_error()
            error = ctypes.WinError(error_code)
            logger.error("DPAPI 解密失败，错误码 %s：%s", error_code, error)
            return ""
        try:
            decrypted = ctypes.string_at(blob_out.pbData, blob_out.cbData)
            return decrypted.decode("utf-16-le")
        finally:
            kernel32.LocalFree(blob_out.pbData)
    except Exception:
        logger.exception("DPAPI 解密 API Key 异常")
        return ""
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
            raise ValueError("窗口编号不能为空，请删除空行或填写编号")
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


def resolve_task_windows_balanced(
    profile_values,
    window_values,
    pending_counts: dict[int, int] | None = None,
) -> list[int | None]:
    """Assign unassigned tasks to the least-busy window in sequence order."""
    windows = normalize_window_sequence(window_values)
    if not windows:
        return [None] * len(profile_values)
    counts = {
        number: int((pending_counts or {}).get(number, 0)) for number in windows
    }
    resolved: list[int | None] = []
    for raw_profile in profile_values:
        try:
            profile_no = int(raw_profile or 0)
        except (TypeError, ValueError):
            profile_no = 0
        if profile_no > 0:
            resolved.append(profile_no)
            continue
        candidate = min(
            windows,
            key=lambda number: (counts[number], windows.index(number)),
        )
        counts[candidate] += 1
        resolved.append(candidate)
    return resolved


def app_data_dir() -> Path:
    root = Path(os.getenv("LOCALAPPDATA", Path.home() / ".ophelia-mail-assistant"))
    path = root / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def settings_path() -> Path:
    return app_data_dir() / "settings.json"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _settings_metadata() -> dict[str, object]:
    from . import __version__

    return {
        "schema_version": SETTINGS_SCHEMA_VERSION,
        "app_version": str(__version__),
        "updated_at": _utc_now_iso(),
        "migration_id": SETTINGS_MIGRATION_ID,
    }


def _read_json_dict(path: Path) -> dict:
    """Read a JSON object, returning an empty dict for missing/corrupt files."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        logger.error("读取设置失败，文件路径：%s；错误：%s", path, exc)
        return {}
    if not isinstance(data, dict):
        logger.error("设置文件不是 JSON 对象，文件路径：%s", path)
        return {}
    return data


def _atomic_write_json(path: Path, data: dict) -> None:
    """Write JSON to a temp file, validate it, then atomically replace."""
    temp_path = path.with_name(path.name + ".tmp")
    try:
        temp_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        json.loads(temp_path.read_text(encoding="utf-8"))
        os.replace(temp_path, path)
    except Exception:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _append_migration_log(record: dict) -> None:
    path = app_data_dir() / "migrations.jsonl"
    try:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _backup_settings(path: Path) -> Path | None:
    try:
        backup_dir = app_data_dir() / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        target = backup_dir / f"settings_{stamp}.json"
        target.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        return target
    except OSError as exc:
        logger.warning("设置迁移备份失败：%s", exc)
        return None


@dataclass
class Settings:
    browser_provider: str = "morelogin"
    morelogin_url: str = "http://127.0.0.1:40000"
    adspower_url: str = "http://127.0.0.1:50325"
    adspower_api_key: str = ""
    bitbrowser_url: str = "http://127.0.0.1:54345"
    sender_name: str = DEFAULT_SENDER_NAME
    active_template_name: str = ""
    signature: str = ""
    language: str = "zh"
    theme_mode: str = "light"
    skin_name: str = "bit-light"
    skin_colors: dict[str, str] = field(default_factory=dict)
    background_image: str = ""
    last_update_check_at: str = ""
    subject_template: str = DEFAULT_SUBJECT_TEMPLATE
    body_template: str = DEFAULT_BODY_TEMPLATE
    custom_variables: dict[str, str] = field(default_factory=default_custom_variables)
    custom_variable_keys: list[str] = field(default_factory=list)
    hidden_system_variables: list[str] = field(default_factory=list)
    saved_templates: list[dict] = field(default_factory=list)
    window_bindings: dict[str, dict] = field(default_factory=dict)
    update_url: str = ""
    window_sequence: list[int] = field(default_factory=list)
    auto_click_send: bool = True
    auto_send_confirm: bool = True

    def __post_init__(self) -> None:
        self._api_key_dirty = False
        self._persisted_api_key = ""
        if self.browser_provider not in {"morelogin", "adspower", "bitbrowser"}:
            self.browser_provider = "morelogin"
        if self.language not in {"zh", "en"}:
            self.language = "zh"
        if self.theme_mode not in {"light", "dark"}:
            self.theme_mode = "light"
        raw_skin = self.skin_colors if isinstance(self.skin_colors, dict) else {}
        self.skin_colors = {
            str(key): str(value) for key, value in raw_skin.items()
        }
        if not isinstance(self.skin_name, str):
            self.skin_name = "bit-light"
        if not isinstance(self.background_image, str):
            self.background_image = ""
        self.adspower_api_key = _unprotect_secret(self.adspower_api_key)
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
        if not self.hidden_system_variables:
            # Sender comes from the window binding; hide the confusing card.
            self.hidden_system_variables = ["sender_name"]
        if not isinstance(self.saved_templates, list):
            self.saved_templates = []
        else:
            self.saved_templates = [
                entry for entry in self.saved_templates if isinstance(entry, dict)
            ]
        if not isinstance(self.window_bindings, dict):
            self.window_bindings = {}
        else:
            self.window_bindings = {
                str(window): (binding if isinstance(binding, dict) else {})
                for window, binding in self.window_bindings.items()
            }
        self.window_sequence = normalize_window_sequence(self.window_sequence)

    @classmethod
    def load(cls) -> "Settings":
        path = settings_path()
        if not path.exists():
            settings = cls()
            settings._schema_version = SETTINGS_SCHEMA_VERSION
            settings._app_version = ""
            settings._updated_at = ""
            settings._migration_id = ""
            settings._load_problems: list[str] = []
            return settings

        data = _read_json_dict(path)
        if not data:
            settings = cls()
            settings._schema_version = 0
            settings._app_version = ""
            settings._updated_at = ""
            settings._migration_id = ""
            settings._load_problems = [f"设置文件无法解析或不是对象：{path}"]
            return settings

        allowed = cls.__dataclass_fields__.keys()
        problems: list[str] = []
        try:
            settings = cls(
                **{k: v for k, v in data.items() if k in allowed}
            )
        except (TypeError, ValueError) as exc:
            logger.warning(
                "设置整体解析失败，逐字段恢复，文件路径：%s；错误：%s",
                path,
                exc,
            )
            settings = cls()
            for key, value in data.items():
                if key not in allowed:
                    continue
                try:
                    single = cls(**{key: value})
                    setattr(settings, key, getattr(single, key))
                except (TypeError, ValueError) as field_exc:
                    problems.append(f"{key}: {field_exc}")
                    logger.warning(
                        "设置字段解析失败，已恢复默认值，字段：%s，文件：%s；错误：%s",
                        key,
                        path,
                        field_exc,
                    )

        settings._persisted_api_key = str(data.get("adspower_api_key") or "")
        settings._api_key_dirty = False
        settings._schema_version = int(data.get("schema_version") or 0)
        settings._app_version = str(data.get("app_version") or "")
        settings._updated_at = str(data.get("updated_at") or "")
        settings._migration_id = str(data.get("migration_id") or "")
        settings._load_problems = problems

        if settings._schema_version < SETTINGS_SCHEMA_VERSION:
            try:
                backup_path = _backup_settings(path)
                settings.save()
                settings._schema_version = SETTINGS_SCHEMA_VERSION
                settings._app_version = str(__import__("ophelia_assistant").__version__)
                settings._migration_id = SETTINGS_MIGRATION_ID
                settings._updated_at = _utc_now_iso()
                _append_migration_log(
                    {
                        "time": _utc_now_iso(),
                        "kind": "settings",
                        "migration_id": SETTINGS_MIGRATION_ID,
                        "from_schema_version": int(
                            data.get("schema_version") or 0
                        ),
                        "to_schema_version": SETTINGS_SCHEMA_VERSION,
                        "app_version": str(__import__("ophelia_assistant").__version__),
                        "backup": str(backup_path) if backup_path else "",
                        "problems": problems,
                    }
                )
            except Exception as exc:
                logger.warning("设置结构迁移失败：%s", exc)
        return settings

    def mark_api_key_dirty(self) -> None:
        self._api_key_dirty = True

    def save(self, encrypt_api_key: bool = False) -> None:
        path = settings_path()
        existing = _read_json_dict(path)
        data = dict(existing)
        data.update(asdict(self))
        persisted_before = self._persisted_api_key
        dirty_before = self._api_key_dirty
        if self._api_key_dirty or encrypt_api_key:
            if self.adspower_api_key and sys.platform == "win32":
                new_persisted = _protect_secret(
                    self.adspower_api_key
                )
            else:
                new_persisted = self.adspower_api_key
        elif not self._persisted_api_key:
            # A freshly constructed Settings object must not erase an API key
            # that already exists on disk.
            new_persisted = str(existing.get("adspower_api_key") or "")
        else:
            new_persisted = self._persisted_api_key
        data["adspower_api_key"] = new_persisted
        data.update(_settings_metadata())
        try:
            _atomic_write_json(path, data)
        except Exception:
            self._persisted_api_key = persisted_before
            self._api_key_dirty = dirty_before
            raise
        self._persisted_api_key = new_persisted
        self._api_key_dirty = False

    def save_fields(self, fields: dict[str, object]) -> None:
        """Persist only the listed business fields, leaving API key untouched."""
        if not isinstance(fields, dict):
            raise ValueError("保存字段必须是字典")
        allowed = self.__dataclass_fields__
        for key in fields:
            if key == "adspower_api_key":
                raise ValueError(
                    "API Key 只能在设置页面明确修改并点击“保存设置”时保存"
                )
            if key not in allowed:
                raise ValueError(f"不支持保存的设置字段：{key}")

        merged = asdict(self)
        merged["adspower_api_key"] = self._persisted_api_key
        merged.update(fields)
        candidate = Settings(
            **{k: v for k, v in merged.items() if k in allowed}
        )
        candidate._persisted_api_key = self._persisted_api_key
        candidate._api_key_dirty = False

        path = settings_path()
        existing = _read_json_dict(path)
        data = dict(existing)
        data.update({key: getattr(candidate, key) for key in fields})
        data.update(_settings_metadata())
        _atomic_write_json(path, data)
        # Only after the atomic write succeeds do we publish new in-memory
        # state, and only for fields explicitly submitted by this operation.
        # API Key state must stay untouched by window/business partial saves.
        for key in fields:
            setattr(self, key, getattr(candidate, key))

    def save_window_sequence(self, sequence) -> None:
        normalized = normalize_window_sequence(sequence)
        self.save_fields({"window_sequence": normalized})

    def _clean_window_bindings(self, bindings: dict) -> dict[str, dict]:
        if not isinstance(bindings, dict):
            raise ValueError("窗口绑定必须是窗口编号到绑定信息的字典")
        cleaned: dict[str, dict] = {}
        for raw_window, binding in bindings.items():
            try:
                window = str(int(str(raw_window).strip()))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"窗口编号无效：{raw_window}") from exc
            if not isinstance(binding, dict):
                raise ValueError(f"窗口 {window} 的绑定信息无效")
            cleaned[window] = {
                "template_name": str(binding.get("template_name") or ""),
                "sender_name": str(binding.get("sender_name") or ""),
                "locked": bool(binding.get("locked")),
            }
        return cleaned

    def merge_window_bindings(self, bindings: dict) -> None:
        """Update only the submitted windows; keep all historical bindings."""
        cleaned = self._clean_window_bindings(bindings)
        existing = _read_json_dict(settings_path())
        raw_stored = existing.get("window_bindings")
        stored = (
            raw_stored
            if isinstance(raw_stored, dict)
            else dict(self.window_bindings or {})
        )
        merged: dict[str, dict] = {
            str(window): (dict(binding) if isinstance(binding, dict) else {})
            for window, binding in stored.items()
        }
        merged.update(cleaned)
        self.save_fields({"window_bindings": merged})

    def replace_window_bindings(self, bindings: dict) -> None:
        """Replace the whole binding map; only for explicit cleanup/recovery."""
        cleaned = self._clean_window_bindings(bindings)
        self.save_fields({"window_bindings": cleaned})

    def prune_window_bindings(self, keep_windows=None) -> int:
        """Permanently remove bindings outside the current window sequence."""
        if keep_windows is None:
            keep_windows = {str(number) for number in self.window_sequence}
        allowed = {str(window) for window in keep_windows}
        current = dict(self.window_bindings or {})
        kept = {
            str(window): binding
            for window, binding in current.items()
            if str(window) in allowed
        }
        removed = len(current) - len(kept)
        self.save_fields({"window_bindings": kept})
        return removed

    def save_window_bindings(self, bindings: dict) -> None:
        """Backward-compatible alias: normal saves merge, never delete history."""
        self.merge_window_bindings(bindings)
