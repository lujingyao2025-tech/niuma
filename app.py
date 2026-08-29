import ctypes
import logging
import sys
import threading

from ophelia_assistant.ui import main


def _install_logging() -> None:
    from ophelia_assistant.config import app_data_dir

    log_dir = app_data_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    _maintain_logs(log_dir)
    logging.basicConfig(
        filename=log_dir / "app.log",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        encoding="utf-8",
    )
    logger = logging.getLogger("niuma-mail")

    def handle_thread_exception(args) -> None:
        logger.error(
            "Unhandled thread exception",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    def handle_main_exception(exc_type, exc_value, exc_traceback) -> None:
        logger.error(
            "Unhandled exception",
            exc_info=(exc_type, exc_value, exc_traceback),
        )

    threading.excepthook = handle_thread_exception
    sys.excepthook = handle_main_exception


def _maintain_logs(log_dir) -> None:
    """Keep only recent logs and rotate oversized files."""
    from datetime import datetime

    now = datetime.now().timestamp()
    cutoff = now - 3 * 24 * 60 * 60
    for path in log_dir.glob("app*.log"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            pass
    main_log = log_dir / "app.log"
    try:
        if main_log.exists() and main_log.stat().st_size > 5 * 1024 * 1024:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            main_log.replace(log_dir / f"app-{stamp}.log")
    except OSError:
        pass


_MUTEX_HANDLE = None


def _single_instance_guard() -> bool:
    """Windows-only named mutex; do not kill unrelated processes by name."""
    global _MUTEX_HANDLE
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return True
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.CreateMutexW(None, False, "NiuMaMail_SingleInstance")
        if not handle:
            return True
        ERROR_ALREADY_EXISTS = 183
        if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return False
        _MUTEX_HANDLE = handle
        return True
    except Exception:
        return True


def main() -> None:
    if "--legacy-tk" not in sys.argv:
        try:
            import importlib.util

            if importlib.util.find_spec("PySide6") is not None:
                from ophelia_assistant.studio.app import run as run_studio

                run_studio()
                return
        except Exception as exc:
            logging.getLogger("niuma-mail").warning(
                "Qt studio unavailable, falling back to Tkinter: %s", exc
            )
    from ophelia_assistant.ui import main as tk_main

    tk_main()


if __name__ == "__main__":
    _install_logging()
    if not _single_instance_guard():
        sys.exit(0)
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("NiuMaMail.0.90.0")
        except OSError:
            pass
    main()
