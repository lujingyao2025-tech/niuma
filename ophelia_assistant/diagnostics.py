"""Structured execution trail and error reports for remote debugging."""

from __future__ import annotations

import dataclasses
import json
import logging
import platform
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path


logger = logging.getLogger("niuma-mail")


def diagnostics_dir() -> Path:
    from .config import app_data_dir

    path = app_data_dir() / "diagnostics"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _append(record: dict) -> None:
    try:
        path = diagnostics_dir() / "execution.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


def trace_execution(
    task_id: int | None,
    stage: str,
    message: str,
    profile_no: int | None = None,
    extra: dict | None = None,
) -> None:
    record = {
        "time": _now(),
        "event": "task",
        "task_id": task_id,
        "stage": stage,
        "profile_no": profile_no,
        "message": message,
    }
    if extra:
        record["extra"] = extra
    _append(record)
    logger.info("任务 %s 阶段 %s：%s", task_id, stage, message)


def redact_settings(settings) -> dict:
    try:
        if dataclasses.is_dataclass(settings):
            data = dataclasses.asdict(settings)
        elif isinstance(settings, dict):
            data = dict(settings)
        else:
            data = {
                key: getattr(settings, key)
                for key in dir(settings)
                if not key.startswith("_")
            }
    except Exception:
        data = {}
    redacted: dict = {}
    for key, value in data.items():
        lowered = str(key).lower()
        if "key" in lowered or "secret" in lowered or "token" in lowered:
            redacted[key] = "<redacted>"
        else:
            redacted[key] = value
    return redacted


def recent_trail(task_id: int | None = None, limit: int = 200) -> list[dict]:
    records: list[dict] = []
    path = diagnostics_dir() / "execution.jsonl"
    if not path.exists():
        return records
    try:
        for line in path.read_text(encoding="utf-8").splitlines()[-limit:]:
            try:
                records.append(json.loads(line))
            except ValueError:
                pass
    except OSError:
        pass
    if task_id is not None:
        records = [record for record in records if record.get("task_id") == task_id]
    return records


def write_error_report(
    exc: BaseException,
    *,
    task_id: int | None = None,
    stage: str = "",
    profile_no: int | None = None,
    title: str = "",
    screenshot_path: str = "",
    settings=None,
    extra_trail: list | None = None,
    context: dict | None = None,
) -> Path:
    """Write a self-contained error report the user can send back verbatim."""
    from . import __version__

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = diagnostics_dir() / f"error_{stamp}_task{task_id or 0}.log"
    lines = [
        "牛马邮箱错误报告",
        f"生成时间: {_now()}",
        f"版本: v{__version__}",
        f"平台: {platform.platform()}",
        f"任务ID: {task_id}",
        f"失败阶段: {stage or 'unknown'}",
        f"浏览器窗口: {profile_no}",
        f"操作: {title or '未命名操作'}",
    ]
    if screenshot_path:
        lines.append(f"失败截图: {screenshot_path}")
    if context:
        lines.append("")
        lines.append("任务上下文:")
        for key, value in context.items():
            lines.append(f"{key}: {value}")
    lines.append("")
    lines.append("异常类型:")
    lines.append(type(exc).__name__)
    lines.append("")
    lines.append("异常信息:")
    lines.append(str(exc))
    lines.append("")
    lines.append("Traceback:")
    lines.extend(traceback.format_exception(type(exc), exc, exc.__traceback__))
    if settings is not None:
        lines.append("")
        lines.append("设置快照（敏感字段已脱敏）:")
        lines.append(
            json.dumps(
                redact_settings(settings),
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
    if extra_trail:
        lines.append("")
        lines.append("任务执行轨迹:")
        for record in extra_trail:
            lines.append(json.dumps(record, ensure_ascii=False, default=str))
    try:
        path.write_text("\n".join(lines), encoding="utf-8")
    except OSError:
        return path
    return path
