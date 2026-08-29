"""Independent UI state persistence with debounced atomic writes."""

from __future__ import annotations

import json
import os

from PySide6.QtCore import QTimer

from .. import config


class UiStateStore:
    """Store UI-only state in ui_state.json, never inside settings.json."""

    def __init__(self, window, debounce_ms: int = 300) -> None:
        self.window = window
        self._debounce_ms = max(200, min(500, int(debounce_ms)))
        self._data: dict[str, object] = {}
        self._path = config.app_data_dir() / "ui_state.json"
        self._load()
        self._timer = QTimer(window)
        self._timer.setSingleShot(True)
        self._timer.setInterval(self._debounce_ms)
        self._timer.timeout.connect(self.flush)

    def _load(self) -> None:
        try:
            if self._path.exists():
                payload = json.loads(
                    self._path.read_text(encoding="utf-8")
                )
                if isinstance(payload, dict):
                    self._data = {
                        str(key): value
                        for key, value in payload.items()
                    }
        except (OSError, ValueError, TypeError):
            self._data = {}

    def value(self, key: str, default=None, type=None):
        raw = self._data.get(str(key), default)
        if type is bool:
            return bool(raw)
        if type is int:
            try:
                return int(raw)
            except (TypeError, ValueError):
                return default
        return raw

    def setValue(self, key: str, value: object) -> None:
        self._data[str(key)] = value
        self._timer.start()

    def flush(self) -> None:
        self._timer.stop()
        temp_path = self._path.with_name(self._path.name + ".tmp")
        temp_path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temp_path, self._path)
