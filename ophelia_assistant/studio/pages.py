"""Studio pages: activity workspace, contacts, history and settings."""

from __future__ import annotations

from PySide6.QtCore import Qt
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
    QListView,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ..i18n import LANGUAGES, tr
from .icons import icon
from .models import CampaignListModel, HistoryModel
from .panels import (
    CampaignItemDelegate,
    CollapsibleDetails,
    PageHeader,
    StatsBar,
    TaskInspector,
    TaskTablePanel,
    TemplateEditor,
    WindowPanel,
)
from .ui_state import UiStateStore
from .widgets import IconButton, LicenseDialog, SearchBox


SettingsStateStore = UiStateStore


class ActivityPage(QWidget):
    """Campaign list + current activity workspace + detail inspector."""

    def __init__(self, window) -> None:
        super().__init__()
        self.window = window
        self.current_campaign: dict | None = None
        self._draft_model = TaskTablePanel(
            window,
            empty_title=tr("还没有草稿"),
            empty_hint=tr("生成并打开 Gmail 草稿后会显示在这里。"),
            empty_actions=[
                (tr("生成所选"), self.window.generate_selected),
                (tr("打开 Gmail 草稿"), lambda: self.window.open_selected_drafts(False)),
            ],
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.header = PageHeader(tr("活动工作区"), tr("按活动/批次组织联系人、内容、窗口与草稿"))
        search = SearchBox(tr("搜索当前活动联系人"))
        search.submitted.connect(self.window.search_current_campaign)
        self.header.add_widget(search)
        command_button = QPushButton(tr("命令面板  Ctrl+K"))
        command_button.clicked.connect(self.window.open_command_palette)
        self.header.add_widget(command_button)
        new_button = QPushButton(tr("新建活动"))
        new_button.setProperty("class", "primary")
        new_button.clicked.connect(self.window.create_campaign)
        self.header.add_widget(new_button)
        layout.addWidget(self.header)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self._build_campaign_panel())

        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(1)
        splitter.addWidget(self._build_workspace())
        self.inspector = TaskInspector(window)
        self.details_wrapper = CollapsibleDetails(
            self.inspector,
            window.ui_state,
            "activity_details",
        )
        splitter.addWidget(self.details_wrapper)
        self.details_wrapper.splitter = splitter
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([900, 340])
        self.details_wrapper.restore_width()
        splitter.splitterMoved.connect(
            lambda _pos, _index: self.details_wrapper.save_width(
                self.details_wrapper.width()
            )
        )
        body.addWidget(splitter, 1)
        layout.addLayout(body, 1)

        self.tabs.currentChanged.connect(self._on_activity_tab_changed)

    def _on_activity_tab_changed(self, index: int) -> None:
        if index in {1, 2}:
            self.details_wrapper.set_visible_override(False)
        else:
            self.details_wrapper.set_visible_override(None)
        if index == 2:
            self.window.refresh_window_bindings()

    def inspector_auto_show(self) -> None:
        self.details_wrapper.auto_show()

    def _build_campaign_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("panel")
        panel.setFixedWidth(248)
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(10, 12, 10, 12)
        panel_layout.setSpacing(8)

        header_row = QHBoxLayout()
        title = QLabel(tr("活动/批次"))
        title.setObjectName("sectionTitle")
        self.campaign_count_label = QLabel("0")
        self.campaign_count_label.setObjectName("countText")
        header_row.addWidget(title)
        header_row.addWidget(self.campaign_count_label)
        header_row.addStretch(1)
        add_button = IconButton("plus", "#2563EB", tr("新建活动"))
        add_button.clicked.connect(self.window.create_campaign)
        header_row.addWidget(add_button)
        panel_layout.addLayout(header_row)

        self.campaign_search = SearchBox(tr("筛选活动"))
        self.campaign_search.submitted.connect(self._filter_campaigns)
        panel_layout.addWidget(self.campaign_search)

        self.campaign_model = CampaignListModel(self)
        self.campaign_list = QListView()
        self.campaign_list.setObjectName("campaignList")
        self.campaign_list.setModel(self.campaign_model)
        self.campaign_list.setFrameShape(QListView.NoFrame)
        self.campaign_list.setSelectionMode(QListView.SingleSelection)
        self.campaign_list.setEditTriggers(QListView.NoEditTriggers)
        self.campaign_list.setUniformItemSizes(True)
        palette_tokens = getattr(self.window, "palette_tokens", None)
        if palette_tokens is not None:
            self.campaign_list.setItemDelegate(
                CampaignItemDelegate(palette_tokens, self.campaign_list)
            )
        self.campaign_list.selectionModel().selectionChanged.connect(
            self._campaign_selected
        )
        panel_layout.addWidget(self.campaign_list, 1)

        footer = QHBoxLayout()
        rename_button = QPushButton(tr("重命名"))
        rename_button.clicked.connect(self.window.rename_campaign)
        delete_button = QPushButton(tr("删除"))
        delete_button.setProperty("class", "danger")
        delete_button.clicked.connect(self.window.delete_campaign)
        footer.addWidget(rename_button)
        footer.addWidget(delete_button)
        panel_layout.addLayout(footer)
        return panel

    def _filter_campaigns(self, text: str) -> None:
        keyword = text.strip().lower()
        rows = [row for row in self._all_campaigns if not keyword or keyword in str(row["name"]).lower()]
        self.campaign_model.set_campaigns(rows)

    def _campaign_selected(self, _selected, _deselected) -> None:
        index = self.campaign_list.currentIndex()
        campaign = self.campaign_model.campaign_at(index.row()) if index.isValid() else None
        if campaign is not None:
            self.window.select_campaign(int(campaign["id"]))

    def set_campaigns(self, campaigns: list[dict], keep_selection: bool = True) -> None:
        self._all_campaigns = list(campaigns)
        keyword = self.campaign_search.text().strip().lower()
        rows = [row for row in campaigns if not keyword or keyword in str(row["name"]).lower()]
        self.campaign_model.set_campaigns(rows)
        self.campaign_count_label.setText(str(len(campaigns)))
        if not keep_selection or self.current_campaign is None:
            return
        selected_id = int(self.current_campaign["id"])
        for row in range(self.campaign_model.rowCount()):
            candidate = self.campaign_model.campaign_at(row)
            if candidate is not None and int(candidate["id"]) == selected_id:
                self.campaign_list.setCurrentIndex(self.campaign_model.index(row, 0))
                return
        if self.campaign_model.rowCount():
            self.campaign_list.setCurrentIndex(self.campaign_model.index(0, 0))

    def set_current_campaign(self, campaign: dict | None) -> None:
        self.current_campaign = campaign
        if campaign is None:
            self.header.set_subtitle(tr("选择一个活动/批次开始"))
            self.workspace_summary.setText("")
            return
        sent = int(campaign.get("sent_count") or 0)
        drafted = int(campaign.get("drafted_count") or 0)
        pending = int(campaign.get("pending_count") or 0)
        failed = int(campaign.get("failed_count") or 0)
        self.header.set_title(campaign["name"])
        self.header.set_subtitle(
            tr("{total} 条联系人 · 已发送 {sent} · 草稿 {drafted} · 待确认 {pending} · 失败 {failed}")
            .format(total=campaign.get("task_count") or 0, sent=sent, drafted=drafted, pending=pending, failed=failed)
        )

    def _build_workspace(self) -> QWidget:
        workspace = QWidget()
        workspace_layout = QVBoxLayout(workspace)
        workspace_layout.setContentsMargins(14, 12, 10, 12)
        workspace_layout.setSpacing(8)

        self.workspace_summary = QLabel("")
        self.workspace_summary.setObjectName("subtle")
        workspace_layout.addWidget(self.workspace_summary)
        self.stats_bar = StatsBar()
        workspace_layout.addWidget(self.stats_bar)

        actions = QHBoxLayout()
        self.import_button = QPushButton(tr("导入联系人"))
        self.import_button.setProperty("class", "primary")
        self.import_button.clicked.connect(self.window.import_contacts)
        self.add_contact_button = QPushButton(tr("新增联系人"))
        self.add_contact_button.clicked.connect(self.window.add_contact)
        self.generate_button = QPushButton(tr("生成邮件预览"))
        self.generate_button.clicked.connect(self.window.generate_selected)
        self.open_draft_button = QPushButton(tr("填写草稿"))
        self.open_draft_button.clicked.connect(
            lambda: self.window.open_selected_drafts(wait_send=False)
        )
        more_button = QPushButton(tr("更多操作"))
        more_button.clicked.connect(self._more_menu)
        actions.addWidget(self.import_button)
        actions.addWidget(self.add_contact_button)
        actions.addWidget(self.generate_button)
        actions.addWidget(self.open_draft_button)
        actions.addStretch(1)
        actions.addWidget(more_button)
        workspace_layout.addLayout(actions)

        self.tabs = QTabWidget()
        self.contacts_panel = TaskTablePanel(
            self.window,
            empty_title=tr("还没有联系人"),
            empty_hint=tr("导入 Excel/CSV 或手动新增联系人，开始本批次。"),
            empty_actions=[
                (tr("导入联系人"), self.window.import_contacts),
                (tr("载入示例数据"), self.window.load_demo_data),
            ],
        )
        self.tabs.addTab(self.contacts_panel, tr("联系人"))
        self.template_editor = TemplateEditor(self.window)
        self.tabs.addTab(self.template_editor, tr("内容"))
        self.window_panel = WindowPanel(self.window)
        self.tabs.addTab(self.window_panel, tr("窗口"))
        self.tabs.addTab(self._build_draft_tab(), tr("草稿"))
        workspace_layout.addWidget(self.tabs, 1)
        return workspace

    def set_has_selection(self, has_selection: bool) -> None:
        """Move the primary accent to the action that matches the selection."""
        if has_selection:
            self.generate_button.setProperty("class", "primary")
            self.import_button.setProperty("class", "")
        else:
            self.generate_button.setProperty("class", "")
            self.import_button.setProperty("class", "primary")
        for button in (self.generate_button, self.import_button):
            button.style().unpolish(button)
            button.style().polish(button)
            button.update()

    def _build_draft_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        toolbar = QHBoxLayout()
        open_draft = QPushButton(tr("填写草稿"))
        open_draft.clicked.connect(lambda: self.window.open_selected_drafts(wait_send=False))
        wait_send = QPushButton(tr("填写并自动发送"))
        wait_send.clicked.connect(lambda: self.window.open_selected_drafts(wait_send=True))
        mark_sent = QPushButton(tr("标记已发送"))
        mark_sent.clicked.connect(self.window.mark_selected_sent)
        retry = QPushButton(tr("重试失败"))
        retry.clicked.connect(self.window.retry_failed_drafts)
        undo = QPushButton(tr("撤销发送"))
        undo.clicked.connect(self.window.unmark_selected)
        confirm_unsent = QPushButton(tr("确认未发送"))
        confirm_unsent.clicked.connect(self.window.confirm_tasks_unsent)
        confirm_sent = QPushButton(tr("确认已发送"))
        confirm_sent.clicked.connect(self.window.confirm_tasks_sent)
        toolbar.addWidget(open_draft)
        toolbar.addWidget(wait_send)
        toolbar.addWidget(mark_sent)
        toolbar.addWidget(retry)
        toolbar.addWidget(undo)
        toolbar.addWidget(confirm_unsent)
        toolbar.addWidget(confirm_sent)
        toolbar.addStretch(1)
        layout.addLayout(toolbar)
        layout.addWidget(self._draft_model, 1)
        return tab

    def _more_menu(self) -> None:
        menu = QMenu(self)
        menu.addAction(tr("填写并自动发送"), lambda: self.window.open_selected_drafts(wait_send=True))
        menu.addAction(tr("标记所选为已发送"), self.window.mark_selected_sent)
        menu.addAction(tr("撤销已发送标记"), self.window.unmark_selected)
        menu.addSeparator()
        menu.addAction(tr("重试失败草稿"), self.window.retry_failed_drafts)
        menu.addAction(tr("停止当前操作"), self.window.cancel_operation)
        menu.addSeparator()
        menu.addAction(tr("删除所选联系人"), self.window.delete_selected)
        menu.exec(self.sender().mapToGlobal(self.sender().rect().bottomLeft()))

    def set_tasks(self, rows) -> None:
        self.contacts_panel.set_tasks(rows)

    def set_draft_tasks(self, rows) -> None:
        self._draft_model.set_tasks(rows)

    def set_stats(self, stats: dict[str, int]) -> None:
        self.stats_bar.set_stats(stats)

    def selected_task_ids(self) -> list[int]:
        return self.contacts_panel.selected_ids()

    def draft_task_ids(self) -> list[int]:
        return self._draft_model.selected_ids()

    def select_task_ids(self, task_ids: list[int]) -> None:
        self.contacts_panel.select_ids(task_ids)

    def current_campaign_id(self) -> int | None:
        if self.current_campaign is None:
            return None
        return int(self.current_campaign["id"])

    def current_campaign_name(self) -> str:
        if self.current_campaign is None:
            return ""
        return str(self.current_campaign["name"])


