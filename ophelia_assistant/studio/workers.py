"""Background workers for studio operations."""

from __future__ import annotations

import logging
import threading
from typing import Callable

from PySide6.QtCore import QObject, QRunnable, Signal

from ..operation import OperationCancelledError


logger = logging.getLogger("niuma-mail")


class WorkerSignals(QObject):
    progress = Signal(int, str)
    task_status = Signal(str, str, str, str)
    done = Signal(object)
    error = Signal(object)
    finished = Signal()


class FunctionWorker(QRunnable):
    def __init__(
        self,
        fn: Callable,
        signals: WorkerSignals,
        cancel_event: threading.Event,
        *args,
        **kwargs,
    ) -> None:
        super().__init__()
        self.fn = fn
        self.signals = signals
        self.cancel_event = cancel_event
        self.args = args
        self.kwargs = kwargs

    def run(self) -> None:
        try:
            result = self.fn(self.cancel_event, *self.args, **self.kwargs)
        except OperationCancelledError:
            pass
        except Exception as exc:
            logger.exception("Studio background task failed")
            self.signals.error.emit(exc)
        else:
            self.signals.done.emit(result)
        finally:
            self.signals.finished.emit()
