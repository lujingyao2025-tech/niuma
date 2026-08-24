from __future__ import annotations

import threading


class OperationCancelledError(RuntimeError):
    """Raised when a newer user operation supersedes the current operation."""


def check_cancel(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise OperationCancelledError("任务已被新的手动操作终止")


class OperationController:
    """Owns the single logical foreground operation without blocking new work."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active_event: threading.Event | None = None
        self._serial = 0

    def begin(self) -> tuple[threading.Event, int]:
        with self._lock:
            if self._active_event is not None:
                self._active_event.set()
            self._serial += 1
            event = threading.Event()
            self._active_event = event
            return event, self._serial

    def is_current(self, event: threading.Event, serial: int) -> bool:
        with self._lock:
            return self._active_event is event and self._serial == serial

    def finish(self, event: threading.Event, serial: int) -> None:
        with self._lock:
            if self._active_event is event and self._serial == serial:
                self._active_event = None