class ContactsPage(QWidget):
    def __init__(self, window) -> None:
        super().__init__()
        self.window = window
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.header = PageHeader(tr("联系人"), tr("跨活动查看与编辑全部联系人"))
        search = SearchBox(tr("搜索姓名 / 地区 / 邮箱"))
        search.submitted.connect(self.window.search_all_contacts)
        self.header.add_widget(search)
        import_button = QPushButton(tr("导入联系人"))
        import_button.setProperty("class", "primary")
        import_button.clicked.connect(self.window.import_contacts)
        self.header.add_widget(import_button)
        layout.addWidget(self.header)

        body = QSplitter(Qt.Horizontal)
        body.setHandleWidth(1)
        self.panel = TaskTablePanel(
            window,
            extra_columns=[("campaign_name", "活动/批次")],
            empty_title=tr("还没有联系人"),
            empty_hint=tr("从任意活动导入联系人后会显示在这里。"),
            empty_actions=[
                (tr("导入联系人"), window.import_contacts),
                (tr("载入示例数据"), window.load_demo_data),
            ],
        )
        body.addWidget(self.panel)
        self.inspector = TaskInspector(window)
        self.details_wrapper = CollapsibleDetails(
            self.inspector,
            window.ui_state,
            "contacts_details",
        )
        body.addWidget(self.details_wrapper)
        self.details_wrapper.splitter = body
        body.setStretchFactor(0, 4)
        body.setStretchFactor(1, 1)
        body.setSizes([900, 340])
        self.details_wrapper.restore_width()
        body.splitterMoved.connect(
            lambda _pos, _index: self.details_wrapper.save_width(
                self.details_wrapper.width()
            )
        )
        layout.addWidget(body, 1)

    def inspector_auto_show(self) -> None:
        self.details_wrapper.auto_show()

    def set_tasks(self, rows) -> None:
        self.panel.set_tasks(rows)

    def selected_task_ids(self) -> list[int]:
        return self.panel.selected_ids()


