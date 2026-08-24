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
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


TRIAL_DAYS = 3
TRIAL_SECONDS = TRIAL_DAYS * 24 * 60 * 60
AUTHORIZATION_DAYS = 7
AUTHORIZATION_SECONDS = AUTHORIZATION_DAYS * 24 * 60 * 60
ALLOWED_AUTHORIZATION_DAYS = (1, 3, 7, 15, 30)
ALLOWED_AUTHORIZATION_SECONDS = {
    days * 24 * 60 * 60 for days in ALLOWED_AUTHORIZATION_DAYS
}
MAX_CUMULATIVE_AUTHORIZATION_DAYS = 30
MAX_CUMULATIVE_AUTHORIZATION_SECONDS = MAX_CUMULATIVE_AUTHORIZATION_DAYS * 24 * 60 * 60
CLOCK_ROLLBACK_TOLERANCE_SECONDS = 5 * 60
_STATE_VERSION = 2
_CODE_VERSION = 1
_STATE_SIGNING_KEY = b"NiuMaMail-weekly-authorization-state-v2-2026"
_PUBLIC_KEY_RAW = base64.b64decode("SSo6QAY9sejpfzJ2MogyD0DoLkglQhUcFfgRLyz/Fho=")
_REGISTRY_PATH = r"Software\NiuMaMail"
_REGISTRY_VALUE = "WeeklyAuthorizationState"
_CODE_STRUCT = struct.Struct(">B16sIII")


@dataclass(frozen=True)
class TrialStatus:
    active: bool
    reason: str
    started_at: int
    expires_at: int
    remaining_seconds: int


def _machine_digest() -> bytes:
    override = os.getenv("NIUMA_MAIL_MACHINE_ID")
    if override:
        source = override
    else:
        source = ""
        if os.name == "nt":
            try:
                import winreg

                with winreg.OpenKey(
                    winreg.HKEY_LOCAL_MACHINE,
                    r"SOFTWARE\Microsoft\Cryptography",
                ) as key:
                    source = str(winreg.QueryValueEx(key, "MachineGuid")[0])
            except (OSError, ImportError):
                source = ""
        if not source:
            source = f"{platform.node()}|{uuid.getnode()}"
    return hashlib.sha256(source.encode("utf-8")).digest()[:16]


def device_code() -> str:
    raw = _machine_digest().hex().upper()
    return "-".join(raw[index:index + 4] for index in range(0, len(raw), 4))


def _state_path() -> Path:
    override = os.getenv("NIUMA_MAIL_TRIAL_DIR")
    if override:
        root = Path(override)
    elif os.name == "nt":
        root = Path(os.getenv("PROGRAMDATA", r"C:\ProgramData")) / "NiuMaMail"
    else:
        root = Path.home() / ".niuma-mail"
    return root / "weekly_authorization.dat"


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


def _read_file() -> str | None:
    try:
        return _state_path().read_text(encoding="utf-8")
    except OSError:
        return None


def _write_file(raw: str) -> bool:
    path = _state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(raw, encoding="utf-8")
        temporary.replace(path)
        return True
    except OSError:
        return False


def _read_registry() -> str | None:
    if os.name != "nt":
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REGISTRY_PATH) as key:
            return str(winreg.QueryValueEx(key, _REGISTRY_VALUE)[0])
    except (OSError, ImportError):
        return None


def _write_registry(raw: str) -> bool:
    if os.name != "nt":
        return False
    try:
        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _REGISTRY_PATH) as key:
            winreg.SetValueEx(key, _REGISTRY_VALUE, 0, winreg.REG_SZ, raw)
        return True
    except (OSError, ImportError):
        return False


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
    raw_records = [value for value in (_read_file(), _read_registry()) if value]
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
        started_at = current
        last_seen_at = current
        authorized_until = current + TRIAL_SECONDS
        grant_type = "trial"

    rollback = current + CLOCK_ROLLBACK_TOLERANCE_SECONDS < last_seen_at
    active = not rollback and current < authorized_until
    if rollback:
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
    maximum_until = current + MAX_CUMULATIVE_AUTHORIZATION_SECONDS
    if accumulation_base >= maximum_until:
        status = check_trial(now=current)
        return False, "当前累计授权已达到30天上限，请勿继续叠加", status

    authorized_until = min(accumulation_base + authorization_seconds, maximum_until)
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
    return True, f"验证成功，授权时间已叠加；{remaining_text(status)}（最多30天）", status


def remaining_text(status: TrialStatus) -> str:
    if not status.active:
        return "等待管理员验证"
    days, remainder = divmod(status.remaining_seconds, 24 * 60 * 60)
    hours, remainder = divmod(remainder, 60 * 60)
    minutes = remainder // 60
    if days:
        return f"授权剩余 {days}天 {hours}小时"
    return f"授权剩余 {hours}小时 {minutes}分钟"
