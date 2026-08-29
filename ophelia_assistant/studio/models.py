"""Qt models for campaigns, tasks and history."""

from __future__ import annotations

import json
from datetime import datetime

from PySide6.QtCore import (
    QAbstractListModel,
    QAbstractTableModel,
    QModelIndex,
    Qt,
    Signal,
)

from ..i18n import tr


STATUS_TEXT = {
    "new": "新任务",
    "ready": "待确认",
    "needs_review": "需要手动修改",
    "drafted": "Gmail 草稿",
    "sent": "已发送",
    "replied": "已回复",
}


def status_text(status: str) -> str:
    return tr(STATUS_TEXT.get(str(status or ""), str(status or "")))


def _display_name(row) -> str:
    name = str(row.get("name_override") or "")
    if name:
        return name
    parts = [
        value
        for value in (row.get("first_name"), row.get("last_name"))
        if value
    ]
    return " ".join(parts)


def _location(row) -> str:
    return str(row.get("location_override") or row.get("location") or "")


class CampaignListModel(QAbstractListModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._rows: list[dict] = []

    def set_campaigns(self, rows: list[dict]) -> None:
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._rows)):
            return None
        campaign = self._rows[index.row()]
        if role == Qt.DisplayRole:
            return campaign["name"]
        if role == Qt.UserRole:
            return campaign
        if role == Qt.ToolTipRole:
            note = str(campaign.get("note") or "")
            return note or campaign["name"]
        if role == Qt.DecorationRole:
            return None
        return None

    def campaign_at(self, row: int) -> dict | None:
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return None


class TaskTableModel(QAbstractTableModel):
    """High-density editable task table."""

    data_commit = Signal(int, str, object)

    COLUMNS = (
        ("status", "状态"),
        ("name", "名字"),
        ("location", "地区"),
        ("email", "邮箱"),
        ("profile", "窗口"),
        ("subject", "主题"),
        ("updated", "最近更新"),
    )
    EDITABLE = {"name": "name_override", "location": "location_override", "profile": "profile_no"}

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._rows: list[dict] = []
        self._extra_columns: list[str] = []

    def set_columns(self, extra: list[str] | None = None) -> None:
        self.beginResetModel()
        self._extra_columns = list(extra or [])
        self.endResetModel()

    def columns(self) -> list[tuple[str, str]]:
        return list(self.COLUMNS) + [
            (key, tr(label)) for key, label in self._extra_columns
        ]

    def set_tasks(self, rows) -> None:
        self.beginResetModel()
        self._rows = [dict(row) for row in rows]
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(self.columns())

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation != Qt.Horizontal or role != Qt.DisplayRole:
            return None
        if 0 <= section < len(self.columns()):
            return self.columns()[section][1]
        return None

    def _row(self, row: int) -> dict:
        return self._rows[row]

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._rows)):
            return None
        row = self._rows[index.row()]
        key = self.columns()[index.column()][0]
        if role == Qt.DisplayRole:
            if key == "status":
                return status_text(row.get("status"))
            if key == "name":
                return _display_name(row)
            if key == "location":
                return _location(row)
            if key == "email":
                return str(row.get("recipient_email") or "")
            if key == "profile":
                profile = str(row.get("profile_no") or "")
                return profile if profile != "0" else ""
            if key == "subject":
                return str(row.get("subject") or "")
            if key == "updated":
                return self._updated_text(row)
            if key.startswith("campaign_"):
                return str(row.get("campaign_name") or "")
            return str(row.get(key) or "")
        if role == Qt.UserRole:
            return row
        if role == Qt.ForegroundRole:
            if key == "status":
                status = str(row.get("status") or "")
                if status in {"ready", "needs_review", "drafted"}:
                    from PySide6.QtGui import QColor

                    colors = {
                        "ready": "#C7740A",
                        "needs_review": "#DC2626",
                        "drafted": "#0284C7",
                    }
                    return QColor(colors[status])
            if key == "email" or key == "profile":
                from PySide6.QtGui import QColor

                return QColor("#5B6775")
        if role == Qt.TextAlignmentRole:
            if key in {"profile", "updated"}:
                return int(Qt.AlignVCenter | Qt.AlignRight)
            return int(Qt.AlignVCenter | Qt.AlignLeft)
        if role == Qt.ToolTipRole:
            if key == "subject" and row.get("subject"):
                return str(row["subject"])
            if row.get("last_error"):
                return tr("最近错误：") + str(row["last_error"])
        return None

    def _updated_text(self, row) -> str:
        raw = (
            row.get("drafted_at")
            or row.get("generated_at")
            or row.get("sent_at")
            or row.get("created_at")
            or ""
        )
        if not raw:
            return ""
        try:
            parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            return parsed.astimezone().strftime("%m-%d %H:%M")
        except ValueError:
            return str(raw)[:16]

    def flags(self, index):
        if not index.isValid():
            return Qt.NoItemFlags
        flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        key = self.columns()[index.column()][0]
        if key in self.EDITABLE:
            flags |= Qt.ItemIsEditable
        return flags

    def setData(self, index, value, role=Qt.EditRole) -> bool:
        if role != Qt.EditRole or not index.isValid():
            return False
        key = self.columns()[index.column()][0]
        if key not in self.EDITABLE:
            return False
        row = self._rows[index.row()]
        task_id = int(row.get("id") or 0)
        if not task_id:
            return False
        field = self.EDITABLE[key]
        self.data_commit.emit(task_id, field, str(value or ""))
        return True

    def task_ids(self) -> list[int]:
        return [int(row.get("id") or 0) for row in self._rows if row.get("id")]

    def task_at(self, row: int) -> dict | None:
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return None

    def find_task_id(self, task_id: int) -> int:
        for row, item in enumerate(self._rows):
            if int(item.get("id") or 0) == task_id:
                return row
        return -1
