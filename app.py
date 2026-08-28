import ctypes
import logging
import sys
import threading
from ctypes import wintypes

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


def _terminate_other_niuma_instances() -> None:
    """Windows-only: keep a single NiuMaMail instance by killing the others."""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return
    kernel32 = ctypes.windll.kernel32
    PROCESS_TERMINATE = 0x0001
    TH32CS_SNAPPROCESS = 0x00000002
    INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == INVALID_HANDLE_VALUE:
        return
    current_pid = kernel32.GetCurrentProcessId()
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
    found = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
    targets: list[int] = []
    while found:
        name = str(entry.szExeFile).lower()
        if name.startswith("niumamail") and name.endswith(".exe"):
            if entry.th32ProcessID != current_pid:
                targets.append(int(entry.th32ProcessID))
        found = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    kernel32.CloseHandle(snapshot)
    for pid in targets:
        handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
        if handle:
            kernel32.TerminateProcess(handle, 1)
            kernel32.CloseHandle(handle)


if __name__ == "__main__":
    _install_logging()
    _terminate_other_niuma_instances()
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("NiuMaMail.0.90.0")
        except OSError:
            pass
    main()
