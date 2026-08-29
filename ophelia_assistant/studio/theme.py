"""Studio visual tokens and QSS.

The studio is a restrained light-first professional surface: cool neutral
workspaces, hairline borders, one blue accent reserved for selection and the
primary action. There is no deep-navy shell, no metric-card wall and no glass.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Palette:
    name: str
    window: str
    surface: str
    raised: str
    border: str
    border_strong: str
    text: str
    muted: str
    faint: str
    accent: str
    accent_hover: str
    accent_soft: str
    selection: str
    hover: str
    ok: str
    warn: str
    danger: str
    info: str
    ok_soft: str
    warn_soft: str
    danger_soft: str
    info_soft: str
    focus: str
    rail_active: str


LIGHT = Palette(
    name="light",
    window="#F3F5F7",
    surface="#FFFFFF",
    raised="#FFFFFF",
    border="#E2E7EC",
    border_strong="#C7D0D9",
    text="#1C2430",
    muted="#5B6775",
    faint="#8B97A5",
    accent="#2563EB",
    accent_hover="#1D4ED8",
    accent_soft="#EAF1FE",
    selection="#D8E6FC",
    hover="#EEF2F6",
    ok="#16A34A",
    warn="#C7740A",
    danger="#DC2626",
    info="#0284C7",
    ok_soft="#E5F5EB",
    warn_soft="#FBF0DA",
    danger_soft="#FCEAEA",
    info_soft="#E2F2FA",
    focus="#2563EB",
    rail_active="#EAF1FE",
)

DARK = Palette(
    name="dark",
    window="#0F1419",
    surface="#171E26",
    raised="#1D2733",
    border="#2B3542",
    border_strong="#3D4A5A",
    text="#EDF2F7",
    muted="#A2AFBE",
    faint="#75849A",
    accent="#6D9CFF",
    accent_hover="#8CB2FF",
    accent_soft="#1C2F52",
    selection="#20345C",
    hover="#232F3D",
    ok="#3DD68C",
    warn="#F0B54E",
    danger="#F87171",
    info="#4BB8F0",
    ok_soft="#123527",
    warn_soft="#3A2B13",
    danger_soft="#3A1D22",
    info_soft="#123242",
    focus="#8CB2FF",
    rail_active="#20345C",
)


def palette_for(theme_mode: str) -> Palette:
    return DARK if theme_mode == "dark" else LIGHT


def build_qss(p: Palette) -> str:
    """Return the application stylesheet for one palette."""
    css = """