class HistoryModel(QAbstractTableModel):
    COLUMNS = (
        ("time", "时间"),
        ("email", "邮箱"),
        ("name", "名字"),
        ("status", "状态"),
        ("result", "结果"),
    )

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._rows: list[dict] = []

    def set_rows(self, rows) -> None:
        self.beginResetModel()
        self._rows = [dict(row) for row in rows]
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(self.COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation != Qt.Horizontal or role != Qt.DisplayRole:
            return None
        if 0 <= section < len(self.COLUMNS):
            return tr(self.COLUMNS[section][1])
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._rows)):
            return None
        row = self._rows[index.row()]
        key = self.COLUMNS[index.column()][0]
        if role == Qt.DisplayRole:
            if key == "time":
                stamp = row.get("sent_at") or row.get("created_at") or ""
                return str(stamp or "")[:16].replace("T", " ")
            if key == "email":
                return str(row.get("recipient_email") or "")
            if key == "name":
                return _display_name(row)
            if key == "status":
                return status_text(row.get("status"))
            if key == "result":
                if row.get("last_error"):
                    return str(row["last_error"])[:60]
                if row.get("status") in {"sent", "replied"}:
                    return tr("已确认")
                return tr("未发送")
        if role == Qt.UserRole:
            return row
        if role == Qt.ToolTipRole:
            if row.get("last_error"):
                return str(row["last_error"])
            subject = str(row.get("subject") or "")
            return subject or None
        return None

    def task_ids(self) -> list[int]:
        return [int(row.get("id") or 0) for row in self._rows if row.get("id")]

    def task_at(self, row: int) -> dict | None:
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return None


def custom_variables_of(row) -> dict[str, str]:
    try:
        raw = json.loads(str(row.get("custom_variables") or "{}"))
    except (ValueError, TypeError):
        raw = {}
    if not isinstance(raw, dict):
        return {}
    return {str(key): str(value) for key, value in raw.items() if str(value)}
