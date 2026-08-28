from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import platform
import struct
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


TRIAL_DAYS = 3
TRIAL_SECONDS = TRIAL_DAYS * 24 * 60 * 60
AUTHORIZATION_DAYS = 7
AUTHORIZATION_SECONDS = AUTHORIZATION_DAYS * 24 * 60 * 60
ALLOWED_AUTHORIZATION_DAYS = (1, 3, 7, 15, 30, 60, 90, 180, 360)
ALLOWED_AUTHORIZATION_SECONDS = {
    days * 24 * 60 * 60 for days in ALLOWED_AUTHORIZATION_DAYS
}
CLOCK_ROLLBACK_TOLERANCE_SECONDS = 5 * 60
_STATE_VERSION = 2
_CODE_VERSION = 1
_STATE_SIGNING_KEY = b"NiuMaMail-weekly-authorization-state-v2-2026"
_EVER_MARKER_VERSION = 1
_EVER_MARKER_SIGNING_KEY = b"NiuMaMail-ever-installed-marker-v1-2026"
_PUBLIC_KEY_RAW = base64.b64decode("bC2CFf3GNyzLHgfJXChp2/MxLYZjPWANWC2qMYpT104=")
_REGISTRY_PATH = r"Software\NiuMaMail"
_REGISTRY_VALUE = "WeeklyAuthorizationState"
_EVER_MARKER_REGISTRY_VALUE = "EverInstalled"
_CODE_STRUCT = struct.Struct(">B16sIII")
_MACHINE_SOURCE_CACHE: str | None = None


@dataclass(frozen=True)
class TrialStatus:
    active: bool
    reason: str
    started_at: int
    expires_at: int
    remaining_seconds: int


def _volume_serial() -> str:
    if os.name != "nt":
        return ""
    try:
        import ctypes

        serial = ctypes.c_ulong(0)
        name_buffer = ctypes.create_unicode_buffer(261)
        fs_buffer = ctypes.create_unicode_buffer(261)
        if ctypes.windll.kernel32.GetVolumeInformationW(
            ctypes.c_wchar_p("C:\\"),
            name_buffer,
            261,
            ctypes.byref(serial),
            None,
            None,
            fs_buffer,
            261,
        ):
            return f"{serial.value:08X}"
    except Exception:
        pass
    return ""


def _wmic_query(alias: str, field: str) -> str:
    """Fetch a hardware identifier through wmic with a short timeout."""
    if os.name != "nt":
        return ""
    try:
        import subprocess

        result = subprocess.run(
            ["wmic", alias, "get", field, "/value"],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=0x08000000,
        )
        for line in (result.stdout or "").splitlines():
            if line.startswith(field + "="):
                value = line.split("=", 1)[1].strip()
                if value:
                    return value
    except Exception:
        pass
    return ""


def _machine_digest() -> bytes:
    global _MACHINE_SOURCE_CACHE
    override = os.getenv("NIUMA_MAIL_MACHINE_ID")
    if override:
        return hashlib.sha256(override.encode("utf-8")).digest()[:16]
    if _MACHINE_SOURCE_CACHE is None:
        parts: list[str] = []
        if os.name == "nt":
            try:
                import winreg

                with winreg.OpenKey(
                    winreg.HKEY_LOCAL_MACHINE,
                    r"SOFTWARE\Microsoft\Cryptography",
                ) as key:
                    parts.append(str(winreg.QueryValueEx(key, "MachineGuid")[0]))
            except (OSError, ImportError):
                pass
            try:
                import winreg

                with winreg.OpenKey(
                    winreg.HKEY_LOCAL_MACHINE,
                    r"SOFTWARE\Microsoft\Windows NT\CurrentVersion",
                ) as key:
                    parts.append(str(winreg.QueryValueEx(key, "ProductId")[0]))
            except (OSError, ImportError):
                pass
            volume_serial = _volume_serial()
            if volume_serial:
                parts.append(volume_serial)
            bios_uuid = _wmic_query("csproduct", "uuid")
            if bios_uuid:
                parts.append(bios_uuid)
            disk_serial = _wmic_query("diskdrive", "serialnumber")
            if disk_serial:
                parts.append(disk_serial)
            parts.append(str(uuid.getnode()))
        if not parts:
            parts.append(f"{platform.node()}|{uuid.getnode()}")
        _MACHINE_SOURCE_CACHE = "|".join(parts)
    return hashlib.sha256(_MACHINE_SOURCE_CACHE.encode("utf-8")).digest()[:16]


