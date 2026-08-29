"""Main studio window: 56px rail, pages, command palette and business wiring."""

from __future__ import annotations

import logging
import inspect
import os
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from hashlib import sha256
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QThreadPool
from PySide6.QtGui import QColor, QFont, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QStatusBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..batch import assign_windows, group_tasks_by_window
from ..config import (
    BATCH_DRAFT_INTERVAL_SECONDS,
    MAX_CONCURRENT_TASKS,
    MAX_GENERATE_TASKS,
    MAX_WINDOW_SEQUENCE,
    Settings,
    app_data_dir,
    is_newer_version,
)
from ..database import Database, now_iso
from ..diagnostics import write_error_report
from ..i18n import LANGUAGES, set_language, tr
from ..mail_content import render_email
from ..morelogin import BROWSER_PROVIDER_NAMES, create_browser_provider
from ..operation import OperationCancelledError, OperationController, check_cancel
from ..trial import check_trial, device_code, remaining_text, verify_authorization_code
from ..update_security import sha256_hex, verify_update_payload
from ..workflow import Workflow
from .icons import icon, make_pixmap
from .pages import (
    ActivityPage,
    ContactsPage,
    HistoryPage,
    SettingsPage,
    TemplatesPage,
)
from .theme import STATUS_COLORS, build_qss, palette_for
from .widgets import (
    CommandPalette,
    ContactPromptDialog,
    ImportContactsDialog,
    LicenseDialog,
    TaskProgressDialog,
    TextPromptDialog,
)
from .workers import FunctionWorker, WorkerSignals


