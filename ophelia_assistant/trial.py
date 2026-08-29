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
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .config import APP_NAME


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
_CURRENT_STATE_VERSION = 3
_LEGACY_STATE_VERSIONS = {2}
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
_STATE_MIGRATION_ID = "niuma-license-primary-v3-2026-08-30"
_AUTHORIZATION_ANOMALY_TOLERANCE_SECONDS = 24 * 60 * 60


@dataclass(frozen=True)
class TrialStatus:
    active: bool
    reason: str
    started_at: int
    expires_at: int
    remaining_seconds: int
    suspicious: bool = False
    source: str = ""
    state_path: str = ""
    state_version: int = 0
    machine_digest: str = ""
    backup_used: bool = False
    migration_id: str = ""
    last_changed_at: int = 0
    change_reason: str = ""
    migrated_at: int = 0
    records_count: int = 0
    info: dict = field(default_factory=dict)


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


def _primary_state_path() -> Path:
    override = os.getenv("NIUMA_MAIL_TRIAL_DIR")
    if override:
        return Path(override) / "weekly_authorization.dat"
    local_data = (
        Path(os.getenv("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        / APP_NAME
        / "weekly_authorization.dat"
    )
    return local_data


def _state_paths() -> list[Path]:
    """Return the single primary authorization state path."""
    return [_primary_state_path()]


def _legacy_state_candidates() -> list[Path]:
    """Return old data directories that may still hold pre-v3 state."""
    override = os.getenv("NIUMA_MAIL_TRIAL_DIR")
    if override:
        return []
    candidates: list[Path] = []
    if os.name == "nt":
        program_data = (
            Path(os.getenv("PROGRAMDATA", r"C:\ProgramData"))
            / "NiuMaMail"
            / "weekly_authorization.dat"
        )
        candidates.append(program_data)
    local_data = (
        Path(os.getenv("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        / "NiuMaMail"
        / "weekly_authorization.dat"
    )
    if local_data not in candidates:
        candidates.append(local_data)
    roaming_data = (
        Path(os.getenv("APPDATA", Path.home() / "AppData" / "Roaming"))
        / "NiuMaMail"
        / "weekly_authorization.dat"
    )
    if roaming_data not in candidates:
        candidates.append(roaming_data)
    home_data = Path.home() / ".niuma-mail" / "weekly_authorization.dat"
    if home_data not in candidates:
        candidates.append(home_data)
    install_data = (
        Path(os.getenv("ProgramFiles", r"C:\Program Files"))
        / "NiuMaMail"
        / "weekly_authorization.dat"
    )
    if install_data not in candidates:
        candidates.append(install_data)
    return candidates


def _ever_marker_paths() -> list[Path]:
    override = os.getenv("NIUMA_MAIL_TRIAL_DIR")
    if override:
        return [Path(override) / "ever_installed.dat"]
    return [
        Path(os.getenv("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        / APP_NAME
        / "ever_installed.dat"
    ]


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
        record_version = int(payload.get("version", 0))
        if record_version != _CURRENT_STATE_VERSION and (
            record_version not in _LEGACY_STATE_VERSIONS
        ):
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
        payload.setdefault("grant_source", "legacy")
        payload.setdefault("granted_at", int(payload.get("last_seen_at", started)))
        payload.setdefault("change_reason", "")
        payload.setdefault("migration_id", "")
        payload.setdefault("migrated_at", 0)
        payload.setdefault("backup_used", False)
        payload.setdefault("updated_at", int(payload.get("last_seen_at", started)))
        payload["version"] = record_version
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


def _journal_path() -> Path:
    override = os.getenv("NIUMA_MAIL_TRIAL_DIR")
    if override:
        return Path(override) / "license_journal.jsonl"
    from .config import app_data_dir

    return app_data_dir() / "license_journal.jsonl"


def _data_dir() -> Path:
    override = os.getenv("NIUMA_MAIL_TRIAL_DIR")
    if override:
        return Path(override)
    from .config import app_data_dir

    return app_data_dir()


def _journal_append(record: dict) -> None:
    try:
        path = _data_dir() / "license_journal.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _journal_last() -> dict | None:
    path = _journal_path()
    if not path.exists():
        return None
    try:
        records = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        return records[-1] if records else None
    except (OSError, ValueError, TypeError):
        return None


def _append_migration_log(record: dict) -> None:
    path = _data_dir() / "migrations.jsonl"
    try:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _read_candidate(path: Path) -> tuple[str, dict | None]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return "", None
    if not raw:
        return "", None
    return raw, _decode_record(raw, _machine_digest().hex())


def _load_state_records() -> list[tuple[Path, str, dict]]:
    """Load valid records from the primary path, or legacy paths if absent."""
    primary = _primary_state_path()
    if primary.exists():
        raw, record = _read_candidate(primary)
        if raw:
            return [(primary, raw, record)] if record is not None else []
        return []

    found: list[tuple[Path, str, dict]] = []
    for path in _legacy_state_candidates():
        raw, record = _read_candidate(path)
        if raw and record is not None:
            found.append((path, raw, record))
    return found


def _invalid_state_present() -> bool:
    primary = _primary_state_path()
    if primary.exists():
        raw, record = _read_candidate(primary)
        return bool(raw) and record is None
    for path in _legacy_state_candidates():
        raw, record = _read_candidate(path)
        if raw and record is None:
            return True
    return False


def _choose_canonical(
    records: list[tuple[Path, str, dict]],
) -> tuple[Path, dict] | None:
    if not records:
        return None
    if len(records) == 1:
        return records[0][0], records[0][2]
    ranked = sorted(
        records,
        key=lambda item: (
            int(item[2].get("last_seen_at") or 0),
            int(item[2].get("version") or 0),
            int(item[2].get("authorized_until") or 0),
            str(item[2].get("migration_id") or ""),
        ),
        reverse=True,
    )
    return ranked[0][0], ranked[0][2]


def _explained_by_journal(authorized_until: int) -> bool:
    last = _journal_last()
    if last is None:
        return False
    if last.get("event") == "anomaly_detected":
        return False
    if last.get("event") in {
        "activation",
        "renewal",
        "migration",
        "recovery",
        "trial_creation",
    }:
        try:
            return int(last["new_until"]) == int(authorized_until)
        except (KeyError, TypeError, ValueError):
            return False
    try:
        return (
            abs(int(last.get("new_until") or 0) - int(authorized_until))
            <= _AUTHORIZATION_ANOMALY_TOLERANCE_SECONDS
        )
    except (TypeError, ValueError):
        return False


def _is_suspicious(record: dict) -> bool:
    """Detect unexplained authorization jumps in records without a journal."""
    authorized_until = int(record["authorized_until"])
    if _explained_by_journal(authorized_until):
        return False
    redeemed_codes = record.get("redeemed_codes") or {}
    grant_type = str(record.get("grant_type") or "")
    if grant_type != "admin" or not redeemed_codes:
        return False
    latest_code_expiry = max(int(value) for value in redeemed_codes.values())
    if authorized_until <= latest_code_expiry + _AUTHORIZATION_ANOMALY_TOLERANCE_SECONDS:
        return False
    return True


def _backup_legacy_file(path: Path) -> Path | None:
    try:
        backup_dir = _data_dir() / "backups" / "license"
        backup_dir.mkdir(parents=True, exist_ok=True)
        target = backup_dir / f"{path.parent.name}_{path.name}"
        target.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        return target
    except OSError:
        return None


def _save_state_v3(
    started_at: int,
    last_seen_at: int,
    authorized_until: int,
    grant_type: str,
    redeemed_codes: dict[str, int] | None = None,
    *,
    grant_source: str = "trial",
    change_reason: str = "",
    granted_at: int | None = None,
    migration_id: str = "",
    migrated_at: int = 0,
    backup_used: bool = False,
) -> bool:
    payload = {
        "version": _CURRENT_STATE_VERSION,
        "machine": _machine_digest().hex(),
        "started_at": started_at,
        "last_seen_at": last_seen_at,
        "authorized_until": authorized_until,
        "grant_type": grant_type,
        "grant_source": grant_source,
        "granted_at": int(granted_at or last_seen_at),
        "change_reason": change_reason,
        "migration_id": migration_id,
        "migrated_at": migrated_at,
        "backup_used": backup_used,
        "updated_at": int(time.time()),
        "redeemed_codes": redeemed_codes or {},
    }
    raw = _signed_record(payload)
    return _write_file(raw)


def _save_state(
    started_at: int,
    last_seen_at: int,
    authorized_until: int,
    grant_type: str,
    redeemed_codes: dict[str, int] | None = None,
) -> bool:
    """Backward-compatible wrapper that writes the current v3 format."""
    return _save_state_v3(
        started_at,
        last_seen_at,
        authorized_until,
        grant_type,
        redeemed_codes,
        grant_source="legacy",
        change_reason="旧版记录写回",
    )


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


def _records() -> tuple[list[str], list[dict[str, object]]]:
    loaded = _load_state_records()
    raw_records = [raw for _path, raw, _record in loaded]
    decoded = [record for _path, _raw, record in loaded]
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
    if _invalid_state_present():
        return TrialStatus(
            False,
            "授权记录无效或已被修改，请联系管理员验证",
            current,
            current,
            0,
            state_path=str(_primary_state_path()),
        )
    loaded = _load_state_records()
    raw_records = [raw for _path, raw, _record in loaded]
    decoded = [record for _path, _raw, record in loaded]
    if raw_records and not decoded:
        return TrialStatus(
            False,
            "授权记录无效或已被修改，请联系管理员验证",
            current,
            current,
            0,
            state_path=str(_primary_state_path()),
        )

    canonical_pair = _choose_canonical(loaded) if loaded else None
    canonical_path = canonical_pair[0] if canonical_pair else None
    canonical = canonical_pair[1] if canonical_pair else None
    cleared_reset = False
    started_at = last_seen_at = authorized_until = current
    grant_type = "trial"
    redeemed_codes: dict[str, int] = {}
    suspicious = False
    grant_source = ""
    change_reason = ""
    granted_at = 0
    migrated_at = 0
    backup_used = False
    migration_id = ""

    if canonical is not None:
        started_at = int(canonical["started_at"])
        last_seen_at = int(canonical["last_seen_at"])
        authorized_until = int(canonical["authorized_until"])
        grant_type = "admin" if canonical.get("grant_type") == "admin" else "trial"
        redeemed_codes = _active_redeemed_codes([canonical], current)
        state_version = int(canonical.get("version") or 0)
        grant_source = str(
            canonical.get("grant_source")
            or ("legacy" if state_version != _CURRENT_STATE_VERSION else "trial")
        )
        change_reason = str(canonical.get("change_reason") or "")
        granted_at = int(canonical.get("granted_at") or 0)
        migrated_at = int(canonical.get("migrated_at") or 0)
        backup_used = bool(canonical.get("backup_used"))
        migration_id = str(canonical.get("migration_id") or "")

        suspicious = _is_suspicious(
            {**canonical, "authorized_until": authorized_until}
        )
        if canonical_path is not None and str(canonical_path) != str(_primary_state_path()):
            if suspicious:
                grant_source = "legacy"
                change_reason = change_reason or "旧版数据目录只读"
            else:
                backup_used = _backup_legacy_file(canonical_path) is not None
                migration_id = _STATE_MIGRATION_ID
                migrated_at = current
                grant_source = "migration"
                change_reason = change_reason or "从旧版数据目录迁移到唯一主数据目录"
                saved = _save_state_v3(
                    started_at,
                    current,
                    authorized_until,
                    grant_type,
                    redeemed_codes,
                    grant_source=grant_source,
                    change_reason=change_reason,
                    granted_at=granted_at or current,
                    migration_id=migration_id,
                    migrated_at=migrated_at,
                    backup_used=backup_used,
                )
                if not saved:
                    return TrialStatus(
                        False,
                        "无法保存授权记录，请联系管理员",
                        started_at,
                        authorized_until,
                        0,
                    )
                _journal_append(
                    {
                        "time": current,
                        "event": "migration",
                        "new_until": authorized_until,
                        "reason": change_reason,
                        "migration_id": migration_id,
                    }
                )
                _append_migration_log(
                    {
                        "time": current,
                        "kind": "license",
                        "migration_id": migration_id,
                        "from_path": str(canonical_path),
                        "to_path": str(_primary_state_path()),
                        "backup_used": backup_used,
                    }
                )
                canonical_path = _primary_state_path()
                state_version = _CURRENT_STATE_VERSION
        elif state_version != _CURRENT_STATE_VERSION and not suspicious:
            migration_id = _STATE_MIGRATION_ID
            migrated_at = current
            grant_source = grant_source or "migration"
            change_reason = change_reason or "授权状态格式升级"
            saved = _save_state_v3(
                started_at,
                current,
                authorized_until,
                grant_type,
                redeemed_codes,
                grant_source=grant_source,
                change_reason=change_reason,
                granted_at=granted_at or current,
                migration_id=migration_id,
                migrated_at=migrated_at,
                backup_used=backup_used,
            )
            if not saved:
                return TrialStatus(
                    False,
                    "无法保存授权记录，请联系管理员",
                    started_at,
                    authorized_until,
                    0,
                )
            _journal_append(
                {
                    "time": current,
                    "event": "migration",
                    "new_until": authorized_until,
                    "reason": change_reason,
                    "migration_id": migration_id,
                }
            )
            state_version = _CURRENT_STATE_VERSION
    else:
        if _read_marker():
            cleared_reset = True
        else:
            started_at = current
            last_seen_at = current
            authorized_until = current + TRIAL_SECONDS
            grant_type = "trial"
            grant_source = "trial"
            change_reason = "首次试用启动"
            granted_at = current
            if not _save_state_v3(
                started_at,
                current,
                authorized_until,
                grant_type,
                redeemed_codes,
                grant_source=grant_source,
                change_reason=change_reason,
                granted_at=granted_at,
            ):
                return TrialStatus(
                    False,
                    "无法保存授权记录，请联系管理员",
                    started_at,
                    authorized_until,
                    0,
                )
            _journal_append(
                {
                    "time": current,
                    "event": "trial_creation",
                    "new_until": authorized_until,
                    "reason": change_reason,
                }
            )
            _write_marker()

    rollback = current + CLOCK_ROLLBACK_TOLERANCE_SECONDS < last_seen_at
    active = not rollback and current < authorized_until
    if suspicious:
        last_journal = _journal_last()
        if (
            last_journal is None
            or last_journal.get("event") != "anomaly_detected"
            or int(last_journal.get("new_until") or 0) != authorized_until
        ):
            _journal_append(
                {
                    "time": current,
                    "event": "anomaly_detected",
                    "new_until": authorized_until,
                    "reason": "授权状态异常，已暂停自动写回",
                    "state_path": str(
                        canonical_path or _primary_state_path()
                    ),
                }
            )
    if cleared_reset:
        reason = "检测到授权记录被清除，试用已结束；请联系管理员重新验证"
    elif rollback:
        reason = "检测到系统时间回拨，功能已锁定，请联系管理员验证"
    elif suspicious:
        reason = "检测到授权状态异常，请核对（已暂停自动写回）"
    elif not active:
        reason = (
            "3天试用期已结束，请联系管理员获取验证码"
            if grant_type == "trial"
            else "管理员授权已过期，请联系管理员获取验证码"
        )
    else:
        reason = ""

    if not suspicious and not cleared_reset and current > last_seen_at:
        saved = _save_state_v3(
            started_at,
            current,
            authorized_until,
            grant_type,
            redeemed_codes,
            grant_source=grant_source or "trial",
            change_reason=change_reason or "最后在线时间更新",
            granted_at=granted_at or last_seen_at,
            migration_id=migration_id or _STATE_MIGRATION_ID,
            migrated_at=migrated_at,
            backup_used=backup_used,
        )
        if not saved:
            return TrialStatus(
                False,
                "无法保存授权记录，请联系管理员",
                started_at,
                authorized_until,
                0,
            )
    remaining = max(0, authorized_until - current) if active else 0
    return TrialStatus(
        active,
        reason,
        started_at,
        authorized_until,
        remaining,
        suspicious=suspicious,
        source=grant_source or ("trial" if grant_type == "trial" else "admin"),
        state_path=str(canonical_path or _primary_state_path()),
        state_version=int((canonical or {}).get("version") or _CURRENT_STATE_VERSION),
        machine_digest=str((canonical or {}).get("machine") or _machine_digest().hex()),
        backup_used=backup_used,
        migration_id=migration_id,
        last_changed_at=granted_at or last_seen_at,
        change_reason=change_reason,
        migrated_at=migrated_at,
        records_count=len(decoded),
        info={
            "authorized_until": authorized_until,
            "last_seen_at": last_seen_at,
            "redeemed_code_count": len(redeemed_codes),
            "rollback": rollback,
            "cleared_reset": cleared_reset,
        },
    )


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

    if _invalid_state_present():
        status = check_trial(now=current)
        return False, "授权记录无效或已被修改，请联系管理员验证", status

    loaded = _load_state_records()
    canonical_pair = _choose_canonical(loaded) if loaded else None
    canonical = canonical_pair[1] if canonical_pair else None
    if canonical is not None:
        redeemed_codes = {
            str(code_id): int(expires_at)
            for code_id, expires_at in (canonical.get("redeemed_codes") or {}).items()
            if int(expires_at) > current
        }
        started_at = min(int(canonical["started_at"]), current)
        existing_until = int(canonical["authorized_until"])
    else:
        redeemed_codes = {}
        started_at = current
        existing_until = current
    code_id = hashlib.sha256(token).hexdigest()
    if code_id in redeemed_codes:
        status = check_trial(now=current)
        return False, "该验证码已经使用过，不能重复叠加", status

    accumulation_base = max(current, existing_until)
    authorized_until = accumulation_base + authorization_seconds
    redeemed_codes[code_id] = expires_at
    grant_source = "activation" if existing_until <= current else "renewal"
    change_reason = "首次激活验证码" if grant_source == "activation" else "续期验证码"
    if not _save_state_v3(
        started_at,
        current,
        authorized_until,
        "admin",
        redeemed_codes,
        grant_source=grant_source,
        change_reason=change_reason,
        granted_at=current,
        migration_id=_STATE_MIGRATION_ID,
    ):
        status = TrialStatus(False, "无法保存授权记录，请联系管理员", started_at, authorized_until, 0)
        return False, status.reason, status
    _journal_append(
        {
            "time": current,
            "event": grant_source,
            "old_until": existing_until,
            "new_until": authorized_until,
            "reason": change_reason,
            "code_id": code_id[:12],
        }
    )
    status = check_trial(now=current)
    expiry_date = datetime.fromtimestamp(authorized_until).strftime("%Y-%m-%d")
    return True, f"验证成功，授权时间已累计；{remaining_text(status)}，到期 {expiry_date}", status


def authorization_info(status: TrialStatus | None = None) -> dict:
    """Return redacted authorization diagnostics for the settings page."""
    if status is None:
        status = check_trial()
    loaded = _load_state_records()
    canonical_pair = _choose_canonical(loaded) if loaded else None
    canonical = canonical_pair[1] if canonical_pair else None
    expires_at = int(status.expires_at or 0)
    return {
        "primary_state_path": str(_primary_state_path()),
        "state_path": status.state_path or str(_primary_state_path()),
        "legacy_candidates": [str(path) for path in _legacy_state_candidates()],
        "records_count": status.records_count or len(loaded),
        "authorized_until": expires_at,
        "authorized_until_iso": (
            datetime.fromtimestamp(expires_at).isoformat() if expires_at else ""
        ),
        "remaining_seconds": int(status.remaining_seconds),
        "started_at": int(status.started_at),
        "last_seen_at": int((status.info or {}).get("last_seen_at") or status.started_at),
        "grant_source": status.source,
        "last_changed_at": int(status.last_changed_at),
        "change_reason": status.change_reason,
        "state_version": int(status.state_version),
        "machine_digest": status.machine_digest,
        "backup_used": bool(status.backup_used),
        "migrated_at": int(status.migrated_at),
        "migration_id": status.migration_id,
        "suspicious": bool(status.suspicious),
        "redeemed_code_count": (
            len(canonical.get("redeemed_codes") or {}) if canonical else 0
        ),
        "journal_path": str(_journal_path()),
        "journal_last": _journal_last(),
    }


def remaining_text(status: TrialStatus) -> str:
    if not status.active:
        return "等待管理员验证"
    days, remainder = divmod(status.remaining_seconds, 24 * 60 * 60)
    hours, remainder = divmod(remainder, 60 * 60)
    minutes = remainder // 60
    if days:
        return f"授权剩余 {days}天 {hours}小时"
    return f"授权剩余 {hours}小时 {minutes}分钟"