def device_code() -> str:
    raw = _machine_digest().hex().upper()
    return "-".join(raw[index:index + 4] for index in range(0, len(raw), 4))


def _state_paths() -> list[Path]:
    override = os.getenv("NIUMA_MAIL_TRIAL_DIR")
    if override:
        return [Path(override) / "weekly_authorization.dat"]
    paths: list[Path] = []
    if os.name == "nt":
        program_data = (
            Path(os.getenv("PROGRAMDATA", r"C:\ProgramData"))
            / "NiuMaMail"
            / "weekly_authorization.dat"
        )
        paths.append(program_data)
    local_data = (
        Path(os.getenv("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        / "NiuMaMail"
        / "weekly_authorization.dat"
    )
    if local_data not in paths:
        paths.append(local_data)
    if not paths:
        paths.append(Path.home() / ".niuma-mail" / "weekly_authorization.dat")
    return paths


def _ever_marker_paths() -> list[Path]:
    override = os.getenv("NIUMA_MAIL_TRIAL_DIR")
    if override:
        return [Path(override) / "ever_installed.dat"]
    local_data = (
        Path(os.getenv("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        / "NiuMaMail"
        / "ever_installed.dat"
    )
    paths = [local_data]
    if os.name == "nt":
        program_data = (
            Path(os.getenv("PROGRAMDATA", r"C:\ProgramData"))
            / "NiuMaMail"
            / "ever_installed.dat"
        )
        if program_data not in paths:
            paths.append(program_data)
    return paths


def _registry_disabled() -> bool:
    return os.getenv("NIUMA_MAIL_DISABLE_REGISTRY") == "1"


def _signed_record(payload: dict[str, object]) -> str:
    canonical = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    signature = hmac.new(
        _STATE_SIGNING_KEY, canonical.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return json.dumps({"payload": payload, "signature": signature}, separators=(",", ":"))


def _decode_record(raw: str, machine_hex: str) -> dict[str, object] | None:
    try:
        envelope = json.loads(raw)
        payload = envelope["payload"]
        signature = str(envelope["signature"])
        canonical = json.dumps(
            payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")
        )
        expected = hmac.new(
            _STATE_SIGNING_KEY, canonical.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        if int(payload.get("version", 0)) != _STATE_VERSION:
            return None
        if str(payload.get("machine", "")) != machine_hex:
            return None
        started = int(payload["started_at"])
        last_seen = int(payload["last_seen_at"])
        authorized_until = int(payload["authorized_until"])
        if started <= 0 or last_seen < started or authorized_until < started:
            return None
        raw_redeemed_codes = payload.get("redeemed_codes", {})
        if not isinstance(raw_redeemed_codes, dict):
            return None
        redeemed_codes: dict[str, int] = {}
        for code_id, code_expires_at in raw_redeemed_codes.items():
            clean_code_id = str(code_id)
            clean_expires_at = int(code_expires_at)
            if len(clean_code_id) != 64 or clean_expires_at <= 0:
                return None
            int(clean_code_id, 16)
            redeemed_codes[clean_code_id] = clean_expires_at
        payload["redeemed_codes"] = redeemed_codes
        return payload
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _read_file() -> list[str]:
    values: list[str] = []
    for path in _state_paths():
        try:
            raw = path.read_text(encoding="utf-8")
            if raw and raw not in values:
                values.append(raw)
        except OSError:
            pass
    return values


def _write_file(raw: str) -> bool:
    saved = False
    for path in _state_paths():
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(raw, encoding="utf-8")
            temporary.replace(path)
            saved = True
        except OSError:
            pass
    return saved


def _read_registry() -> str | None:
    if os.name != "nt" or _registry_disabled():
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REGISTRY_PATH) as key:
            return str(winreg.QueryValueEx(key, _REGISTRY_VALUE)[0])
    except (OSError, ImportError):
        return None


def _write_registry(raw: str) -> bool:
    if os.name != "nt" or _registry_disabled():
        return False
    try:
        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _REGISTRY_PATH) as key:
            winreg.SetValueEx(key, _REGISTRY_VALUE, 0, winreg.REG_SZ, raw)
        return True
    except (OSError, ImportError):
        return False


def _signed_marker() -> str:
    payload = {
        "version": _EVER_MARKER_VERSION,
        "machine": _machine_digest().hex(),
        "first_seen_at": int(time.time()),
    }
    canonical = json.dumps(
        payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    )
    signature = hmac.new(
        _EVER_MARKER_SIGNING_KEY, canonical.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return json.dumps(
        {"payload": payload, "signature": signature}, separators=(",", ":")
    )


def _decode_marker(raw: str) -> bool:
    try:
        envelope = json.loads(raw)
        payload = envelope["payload"]
        signature = str(envelope["signature"])
        canonical = json.dumps(
            payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")
        )
        expected = hmac.new(
            _EVER_MARKER_SIGNING_KEY, canonical.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return False
        if int(payload.get("version", 0)) != _EVER_MARKER_VERSION:
            return False
        if str(payload.get("machine", "")) != _machine_digest().hex():
            return False
        return True
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False


def _read_marker() -> bool:
    raw_records: list[str] = []
    for marker_path in _ever_marker_paths():
        if marker_path.exists():
            try:
                raw = marker_path.read_text(encoding="utf-8")
                if raw:
                    raw_records.append(raw)
            except OSError:
                pass
    if os.name == "nt" and not _registry_disabled():
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REGISTRY_PATH) as key:
                raw_records.append(
                    str(winreg.QueryValueEx(key, _EVER_MARKER_REGISTRY_VALUE)[0])
                )
        except (OSError, ImportError):
            pass
    return any(_decode_marker(raw) for raw in raw_records if raw)


def _write_marker() -> bool:
    raw = _signed_marker()
    file_saved = False
    for marker_path in _ever_marker_paths():
        try:
            marker_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = marker_path.with_suffix(".tmp")
            temporary.write_text(raw, encoding="utf-8")
            temporary.replace(marker_path)
            file_saved = True
        except OSError:
            pass
    registry_saved = False
    if os.name == "nt" and not _registry_disabled():
        try:
            import winreg

            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _REGISTRY_PATH) as key:
                winreg.SetValueEx(
                    key, _EVER_MARKER_REGISTRY_VALUE, 0, winreg.REG_SZ, raw
                )
            registry_saved = True
        except (OSError, ImportError):
            pass
    return file_saved or registry_saved


def _save_state(
    started_at: int,
    last_seen_at: int,
    authorized_until: int,
    grant_type: str,
    redeemed_codes: dict[str, int] | None = None,
) -> bool:
    payload = {
        "version": _STATE_VERSION,
        "machine": _machine_digest().hex(),
        "started_at": started_at,
        "last_seen_at": last_seen_at,
        "authorized_until": authorized_until,
        "grant_type": grant_type,
        "redeemed_codes": redeemed_codes or {},
    }
    raw = _signed_record(payload)
    file_saved = _write_file(raw)
    registry_saved = _write_registry(raw)
    return file_saved or registry_saved


def _records() -> tuple[list[str], list[dict[str, object]]]:
    machine_hex = _machine_digest().hex()
    raw_records = list(_read_file())
    registry_value = _read_registry()
    if registry_value and registry_value not in raw_records:
        raw_records.append(registry_value)
    decoded = [
        record
        for raw in raw_records
        if (record := _decode_record(raw, machine_hex)) is not None
    ]
    return raw_records, decoded


def _active_redeemed_codes(
    records: list[dict[str, object]], current: int
) -> dict[str, int]:
    merged: dict[str, int] = {}
    for record in records:
        redeemed_codes = record.get("redeemed_codes", {})
        if not isinstance(redeemed_codes, dict):
            continue
        for code_id, expires_at in redeemed_codes.items():
            clean_expires_at = int(expires_at)
            if clean_expires_at > current:
                merged[str(code_id)] = max(merged.get(str(code_id), 0), clean_expires_at)
    return merged


def check_trial(now: int | None = None) -> TrialStatus:
    """Return weekly authorization status without deleting application data."""
    current = int(time.time() if now is None else now)
    raw_records, decoded = _records()
    if raw_records and not decoded:
        return TrialStatus(False, "授权记录无效或已被修改，请联系管理员验证", current, current, 0)

    redeemed_codes = _active_redeemed_codes(decoded, current)
    cleared_reset = False
    if decoded:
        started_at = min(int(record["started_at"]) for record in decoded)
        last_seen_at = max(int(record["last_seen_at"]) for record in decoded)
        authorized_until = max(int(record["authorized_until"]) for record in decoded)
        explicit_admin = any(record.get("grant_type") == "admin" for record in decoded)
        legacy_admin = authorized_until > started_at + AUTHORIZATION_SECONDS
        grant_type = "admin" if explicit_admin or legacy_admin else "trial"
        if grant_type == "trial":
            # Keep the original first-use timestamp, but migrate older 1-day
            # trial records to the current 3-day free period.
            authorized_until = started_at + TRIAL_SECONDS
    else:
        if _read_marker():
            cleared_reset = True
            started_at = current
            last_seen_at = current
            authorized_until = current
            grant_type = "trial"
        else:
            started_at = current
            last_seen_at = current
            authorized_until = current + TRIAL_SECONDS
            grant_type = "trial"
            _write_marker()

    rollback = current + CLOCK_ROLLBACK_TOLERANCE_SECONDS < last_seen_at
    active = not rollback and current < authorized_until
    if cleared_reset:
        reason = "检测到授权记录被清除，试用已结束；请联系管理员重新验证"
    elif rollback:
        reason = "检测到系统时间回拨，功能已锁定，请联系管理员验证"
    elif not active:
        reason = (
            "3天试用期已结束，请联系管理员获取验证码"
            if grant_type == "trial"
            else "管理员授权已过期，请联系管理员获取验证码"
        )
    else:
        reason = ""

    if not _save_state(
        started_at,
        max(current, last_seen_at),
        authorized_until,
        grant_type,
        redeemed_codes,
    ):
        return TrialStatus(False, "无法保存授权记录，请联系管理员", started_at, authorized_until, 0)
    remaining = max(0, authorized_until - current) if active else 0
    return TrialStatus(active, reason, started_at, authorized_until, remaining)


def verify_authorization_code(
    code: str, now: int | None = None
) -> tuple[bool, str, TrialStatus]:
    current = int(time.time() if now is None else now)
    normalized = "".join(character for character in code.upper() if character.isalnum())
    try:
        padded = normalized + "=" * ((8 - len(normalized) % 8) % 8)
        token = base64.b32decode(padded, casefold=True)
        signed_payload, signature = token[:-64], token[-64:]
        version, machine, issued_at, expires_at, _nonce = _CODE_STRUCT.unpack(signed_payload)
        Ed25519PublicKey.from_public_bytes(_PUBLIC_KEY_RAW).verify(signature, signed_payload)
    except (ValueError, struct.error, InvalidSignature):
        status = check_trial(now=current)
        return False, "验证码无效，请核对后重新输入", status

    if version != _CODE_VERSION or not hmac.compare_digest(machine, _machine_digest()):
        status = check_trial(now=current)
        return False, "验证码与本机设备码不匹配", status
    if issued_at > current + CLOCK_ROLLBACK_TOLERANCE_SECONDS:
        status = check_trial(now=current)
        return False, "系统时间不正确，验证码尚未生效", status
    if expires_at <= current:
        status = check_trial(now=current)
        return False, "验证码已经过期，请联系管理员重新生成", status
    authorization_seconds = expires_at - issued_at
    if authorization_seconds not in ALLOWED_AUTHORIZATION_SECONDS:
        status = check_trial(now=current)
        return False, "验证码有效期异常", status

    _raw, decoded = _records()
    redeemed_codes = _active_redeemed_codes(decoded, current)
    code_id = hashlib.sha256(token).hexdigest()
    if code_id in redeemed_codes:
        status = check_trial(now=current)
        return False, "该验证码已经使用过，不能重复叠加", status

    started_at = min([int(record["started_at"]) for record in decoded] or [current])
    existing_until = max([int(record["authorized_until"]) for record in decoded] or [current])
    accumulation_base = max(current, existing_until)
    authorized_until = accumulation_base + authorization_seconds
    redeemed_codes[code_id] = expires_at
    if not _save_state(
        started_at,
        current,
        authorized_until,
        "admin",
        redeemed_codes,
    ):
        status = TrialStatus(False, "无法保存授权记录，请联系管理员", started_at, authorized_until, 0)
        return False, status.reason, status
    status = check_trial(now=current)
    expiry_date = datetime.fromtimestamp(authorized_until).strftime("%Y-%m-%d")
    return True, f"验证成功，授权时间已累计；{remaining_text(status)}，到期 {expiry_date}", status


def remaining_text(status: TrialStatus) -> str:
    if not status.active:
        return "等待管理员验证"
    days, remainder = divmod(status.remaining_seconds, 24 * 60 * 60)
    hours, remainder = divmod(remainder, 60 * 60)
    minutes = remainder // 60
    if days:
        return f"授权剩余 {days}天 {hours}小时"
    return f"授权剩余 {hours}小时 {minutes}分钟"