QWidget {
    font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
    font-size: 13px;
    color: @text;
    background: @window;
}
QMainWindow, QDialog {
    background: @window;
}
QLabel {
    background: transparent;
}
QFrame#panel, QFrame#card {
    background: @surface;
    border: 1px solid @border;
    border-radius: 4px;
}
QFrame#rail {
    background: @surface;
    border: none;
    border-right: 1px solid @border;
}
QFrame#pageHeader {
    background: @surface;
    border: none;
    border-bottom: 1px solid @border;
}
QFrame#footer {
    background: @surface;
    border: none;
    border-top: 1px solid @border;
}
QLabel#pageTitle {
    font-size: 16px;
    font-weight: 700;
    color: @text;
}
QLabel#sectionTitle {
    font-size: 13px;
    font-weight: 700;
    color: @text;
}
QLabel#subtle {
    color: @muted;
    font-size: 12px;
}
QLabel#tiny {
    color: @faint;
    font-size: 11px;
}
QLabel#countText {
    color: @muted;
    font-size: 12px;
    font-weight: 600;
}
QLabel#statusText {
    color: @muted;
    font-size: 12px;
}
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background: @raised;
    color: @text;
    border: 1px solid @border;
    border-radius: 4px;
    padding: 5px 8px;
    selection-background-color: @selection;
    selection-color: @text;
}
QLineEdit:hover, QPlainTextEdit:hover, QTextEdit:hover, QSpinBox:hover,
QDoubleSpinBox:hover, QComboBox:hover {
    border-color: @border_strong;
}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus,
QDoubleSpinBox:focus, QComboBox:focus {
    border-color: @focus;
}
QLineEdit:disabled, QPlainTextEdit:disabled, QTextEdit:disabled,
QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {
    color: @faint;
    background: @window;
}
QPushButton {
    background: @raised;
    color: @text;
    border: 1px solid @border;
    border-radius: 4px;
    padding: 6px 12px;
}
QPushButton:hover {
    background: @hover;
    border-color: @border_strong;
}
QPushButton:pressed {
    background: @selection;
}
QPushButton:focus {
    border: 1px solid @focus;
}
QPushButton:disabled {
    color: @faint;
    background: @window;
}
QPushButton[class="primary"] {
    background: @accent;
    color: #FFFFFF;
    border: 1px solid @accent;
    font-weight: 600;
}
QPushButton[class="primary"]:hover {
    background: @accent_hover;
    border-color: @accent_hover;
}
QPushButton[class="primary"]:disabled {
    background: @faint;
    border-color: @faint;
    color: #FFFFFF;
}
QPushButton[class="danger"] {
    color: @danger;
}
QPushButton[class="danger"]:hover {
    background: @danger_soft;
    border-color: @danger;
}
QToolButton {
    background: transparent;
    border: none;
    border-radius: 4px;
    padding: 5px;
}
QToolButton:hover {
    background: @hover;
}
QToolButton:pressed {
    background: @selection;
}
QToolButton:checked {
    background: @accent_soft;
}
QToolButton:focus {
    border: 1px solid @focus;
}
QToolButton#railButton {
    border-radius: 6px;
    padding: 8px;
}
QToolButton#railButton:checked {
    background: @rail_active;
}
QToolButton#iconButton {
    padding: 4px;
}
QTableView, QListView, QTreeView, QTableWidget, QListWidget {
    background: @raised;
    alternate-background-color: @surface;
    border: 1px solid @border;
    border-radius: 4px;
    gridline-color: @border;
    selection-background-color: @selection;
    selection-color: @text;
    outline: none;
}
QTableView::item, QListWidget::item, QTreeView::item, QTableWidget::item {
    padding: 2px 6px;
    border: none;
}
QTableView::item:selected, QTreeView::item:selected,
QTableWidget::item:selected, QListWidget::item:selected {
    background: @selection;
    color: @text;
}
QTableView::item:hover, QTreeView::item:hover,
QTableWidget::item:hover, QListWidget::item:hover {
    background: @hover;
}
QHeaderView::section {
    background: @hover;
    color: @muted;
    border: none;
    border-right: 1px solid @border;
    border-bottom: 1px solid @border;
    padding: 6px 8px;
    font-weight: 600;
}
QListView#campaignList::item {
    padding: 7px 10px;
    border-left: 3px solid transparent;
    border-radius: 0;
}
QListView#campaignList::item:hover {
    background: @hover;
}
QListView#campaignList::item:selected {
    background: @accent_soft;
    border-left: 3px solid @accent;
}
QTabWidget::pane {
    border: 1px solid @border;
    background: @surface;
    top: -1px;
}
QTabBar::tab {
    background: transparent;
    color: @muted;
    padding: 8px 14px;
    border: none;
    border-bottom: 2px solid transparent;
    font-weight: 600;
}
QTabBar::tab:hover {
    color: @text;
}
QTabBar::tab:selected {
    color: @text;
    border-bottom: 2px solid @accent;
}
QMenu {
    background: @raised;
    color: @text;
    border: 1px solid @border;
    border-radius: 4px;
    padding: 4px;
}
QMenu::item {
    padding: 6px 22px 6px 10px;
    border-radius: 3px;
}
QMenu::item:selected {
    background: @selection;
}
QMenu::separator {
    height: 1px;
    background: @border;
    margin: 4px 6px;
}
QStatusBar {
    background: @surface;
    color: @muted;
    border-top: 1px solid @border;
}
QStatusBar QLabel {
    color: @muted;
    padding: 2px 8px;
}
QProgressBar {
    background: @hover;
    border: 1px solid @border;
    border-radius: 3px;
    text-align: center;
    color: @text;
    min-height: 10px;
}
QProgressBar::chunk {
    background: @accent;
    border-radius: 3px;
}
QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: @border_strong;
    border-radius: 5px;
    min-height: 28px;
}
QScrollBar::handle:vertical:hover {
    background: @faint;
}
QScrollBar:horizontal {
    background: transparent;
    height: 10px;
    margin: 0;
}
QScrollBar::handle:horizontal {
    background: @border_strong;
    border-radius: 5px;
    min-width: 28px;
}
QScrollBar::add-line, QScrollBar::sub-line {
    width: 0;
    height: 0;
}
QSplitter::handle {
    background: @border;
}
QSplitter::handle:horizontal {
    width: 1px;
}
QSplitter::handle:vertical {
    height: 1px;
}
QToolTip {
    background: @raised;
    color: @text;
    border: 1px solid @border;
    border-radius: 3px;
    padding: 4px 6px;
}
QMessageBox {
    background: @surface;
}
QMessageBox QLabel {
    color: @text;
    background: transparent;
}
QComboBox QAbstractItemView {
    background: @raised;
    color: @text;
    selection-background-color: @selection;
    selection-color: @text;
    border: 1px solid @border;
}
QCheckBox {
    spacing: 6px;
    color: @text;
}
QCheckBox::indicator {
    width: 14px;
    height: 14px;
    border: 1px solid @border_strong;
    border-radius: 3px;
    background: @raised;
}
QCheckBox::indicator:checked {
    background: @accent;
    border-color: @accent;
}
QCheckBox::indicator:hover {
    border-color: @accent;
}
"""
    replacements = {
        "@window": p.window,
        "@surface": p.surface,
        "@raised": p.raised,
        "@border": p.border,
        "@border_strong": p.border_strong,
        "@text": p.text,
        "@muted": p.muted,
        "@faint": p.faint,
        "@accent": p.accent,
        "@accent_hover": p.accent_hover,
        "@accent_soft": p.accent_soft,
        "@selection": p.selection,
        "@hover": p.hover,
        "@ok": p.ok,
        "@warn": p.warn,
        "@danger": p.danger,
        "@info": p.info,
        "@ok_soft": p.ok_soft,
        "@warn_soft": p.warn_soft,
        "@danger_soft": p.danger_soft,
        "@info_soft": p.info_soft,
        "@focus": p.focus,
        "@rail_active": p.rail_active,
    }
    for token, value in replacements.items():
        css = css.replace(token, value)
    return css


STATUS_COLORS = {
    "new": "muted",
    "ready": "warn",
    "needs_review": "danger",
    "drafted": "info",
    "sent": "ok",
    "replied": "ok",
}