logger = logging.getLogger("niuma-mail")


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = Settings.load()
        set_language(self.settings.language)
        self.db = Database()
        self.db.backup()
        self.workflow = Workflow(self.db, self.settings)
        self.trial_status = check_trial()
        self.operations = OperationController()
        self.thread_pool = QThreadPool(self)
        self.thread_pool.setMaxThreadCount(4)
        self.settings_store = QSettings("NiuMaMail", "Studio")
        self._busy = False
        self._licensed = bool(self.trial_status.active)
        self._closing = False
        self._window_locks: dict[int, threading.Lock] = {}
        self._window_locks_guard = threading.Lock()
        self._campaigns: list[dict] = []
        self._campaign_search_text = ""
        self._command_palette: CommandPalette | None = None
        self._current_progress_dialog: TaskProgressDialog | None = None
        self._current_page = 0

        self.setWindowTitle(f"{tr('牛马邮箱')} · {tr('外贸邮件工作室')} v{__version__}")
        self.resize(1440, 880)
        self.setMinimumSize(1180, 720)
        self._apply_palette()
        self._build_ui()
        self._install_shortcuts()
        self.refresh_all()
        self.apply_license_state()
        geometry = self.settings_store.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)
        last_campaign = self.settings_store.value("last_campaign_id", type=int)
        if last_campaign:
            campaign_ids = {int(item["id"]) for item in self._campaigns}
            if last_campaign in campaign_ids:
                self.select_campaign(last_campaign)

    # ---------------------------------------------------------------- UI
    def _apply_palette(self) -> None:
        self.palette_tokens = palette_for(self.settings.theme_mode)
        QApplication.instance().setStyleSheet(build_qss(self.palette_tokens))

    def _build_ui(self) -> None:
        self.pages = [
            ActivityPage(self),
            ContactsPage(self),
            TemplatesPage(self),
            HistoryPage(self),
            SettingsPage(self),
        ]
        self.rail_buttons: list[QToolButton] = []
        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_rail())
        self.stack = QStackedWidget()
        for page in self.pages:
            self.stack.addWidget(page)
        layout.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        status = QStatusBar()
        status.setSizeGripEnabled(False)
        self.status_label = QLabel(tr("就绪"))
        self.license_label = QLabel("")
        status.addWidget(self.status_label, 1)
        status.addPermanentWidget(self.license_label)
        self.setStatusBar(status)
        self._set_window_icon()

    def _build_rail(self) -> QWidget:
        rail = QFrame()
        rail.setObjectName("rail")
        rail.setFixedWidth(56)
        layout = QVBoxLayout(rail)
        layout.setContentsMargins(6, 10, 6, 10)
        layout.setSpacing(4)

        brand = QLabel()
        try:
            pixmap = make_pixmap("activity", self.palette_tokens.accent, 26)
            brand.setPixmap(pixmap)
        except Exception:
            pass
        brand.setAlignment(Qt.AlignCenter)
        brand.setToolTip(tr("牛马邮箱 · 外贸邮件工作室"))
        layout.addWidget(brand)
        layout.addSpacing(8)

        entries = [
            ("activity", tr("活动"), 0),
            ("contacts", tr("联系人"), 1),
            ("template", tr("模板"), 2),
            ("history", tr("历史"), 3),
        ]
        for name, tooltip, index in entries:
            button = QToolButton()
            button.setObjectName("railButton")
            button.setIcon(icon(name, self.palette_tokens.muted, 20))
            button.setIconSize(button.icon().actualSize(button.icon().pixmap(20, 20).size()))
            button.setFixedSize(44, 44)
            button.setToolTip(tooltip)
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda _checked=False, i=index: self.switch_page(i))
            layout.addWidget(button, 0, Qt.AlignHCenter)
            self.rail_buttons.append(button)

        layout.addStretch(1)
        settings_button = QToolButton()
        settings_button.setObjectName("railButton")
        settings_button.setIcon(icon("settings", self.palette_tokens.muted, 20))
        settings_button.setIconSize(settings_button.icon().actualSize(settings_button.icon().pixmap(20, 20).size()))
        settings_button.setFixedSize(44, 44)
        settings_button.setToolTip(tr("设置"))
        settings_button.setCheckable(True)
        settings_button.setCursor(Qt.PointingHandCursor)
        settings_button.clicked.connect(lambda _checked=False: self.switch_page(4))
        layout.addWidget(settings_button, 0, Qt.AlignHCenter)
        self.rail_buttons.append(settings_button)

        self._sync_rail()
        return rail

    def _sync_rail(self) -> None:
        for index, button in enumerate(self.rail_buttons):
            button.setChecked(index == self._current_page)

    def _install_shortcuts(self) -> None:
        QShortcut(QKeySequence("Ctrl+K"), self, self.open_command_palette)
        QShortcut(QKeySequence("Ctrl+N"), self, self.create_campaign)
        QShortcut(QKeySequence("Ctrl+I"), self, self.import_contacts)
        QShortcut(QKeySequence("Ctrl+Return"), self, self.generate_selected)
        QShortcut(QKeySequence("Ctrl+D"), self, lambda: self.open_selected_drafts(wait_send=False))
        QShortcut(QKeySequence("F5"), self, self.refresh_all)

    def _set_window_icon(self) -> None:
        base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent.parent))
        icon_path = base / "assets" / "niuma-mail-icon.ico"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

    def switch_page(self, index: int) -> None:
        if not (0 <= index < self.stack.count()):
            return
        self._current_page = index
        self.stack.setCurrentIndex(index)
        self._sync_rail()
        if index == 2:
            self.pages[2].reload()
        if index == 4:
            self.pages[4].load()

    # ------------------------------------------------------------- status
    def set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def show_error(self, title: str, message: str) -> None:
        self.set_status(f"{title}：{message}")
        QMessageBox.critical(self, title, message)

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        for page in self.pages:
            if hasattr(page, "inspector"):
                page.inspector.set_operation_busy(busy)
        if busy:
            self.set_status(tr("处理中…"))

    def log_operation(self, text: str) -> None:
        inspector = self._active_inspector()
        if inspector is not None:
            inspector.log(text)

    def _active_inspector(self):
        page = self.pages[self._current_page] if self.pages else None
        if hasattr(page, "inspector"):
            return page.inspector
        return None

    def _update_license_label(self) -> None:
        self.license_label.setText(
            f"{tr(remaining_text(self.trial_status))} · "
            f"{tr('本地模式') if self._licensed else tr('功能已锁定')}"
        )

    def apply_license_state(self) -> None:
        self._licensed = bool(self.trial_status.active)
        self._update_license_label()
        enabled = self._licensed
        activity = self.pages[0]
        for button in (
            activity.import_button,
            activity.generate_button,
            activity.open_draft_button,
            getattr(activity, "add_contact_button", None),
            activity.inspector.generate_button,
            activity.inspector.auto_send_button,
            activity.window_panel.auto_fill_button,
        ):
            if button is not None:
                button.setEnabled(enabled)

    def require_license(self) -> bool:
        self.trial_status = check_trial()
        if self.trial_status.active:
            self.apply_license_state()
            return True
        dialog = LicenseDialog(self, tr(remaining_text(self.trial_status)))
        dialog.exec()
        self.trial_status = check_trial()
        self.apply_license_state()
        return self.trial_status.active

    # ------------------------------------------------------------- refresh
    def refresh_all(self) -> None:
        self.refresh_campaigns()
        self.refresh_tasks()
        self.refresh_history()
        self.refresh_stats()
        self.pages[2].reload()
        self.pages[4].load()
        self._update_license_label()

    def refresh_stats(self) -> None:
        self.pages[0].set_stats(self.db.stats())

    def refresh_campaigns(self) -> None:
        self._campaigns = self.db.list_campaigns()
        if not self._campaigns:
            default_id = self.db.create_campaign("默认批次")
            self._campaigns = self.db.list_campaigns()
            self.select_campaign(default_id)
            return
        self.pages[0].set_campaigns(self._campaigns, keep_selection=True)
        if self.pages[0].current_campaign_id() is None:
            self.select_campaign(int(self._campaigns[0]["id"]))

    def select_campaign(self, campaign_id: int) -> None:
        campaign = next(
            (item for item in self._campaigns if int(item["id"]) == campaign_id),
            None,
        )
        if campaign is None:
            return
        self.settings_store.setValue("last_campaign_id", int(campaign_id))
        self.pages[0].set_current_campaign(campaign)
        self.pages[0].contacts_panel.clear_selection()
        self.refresh_tasks()

    def refresh_tasks(self) -> None:
        campaign_id = self.pages[0].current_campaign_id()
        if campaign_id is None:
            return
        rows = self.db.tasks_by_campaign(campaign_id, search=self._campaign_search_text)
        self.pages[0].set_tasks(rows)
        draft_rows = [
            row
            for row in rows
            if row["status"] in {
                "generated", "filling", "drafted", "sending",
                "failed", "needs_review",
            }
        ]
        self.pages[0].set_draft_tasks(draft_rows)
        self._refresh_all_tasks()

    def _refresh_all_tasks(self, search: str = "") -> None:
        rows = [dict(row) for row in self.db.list_tasks()]
        name_by_id = {int(item["id"]): item["name"] for item in self._campaigns}
        for row in rows:
            row["campaign_name"] = name_by_id.get(int(row["campaign_id"] or 0), "")
        if search:
            keyword = search.lower()
            rows = [
                row
                for row in rows
                if keyword
                in " ".join(
                    [
                        str(row.get("name_override") or ""),
                        str(row.get("first_name") or ""),
                        str(row.get("last_name") or ""),
                        str(row.get("location_override") or ""),
                        str(row.get("location") or ""),
                        str(row.get("recipient_email") or ""),
                    ]
                ).lower()
            ]
        self.pages[1].set_tasks(rows)

    def refresh_history(self) -> None:
        page = self.pages[3]
        rows = self.db.list_tasks()
        filter_value = page.current_filter()
        if filter_value == "failed":
            rows = [row for row in rows if row["last_error"]]
        elif filter_value:
            rows = [row for row in rows if row["status"] == filter_value]
        else:
            rows = [
                row
                for row in rows
                if row["status"] in {"sent", "replied"} or row["last_error"]
            ]
        keyword = page.search_text().lower()
        if keyword:
            rows = [
                row
                for row in rows
                if keyword
                in " ".join(
                    [
                        str(row.get("recipient_email") or ""),
                        str(row.get("name_override") or ""),
                        str(row.get("first_name") or ""),
                        str(row.get("last_name") or ""),
                    ]
                ).lower()
            ]
        page.set_rows(rows)

    # -------------------------------------------------------------- search
    def search_current_campaign(self, text: str) -> None:
        self._campaign_search_text = text
        self.refresh_tasks()
        self.set_status(tr("已按条件筛选当前活动"))

    def search_all_contacts(self, text: str) -> None:
        self._refresh_all_tasks(search=text)

    # ----------------------------------------------------------- campaigns
    def create_campaign(self) -> None:
        name = TextPromptDialog.get_text(
            self,
            "新建活动/批次",
            "活动/批次名称",
            placeholder="例如：Seattle 客户",
        )
        if name is None:
            return
        campaign_id = self.db.create_campaign(name)
        self.refresh_campaigns()
        self.select_campaign(campaign_id)
        self.set_status(tr("活动/批次已创建：{name}").format(name=name))

    def rename_campaign(self) -> None:
        campaign = self.pages[0].current_campaign
        if campaign is None:
            QMessageBox.information(self, tr("未选择活动"), tr("请先在左侧选择一个活动/批次。"))
            return
        name = TextPromptDialog.get_text(
            self,
            "重命名活动/批次",
            "新的活动/批次名称",
            value=str(campaign["name"]),
        )
        if name is None or not name:
            return
        self.db.update_campaign(int(campaign["id"]), name=name)
        self.refresh_campaigns()
        self.set_status(tr("活动/批次已重命名"))

    def delete_campaign(self) -> None:
        campaign = self.pages[0].current_campaign
        if campaign is None:
            QMessageBox.information(self, tr("未选择活动"), tr("请先在左侧选择一个活动/批次。"))
            return
        if len(self._campaigns) <= 1:
            QMessageBox.warning(self, tr("无法删除"), tr("至少保留一个活动/批次。"))
            return
        task_count = int(campaign.get("task_count") or 0)
        if task_count:
            box = QMessageBox(self)
            box.setWindowTitle(tr("删除活动/批次"))
            box.setIcon(QMessageBox.Warning)
            box.setText(
                tr("“{name}”中有 {count} 条联系人。\n"
                   "请选择处理方式：").format(
                    name=campaign["name"], count=task_count
                )
            )
            move_button = box.addButton(
                tr("移动联系人后删除"), QMessageBox.AcceptRole
            )
            delete_button = box.addButton(
                tr("连同联系人永久删除"), QMessageBox.DestructiveRole
            )
            cancel_button = box.addButton(tr("取消"), QMessageBox.RejectRole)
            box.exec()
            clicked = box.clickedButton()
            if clicked is cancel_button:
                return
            if clicked is move_button:
                default = next(
                    (item for item in self._campaigns if item["name"] == "默认批次"),
                    None,
                )
                move_to = int(default["id"]) if default is not None else None
                self.db.delete_campaign(int(campaign["id"]), move_to=move_to)
            elif clicked is delete_button:
                self.db.delete_campaign(int(campaign["id"]))
        else:
            self.db.delete_campaign(int(campaign["id"]))
        self.refresh_campaigns()
        self.set_status(tr("活动/批次已删除"))

    # ------------------------------------------------------------ contacts
    def import_contacts(self) -> None:
        if not self.require_license():
            return
        campaigns = self.db.list_campaigns()
        dialog = ImportContactsDialog(self, campaigns)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        entries = dialog.entries
        contacts = [
            (entry.get("name", ""), entry.get("location", ""), entry.get("email", ""))
            for entry in entries
        ]
        custom_list = [
            {
                key: value
                for key, value in entry.items()
                if key.startswith("custom_") or key.startswith("变量")
            }
            for entry in entries
        ]
        task_ids = self.db.add_local_tasks(
            contacts,
            custom_list,
            campaign_id=dialog.selected_campaign_id,
        )
        self.refresh_all()
        self.set_status(tr("已导入 {count} 条联系人").format(count=len(task_ids)))

    def add_contact(self) -> None:
        if not self.require_license():
            return
        dialog = ContactPromptDialog(self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        name, location, email = dialog.values()
        if not email:
            QMessageBox.warning(self, tr("缺少邮箱"), tr("请填写联系人邮箱。"))
            return
        campaign_id = self.pages[0].current_campaign_id()
        self.db.add_local_task(name, location, email, campaign_id=campaign_id)
        self.refresh_all()
        self.set_status(tr("联系人已添加：{email}").format(email=email))

    def delete_selected(self) -> None:
        ids = list(dict.fromkeys(self.pages[0].selected_task_ids() + self.pages[0].draft_task_ids() + self.pages[1].selected_task_ids()))
        if not ids:
            QMessageBox.information(self, tr("未选择联系人"), tr("请先在表格中选择联系人。"))
            return
        answer = QMessageBox.question(
            self,
            tr("删除联系人"),
            tr("确定删除所选 {count} 条联系人？删除后无法恢复。").format(count=len(ids)),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self.db.delete_tasks(ids)
        self.refresh_all()
        self.set_status(tr("已删除 {count} 条联系人").format(count=len(ids)))

    def on_task_edit(self, task_id: int, field: str, value: object) -> None:
        try:
            if field == "profile_no":
                raw = str(value or "")
                profile = int(raw) if raw else 0
                self.db.update_task(task_id, profile_no=profile)
            else:
                self.db.update_task(task_id, **{field: str(value or "")})
        except ValueError as exc:
            self.show_error(tr("保存失败"), str(exc))
            return
        self.refresh_tasks()
        self.set_status(tr("联系人已更新"))

    def on_task_selection(self, task_ids: list[int]) -> None:
        inspector = self._active_inspector()
        self.pages[0].set_has_selection(bool(task_ids))
        if inspector is None:
            return
        if not task_ids:
            inspector.clear()
            return
        row = self.db.get_task(task_ids[0])
        inspector.show_task(dict(row) if row is not None else None)

    def on_task_double_clicked(self, row: dict) -> None:
        subject = str(row.get("subject") or "")
        body = str(row.get("body") or "")
        message = (
            f"{tr('状态')}: {row.get('status')}\n"
            f"{tr('邮箱')}: {row.get('recipient_email')}\n\n"
            f"{tr('主题')}: {subject}\n\n{body}"
        )
        QMessageBox.information(self, tr("联系人详情"), message)

    # ----------------------------------------------------------- operations
    def run_async(self, fn, on_done=None, on_error=None, *args, **kwargs) -> None:
        if self._closing:
            return
        cancel_event, serial = self.operations.begin()
        self.set_busy(True)
        signals = WorkerSignals()
        signals.progress.connect(self._on_progress)
        signals.task_status.connect(self._on_task_status)

        def runner(cancel_event, *run_args, **run_kwargs):
            try:
                parameters = inspect.signature(fn).parameters
                accepts_status = any(
                    parameter.name == "task_status"
                    or parameter.kind == inspect.Parameter.VAR_POSITIONAL
                    for parameter in parameters.values()
                )
            except (TypeError, ValueError):
                accepts_status = True
            if accepts_status:
                return fn(
                    cancel_event,
                    signals.progress.emit,
                    signals.task_status.emit,
                    *run_args,
                    **run_kwargs,
                )
            return fn(
                cancel_event,
                signals.progress.emit,
                *run_args,
                **run_kwargs,
            )

        def on_done_result(result):
            if self.operations.is_current(cancel_event, serial) and on_done is not None:
                on_done(result)

        def on_error_message(exc):
            if self.operations.is_current(cancel_event, serial):
                self._close_progress_dialog()
                report_path = write_error_report(
                    exc,
                    title=tr("后台操作失败"),
                    settings=self.settings,
                )
                if on_error is not None:
                    on_error(exc)
                else:
                    self.show_error(
                        tr("操作失败"),
                        f"{exc}\n\n{tr('错误报告已保存：{path}')}"
                        .format(path=report_path),
                    )

        def on_finished():
            if self.operations.is_current(cancel_event, serial):
                self.operations.finish(cancel_event, serial)
                self.set_busy(False)
                self.refresh_all()
                self.set_status(tr("操作完成"))
            self._close_progress_dialog()

        signals.done.connect(on_done_result)
        signals.error.connect(on_error_message)
        signals.finished.connect(on_finished)
        worker = FunctionWorker(
            runner,
            signals,
            cancel_event=cancel_event,
            *args,
            **kwargs,
        )
        self.thread_pool.start(worker)

    def _on_progress(self, value: int, text: str) -> None:
        dialog = self._current_progress_dialog
        if dialog is not None:
            dialog.update_progress(value, text)

    def _on_task_status(
        self, profile: str, email: str, stage: str, result: str
    ) -> None:
        dialog = self._current_progress_dialog
        if dialog is not None:
            dialog.update_task(profile, email, stage, result)

    def cancel_operation(self) -> None:
        cancel_event, serial = self.operations.begin()
        self.operations.finish(cancel_event, serial)
        self.set_busy(False)
        self.set_status(tr("当前操作已停止"))

    def _selected_ids(self) -> list[int]:
        ids = self.pages[0].selected_task_ids() or self.pages[0].draft_task_ids()
        if not ids:
            ids = self.pages[1].selected_task_ids()
        return list(dict.fromkeys(ids))

    def _cap_task_ids(self, task_ids: list[int]) -> list[int]:
        unique = list(dict.fromkeys(int(task_id) for task_id in task_ids))
        if len(unique) > MAX_CONCURRENT_TASKS:
            self.set_status(
                tr("一次最多执行 {limit} 条，已截取前 {limit} 条")
                .format(limit=MAX_CONCURRENT_TASKS)
            )
            return unique[:MAX_CONCURRENT_TASKS]
        return unique

    def _window_lock(self, profile: int) -> threading.Lock:
        with self._window_locks_guard:
            return self._window_locks.setdefault(int(profile), threading.Lock())

    def generate_selected(self) -> None:
        if not self.require_license():
            return
        ids = self._selected_ids()
        if not ids:
            QMessageBox.information(self, tr("未选择联系人"), tr("请先选择要生成的联系人。"))
            return
        ids = self._cap_task_ids(ids)
        self.generate_tasks(ids)

    def load_demo_data(self) -> None:
        existing = self.db.list_campaigns()
        if any(str(campaign["name"]).startswith("示例") for campaign in existing):
            QMessageBox.information(
                self,
                tr("示例数据"),
                tr("示例数据已存在，可直接删除。"),
            )
            return
        answer = QMessageBox.question(
            self,
            tr("载入示例数据"),
            tr("将创建 3 个“示例”活动并加入联系人、邮件预览与状态，方便查看完整工作流。是否载入？"),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if answer != QMessageBox.Yes:
            return
        demo_campaigns = [
            ("示例：Seattle 客户", "美国西海岸新联系人", [
                ("Alex Walker", "Seattle", "alex.walker@example.com"),
                ("Mia Chen", "Bellevue", "mia.chen@example.com"),
                ("Noah Williams", "Tacoma", "noah.williams@example.com"),
            ]),
            ("示例：德国经销商", "批发与渠道客户", [
                ("Ben Mueller", "Munich", "ben.mueller@example.com"),
                ("Cara Vogel", "Berlin", "cara.vogel@example.com"),
            ]),
            ("示例：八月跟进", "二次跟进未回复客户", [
                ("Lena Fischer", "Frankfurt", "lena.fischer@example.com"),
                ("Tobias Klein", "Hamburg", "tobias@example.com"),
            ]),
        ]
        for name, note, contacts in demo_campaigns:
            campaign_id = self.db.create_campaign(name, note)
            task_ids = self.db.add_local_tasks(
                contacts,
                [None] * len(contacts),
                campaign_id=campaign_id,
            )
            for task_id in task_ids[:2]:
                self.workflow.generate_local(task_id)
            if name.startswith("示例：Seattle"):
                self.db.update_task(task_ids[0], status="drafted", drafted_at=now_iso())
                self.db.update_task(task_ids[1], status="sent", sent_at=now_iso())
        self.refresh_all()
        target = next(
            (campaign for campaign in self._campaigns if campaign["name"] == "示例：Seattle 客户"),
            None,
        )
        if target is not None:
            self.select_campaign(int(target["id"]))
        self.set_status(tr("示例数据已载入，可直接删除"))

    def generate_tasks(self, task_ids: list[int]) -> None:
        if not self.require_license():
            return
        ids = [int(task_id) for task_id in task_ids]
        if not ids:
            return
        ids = list(dict.fromkeys(ids))[:MAX_GENERATE_TASKS]
        def run(cancel_event, progress, task_status, task_ids):
            def worker(ce, pr, task_id):
                row = self.db.get_task(task_id)
                email = row["recipient_email"] if row is not None else ""
                task_status("", email, tr("生成中"), "")
                return self.workflow.generate_local(task_id, cancel_event=ce)

            return self._run_tasks_parallel(
                cancel_event,
                progress,
                task_ids,
                worker,
                task_status=task_status,
            )

        def done(result):
            count = (result or {}).get("completed", 0)
            dialog = self._current_progress_dialog
            if dialog is not None:
                dialog.finish(
                    tr("生成完成：成功 {success} · 失败 {failed}")
                    .format(
                        success=count - (result or {}).get("failed", 0),
                        failed=(result or {}).get("failed", 0),
                    )
                )
            self.set_status(tr("已生成 {count} 封邮件预览").format(count=count))

        dialog = TaskProgressDialog(self, tr("生成邮件预览"))
        self._current_progress_dialog = dialog
        dialog.show()

        self.run_async(run, on_done=done, task_ids=ids)

    def _close_progress_dialog(self) -> None:
        dialog = self._current_progress_dialog
        self._current_progress_dialog = None
        if dialog is not None:
            dialog.close()
            dialog.deleteLater()

    def _run_tasks_parallel(
        self,
        cancel_event,
        progress,
        task_ids: list[int],
        worker,
        stagger: bool = False,
        task_status=None,
    ) -> dict:
        """Run task workers concurrently; returns structured batch results."""
        total = len(task_ids)
        if not total:
            return {"completed": 0, "sent": 0, "failed": 0, "needs_review": 0}
        max_workers = min(MAX_CONCURRENT_TASKS, total)
        result = {"completed": 0, "sent": 0, "failed": 0, "needs_review": 0}
        lock = threading.Lock()

        def emit_final(task_id, default_label):
            if task_status is None:
                return
            row = self.db.get_task(task_id)
            email = row["recipient_email"] if row is not None else ""
            profile = row["profile_no"] if row is not None else ""
            status = row["status"] if row is not None else ""
            label = {
                "sent": tr("发送成功"),
                "failed": tr("发送失败"),
                "needs_review": tr("需要确认"),
                "cancelled": tr("已取消"),
                "generated": tr("生成成功"),
                "drafted": tr("草稿完成"),
            }.get(status, default_label)
            task_status(str(profile), email, tr("完成"), label)

        with ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="niuma-studio",
        ) as executor:
            futures = []
            for index, task_id in enumerate(task_ids):
                delay = index * BATCH_DRAFT_INTERVAL_SECONDS if stagger else 0

                def one(ce, pr, task_id, delay):
                    if delay:
                        deadline = time.monotonic() + delay
                        while time.monotonic() < deadline:
                            check_cancel(ce)
                            time.sleep(0.2)
                    return worker(ce, pr, task_id)

                futures.append(
                    executor.submit(one, cancel_event, progress, task_id, delay)
                )
            for future in as_completed(futures):
                try:
                    future.result()
                except OperationCancelledError:
                    for pending in futures:
                        pending.cancel()
                    raise
                except Exception:
                    row = self.db.get_task(task_id)
                    status = row["status"] if row is not None else ""
                    with lock:
                        if status == "needs_review":
                            result["needs_review"] += 1
                        else:
                            result["failed"] += 1
                        result["completed"] += 1
                    progress(
                        int(result["completed"] * 100 / total),
                        tr("已完成 {completed}/{total} · 成功 {sent} · 失败 {failed}")
                        .format(
                            completed=result["completed"],
                            total=total,
                            sent=result["sent"],
                            failed=result["failed"],
                        ),
                    )
                    emit_final(task_id, tr("失败"))
                    continue
                row = self.db.get_task(task_id)
                status = row["status"] if row is not None else ""
                with lock:
                    if status == "sent":
                        result["sent"] += 1
                    elif status == "needs_review":
                        result["needs_review"] += 1
                    elif status == "failed":
                        result["failed"] += 1
                    result["completed"] += 1
                progress(
                    int(result["completed"] * 100 / total),
                    tr("已完成 {completed}/{total} · 成功 {sent} · 失败 {failed}")
                    .format(
                        completed=result["completed"],
                        total=total,
                        sent=result["sent"],
                        failed=result["failed"],
                    ),
                )
                emit_final(task_id, tr("完成"))
        return result

    def _run_window_parallel(
        self,
        cancel_event,
        progress,
        task_ids: list[int],
        worker,
        task_status=None,
    ) -> dict:
        """Different windows run concurrently; one window processes serially."""
        rows = [dict(row) for row in self.db.get_tasks(task_ids)]
        by_window, unassigned = group_tasks_by_window(rows)
        if unassigned:
            raise RuntimeError(
                tr("部分任务未绑定浏览器窗口，已取消执行：")
                + ",".join(str(task_id) for task_id in unassigned[:8])
            )
        groups = list(by_window.items())
        if not groups:
            return {"completed": 0, "sent": 0, "failed": 0, "needs_review": 0}
        max_workers = min(MAX_CONCURRENT_TASKS, len(groups))
        result = {"completed": 0, "sent": 0, "failed": 0, "needs_review": 0}
        lock = threading.Lock()
        total = len(task_ids)

        def emit_final(task_id, default_label):
            if task_status is None:
                return
            row = self.db.get_task(task_id)
            email = row["recipient_email"] if row is not None else ""
            profile = row["profile_no"] if row is not None else ""
            status = row["status"] if row is not None else ""
            label = {
                "sent": tr("发送成功"),
                "failed": tr("发送失败"),
                "needs_review": tr("需要确认"),
                "cancelled": tr("已取消"),
            }.get(status, default_label)
            task_status(str(profile), email, tr("完成"), label)

        def run_window(ce, pr, profile, group):
            with self._window_lock(profile):
                for index, task_id in enumerate(group):
                    check_cancel(ce)
                    if index:
                        deadline = time.monotonic() + BATCH_DRAFT_INTERVAL_SECONDS
                        while time.monotonic() < deadline:
                            check_cancel(ce)
                            time.sleep(0.2)
                    try:
                        worker(ce, pr, task_id)
                    except OperationCancelledError:
                        raise
                    except Exception:
                        row = self.db.get_task(task_id)
                        status = row["status"] if row is not None else ""
                        with lock:
                            if status == "needs_review":
                                result["needs_review"] += 1
                            else:
                                result["failed"] += 1
                            result["completed"] += 1
                            current = result["completed"]
                        pr(
                            int(current * 100 / total),
                            tr("已完成 {completed}/{total} · 成功 {sent} · "
                               "失败 {failed} · 需确认 {review}")
                            .format(
                                completed=current,
                                total=total,
                                sent=result["sent"],
                                failed=result["failed"],
                                review=result["needs_review"],
                            ),
                        )
                        emit_final(task_id, tr("失败"))
                        continue
                    row = self.db.get_task(task_id)
                    status = row["status"] if row is not None else ""
                    with lock:
                        if status == "sent":
                            result["sent"] += 1
                        elif status == "needs_review":
                            result["needs_review"] += 1
                        elif status == "failed":
                            result["failed"] += 1
                        result["completed"] += 1
                        current = result["completed"]
                    pr(
                        int(current * 100 / total),
                        tr("已完成 {completed}/{total} · 成功 {sent} · "
                           "失败 {failed} · 需确认 {review}")
                        .format(
                            completed=current,
                            total=total,
                            sent=result["sent"],
                            failed=result["failed"],
                            review=result["needs_review"],
                        ),
                    )
                    emit_final(task_id, tr("完成"))

        with ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="niuma-window",
        ) as executor:
            futures = [
                executor.submit(
                    run_window, cancel_event, progress, profile, group
                )
                for profile, group in groups
            ]
            try:
                for future in as_completed(futures):
                    future.result()
            except OperationCancelledError:
                for pending in futures:
                    pending.cancel()
                raise
        return result

    def open_selected_drafts(
        self, wait_send: bool = False, forced_ids: list[int] | None = None
    ) -> None:
        if not self.require_license():
            return
        ids = list(forced_ids) if forced_ids else self._selected_ids()
        if not ids:
            QMessageBox.information(self, tr("未选择联系人"), tr("请先选择要填写草稿的联系人。"))
            return
        ids = self._cap_task_ids(ids)

        matched_ids: list[int] = ids
        waiting_ids: list[int] = []
        pre_failed_count = 0
        if wait_send:
            rows = [dict(row) for row in self.db.get_tasks(ids)]
            open_windows: list[int] = []
            try:
                provider = create_browser_provider(self.settings)
                list_open = getattr(provider, "list_running_windows", None)
                raw_windows = (
                    list_open() if list_open is not None else provider.list_windows()
                )
                open_windows = [
                    int(number)
                    for number, _name in raw_windows
                    if str(number).isdigit()
                ]
            except Exception as exc:
                self.show_error(tr("读取窗口失败"), str(exc))
                return
            if not open_windows:
                QMessageBox.warning(
                    self,
                    tr("没有可用窗口"),
                    tr("未检测到已经打开并且可以连接的浏览器窗口。\n"
                       "请先打开浏览器窗口后重试。"),
                )
                return
            assignments = assign_windows(rows, open_windows)
            matched_ids = []
            waiting_ids = []
            for item in assignments:
                task_id = item["task_id"]
                profile = item["profile_no"]
                if profile is None:
                    waiting_ids.append(task_id)
                    if item["conflict"] == "window_not_open":
                        self.db.update_task(
                            task_id,
                            status="needs_review",
                            needs_manual_review=1,
                            window_assignment_type=item["type"],
                            last_error=tr("窗口 {window} 当前没有打开，请打开该浏览器窗口后重试")
                            .format(window=item.get("requested_window") or ""),
                            failure_stage="window_not_open",
                        )
                    elif item["conflict"] == "window_conflict":
                        self.db.update_task(
                            task_id,
                            status="needs_review",
                            needs_manual_review=1,
                            window_assignment_type=item["type"],
                            last_error=tr("窗口冲突：多个任务使用了同一窗口 {window}")
                            .format(window=item.get("requested_window") or ""),
                            failure_stage="window_conflict",
                        )
                    else:
                        self.db.update_task(
                            task_id,
                            status="waiting_window",
                            window_assignment_type=item["type"],
                            last_error="",
                            failure_stage="",
                        )
                else:
                    matched_ids.append(task_id)
                    self.db.update_task(
                        task_id,
                        status="assigned",
                        profile_no=profile,
                        assigned_at=now_iso(),
                        window_assignment_type=item["type"],
                        last_error="",
                        failure_stage="",
                    )
            # Re-resolve sender after window assignment and re-render content.
            pre_failed: list[int] = []
            for item in assignments:
                if item["profile_no"] is None:
                    continue
                task_id = item["task_id"]
                row = dict(self.db.get_task(task_id))
                if row.get("status") in {"sent", "replied"}:
                    continue
                candidate = dict(row)
                candidate["profile_no"] = item["profile_no"]
                profile_changed = (
                    int(row.get("profile_no") or 0) != item["profile_no"]
                )
                context_changed = (
                    str(row.get("render_context_hash") or "")
                    != self.workflow._render_context_hash(candidate)
                )
                if profile_changed or context_changed:
                    try:
                        self.workflow.generate_local(task_id)
                    except Exception as exc:
                        pre_failed.append(task_id)
                        self.db.update_task(
                            task_id,
                            status="failed",
                            failure_stage="resolve_sender",
                            last_error=str(exc),
                        )
            matched_ids = [
                task_id
                for task_id in matched_ids
                if task_id not in pre_failed
            ]
            pre_failed_count = len(pre_failed)
            if self.settings.auto_send_confirm:
                manual_count = sum(
                    1 for item in assignments if item["type"] in {"manual", "manual_locked"}
                )
                auto_count = sum(1 for item in assignments if item["type"] == "auto")
                conflict_count = sum(1 for item in assignments if item["conflict"])
                window_text = ", ".join(
                    str(item["profile_no"]) for item in assignments if item["profile_no"]
                ) or tr("无")
                answer = QMessageBox.question(
                    self,
                    tr("自动发送确认"),
                    tr("本次选择：{count} 封\n"
                       "手动窗口：{manual} · 自动匹配：{auto}\n"
                       "等待窗口：{waiting} · 无效/冲突：{conflict}\n"
                       "实际执行：{matched} 封\n"
                       "实际窗口：{windows}\n\n"
                       "发送前会重新校验 Gmail 字段，确认后开始？")
                    .format(
                        count=len(ids),
                        manual=manual_count,
                        auto=auto_count,
                        waiting=len(waiting_ids),
                        conflict=conflict_count,
                        matched=len(matched_ids),
                        windows=window_text,
                    ),
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.Yes,
                )
                if answer != QMessageBox.Yes:
                    return
            if not matched_ids:
                QMessageBox.information(
                    self,
                    tr("没有可执行任务"),
                    tr("可用浏览器窗口不足，请先打开并连接浏览器窗口。"),
                )
                self.refresh_all()
                return

        def run(cancel_event, progress, task_status, task_ids, waiting):
            workflow = self.workflow

            def worker(ce, pr, task_id):
                row = self.db.get_task(task_id)
                email = row["recipient_email"] if row is not None else ""
                profile = row["profile_no"] if row is not None else ""

                def stage(value, text):
                    task_status(str(profile), email, text, "")
                    pr(
                        -1,
                        f"{tr('当前')}：{email} · {tr('窗口')}：{profile}\n{text}",
                    )

                if waiting:
                    return workflow.open_and_send(
                        task_id,
                        progress=stage,
                        cancel_event=ce,
                    )
                return workflow.open_draft(
                    task_id,
                    progress=stage,
                    cancel_event=ce,
                )

            if waiting:
                return self._run_window_parallel(
                    cancel_event,
                    progress,
                    task_ids,
                    worker,
                    task_status=task_status,
                )
            return self._run_window_parallel(
                cancel_event,
                progress,
                task_ids,
                worker,
                task_status=task_status,
            )

        def done(result):
            result = result or {}
            result["failed"] = result.get("failed", 0) + pre_failed_count
            if wait_send:
                summary = tr("自动发送完成：成功 {sent} · 失败 {failed} · "
                             "需确认 {review} · 等待窗口 {waiting}")
                dialog = self._current_progress_dialog
                if dialog is not None:
                    dialog.finish(
                        summary.format(
                            sent=result.get("sent", 0),
                            failed=result.get("failed", 0),
                            review=result.get("needs_review", 0),
                            waiting=len(waiting_ids),
                        )
                    )
                self.set_status(
                    summary.format(
                        sent=result.get("sent", 0),
                        failed=result.get("failed", 0),
                        review=result.get("needs_review", 0),
                        waiting=len(waiting_ids),
                    )
                )
            else:
                count = result.get("completed", 0)
                dialog = self._current_progress_dialog
                if dialog is not None:
                    dialog.finish(tr("已填写 {count} 封 Gmail 草稿").format(count=count))
                self.set_status(tr("已填写 {count} 封 Gmail 草稿").format(count=count))

        dialog = TaskProgressDialog(
            self,
            tr("填写并自动发送") if wait_send else tr("填写 Gmail 草稿"),
        )
        self._current_progress_dialog = dialog
        dialog.show()
        self.run_async(run, on_done=done, task_ids=matched_ids, waiting=wait_send)

    def send_single_task(self, task_id: int) -> None:
        self.open_selected_drafts(wait_send=True, forced_ids=[int(task_id)])

    def mark_selected_sent(self) -> None:
        ids = self._selected_ids()
        if not ids:
            QMessageBox.information(self, tr("未选择联系人"), tr("请先选择已发送的邮件。"))
            return
        rows = [dict(row) for row in self.db.get_tasks(ids)]
        allowed = [
            int(row["id"])
            for row in rows
            if row["status"] in {"drafted", "sending", "failed"}
            or row.get("drafted_at") or row.get("send_clicked_at")
        ]
        if not allowed:
            QMessageBox.warning(
                self,
                tr("不能标记"),
                tr("所选任务尚未生成并填写草稿，不能标记为已发送。"),
            )
            return
        self.db.mark_sent(allowed)
        self.refresh_all()
        self.set_status(tr("已标记 {count} 封邮件为已发送").format(count=len(allowed)))

    def unmark_selected(self) -> None:
        ids = self._selected_ids()
        if not ids:
            return
        self.db.unmark_sent(ids)
        self.refresh_all()
        self.set_status(tr("已撤销 {count} 封邮件的发送标记").format(count=len(ids)))

    def confirm_tasks_unsent(self) -> None:
        ids = list(dict.fromkeys(
            self.pages[0].draft_task_ids() + self._selected_ids()
        ))
        if not ids:
            QMessageBox.information(self, tr("未选择任务"), tr("请先选择需要人工确认的任务。"))
            return
        rows = [dict(row) for row in self.db.get_tasks(ids)]
        review_ids = [
            int(row["id"]) for row in rows if row.get("status") == "needs_review"
        ]
        if not review_ids:
            QMessageBox.information(
                self,
                tr("没有可确认任务"),
                tr("所选任务中没有需要人工确认（needs_review）的任务。"),
            )
            return
        skipped = len(ids) - len(review_ids)
        emails = "\n".join(
            str(row["recipient_email"])
            for row in rows
            if row.get("status") == "needs_review"
        )
        answer = QMessageBox.question(
            self,
            tr("确认未发送"),
            tr("确认以下 {count} 个任务在 Gmail 中尚未发送？\n\n{emails}")
            .format(count=len(review_ids), emails=emails)
            + (tr("\n\n另有 {skipped} 个非人工确认任务已跳过。").format(skipped=skipped) if skipped else ""),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        for task_id in review_ids:
            self.db.update_task(
                task_id,
                status="generated",
                send_attempt_started_at="",
                send_clicked_at="",
                needs_manual_review=0,
                last_error="",
                failure_stage="",
            )
        self.refresh_all()
        self.set_status(
            tr("已确认未发送，{count} 个任务可重新执行").format(count=len(review_ids))
        )

    def confirm_tasks_sent(self) -> None:
        ids = list(dict.fromkeys(
            self.pages[0].draft_task_ids() + self._selected_ids()
        ))
        if not ids:
            QMessageBox.information(self, tr("未选择任务"), tr("请先选择需要人工确认的任务。"))
            return
        rows = [dict(row) for row in self.db.get_tasks(ids)]
        review_ids = [
            int(row["id"]) for row in rows if row.get("status") == "needs_review"
        ]
        if not review_ids:
            QMessageBox.information(
                self,
                tr("没有可确认任务"),
                tr("所选任务中没有需要人工确认（needs_review）的任务。"),
            )
            return
        skipped = len(ids) - len(review_ids)
        emails = "\n".join(
            str(row["recipient_email"])
            for row in rows
            if row.get("status") == "needs_review"
        )
        answer = QMessageBox.question(
            self,
            tr("确认已发送"),
            tr("确认以下 {count} 个任务已在 Gmail 发送成功？\n\n{emails}")
            .format(count=len(review_ids), emails=emails)
            + (tr("\n\n另有 {skipped} 个非人工确认任务已跳过。").format(skipped=skipped) if skipped else ""),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        for task_id in review_ids:
            self.db.update_task(
                task_id,
                status="sent",
                sent_at=now_iso(),
                sent_method="manual",
                needs_manual_review=0,
                last_error="",
                failure_stage="",
            )
        self.refresh_all()
        self.set_status(
            tr("已确认在 Gmail 中发送成功，共 {count} 封").format(count=len(review_ids))
        )

    def unmark_selected_history(self) -> None:
        ids = self.pages[3].selected_task_ids()
        if not ids:
            return
        self.db.unmark_sent(ids)
        self.refresh_all()
        self.set_status(tr("已撤销发送标记"))

    def delete_selected_history(self) -> None:
        ids = self.pages[3].selected_task_ids()
        if not ids:
            QMessageBox.information(self, tr("未选择记录"), tr("请先选择历史记录。"))
            return
        answer = QMessageBox.question(
            self,
            tr("删除历史记录"),
            tr("确定删除所选 {count} 条记录？").format(count=len(ids)),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self.db.delete_tasks(ids)
        self.refresh_all()

    def clear_history(self) -> None:
        rows = self.pages[3].model._rows
        ids = [int(row["id"]) for row in rows if row.get("id")]
        if not ids:
            return
        answer = QMessageBox.question(
            self,
            tr("清空历史"),
            tr("确定清空当前筛选下的 {count} 条历史记录？").format(count=len(ids)),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self.db.delete_tasks(ids)
        self.refresh_all()

    def retry_failed_drafts(self) -> None:
        if not self.require_license():
            return
        selected = self.pages[0].draft_task_ids()
        if selected:
            ids = selected
        else:
            campaign_id = self.pages[0].current_campaign_id()
            rows = (
                self.db.tasks_by_campaign(campaign_id)
                if campaign_id is not None
                else self.db.list_tasks()
            )
            ids = [
                int(row["id"])
                for row in rows
                if row["status"] == "failed"
            ]
        ids = self._cap_task_ids(ids)
        if not ids:
            QMessageBox.information(self, tr("没有失败任务"), tr("当前没有可重试的失败任务。"))
            return

        def run(cancel_event, progress, task_status, task_ids):
            workflow = self.workflow

            def worker(ce, pr, task_id):
                row = self.db.get_task(task_id)
                email = row["recipient_email"] if row is not None else ""
                profile = row["profile_no"] if row is not None else ""

                def stage(value, text):
                    task_status(str(profile), email, text, "")
                    pr(
                        -1,
                        f"{tr('当前')}：{email} · {tr('窗口')}：{profile}\n{text}",
                    )

                workflow.generate_local(task_id, cancel_event=ce)
                return workflow.open_and_send(
                    task_id,
                    progress=stage,
                    cancel_event=ce,
                )

            return self._run_window_parallel(
                cancel_event,
                progress,
                task_ids,
                worker,
                task_status=task_status,
            )

        self.run_async(run, task_ids=ids)

    # ------------------------------------------------------------- content
    def save_template(self, subject: str, body: str, sender: str, signature: str) -> None:
        self.settings.subject_template = subject
        self.settings.body_template = body
        self.settings.sender_name = sender or self.settings.sender_name
        self.settings.signature = signature
        self.settings.save()
        self.set_status(tr("模板已保存"))

    def save_template_library(self, name: str, subject: str, body: str, sender: str, signature: str) -> None:
        templates = [item for item in self.settings.saved_templates if item.get("name") != name]
        templates.append(
            {
                "name": name,
                "subject_template": subject,
                "body_template": body,
                "sender_name": sender,
                "signature": signature,
                "custom_variables": dict(self.settings.custom_variables),
            }
        )
        self.settings.saved_templates = templates
        self.settings.save()
        self.set_status(tr("模板已存入模板库：{name}").format(name=name))

    def load_template_library(self, name: str) -> None:
        template = next(
            (item for item in self.settings.saved_templates if item.get("name") == name),
            None,
        )
        if template is None:
            return
        self.settings.subject_template = str(template.get("subject_template") or "")
        self.settings.body_template = str(template.get("body_template") or "")
        self.settings.sender_name = str(template.get("sender_name") or self.settings.sender_name)
        self.settings.signature = str(template.get("signature") or "")
        self.settings.save()
        self.set_status(tr("已载入模板：{name}").format(name=name))

    def delete_template_library(self, name: str) -> None:
        self.settings.saved_templates = [
            item for item in self.settings.saved_templates if item.get("name") != name
        ]
        self.settings.save()
        self.set_status(tr("模板已删除：{name}").format(name=name))

    def preview_template(self, subject: str, body: str, sender: str, signature: str) -> None:
        try:
            rows = self.db.list_tasks()
        except Exception:
            rows = []
        name = "Alex"
        location = "Seattle"
        if rows:
            row = rows[0]
            name = str(row.get("name_override") or row.get("first_name") or "Alex")
            location = str(row.get("location_override") or row.get("location") or "Seattle")
        try:
            rendered_subject, rendered_body = render_email(
                name,
                location,
                sender or self.settings.sender_name,
                subject,
                body,
                self.settings.custom_variables,
            )
            if signature:
                rendered_body = rendered_body.rstrip() + "\n\n" + signature.strip()
        except ValueError as exc:
            QMessageBox.warning(self, tr("模板预览失败"), str(exc))
            return
        QMessageBox.information(
            self,
            tr("模板预览"),
            f"{tr('主题')}：{rendered_subject}\n\n{rendered_body}",
        )

    # -------------------------------------------------------------- windows
    def auto_fill_windows(self) -> None:
        def run(cancel_event, progress):
            progress(0, tr("正在读取浏览器窗口…"))
            provider = create_browser_provider(self.settings)
            list_open = getattr(provider, "list_running_windows", None)
            if list_open is not None:
                return list_open(verify_connection=True)
            return provider.list_windows()

        def done(windows):
            numbers: list[int] = []
            for number, _name in windows:
                try:
                    parsed = int(number)
                except (TypeError, ValueError):
                    continue
                if parsed > 0 and parsed not in numbers and len(numbers) < MAX_WINDOW_SEQUENCE:
                    numbers.append(parsed)
            if not numbers:
                self.show_error(tr("未发现窗口"), tr("浏览器窗口应用没有返回可用窗口。"))
                return
            self.settings.window_sequence = numbers
            self.settings.save()
            self.pages[0].window_panel.load()
            self.set_status(tr("已填充 {count} 个窗口编号").format(count=len(numbers)))

        def error(exc):
            self.show_error(tr("读取窗口失败"), str(exc))

        self.run_async(run, on_done=done, on_error=error)

    # ------------------------------------------------------------- settings
    def apply_settings(self) -> None:
        settings = self.settings
        set_language(settings.language)
        self.workflow = Workflow(self.db, settings)
        self.setWindowTitle(
            f"{tr('牛马邮箱')} · {tr('外贸邮件工作室')} v{__version__}"
        )
        current_campaign = self.pages[0].current_campaign_id()
        self._apply_palette()
        self._build_ui()
        self.refresh_all()
        if current_campaign is not None:
            self.select_campaign(current_campaign)
        self.apply_license_state()

    def toggle_theme(self) -> None:
        self.settings.theme_mode = "dark" if self.settings.theme_mode != "dark" else "light"
        self.settings.save()
        self.apply_settings()

    def toggle_language(self) -> None:
        self.settings.language = "en" if self.settings.language != "en" else "zh"
        self.settings.save()
        self.apply_settings()

    def backup_database(self) -> None:
        path = self.db.backup()
        if path is None:
            self.show_error(tr("备份失败"), tr("无法写入数据库备份。"))
            return
        self.set_status(tr("数据库已备份：{path}").format(path=path))

    def restore_database(self) -> None:
        backups = self.db.list_backups()
        if not backups:
            QMessageBox.information(self, tr("没有备份"), tr("当前没有可恢复的数据库备份。"))
            return
        path, _filter = QFileDialog.getOpenFileName(
            self,
            tr("选择备份文件"),
            str(app_data_dir() / "backups"),
            "Database (*.db)",
        )
        if not path:
            return
        try:
            with open(path, "rb") as handle:
                header = handle.read(16)
        except OSError as exc:
            self.show_error(tr("恢复失败"), str(exc))
            return
        if not header.startswith(b"SQLite format 3"):
            self.show_error(tr("恢复失败"), tr("备份文件不是有效的数据库。"))
            return
        self.db.backup()
        answer = QMessageBox.question(
            self,
            tr("恢复数据库"),
            tr("恢复将覆盖当前全部数据（已自动备份当前数据库）。确定继续？"),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        if self.db.restore_backup(path):
            self.refresh_all()
            self.set_status(tr("数据库已恢复"))
        else:
            self.show_error(tr("恢复失败"), tr("备份文件无法读取。"))

    def open_log_folder(self) -> None:
        from ..diagnostics import diagnostics_dir

        folder = str(diagnostics_dir())
        try:
            if sys.platform == "win32":
                os.startfile(folder)
            else:
                os.system(f'explorer "{folder}"')
        except OSError as exc:
            self.show_error(tr("打开失败"), str(exc))

    def check_update(self) -> None:
        url = self.settings.update_url.strip()
        if not url:
            QMessageBox.information(self, tr("检查更新"), tr("尚未配置更新地址，请联系管理员。"))
            return

        def run(cancel_event, progress):
            import requests

            progress(0, tr("正在检查更新…"))
            manifest_response = requests.get(url, timeout=10)
            manifest_response.raise_for_status()
            payload = manifest_response.json()
            valid, reason = verify_update_payload(payload)
            if not valid:
                raise RuntimeError(
                    tr("更新清单校验失败：{reason}").format(reason=reason)
                )
            remote_version = str(payload.get("version") or payload.get("remote_version") or "")
            download_url = str(payload.get("url") or "")
            current = str(__version__)
            if not (remote_version and is_newer_version(remote_version, current)):
                return ("current", current)
            progress(40, tr("正在下载并校验新版本…"))
            response = requests.get(download_url, timeout=120, stream=True)
            response.raise_for_status()
            digest = sha256()
            with tempfile.NamedTemporaryFile(
                delete=False, suffix=".exe"
            ) as handle:
                for chunk in response.iter_content(chunk_size=1 << 16):
                    check_cancel(cancel_event)
                    handle.write(chunk)
                    digest.update(chunk)
                downloaded_path = handle.name
            if digest.hexdigest().lower() != str(payload.get("sha256") or "").lower():
                try:
                    os.unlink(downloaded_path)
                except OSError:
                    pass
                raise RuntimeError(tr("下载文件 SHA-256 校验失败，已取消安装"))
            return ("update", remote_version, current, downloaded_path)

        def done(result):
            if result[0] == "current":
                QMessageBox.information(
                    self,
                    tr("检查更新"),
                    tr("当前已是最新版本：v{version}").format(version=result[1]),
                )
                return
            _kind, remote_version, current, downloaded_path = result
            text = tr("发现新版本：v{remote}\n当前版本：v{current}").format(
                remote=remote_version, current=current
            )
            answer = QMessageBox.question(
                self,
                tr("发现新版本"),
                text + "\n\n" + tr("签名与 SHA-256 已校验，是否启动新版本？"),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if answer == QMessageBox.Yes and sys.platform == "win32":
                os.startfile(downloaded_path)

        def error(exc):
            self.show_error(tr("检查更新失败"), str(exc))

        self.run_async(run, on_done=done, on_error=error)

    # ----------------------------------------------------------- command bar
    def open_command_palette(self) -> None:
        actions = [
            (tr("打开错误报告文件夹"), "", self.open_log_folder),
            (tr("载入示例数据"), "", self.load_demo_data),
            (tr("新建活动/批次"), tr("Ctrl+N"), self.create_campaign),
            (tr("导入联系人"), tr("Ctrl+I"), self.import_contacts),
            (tr("新增联系人"), "", self.add_contact),
            (tr("生成所选邮件预览"), tr("Ctrl+Enter"), self.generate_selected),
            (tr("填写草稿"), tr("Ctrl+D"), lambda: self.open_selected_drafts(False)),
            (tr("填写并自动发送"), "", lambda: self.open_selected_drafts(True)),
            (tr("标记所选为已发送"), "", self.mark_selected_sent),
            (tr("撤销已发送标记"), "", self.unmark_selected),
            (tr("确认未发送并可重试"), "", self.confirm_tasks_unsent),
            (tr("确认已在 Gmail 发送成功"), "", self.confirm_tasks_sent),
            (tr("重试失败草稿"), "", self.retry_failed_drafts),
            (tr("停止当前操作"), "", self.cancel_operation),
            (tr("删除所选联系人"), "", self.delete_selected),
            (tr("保存模板"), "", lambda: self.pages[0].template_editor.save()),
            (tr("从浏览器自动填充窗口"), "", self.auto_fill_windows),
            (tr("切换到活动页面"), "", lambda: self.switch_page(0)),
            (tr("打开联系人页面"), "", lambda: self.switch_page(1)),
            (tr("打开模板页面"), "", lambda: self.switch_page(2)),
            (tr("打开历史页面"), "", lambda: self.switch_page(3)),
            (tr("打开设置"), "", lambda: self.switch_page(4)),
            (tr("切换浅色/深色主题"), "", self.toggle_theme),
            (tr("切换中/英文界面"), "", self.toggle_language),
            (tr("检查更新"), "", self.check_update),
        ]
        palette = CommandPalette(self, actions)
        self._command_palette = palette
        palette.show_below_header()

    # ---------------------------------------------------------------- close
    def closeEvent(self, event) -> None:
        self._closing = True
        cancel_event, serial = self.operations.begin()
        self.thread_pool.clear()
        if not self.thread_pool.waitForDone(30_000):
            answer = QMessageBox.question(
                self,
                tr("任务仍在运行"),
                tr("仍有后台任务未结束（例如正在等待 Gmail 发送结果）。"
                   "确定立即退出？"),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                self._closing = False
                event.ignore()
                return
        self.operations.finish(cancel_event, serial)
        self.settings_store.setValue("geometry", self.saveGeometry())
        campaign_id = self.pages[0].current_campaign_id()
        if campaign_id is not None:
            self.settings_store.setValue("last_campaign_id", campaign_id)
        super().closeEvent(event)