class HistoryPage(QWidget):
    def __init__(self, window) -> None:
        super().__init__()
        self.window = window
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.header = PageHeader(tr("历史"), tr("已发送、已回复与失败记录"))
        self.filter_combo = QComboBox()
        self.filter_combo.addItem(tr("全部状态"), "")
        self.filter_combo.addItem(tr("已发送"), "sent")
        self.filter_combo.addItem(tr("已回复"), "replied")
        self.filter_combo.addItem(tr("失败记录"), "failed")
        self.filter_combo.currentIndexChanged.connect(
            lambda _index: self.window.refresh_history()
        )
        self.search = SearchBox(tr("搜索历史邮箱 / 名字"))
        self.search.submitted.connect(lambda _text: self.window.refresh_history())
        self.header.add_widget(self.filter_combo)
        self.header.add_widget(self.search)
        layout.addWidget(self.header)

        toolbar = QHBoxLayout()
        undo_button = QPushButton(tr("撤销已发送"))
        undo_button.clicked.connect(self.window.unmark_selected_history)
        delete_button = QPushButton(tr("删除所选"))
        delete_button.setProperty("class", "danger")
        delete_button.clicked.connect(self.window.delete_selected_history)
        clear_button = QPushButton(tr("清空历史"))
        clear_button.setProperty("class", "danger")
        clear_button.clicked.connect(self.window.clear_history)
        toolbar.addWidget(undo_button)
        toolbar.addWidget(delete_button)
        toolbar.addWidget(clear_button)
        toolbar.addStretch(1)
        layout.addLayout(toolbar)

        self.model = HistoryModel(self)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(30)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.doubleClicked.connect(self._show_detail)
        layout.addWidget(self.table, 1)

    def set_rows(self, rows) -> None:
        self.model.set_rows(rows)

    def selected_task_ids(self) -> list[int]:
        rows = self.table.selectionModel().selectedRows()
        ids: list[int] = []
        for index in rows:
            row = self.model.task_at(index.row())
            if row is not None and row.get("id"):
                ids.append(int(row["id"]))
        return ids

    def current_filter(self) -> str:
        return str(self.filter_combo.currentData() or "")

    def search_text(self) -> str:
        return self.search.text().strip()

    def _show_detail(self, index) -> None:
        row = self.model.task_at(index.row())
        if row is None:
            return
        subject = str(row.get("subject") or "")
        body = str(row.get("body") or "")
        message = f"{tr('主题')}: {subject}\n\n{body}\n\n{tr('最近错误')}: {row.get('last_error') or ''}"
        QMessageBox.information(self, tr("历史详情"), message)


