"""Per-stage timing for email generation and Gmail automation."""

from __future__ import annotations

import json
import time


STAGE_ORDER = (
    "get_window",
    "connect",
    "open_gmail",
    "open_compose",
    "locate_recipient",
    "fill_recipient",
    "fill_subject",
    "fill_body",
    "validate",
    "locate_send",
    "click_send",
    "wait_send",
)


class StageTimer:
    """Record monotonic durations for named stages in order."""

    def __init__(self) -> None:
        self._starts: dict[str, float] = {}
        self._durations: dict[str, float] = {}
        self._last_stage = ""
        self.total_started = time.monotonic()

    def begin(self, stage: str) -> None:
        self._starts[stage] = time.monotonic()
        self._last_stage = stage

    def stop(self, stage: str) -> None:
        started = self._starts.get(stage)
        if started is None:
            return
        self._durations[stage] = time.monotonic() - started
        self._starts.pop(stage, None)

    def next(self, stage: str) -> None:
        if self._last_stage and self._last_stage != stage:
            self.stop(self._last_stage)
        self.begin(stage)

    def stop_all(self) -> None:
        for stage in list(self._starts):
            self.stop(stage)

    def inherit(self, other: "StageTimer") -> None:
        """Copy already-closed stage durations (e.g. shared connect time)."""
        self._durations.update(other._durations)

    def duration(self, stage: str) -> float:
        return round(float(self._durations.get(stage, 0.0)), 3)

    def snapshot(self) -> dict[str, float]:
        self.stop_all()
        return {stage: self.duration(stage) for stage in STAGE_ORDER if stage in self._durations}

    def to_json(self) -> str:
        return json.dumps(self.snapshot(), ensure_ascii=False, sort_keys=True)

    def total(self) -> float:
        return round(time.monotonic() - self.total_started, 3)

    def summary(self, profile_no: int | str = "") -> str:
        parts = []
        mapping = {
            "connect": "连接",
            "open_gmail": "打开Gmail",
            "open_compose": "打开Compose",
            "locate_recipient": "定位收件人",
            "fill_recipient": "填写收件人",
            "fill_subject": "填写主题",
            "fill_body": "填写正文",
            "validate": "校验",
            "locate_send": "定位Send",
            "click_send": "点击Send",
            "wait_send": "发送确认",
        }
        for stage in STAGE_ORDER:
            if stage in self._durations:
                parts.append(
                    f"{mapping.get(stage, stage)}{self.duration(stage):.1f}秒"
                )
        total = self.total()
        if parts:
            return f"窗口{profile_no}：{'，'.join(parts)}，总计{total:.1f}秒"
        return f"窗口{profile_no}：无阶段耗时记录"
