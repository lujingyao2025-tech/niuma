"""Reusable studio widgets and lightweight dialogs."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..i18n import tr
from .contact_io import parse_contacts_file, write_import_template
from .icons import icon


class IconButton(QToolButton):
    """Square tool button with a hand-drawn line icon."""

    def __init__(
        self,
        name: str,
        color: str,
        tooltip: str = "",
        size: int = 18,
        parent=None,
        checkable: bool = False,
    ) -> None:
        super().__init__(parent)
        self._icon_name = name
        self._icon_color = color
        self.setIcon(icon(name, color, size))
        self.setIconSize(self.icon().actualSize(self.icon().pixmap(18, 18).size()))
        self.setFixedSize(32, 32)
        self.setObjectName("iconButton")
        self.setToolTip(tooltip)
        self.setCursor(Qt.PointingHandCursor)
        self.setCheckable(checkable)

    def set_icon_color(self, color: str) -> None:
        self._icon_color = color
        self.setIcon(icon(self._icon_name, color, 18))


class SearchBox(QLineEdit):
    submitted = Signal(str)

    def __init__(self, placeholder: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.setClearButtonEnabled(True)
        self.setFixedHeight(30)
        self.setMinimumWidth(180)
        self.returnPressed.connect(lambda: self.submitted.emit(self.text().strip()))


class CommandPalette(QDialog):
    """Frameless Ctrl+K action search over the current window."""

    def __init__(self, parent, actions: list[tuple[str, str, object]]) -> None:
        super().__init__(parent)
        self._actions = list(actions)
        self.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint)
        self.setFixedSize(520, 360)
        self.setObjectName("commandPalette")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("搜索命令…  ↑↓ 选择，Enter 执行"))
        self.search.setFixedHeight(34)
        self.search.textChanged.connect(self._filter)
        layout.addWidget(self.search)

        self.list = QListWidget()
        self.list.setFrameShape(QListWidget.NoFrame)
        layout.addWidget(self.list, 1)
        self.empty_label = QLabel(tr("没有匹配的命令"))
        self.empty_label.setObjectName("subtle")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.hide()
        layout.addWidget(self.empty_label)
        self._populate()
        self.search.setFocus()

    def _populate(self) -> None:
        self.list.clear()
        for title, hint, _callback in self._actions:
            item = QListWidgetItem(f"{title}    {hint}" if hint else title)
            item.setData(Qt.UserRole, title)
            self.list.addItem(item)
        self.list.setCurrentRow(0)

    def _filter(self, text: str) -> None:
        keyword = text.strip().lower()
        self.list.clear()
        for title, hint, _callback in self._actions:
            haystack = f"{title} {hint}".lower()
            if not keyword or keyword in haystack:
                item = QListWidgetItem(f"{title}    {hint}" if hint else title)
                item.setData(Qt.UserRole, title)
                self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)
        self.empty_label.setVisible(self.list.count() == 0)
        self.list.setVisible(self.list.count() > 0)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key_Up, Qt.Key_Down):
            super().keyPressEvent(event)
            return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._run_current()
            return
        if event.key() == Qt.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(event)

    def _run_current(self) -> None:
        item = self.list.currentItem()
        if item is None:
            return
        title = item.data(Qt.UserRole)
        for action_title, _hint, callback in self._actions:
            if action_title == title:
                self.accept()
                callback()
                return

    def show_below_header(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            self.move(200, 120)
            return
        top_left = parent.mapToGlobal(parent.rect().topLeft())
        self.move(top_left + QPoint(max(80, (parent.width() - self.width()) // 2), 64))
        self.show()
        self.raise_()
        self.search.setFocus()


class TextPromptDialog(QDialog):
    def __init__(
        self,
        parent,
        title: str,
        label: str,
        value: str = "",
        placeholder: str = "",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(360)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        layout.addWidget(QLabel(label))
        self.edit = QLineEdit(value)
        self.edit.setPlaceholderText(placeholder)
        self.edit.selectAll()
        layout.addWidget(self.edit)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton(tr("取消"))
        cancel.clicked.connect(self.reject)
        ok = QPushButton(tr("确定"))
        ok.setProperty("class", "primary")
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(ok)
        layout.addLayout(buttons)
        self.edit.setFocus()

    @staticmethod
    def get_text(parent, title: str, label: str, value: str = "", placeholder: str = "") -> str | None:
        dialog = TextPromptDialog(parent, tr(title), tr(label), value, tr(placeholder))
        if dialog.exec() != QDialog.Accepted:
            return None
        return dialog.edit.text().strip()


class ContactPromptDialog(QDialog):
    def __init__(self, parent) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("新增联系人"))
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        form = QFormLayout()
        form.setSpacing(8)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText(tr("例如：Alex Walker"))
        self.location_edit = QLineEdit()
        self.location_edit.setPlaceholderText(tr("例如：Seattle"))
        self.email_edit = QLineEdit()
        self.email_edit.setPlaceholderText("alex@example.com")
        form.addRow(tr("名字"), self.name_edit)
        form.addRow(tr("地区"), self.location_edit)
        form.addRow(tr("邮箱"), self.email_edit)
        layout.addLayout(form)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton(tr("取消"))
        cancel.clicked.connect(self.reject)
        ok = QPushButton(tr("添加"))
        ok.setProperty("class", "primary")
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(ok)
        layout.addLayout(buttons)

    def values(self) -> tuple[str, str, str]:
        return (
            self.name_edit.text().strip(),
            self.location_edit.text().strip(),
            self.email_edit.text().strip(),
        )


class ImportContactsDialog(QDialog):
    def __init__(self, parent, campaigns: list[dict]) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("导入联系人"))
        self.setMinimumSize(640, 460)
        self.entries: list[dict] = []
        self.selected_campaign_id: int | None = None
        self._campaigns = campaigns

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        form = QFormLayout()
        file_row = QHBoxLayout()
        self.file_edit = QLineEdit()
        self.file_edit.setReadOnly(True)
        self.file_edit.setPlaceholderText(tr("选择 .xlsx 或 .csv 联系人文件"))
        browse = QPushButton(tr("选择文件"))
        browse.clicked.connect(self._browse)
        template = QPushButton(tr("下载模板"))
        template.clicked.connect(self._download_template)
        file_row.addWidget(self.file_edit, 1)
        file_row.addWidget(browse)
        file_row.addWidget(template)
        form.addRow(tr("联系人文件"), file_row)

        self.campaign_combo = QComboBox()
        for campaign in campaigns:
            self.campaign_combo.addItem(campaign["name"], campaign["id"])
        form.addRow(tr("加入活动/批次"), self.campaign_combo)
        layout.addLayout(form)

        self.count_label = QLabel("")
        self.count_label.setObjectName("subtle")
        layout.addWidget(self.count_label)

        self.preview = QTableWidget(0, 5)
        self.preview.setHorizontalHeaderLabels(
            [tr("名字"), tr("地区"), tr("邮箱"), tr("变量"), tr("预览")]
        )
        self.preview.horizontalHeader().setStretchLastSection(True)
        self.preview.setEditTriggers(QTableWidget.NoEditTriggers)
        self.preview.verticalHeader().setVisible(False)
        layout.addWidget(self.preview, 1)

        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color:#DC2626;")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        layout.addWidget(self.error_label)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton(tr("取消"))
        cancel.clicked.connect(self.reject)
        self.import_button = QPushButton(tr("导入联系人"))
        self.import_button.setProperty("class", "primary")
        self.import_button.clicked.connect(self._accept_entries)
        buttons.addWidget(cancel)
        buttons.addWidget(self.import_button)
        layout.addLayout(buttons)

    def _browse(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self,
            tr("选择联系人文件"),
            str(Path.home()),
            tr("联系人文件 (*.xlsx *.csv);;Excel (*.xlsx);;CSV (*.csv)"),
        )
        if not path:
            return
        self.file_edit.setText(path)
        self._load_file(path)

    def _load_file(self, path: str) -> None:
        try:
            entries = parse_contacts_file(path)
        except Exception as exc:
            self.entries = []
            self.error_label.setText(str(exc))
            self.error_label.show()
            self.preview.setRowCount(0)
            self.count_label.setText("")
            return
        self.entries = entries
        self.error_label.hide()
        self.count_label.setText(tr("解析到 {count} 条联系人，导入前请确认预览。").format(count=len(entries)))
        self.preview.setRowCount(min(6, len(entries)))
        for row, entry in enumerate(entries[:6]):
            variables = [
                value
                for key, value in entry.items()
                if key.startswith("custom_") or key.startswith("变量")
            ]
            values = [
                entry.get("name", ""),
                entry.get("location", ""),
                entry.get("email", ""),
                "; ".join(variables[:3]),
                tr("有效") if entry.get("email") else tr("缺少邮箱"),
            ]
            for column, value in enumerate(values):
                self.preview.setItem(row, column, QTableWidgetItem(value))

    def _download_template(self) -> None:
        path, _filter = QFileDialog.getSaveFileName(
            self,
            tr("保存导入模板"),
            str(Path.home() / "联系人导入模板.xlsx"),
            "Excel (*.xlsx)",
        )
        if not path:
            return
        try:
            write_import_template(path)
        except Exception as exc:
            QMessageBox.critical(self, tr("导出失败"), str(exc))
            return
        QMessageBox.information(self, tr("模板已保存"), tr("模板已保存：{path}").format(path=path))

    def _accept_entries(self) -> None:
        if not self.entries:
            self.error_label.setText(tr("没有可导入的联系人，请先选择文件。"))
            self.error_label.show()
            return
        self.selected_campaign_id = int(self.campaign_combo.currentData())
        self.accept()


class LicenseDialog(QDialog):
    """Device-bound admin code verification."""

    def __init__(self, parent, status_text: str) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("管理员授权"))
        self.setMinimumWidth(440)
        self.verified = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)

        from ..trial import device_code

        heading = QLabel(tr("设备绑定授权"))
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading)

        note = QLabel(status_text)
        note.setObjectName("subtle")
        note.setWordWrap(True)
        layout.addWidget(note)

        code_row = QHBoxLayout()
        self.code_edit = QLineEdit()
        self.code_edit.setReadOnly(True)
        self.code_edit.setText(device_code())
        copy_code = QPushButton(tr("复制设备码"))
        copy_code.clicked.connect(self._copy_device_code)
        code_row.addWidget(self.code_edit, 1)
        code_row.addWidget(copy_code)
        layout.addLayout(code_row)

        layout.addWidget(QLabel(tr("联系管理员获取验证码：@ls0514")))

        verify_row = QHBoxLayout()
        self.verify_edit = QLineEdit()
        self.verify_edit.setPlaceholderText(tr("输入管理员验证码"))
        verify_button = QPushButton(tr("立即验证"))
        verify_button.setProperty("class", "primary")
        verify_button.clicked.connect(self._verify)
        verify_row.addWidget(self.verify_edit, 1)
        verify_row.addWidget(verify_button)
        layout.addLayout(verify_row)

        self.result_label = QLabel("")
        self.result_label.setWordWrap(True)
        self.result_label.hide()
        layout.addWidget(self.result_label)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        close = QPushButton(tr("关闭"))
        close.clicked.connect(self.reject)
        buttons.addWidget(close)
        layout.addLayout(buttons)

    def _copy_device_code(self) -> None:
        QApplication.clipboard().setText(self.code_edit.text())

    def _verify(self) -> None:
        from ..trial import verify_authorization_code

        code = self.verify_edit.text().strip()
        if not code:
            self.result_label.setText(tr("请先填写管理员提供的验证码。"))
            self.result_label.setStyleSheet("color:#C7740A;")
            self.result_label.show()
            return
        verified, message, _status = verify_authorization_code(code)
        self.result_label.setText(message)
        self.result_label.setStyleSheet("color:#DC2626;" if not verified else "color:#16A34A;")
        self.result_label.show()
        if verified:
            self.verified = True
            self.verify_edit.clear()
