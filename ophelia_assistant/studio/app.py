"""QApplication entry point for the studio."""

from __future__ import annotations

import sys

import logging

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication


def _install_error_report_hook() -> None:
    from ..diagnostics import write_error_report

    def hook(exc_type, exc_value, exc_tb) -> None:
        exc = exc_value.with_traceback(exc_tb)
        try:
            report_path = write_error_report(
                exc,
                title="未处理异常",
            )
        except Exception:
            report_path = None
        logging.getLogger("niuma-mail").error(
            "Unhandled exception",
            exc_info=(exc_type, exc_value, exc_tb),
        )
        try:
            from PySide6.QtWidgets import QMessageBox

            text = str(exc)
            if report_path is not None:
                text += f"\n\n错误报告已保存：\n{report_path}"
            QMessageBox.critical(None, "程序错误", text)
        except Exception:
            pass

    sys.excepthook = hook


def run() -> None:
    _install_error_report_hook()
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("NiuMaMail")
    app.setApplicationDisplayName("牛马邮箱")
    app.setFont(QFont("Microsoft YaHei UI", 9))

    from .main_window import MainWindow

    window = MainWindow()
    window.show()
    sys.exit(app.exec())