class TemplatesPage(QWidget):
    def __init__(self, window) -> None:
        super().__init__()
        self.window = window
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.header = PageHeader(tr("模板"), tr("主题、正文、签名与模板库"))
        layout.addWidget(self.header)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(16, 14, 16, 14)
        self.editor = TemplateEditor(window)
        body_layout.addWidget(self.editor)
        layout.addWidget(body, 1)

    def reload(self) -> None:
        self.editor.load()


class SettingsPage(QWidget):
    def __init__(self, window) -> None:
        super().__init__()
        self.window = window
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.header = PageHeader(tr("设置"), tr("浏览器接口、发件人、语言与主题"))
        layout.addWidget(self.header)

        body = QHBoxLayout()
        body.setContentsMargins(16, 14, 16, 14)
        body.setSpacing(14)
        form_card = QFrame()
        form_card.setObjectName("card")
        form_layout = QVBoxLayout(form_card)
        form_layout.setContentsMargins(16, 16, 16, 16)
        form_layout.setSpacing(10)
        title = QLabel(tr("本地与 Gmail 设置"))
        title.setObjectName("sectionTitle")
        form_layout.addWidget(title)

        form = QFormLayout()
        form.setSpacing(9)
        self.provider_combo = QComboBox()
        self.provider_combo.addItem("MoreLogin", "morelogin")
        self.provider_combo.addItem("AdsPower Browser", "adspower")
        self.provider_combo.addItem("BitBrowser Global", "bitbrowser")
        self.morelogin_edit = QLineEdit()
        self.adspower_edit = QLineEdit()
        self.api_key_edit = QLineEdit()
        self.bitbrowser_edit = QLineEdit()
        self.sender_edit = QLineEdit()
        self.language_combo = QComboBox()
        for key, name in LANGUAGES.items():
            self.language_combo.addItem(name, key)
        self.theme_combo = QComboBox()
        self.theme_combo.addItem(tr("浅色"), "light")
        self.theme_combo.addItem(tr("深色"), "dark")
        form.addRow(tr("浏览器窗口应用"), self.provider_combo)
        form.addRow("MoreLogin URL", self.morelogin_edit)
        form.addRow("AdsPower URL", self.adspower_edit)
        form.addRow("AdsPower API Key", self.api_key_edit)
        form.addRow("BitBrowser URL", self.bitbrowser_edit)
        form.addRow(tr("默认发件人"), self.sender_edit)
        form.addRow(tr("界面语言"), self.language_combo)
        form.addRow(tr("主题模式"), self.theme_combo)
        self.auto_send_check = QCheckBox(
            tr("填写完成后自动点击发送（需先确认收件人与内容）")
        )
        form.addRow("", self.auto_send_check)
        self.auto_confirm_check = QCheckBox(
            tr("自动发送前弹出确认窗口（显示任务数量与浏览器窗口）")
        )
        form.addRow("", self.auto_confirm_check)
        form_layout.addLayout(form)
        save_button = QPushButton(tr("保存设置"))
        save_button.setProperty("class", "primary")
        save_button.clicked.connect(self.save)
        form_layout.addWidget(save_button)
        form_layout.addStretch(1)
        body.addWidget(form_card, 3)

        right_column = QVBoxLayout()
        right_column.setSpacing(14)
        license_card = QFrame()
        license_card.setObjectName("card")
        license_layout = QVBoxLayout(license_card)
        license_layout.setContentsMargins(16, 16, 16, 16)
        license_layout.setSpacing(9)
        license_layout.addWidget(self._title(tr("管理员授权")))
        self.license_status = QLabel("")
        self.license_status.setObjectName("subtle")
        self.license_status.setWordWrap(True)
        license_layout.addWidget(self.license_status)
        code_row = QHBoxLayout()
        self.device_code_edit = QLineEdit()
        self.device_code_edit.setReadOnly(True)
        copy_button = QPushButton(tr("复制设备码"))
        copy_button.clicked.connect(self._copy_device_code)
        code_row.addWidget(self.device_code_edit, 1)
        code_row.addWidget(copy_button)
        license_layout.addLayout(code_row)
        verify_row = QHBoxLayout()
        self.verify_edit = QLineEdit()
        self.verify_edit.setPlaceholderText(tr("输入管理员验证码"))
        verify_button = QPushButton(tr("立即验证"))
        verify_button.setProperty("class", "primary")
        verify_button.clicked.connect(self._verify)
        verify_row.addWidget(self.verify_edit, 1)
        verify_row.addWidget(verify_button)
        license_layout.addLayout(verify_row)
        right_column.addWidget(license_card)

        maintenance_card = QFrame()
        maintenance_card.setObjectName("card")
        maintenance_layout = QVBoxLayout(maintenance_card)
        maintenance_layout.setContentsMargins(16, 16, 16, 16)
        maintenance_layout.setSpacing(9)
        maintenance_layout.addWidget(self._title(tr("数据与更新")))
        backup_button = QPushButton(tr("立即备份数据库"))
        backup_button.clicked.connect(self.window.backup_database)
        restore_button = QPushButton(tr("从备份恢复"))
        restore_button.clicked.connect(self.window.restore_database)
        log_button = QPushButton(tr("打开日志/错误报告文件夹"))
        log_button.clicked.connect(self.window.open_log_folder)
        update_button = QPushButton(tr("检查更新"))
        update_button.clicked.connect(lambda: self.window.check_update())
        maintenance_layout.addWidget(backup_button)
        maintenance_layout.addWidget(restore_button)
        maintenance_layout.addWidget(log_button)
        maintenance_layout.addWidget(update_button)
        maintenance_layout.addStretch(1)
        right_column.addWidget(maintenance_card, 1)

        diagnostics_card = QFrame()
        diagnostics_card.setObjectName("card")
        diagnostics_layout = QVBoxLayout(diagnostics_card)
        diagnostics_layout.setContentsMargins(16, 16, 16, 16)
        diagnostics_layout.setSpacing(9)
        diagnostics_layout.addWidget(self._title(tr("数据与授权诊断")))
        self.diagnostics_text = QPlainTextEdit()
        self.diagnostics_text.setReadOnly(True)
        self.diagnostics_text.setMaximumHeight(260)
        diagnostics_layout.addWidget(self.diagnostics_text)
        diagnostics_buttons = QHBoxLayout()
        open_data_button = QPushButton(tr("打开数据目录"))
        open_data_button.clicked.connect(self.window.open_data_folder)
        export_button = QPushButton(tr("导出脱敏诊断"))
        export_button.clicked.connect(self.window.export_redacted_diagnostics)
        check_button = QPushButton(tr("检查数据一致性"))
        check_button.clicked.connect(self.window.check_data_consistency)
        diagnostics_buttons.addWidget(open_data_button)
        diagnostics_buttons.addWidget(export_button)
        diagnostics_buttons.addWidget(check_button)
        diagnostics_layout.addLayout(diagnostics_buttons)
        right_column.addWidget(diagnostics_card, 1)
        body.addLayout(right_column, 2)
        layout.addLayout(body, 1)

        self.load()

    @staticmethod
    def _title(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("sectionTitle")
        return label

    def load(self) -> None:
        settings = self.window.settings
        self.provider_combo.setCurrentIndex(
            max(0, self.provider_combo.findData(settings.browser_provider))
        )
        self.morelogin_edit.setText(settings.morelogin_url)
        self.adspower_edit.setText(settings.adspower_url)
        self.api_key_edit.setText(settings.adspower_api_key)
        self.bitbrowser_edit.setText(settings.bitbrowser_url)
        self.sender_edit.setText(settings.sender_name)
        self.language_combo.setCurrentIndex(
            max(0, self.language_combo.findData(settings.language))
        )
        self.theme_combo.setCurrentIndex(
            max(0, self.theme_combo.findData(settings.theme_mode))
        )
        self.auto_send_check.setChecked(bool(settings.auto_click_send))
        self.auto_confirm_check.setChecked(bool(settings.auto_send_confirm))
        from ..trial import device_code, remaining_text

        self.device_code_edit.setText(device_code())
        license_text = tr(remaining_text(self.window.trial_status))
        if getattr(self.window.trial_status, "suspicious", False):
            license_text += "\n" + tr(
                "检测到授权状态异常，请核对（已暂停自动写回）"
            )
        self.license_status.setText(license_text)
        self._refresh_diagnostics()

    def _refresh_diagnostics(self) -> None:
        from datetime import datetime

        from .. import __version__
        from ..config import SETTINGS_SCHEMA_VERSION, settings_path
        from ..database import DATABASE_SCHEMA_VERSION
        from ..trial import authorization_info, remaining_text

        settings = self.window.settings
        auth = authorization_info(self.window.trial_status)
        sequence = list(settings.window_sequence)
        bindings = settings.window_bindings
        current_set = {str(number) for number in sequence}
        hidden_count = sum(
            1 for window in bindings if str(window) not in current_set
        )
        changed_at = int(auth.get("last_changed_at") or 0)
        changed_iso = (
            datetime.fromtimestamp(changed_at).isoformat() if changed_at else "无"
        )
        lines = [
            tr("当前配置文件路径：{path}").format(path=settings_path()),
            tr("当前数据库路径：{path}").format(path=self.window.db.path),
            tr("当前授权文件路径：{path}").format(path=auth["state_path"]),
            tr("当前软件版本：v{version}").format(version=__version__),
            tr("配置结构版本：{version}").format(
                version=getattr(settings, "_schema_version", 0)
                or SETTINGS_SCHEMA_VERSION
            ),
            tr("数据库结构版本：{version}").format(
                version=DATABASE_SCHEMA_VERSION
            ),
            tr("授权状态版本：{version}").format(version=auth["state_version"]),
            tr("当前浏览器类型：{provider}").format(
                provider=settings.browser_provider
            ),
            tr("窗口顺序：{windows}").format(
                windows="、".join(str(number) for number in sequence) or tr("无")
            ),
            tr("当前绑定窗口：{windows}").format(
                windows="、".join(str(number) for number in sequence) or tr("无")
            ),
            tr("历史隐藏绑定数量：{count}").format(count=hidden_count),
            tr("授权到期时间：{time}").format(
                time=auth["authorized_until_iso"] or tr("无")
            ),
            tr("授权来源：{source}").format(source=auth["grant_source"] or tr("无")),
            tr("最近一次授权变更时间：{time}").format(time=changed_iso),
            tr("最近一次变更原因：{reason}").format(
                reason=auth["change_reason"] or tr("无")
            ),
            tr("当前机器码摘要：{digest}").format(digest=auth["machine_digest"]),
            tr("是否读取了备份文件：{value}").format(
                value=tr("是") if auth["backup_used"] else tr("否")
            ),
            tr("是否发生了迁移：{value}").format(
                value=tr("是") if auth.get("migrated_at") else tr("否")
            ),
            tr("授权状态异常：{value}").format(
                value=tr("是") if auth["suspicious"] else tr("否")
            ),
            tr("授权剩余时间：{text}").format(
                text=remaining_text(self.window.trial_status)
            ),
        ]
        self.diagnostics_text.setPlainText("\n".join(lines))

    def save(self) -> None:
        settings = self.window.settings
        new_api_key = self.api_key_edit.text().strip()
        if new_api_key != settings.adspower_api_key:
            settings.mark_api_key_dirty()
        settings.browser_provider = str(self.provider_combo.currentData() or "morelogin")
        settings.morelogin_url = self.morelogin_edit.text().strip()
        settings.adspower_url = self.adspower_edit.text().strip()
        settings.adspower_api_key = new_api_key
        settings.bitbrowser_url = self.bitbrowser_edit.text().strip()
        settings.sender_name = self.sender_edit.text().strip() or "Anna Lee"
        settings.language = str(self.language_combo.currentData() or "zh")
        settings.theme_mode = str(self.theme_combo.currentData() or "light")
        settings.auto_click_send = self.auto_send_check.isChecked()
        settings.auto_send_confirm = self.auto_confirm_check.isChecked()
        try:
            settings.save()
        except Exception as exc:
            QMessageBox.critical(self, tr("保存失败"), str(exc))
            return
        self.window.apply_settings()
        self.window.set_status(tr("设置已保存"))

    def _copy_device_code(self) -> None:
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(self.device_code_edit.text())
        self.window.set_status(tr("设备码已复制"))

    def _verify(self) -> None:
        from ..trial import verify_authorization_code

        code = self.verify_edit.text().strip()
        if not code:
            QMessageBox.warning(self, tr("缺少验证码"), tr("请先填写管理员提供的验证码。"))
            return
        verified, message, status = verify_authorization_code(code)
        self.window.trial_status = status
        self.window.apply_license_state()
        self.verify_edit.clear()
        if verified:
            QMessageBox.information(self, tr("管理员验证"), message)
            self.load()
        else:
            QMessageBox.critical(self, tr("验证失败"), message)
