"""Reusable studio panels: tables, editors, inspector."""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStackedLayout,
    QStyle,
    QStyledItemDelegate,
    QTabWidget,
    QTableView,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..config import MAX_WINDOW_SEQUENCE, normalize_window_sequence
from ..i18n import tr
from .icons import icon, make_pixmap
from .models import TaskTableModel, custom_variables_of, status_text
from .theme import STATUS_COLORS


class StatusBadgeDelegate(QStyledItemDelegate):
    """Draw task status as a compact soft chip instead of plain text."""

    def __init__(self, palette, parent=None) -> None:
        super().__init__(parent)
        self.p = palette

    def paint(self, painter, option, index) -> None:
        row = index.data(Qt.UserRole)
        status = str(row.get("status") or "new") if isinstance(row, dict) else "new"
        mapping = {
            "pending": (self.p.muted, self.p.hover),
            "generated": (self.p.info, self.p.info_soft),
            "waiting_window": (self.p.faint, self.p.hover),
            "assigned": (self.p.accent, self.p.accent_soft),
            "filling": (self.p.accent, self.p.accent_soft),
            "validating": (self.p.accent, self.p.accent_soft),
            "drafted": (self.p.info, self.p.info_soft),
            "sending": (self.p.accent, self.p.accent_soft),
            "sent": (self.p.ok, self.p.ok_soft),
            "replied": (self.p.ok, self.p.ok_soft),
            "failed": (self.p.danger, self.p.danger_soft),
            "needs_review": (self.p.warn, self.p.warn_soft),
            "cancelled": (self.p.faint, self.p.hover),
        }
        text_color, soft_color = mapping.get(status, (self.p.muted, self.p.hover))
        label = status_text(status)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        if option.state & QStyle.State_Selected:
            painter.fillRect(option.rect, QColor(self.p.selection))
        font = QFont(option.font)
        font.setPointSizeF(max(8.5, font.pointSizeF() - 0.5))
        painter.setFont(font)
        metrics = QFontMetrics(font)
        chip = QRectF(
            option.rect.left() + 6,
            option.rect.top() + (option.rect.height() - 20) / 2,
            metrics.horizontalAdvance(label) + 14,
            20,
        )
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(soft_color))
        painter.drawRoundedRect(chip, 4, 4)
        painter.setPen(QColor(text_color))
        painter.drawText(chip, Qt.AlignCenter, label)
        painter.restore()


class CampaignItemDelegate(QStyledItemDelegate):
    """Campaign list rows with name, task count and status summary."""

    def __init__(self, palette, parent=None) -> None:
        super().__init__(parent)
        self.p = palette

    def sizeHint(self, option, index):
        return QSize(option.rect.width() if option.rect.width() > 0 else 220, 58)

    def paint(self, painter, option, index) -> None:
        campaign = index.data(Qt.UserRole)
        if not isinstance(campaign, dict):
            return
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        selected = bool(option.state & QStyle.State_Selected)
        hovered = bool(option.state & QStyle.State_MouseOver)
        background = (
            self.p.selection if selected else (self.p.hover if hovered else self.p.surface)
        )
        painter.fillRect(option.rect, QColor(background))
        if selected:
            painter.fillRect(
                QRectF(option.rect.left(), option.rect.top() + 8, 3, option.rect.height() - 16),
                QColor(self.p.accent),
            )
        rect = option.rect.adjusted(14, 6, -12, -6)
        name_font = QFont(option.font)
        name_font.setPointSizeF(max(9.5, name_font.pointSizeF()))
        name_font.setBold(True)
        painter.setFont(name_font)
        painter.setPen(QColor(self.p.text))
        painter.drawText(
            rect.adjusted(0, 0, -44, -22),
            Qt.AlignLeft | Qt.AlignVCenter,
            str(campaign.get("name") or ""),
        )
        count_font = QFont(option.font)
        count_font.setPointSizeF(max(9, count_font.pointSizeF()))
        count_font.setBold(True)
        painter.setFont(count_font)
        painter.setPen(QColor(self.p.accent))
        painter.drawText(
            rect.adjusted(0, 0, 0, -22),
            Qt.AlignRight | Qt.AlignVCenter,
            str(campaign.get("task_count") or 0),
        )
        sub_font = QFont(option.font)
        sub_font.setPointSizeF(max(8, sub_font.pointSizeF() - 0.5))
        painter.setFont(sub_font)
        painter.setPen(QColor(self.p.muted))
        sub = tr("已发送 {sent} · 草稿 {drafted} · 待确认 {pending}").format(
            sent=campaign.get("sent_count") or 0,
            drafted=campaign.get("drafted_count") or 0,
            pending=campaign.get("pending_count") or 0,
        )
        painter.drawText(
            rect.adjusted(0, 20, 0, 0),
            Qt.AlignLeft | Qt.AlignVCenter,
            sub,
        )
        painter.restore()


