"""QApplication entry point for the studio."""

from __future__ import annotations

import sys

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication


def run() -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("NiuMaMail")
    app.setApplicationDisplayName("牛马邮箱")
    app.setFont(QFont("Microsoft YaHei UI", 9))

    from .main_window import MainWindow

    window = MainWindow()
    window.show()
    sys.exit(app.exec())