class PageHeader(QWidget):
    def __init__(self, title: str, subtitle: str = "") -> None:
        super().__init__()
        self.setObjectName("pageHeader")
        self.setFixedHeight(64)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 10, 18, 10)
        layout.setSpacing(12)
        text_column = QVBoxLayout()
        text_column.setSpacing(1)
        self.title = QLabel(title)
        self.title.setObjectName("pageTitle")
        self.subtitle = QLabel(subtitle)
        self.subtitle.setObjectName("subtle")
        text_column.addWidget(self.title)
        text_column.addWidget(self.subtitle)
        layout.addLayout(text_column)
        layout.addStretch(1)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(8)
        layout.addLayout(self.actions)

    def add_widget(self, widget: QWidget) -> None:
        self.actions.addWidget(widget)

    def set_title(self, title: str) -> None:
        self.title.setText(title)

    def set_subtitle(self, subtitle: str) -> None:
        self.subtitle.setText(subtitle)


class StatsBar(QWidget):
    """Compact one-line task statistics; not a card wall."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.label = QLabel("")
        self.label.setObjectName("countText")
        layout.addWidget(self.label)
        layout.addStretch(1)

    def set_stats(self, stats: dict[str, int]) -> None:
        self.label.setText(
            tr("总 {total} · 待处理 {pending} · 已生成 {generated} "
               "· 等待窗口 {waiting} · 处理中 {processing} "
               "· 成功 {sent} · 失败 {failed} · 需确认 {review}")
            .format(
                total=stats.get("total", 0),
                pending=stats.get("pending", 0),
                generated=stats.get("generated", 0),
                waiting=stats.get("waiting", 0),
                processing=stats.get("processing", 0),
                sent=stats.get("sent", 0),
                failed=stats.get("failed", 0),
                review=stats.get("review", 0),
            )
        )


class TaskTablePanel(QWidget):
    def __init__(
        self,
        window,
        extra_columns: list[tuple[str, str]] | None = None,
        empty_title: str = "",
        empty_hint: str = "",
        empty_actions: list[tuple[str, object]] | None = None,
    ) -> None:
        super().__init__()
        self.window = window
        self.model = TaskTableModel(self)
        self.model.set_columns(extra_columns)
        self.model.data_commit.connect(window.on_task_edit)
        self._empty_actions = list(empty_actions or [])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(30)
        self.table.horizontalHeader().setHighlightSections(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setMinimumSectionSize(70)
        self.table.setEditTriggers(
            QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed
        )
        palette_tokens = getattr(window, "palette_tokens", None)
        if palette_tokens is not None:
            self.table.setItemDelegateForColumn(0, StatusBadgeDelegate(palette_tokens, self.table))
        self.table.selectionModel().selectionChanged.connect(self._selection_changed)
        self.table.doubleClicked.connect(self._double_clicked)
        self._widths = {
            0: 86,
            1: 150,
            2: 120,
            3: 210,
            4: 70,
            5: 260,
            6: 110,
        }

        self.stack = QStackedLayout()
        self.stack.addWidget(self._build_empty_state(empty_title, empty_hint))
        self.stack.addWidget(self.table)
        self.stack.setCurrentWidget(self.table)
        layout.addLayout(self.stack, 1)

        footer = QHBoxLayout()
        self.count_label = QLabel("")
        self.count_label.setObjectName("countText")
        footer.addWidget(self.count_label)
        footer.addStretch(1)
        layout.addLayout(footer)

    def _build_empty_state(self, title: str, hint: str) -> QWidget:
        empty = QWidget()
        empty_layout = QVBoxLayout(empty)
        empty_layout.setContentsMargins(24, 24, 24, 24)
        empty_layout.setSpacing(8)
        empty_layout.addStretch(2)
        palette_tokens = getattr(self.window, "palette_tokens", None)
        icon_label = QLabel()
        color = palette_tokens.muted if palette_tokens is not None else "#8B97A5"
        icon_label.setPixmap(make_pixmap("contacts", color, 44))
        icon_label.setAlignment(Qt.AlignCenter)
        empty_layout.addWidget(icon_label)
        title_label = QLabel(title or tr("还没有联系人"))
        title_label.setObjectName("sectionTitle")
        title_label.setAlignment(Qt.AlignCenter)
        empty_layout.addWidget(title_label)
        hint_label = QLabel(hint or tr("导入 Excel/CSV 或手动新增联系人，开始本批次。"))
        hint_label.setObjectName("subtle")
        hint_label.setWordWrap(True)
        hint_label.setAlignment(Qt.AlignCenter)
        empty_layout.addWidget(hint_label)
        if self._empty_actions:
            buttons = QHBoxLayout()
            buttons.addStretch(1)
            for index, (label, callback) in enumerate(self._empty_actions):
                button = QPushButton(label)
                if index == 0:
                    button.setProperty("class", "primary")
                button.clicked.connect(callback)
                buttons.addWidget(button)
            buttons.addStretch(1)
            empty_layout.addLayout(buttons)
        empty_layout.addStretch(3)
        return empty

    def _selection_changed(self, _selected, _deselected) -> None:
        self.window.on_task_selection(self.selected_ids())
        self._update_count()

    def _double_clicked(self, index) -> None:
        row = self.model.task_at(index.row())
        if row is not None:
            self.window.on_task_double_clicked(row)

    def _update_count(self) -> None:
        selected = len(self.selected_ids())
        total = self.model.rowCount()
        if selected:
            self.count_label.setText(tr("已选 {selected} 条 · 共 {total} 条").format(selected=selected, total=total))
        else:
            self.count_label.setText(tr("共 {total} 条").format(total=total))

    def set_tasks(self, rows) -> None:
        self.model.set_tasks(rows)
        self._apply_widths()
        self._update_count()
        if self.model.rowCount() == 0:
            self.stack.setCurrentWidget(self.stack.widget(0))
        else:
            self.stack.setCurrentWidget(self.table)

    def _apply_widths(self) -> None:
        header = self.table.horizontalHeader()
        for column, width in self._widths.items():
            if column < self.model.columnCount():
                header.resizeSection(column, width)

    def selected_ids(self) -> list[int]:
        rows = self.table.selectionModel().selectedRows()
        ids: list[int] = []
        for index in rows:
            row = self.model.task_at(index.row())
            if row is not None and row.get("id"):
                ids.append(int(row["id"]))
        return ids

    def select_ids(self, task_ids: list[int]) -> None:
        wanted = set(int(task_id) for task_id in task_ids)
        selection = self.table.selectionModel()
        selection.clearSelection()
        for row in range(self.model.rowCount()):
            item = self.model.task_at(row)
            if item is not None and int(item.get("id") or 0) in wanted:
                selection.select(
                    self.model.index(row, 0),
                    selection.SelectionFlag.Select | selection.SelectionFlag.Rows,
                )
        self._update_count()

    def clear_selection(self) -> None:
        self.table.selectionModel().clearSelection()


class TemplateEditor(QWidget):
    def __init__(self, window) -> None:
        super().__init__()
        self.window = window
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        editor = QWidget()
        editor_layout = QVBoxLayout(editor)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.setSpacing(8)

        toolbar = QHBoxLayout()
        self.save_button = QPushButton(tr("保存模板"))
        self.save_button.setProperty("class", "primary")
        self.save_button.clicked.connect(self.save)
        self.save_library_button = QPushButton(tr("存入模板库"))
        self.save_library_button.clicked.connect(self.save_library)
        self.preview_button = QPushButton(tr("预览"))
        self.preview_button.clicked.connect(self.preview)
        toolbar.addWidget(self.save_button)
        toolbar.addWidget(self.save_library_button)
        toolbar.addWidget(self.preview_button)
        toolbar.addStretch(1)
        editor_layout.addLayout(toolbar)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(8)
        self.subject_edit = QLineEdit()
        self.sender_edit = QLineEdit()
        self.signature_edit = QLineEdit()
        form.addRow(tr("主题"), self.subject_edit)
        form.addRow(tr("发件人姓名"), self.sender_edit)
        form.addRow(tr("签名"), self.signature_edit)
        editor_layout.addLayout(form)

        self.body_edit = QPlainTextEdit()
        self.body_edit.setPlaceholderText(tr("正文模板，使用 {变量} 占位符"))
        editor_layout.addWidget(self.body_edit, 1)

        variables = QHBoxLayout()
        variables.addWidget(QLabel(tr("变量")))
        for token in ("{first_name}", "{location}", "{sender_name}"):
            button = QPushButton(token)
            button.setFixedHeight(28)
            button.clicked.connect(lambda _checked=False, t=token: self._insert(t))
            variables.addWidget(button)
        variables.addStretch(1)
        editor_layout.addLayout(variables)

        self.custom_table = QTableWidget(0, 2)
        self.custom_table.setHorizontalHeaderLabels([tr("变量名"), tr("默认值")])
        self.custom_table.verticalHeader().setVisible(False)
        self.custom_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.custom_table.setFixedHeight(130)
        editor_layout.addWidget(self.custom_table)
        layout.addWidget(editor, 3)

        library = QWidget()
        library.setObjectName("card")
        library.setMaximumWidth(260)
        library_layout = QVBoxLayout(library)
        library_layout.setContentsMargins(12, 12, 12, 12)
        library_layout.setSpacing(8)
        library_layout.addWidget(self._section_title(tr("模板库")))
        self.library_list = QListWidget()
        self.library_list.itemDoubleClicked.connect(lambda _item: self.load_library())
        library_layout.addWidget(self.library_list, 1)
        load_button = QPushButton(tr("载入所选"))
        load_button.clicked.connect(self.load_library)
        delete_button = QPushButton(tr("删除所选"))
        delete_button.setProperty("class", "danger")
        delete_button.clicked.connect(self.delete_library)
        library_layout.addWidget(load_button)
        library_layout.addWidget(delete_button)
        layout.addWidget(library)

        self.load()

    @staticmethod
    def _section_title(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("sectionTitle")
        return label

    def _insert(self, token: str) -> None:
        self.body_edit.insertPlainText(token)

    def load(self) -> None:
        settings = self.window.settings
        self.subject_edit.setText(settings.subject_template)
        self.body_edit.setPlainText(settings.body_template)
        self.sender_edit.setText(settings.sender_name)
        self.signature_edit.setText(settings.signature)
        self.library_list.clear()
        for template in settings.saved_templates:
            self.library_list.addItem(str(template.get("name") or ""))
        self._load_custom_variables()

    def _load_custom_variables(self) -> None:
        variables = self.window.settings.custom_variables
        self.custom_table.setRowCount(len(variables))
        for row, (key, value) in enumerate(variables.items()):
            key_item = QTableWidgetItem(key)
            key_item.setFlags(key_item.flags() & ~Qt.ItemIsEditable)
            self.custom_table.setItem(row, 0, key_item)
            self.custom_table.setItem(row, 1, QTableWidgetItem(str(value)))

    def current_values(self) -> tuple[str, str, str, str]:
        return (
            self.subject_edit.text().strip(),
            self.body_edit.toPlainText().strip(),
            self.sender_edit.text().strip(),
            self.signature_edit.text().strip(),
        )

    def save(self) -> None:
        subject, body, sender, signature = self.current_values()
        if not subject or not body:
            QMessageBox.warning(self, tr("模板不完整"), tr("主题和正文不能为空。"))
            return
        self.window.save_template(subject, body, sender, signature)
        self._save_custom_variables()

    def _save_custom_variables(self) -> None:
        variables: dict[str, str] = {}
        for row in range(self.custom_table.rowCount()):
            key_item = self.custom_table.item(row, 0)
            value_item = self.custom_table.item(row, 1)
            if key_item is None:
                continue
            key = key_item.text().strip()
            value = value_item.text().strip() if value_item is not None else ""
            if key:
                variables[key] = value
        self.window.settings.custom_variables = variables
        self.window.settings.custom_variable_keys = list(variables.keys())
        self.window.settings.save()
        self.window.set_status(tr("模板与变量已保存"))

    def save_library(self) -> None:
        from .widgets import TextPromptDialog

        name = TextPromptDialog.get_text(
            self,
            "保存到模板库",
            "模板名称",
            placeholder="例如：八月跟进话术",
        )
        if name is None:
            return
        subject, body, sender, signature = self.current_values()
        self.window.save_template_library(name, subject, body, sender, signature)
        self.load()

    def load_library(self) -> None:
        item = self.library_list.currentItem()
        if item is None:
            return
        self.window.load_template_library(item.text())
        self.load()

    def delete_library(self) -> None:
        item = self.library_list.currentItem()
        if item is None:
            return
        self.window.delete_template_library(item.text())
        self.load()

    def preview(self) -> None:
        subject, body, sender, signature = self.current_values()
        self.window.preview_template(subject, body, sender, signature)


class WindowPanel(QWidget):
    def __init__(self, window) -> None:
        super().__init__()
        self.window = window
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        toolbar = QHBoxLayout()
        self.auto_fill_button = QPushButton(tr("从浏览器自动填充"))
        self.auto_fill_button.clicked.connect(lambda: self.window.auto_fill_windows())
        add_button = QPushButton(tr("添加行"))
        add_button.clicked.connect(self.add_row)
        delete_button = QPushButton(tr("删除行"))
        delete_button.setProperty("class", "danger")
        delete_button.clicked.connect(self.delete_row)
        move_up_button = QPushButton(tr("上移"))
        move_up_button.clicked.connect(lambda: self.move_row(-1))
        move_down_button = QPushButton(tr("下移"))
        move_down_button.clicked.connect(lambda: self.move_row(1))
        self.save_button = QPushButton(tr("保存窗口顺序"))
        self.save_button.setProperty("class", "primary")
        self.save_button.clicked.connect(self.save_sequence)
        toolbar.addWidget(self.auto_fill_button)
        toolbar.addWidget(add_button)
        toolbar.addWidget(delete_button)
        toolbar.addWidget(move_up_button)
        toolbar.addWidget(move_down_button)
        toolbar.addStretch(1)
        toolbar.addWidget(self.save_button)
        layout.addLayout(toolbar)

        self.sequence_table = QTableWidget(0, 2)
        self.sequence_table.setHorizontalHeaderLabels([tr("顺序"), tr("窗口编号")])
        self.sequence_table.verticalHeader().setVisible(False)
        self.sequence_table.horizontalHeader().setStretchLastSection(True)
        self.sequence_table.setFixedHeight(260)
        self.sequence_table.itemChanged.connect(self._on_sequence_item_changed)
        layout.addWidget(self.sequence_table)

        note = QLabel(tr("列表顺序就是任务分配顺序；编号锁定后删除任务才能解除。"))
        note.setObjectName("subtle")
        note.setWordWrap(True)
        layout.addWidget(note)

        layout.addWidget(self._section_title(tr("窗口绑定（模板与发件人）")))
        self.bindings_table = QTableWidget(0, 4)
        self.bindings_table.setHorizontalHeaderLabels(
            [tr("窗口"), tr("模板"), tr("发件人"), tr("锁定")]
        )
        self.bindings_table.verticalHeader().setVisible(False)
        self.bindings_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.bindings_table, 1)

        binding_toolbar = QHBoxLayout()
        self.prune_bindings_button = QPushButton(tr("清理无效绑定"))
        self.prune_bindings_button.setProperty("class", "danger")
        self.prune_bindings_button.clicked.connect(self.prune_invalid_bindings)
        self.save_bindings_button = QPushButton(tr("保存窗口绑定"))
        self.save_bindings_button.setProperty("class", "primary")
        self.save_bindings_button.clicked.connect(self.save_bindings)
        binding_toolbar.addWidget(self.prune_bindings_button)
        binding_toolbar.addStretch(1)
        binding_toolbar.addWidget(self.save_bindings_button)
        layout.addLayout(binding_toolbar)

        self._loading = False
        self.load()

    @staticmethod
    def _section_title(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("sectionTitle")
        return label

    def load(self) -> None:
        self._reload_sequence_table()
        self._reload_bindings_table()

    def _reload_sequence_table(self) -> None:
        settings = self.window.settings
        self._loading = True
        self.sequence_table.setRowCount(len(settings.window_sequence))
        for row, profile_no in enumerate(settings.window_sequence):
            self.sequence_table.setItem(row, 0, QTableWidgetItem(str(row + 1)))
            self.sequence_table.setItem(row, 1, QTableWidgetItem(str(profile_no)))
        self._loading = False

    def sync_after_sequence_change(self) -> None:
        """Refresh sequence rows from settings while keeping live binding edits."""
        self._reload_sequence_table()
        self.refresh_preserving()

    def _reload_bindings_table(self) -> None:
        settings = self.window.settings
        template_names = [str(item.get("name") or "") for item in settings.saved_templates]
        bindings = settings.window_bindings
        all_windows = [str(window) for window in settings.window_sequence]
        self.bindings_table.setRowCount(len(all_windows))
        for row, profile_no in enumerate(all_windows):
            binding = bindings.get(str(profile_no), {})
            self.bindings_table.setItem(row, 0, QTableWidgetItem(str(profile_no)))
            template_cell = QComboBox()
            template_cell.addItem("", "")
            for name in template_names:
                template_cell.addItem(name, name)
            selected_name = str(binding.get("template_name") or "")
            if selected_name and selected_name not in template_names:
                template_cell.addItem(
                    tr("模板已删除/失效：{name}").format(name=selected_name),
                    selected_name,
                )
            template_cell.setCurrentText(
                tr("模板已删除/失效：{name}").format(name=selected_name)
                if selected_name and selected_name not in template_names
                else selected_name
            )
            sender_cell = QLineEdit(str(binding.get("sender_name") or ""))
            lock_cell = QCheckBox()
            lock_cell.setChecked(bool(binding.get("locked")))
            lock_cell.setToolTip(tr("锁定后自动匹配不会覆盖此发件人姓名"))
            self.bindings_table.setCellWidget(row, 1, template_cell)
            self.bindings_table.setCellWidget(row, 2, sender_cell)
            self.bindings_table.setCellWidget(row, 3, lock_cell)

    def _sequence_numbers(self) -> list[int]:
        values: list[str] = []
        for row in range(self.sequence_table.rowCount()):
            item = self.sequence_table.item(row, 1)
            values.append(item.text().strip() if item is not None else "")
        return normalize_window_sequence(values)

    def _sync_bindings_from_sequence(self) -> None:
        """Keep the binding table in sync with the sequence table without saving."""
        try:
            sequence = self._sequence_numbers()
        except ValueError:
            return
        self.window.settings.window_sequence = sequence
        self.refresh_preserving()

    def _on_sequence_item_changed(self, item) -> None:
        if self._loading or item.column() != 1:
            return
        self._sync_bindings_from_sequence()

    def refresh_preserving(self) -> None:
        """Reload template names while keeping each window's live selection."""
        previous: dict[str, dict] = {}
        for row in range(self.bindings_table.rowCount()):
            profile_item = self.bindings_table.item(row, 0)
            if profile_item is None:
                continue
            template_cell = self.bindings_table.cellWidget(row, 1)
            sender_cell = self.bindings_table.cellWidget(row, 2)
            lock_cell = self.bindings_table.cellWidget(row, 3)
            previous[str(profile_item.text().strip())] = {
                "template": (
                    str(template_cell.currentData() or "")
                    if isinstance(template_cell, QComboBox)
                    else ""
                ),
                "sender": (
                    sender_cell.text()
                    if isinstance(sender_cell, QLineEdit)
                    else ""
                ),
                "locked": (
                    bool(lock_cell.isChecked())
                    if isinstance(lock_cell, QCheckBox)
                    else False
                ),
            }
        self._reload_bindings_table()
        for row in range(self.bindings_table.rowCount()):
            profile_item = self.bindings_table.item(row, 0)
            if profile_item is None:
                continue
            state = previous.get(str(profile_item.text().strip()))
            if state is None:
                continue
            template_cell = self.bindings_table.cellWidget(row, 1)
            sender_cell = self.bindings_table.cellWidget(row, 2)
            lock_cell = self.bindings_table.cellWidget(row, 3)
            if isinstance(template_cell, QComboBox):
                template_value = state["template"]
                index = template_cell.findData(template_value)
                if index < 0 and not template_value:
                    index = 0
                if index >= 0:
                    template_cell.setCurrentIndex(index)
            if isinstance(sender_cell, QLineEdit):
                sender_cell.setText(state["sender"])
            if isinstance(lock_cell, QCheckBox):
                lock_cell.setChecked(state["locked"])

    def add_row(self) -> None:
        row = self.sequence_table.rowCount()
        if row >= MAX_WINDOW_SEQUENCE:
            QMessageBox.warning(self, tr("超出限制"), tr("最多 {count} 个窗口编号。").format(count=MAX_WINDOW_SEQUENCE))
            return
        self.sequence_table.insertRow(row)
        self.sequence_table.setItem(row, 0, QTableWidgetItem(str(row + 1)))
        self.sequence_table.setItem(row, 1, QTableWidgetItem(""))
        self._sync_bindings_from_sequence()
        self.sequence_table.editItem(self.sequence_table.item(row, 1))

    def delete_row(self) -> None:
        if not self.sequence_table.selectedIndexes():
            QMessageBox.warning(self, tr("未选择行"), tr("请先选择要删除的窗口行。"))
            return
        rows = sorted({index.row() for index in self.sequence_table.selectedIndexes()}, reverse=True)
        for row in rows:
            self.sequence_table.removeRow(row)
        for row in range(self.sequence_table.rowCount()):
            self.sequence_table.setItem(row, 0, QTableWidgetItem(str(row + 1)))
        self._sync_bindings_from_sequence()

    def move_row(self, direction: int) -> None:
        indexes = self.sequence_table.selectedIndexes()
        if not indexes:
            QMessageBox.warning(self, tr("未选择行"), tr("请先选择要移动的窗口行。"))
            return
        row = sorted({index.row() for index in indexes})[0]
        target = row + direction
        if target < 0 or target >= self.sequence_table.rowCount():
            return
        for column in range(self.sequence_table.columnCount()):
            source_item = self.sequence_table.takeItem(row, column)
            target_item = self.sequence_table.takeItem(target, column)
            self.sequence_table.setItem(row, column, target_item)
            self.sequence_table.setItem(target, column, source_item)
        for index in range(self.sequence_table.rowCount()):
            order_item = self.sequence_table.item(index, 0)
            if order_item is not None:
                order_item.setText(str(index + 1))
        self.sequence_table.selectRow(target)
        self._sync_bindings_from_sequence()

    def save_sequence(self) -> None:
        try:
            normalized = self._sequence_numbers()
        except ValueError as exc:
            QMessageBox.warning(self, tr("窗口顺序无效"), str(exc))
            return
        try:
            self.window.settings.save_window_sequence(normalized)
        except Exception as exc:
            self.window.show_error(tr("窗口顺序保存失败"), str(exc))
            return
        detail = "、".join(str(number) for number in normalized)
        self.window.set_status(tr("窗口顺序已保存：{detail}").format(detail=detail))
        self.sync_after_sequence_change()

    def save_bindings(self) -> None:
        bindings: dict[str, dict] = {}
        current_windows = {str(number) for number in self.window.settings.window_sequence}
        for row in range(self.bindings_table.rowCount()):
            profile_item = self.bindings_table.item(row, 0)
            if profile_item is None:
                continue
            window = str(profile_item.text().strip())
            if window not in current_windows:
                continue
            template_cell = self.bindings_table.cellWidget(row, 1)
            sender_cell = self.bindings_table.cellWidget(row, 2)
            lock_cell = self.bindings_table.cellWidget(row, 3)
            template_name = ""
            if isinstance(template_cell, QComboBox):
                template_name = str(template_cell.currentData() or "")
            sender_name = sender_cell.text().strip() if isinstance(sender_cell, QLineEdit) else ""
            locked = bool(lock_cell.isChecked()) if isinstance(lock_cell, QCheckBox) else False
            bindings[window] = {
                "template_name": template_name,
                "sender_name": sender_name,
                "locked": locked,
            }
        try:
            self.window.settings.merge_window_bindings(bindings)
        except Exception as exc:
            self.window.show_error(tr("窗口绑定保存失败"), str(exc))
            return
        self.window.set_status(
            tr("窗口绑定已保存，共 {count} 个窗口。").format(count=len(bindings))
        )

    def prune_invalid_bindings(self) -> None:
        settings = self.window.settings
        current = {str(number) for number in settings.window_sequence}
        stale = [
            str(window)
            for window in settings.window_bindings
            if str(window) not in current
        ]
        if not stale:
            QMessageBox.information(
                self,
                tr("清理无效绑定"),
                tr("当前没有需要清理的历史绑定。"),
            )
            return
        answer = QMessageBox.question(
            self,
            tr("清理无效绑定"),
            tr("将永久删除 {count} 个已关闭/不在窗口顺序中的绑定。\n"
               "当前窗口的模板、发件人和锁定状态不会改变。")
            .format(count=len(stale)),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        try:
            removed = settings.prune_window_bindings(current)
        except Exception as exc:
            self.window.show_error(tr("清理无效绑定失败"), str(exc))
            return
        self.refresh_preserving()
        self.window.set_status(
            tr("已清理 {count} 个无效绑定。").format(count=removed)
        )


class TaskInspector(QWidget):
    def __init__(self, window) -> None:
        super().__init__()
        self.window = window
        self.current_row: dict | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        self.profile_tab = QWidget()
        profile_layout = QVBoxLayout(self.profile_tab)
        profile_layout.setContentsMargins(12, 12, 12, 12)
        profile_layout.setSpacing(10)
        self.form = QFormLayout()
        self.form.setSpacing(8)
        self.name_edit = QLineEdit()
        self.location_edit = QLineEdit()
        self.profile_edit = QLineEdit()
        self.form.addRow(tr("名字"), self.name_edit)
        self.form.addRow(tr("地区"), self.location_edit)
        self.form.addRow(tr("窗口编号"), self.profile_edit)
        profile_layout.addLayout(self.form)
        self.save_profile_button = QPushButton(tr("保存联系人资料"))
        self.save_profile_button.setProperty("class", "primary")
        self.save_profile_button.clicked.connect(self.save_profile)
        profile_layout.addWidget(self.save_profile_button)
        self.meta_label = QLabel("")
        self.meta_label.setObjectName("subtle")
        self.meta_label.setWordWrap(True)
        profile_layout.addWidget(self.meta_label)
        self.custom_label = QLabel("")
        self.custom_label.setObjectName("tiny")
        self.custom_label.setWordWrap(True)
        profile_layout.addWidget(self.custom_label)
        profile_layout.addStretch(1)
        self.tabs.addTab(self.profile_tab, tr("联系人资料"))

        self.preview_tab = QWidget()
        preview_layout = QVBoxLayout(self.preview_tab)
        preview_layout.setContentsMargins(12, 12, 12, 12)
        preview_layout.setSpacing(8)
        self.subject_label = QLabel(tr("尚未生成主题"))
        self.subject_label.setObjectName("sectionTitle")
        self.subject_label.setWordWrap(True)
        self.subject_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        preview_layout.addWidget(self.subject_label)
        self.preview_browser = QTextBrowser()
        self.preview_browser.setPlaceholderText(tr("选中联系人后，在这里检查邮件预览。"))
        preview_layout.addWidget(self.preview_browser, 1)
        preview_actions = QHBoxLayout()
        copy_subject = QPushButton(tr("复制主题"))
        copy_subject.clicked.connect(self.copy_subject)
        copy_body = QPushButton(tr("复制正文"))
        copy_body.clicked.connect(self.copy_body)
        self.generate_button = QPushButton(tr("生成邮件预览"))
        self.generate_button.setProperty("class", "primary")
        self.generate_button.clicked.connect(self.generate)
        self.auto_send_button = QPushButton(tr("填写并自动发送"))
        self.auto_send_button.setProperty("class", "primary")
        self.auto_send_button.clicked.connect(self.auto_send)
        preview_actions.addWidget(copy_subject)
        preview_actions.addWidget(copy_body)
        preview_actions.addStretch(1)
        preview_actions.addWidget(self.generate_button)
        preview_actions.addWidget(self.auto_send_button)
        preview_layout.addLayout(preview_actions)
        self.tabs.addTab(self.preview_tab, tr("邮件预览"))

        self.operation_tab = QWidget()
        operation_layout = QVBoxLayout(self.operation_tab)
        operation_layout.setContentsMargins(12, 12, 12, 12)
        operation_layout.setSpacing(8)
        self.operation_text = QPlainTextEdit()
        self.operation_text.setReadOnly(True)
        operation_layout.addWidget(self.operation_text, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        operation_layout.addWidget(self.progress)
        self.operation_status = QLabel("")
        self.operation_status.setObjectName("subtle")
        self.operation_status.setWordWrap(True)
        operation_layout.addWidget(self.operation_status)
        self.cancel_button = QPushButton(tr("停止当前操作"))
        self.cancel_button.setProperty("class", "danger")
        self.cancel_button.clicked.connect(self.window.cancel_operation)
        self.cancel_button.setEnabled(False)
        operation_layout.addWidget(self.cancel_button)
        self.tabs.addTab(self.operation_tab, tr("当前操作"))

        self.clear()

    def show_task(self, row: dict | None) -> None:
        self.current_row = row
        if row is None:
            self.clear()
            return
        self.name_edit.setText(str(row.get("name_override") or ""))
        self.location_edit.setText(str(row.get("location_override") or row.get("location") or ""))
        self.profile_edit.setText(str(row.get("profile_no") or ""))
        profile = str(row.get("profile_no") or "0")
        self.meta_label.setText(
            f"{status_text(row.get('status'))} · {str(row.get('recipient_email') or '')}\n"
            f"{tr('创建于')} {str(row.get('created_at') or '')[:16]}"
        )
        variables = custom_variables_of(row)
        if variables:
            text = "\n".join(f"{key}: {value}" for key, value in variables.items())
            self.custom_label.setText(tr("自定义变量\n{text}").format(text=text))
        else:
            self.custom_label.setText("")
        self.subject_label.setText(str(row.get("subject") or tr("尚未生成主题")))
        self.preview_browser.setPlainText(str(row.get("body") or tr("尚未生成正文，请先点击“生成预览”。")))
        self.generate_button.setEnabled(True)
        self.auto_send_button.setEnabled(True)

    def clear(self) -> None:
        self.current_row = None
        self.name_edit.clear()
        self.location_edit.clear()
        self.profile_edit.clear()
        self.meta_label.setText(tr("未选择联系人"))
        self.custom_label.setText("")
        self.subject_label.setText(tr("尚未生成主题"))
        self.preview_browser.clear()
        self.generate_button.setEnabled(False)
        self.auto_send_button.setEnabled(False)

    def save_profile(self) -> None:
        if self.current_row is None or not self.current_row.get("id"):
            return
        self.window.on_task_edit(
            int(self.current_row["id"]),
            "name_override",
            self.name_edit.text().strip(),
        )
        self.window.on_task_edit(
            int(self.current_row["id"]),
            "location_override",
            self.location_edit.text().strip(),
        )
        profile_value = self.profile_edit.text().strip()
        if profile_value:
            self.window.on_task_edit(
                int(self.current_row["id"]),
                "profile_no",
                profile_value,
            )
        self.window.set_status(tr("联系人资料已保存"))

    def copy_subject(self) -> None:
        from PySide6.QtWidgets import QApplication

        if self.current_row and self.current_row.get("subject"):
            QApplication.clipboard().setText(str(self.current_row["subject"]))
            self.window.set_status(tr("主题已复制"))

    def copy_body(self) -> None:
        from PySide6.QtWidgets import QApplication

        if self.current_row and self.current_row.get("body"):
            QApplication.clipboard().setText(str(self.current_row["body"]))
            self.window.set_status(tr("正文已复制"))

    def generate(self) -> None:
        if self.current_row is None or not self.current_row.get("id"):
            return
        self.window.generate_tasks([int(self.current_row["id"])])

    def auto_send(self) -> None:
        if self.current_row is None or not self.current_row.get("id"):
            return
        self.window.send_single_task(int(self.current_row["id"]))

    def set_operation_busy(self, busy: bool) -> None:
        self.cancel_button.setEnabled(busy)
        self.progress.setVisible(busy)
        self.progress.setRange(0, 0 if busy else 1)

    def log(self, text: str) -> None:
        self.operation_text.appendPlainText(text)
        self.operation_status.setText(text)

    def set_progress(self, value: int, text: str) -> None:
        self.progress.setRange(0, 100)
        self.progress.setValue(max(0, min(100, value)))
        if text:
            self.operation_status.setText(text)


class CollapsibleDetails(QWidget):
    """Collapsible right details panel with persisted width and state."""

    def __init__(
        self,
        inspector: TaskInspector,
        settings_store,
        state_key: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.inspector = inspector
        self.settings_store = settings_store
        self.state_key = state_key
        self._expanded = bool(
            settings_store.value(f"{state_key}_expanded", False, type=bool)
        )
        self._saved_width = int(
            settings_store.value(f"{state_key}_width", 340, type=int)
        )
        self._visible_override: bool | None = None
        self.splitter: QSplitter | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.toggle_button = QToolButton()
        self.toggle_button.setCheckable(True)
        self.toggle_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.toggle_button.setText(tr("详情"))
        self.toggle_button.setFixedHeight(30)
        self.toggle_button.clicked.connect(self.set_expanded)
        layout.addWidget(self.toggle_button)
        layout.addWidget(inspector, 1)
        self._apply_state()

    def _apply_state(self) -> None:
        visible = (
            self._visible_override
            if self._visible_override is not None
            else self._expanded
        )
        if visible:
            self.inspector.setVisible(True)
            self.setMinimumWidth(300)
            self.setMaximumWidth(420)
            self._restore_width()
        else:
            self.inspector.setVisible(False)
            self.setMinimumWidth(36)
            self.setMaximumWidth(36)
            self.setFixedWidth(36)
        self.toggle_button.setChecked(visible)
        self.toggle_button.setArrowType(
            Qt.LeftArrow if visible else Qt.RightArrow
        )
        self.setVisible(True)

    def _restore_width(self) -> None:
        if not self._expanded or self.splitter is None:
            return
        width = max(300, min(420, self._saved_width))
        splitter_width = max(0, self.splitter.width())
        left = max(300, splitter_width - width)
        self.splitter.setSizes([left, width])

    def restore_width(self) -> None:
        self._restore_width()

    def set_expanded(self, expanded: bool) -> None:
        self._expanded = bool(expanded)
        self._apply_state()
        self._restore_width()
        self.settings_store.setValue(
            f"{self.state_key}_expanded", self._expanded
        )

    def is_expanded(self) -> bool:
        return self._expanded

    def saved_width(self) -> int:
        return self._saved_width

    def auto_show(self) -> None:
        if self._visible_override is not False:
            self.set_expanded(True)

    def set_visible_override(self, visible: bool | None) -> None:
        self._visible_override = None if visible is None else bool(visible)
        self._apply_state()

    def save_width(self, width: int) -> None:
        if not self._expanded:
            return
        width = int(width)
        if width < 300:
            width = 300
        self._saved_width = min(420, width)
        self.settings_store.setValue(
            f"{self.state_key}_width", self._saved_width
        )
