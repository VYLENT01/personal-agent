"""پنجره اصلی: ناوبری، صفحه‌ها، نوار وضعیت Ollama و تم تیره.

- ناوبری با QListWidget + QStackedWidget (چت / داشبورد / تنظیمات)
- نوار وضعیت، وضعیت Ollama را هر ۱۰ ثانیه در thread جدا بررسی می‌کند
- جهت چیدمان بر اساس زبان تنظیمات (RTL فارسی / LTR انگلیسی)
"""
from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.bootstrap import AppContext
from app.config import defaults as d
from app.ui.chat import ChatPage
from app.ui.dashboard import DashboardPage
from app.ui.settings import SettingsPage
from app.ui.workers import start_worker

logger = logging.getLogger(__name__)

DARK_QSS = """
QWidget { background-color: #1f242c; color: #e8ecf3; font-size: 13px; }
QMainWindow { background-color: #1f242c; }
#sidebar { background-color: #191d24; border: none; }
QListWidget#nav { background: transparent; border: none; color: #aeb8c6; outline: 0; }
QListWidget#nav::item { padding: 12px 18px; margin: 4px 10px; border-radius: 8px; }
QListWidget#nav::item:selected { background-color: #2c3f66; color: #ffffff; }
QListWidget#nav::item:hover { background-color: #232a34; }
#pageHeader { font-size: 16px; font-weight: 600; color: #ffffff; }
QFrame#card { background-color: #262c36; border-radius: 10px; }
QLabel#cardTitle { color: #8f9aab; font-size: 12px; }
QLabel#cardValue { color: #ffffff; font-size: 17px; font-weight: 600; }
QFrame#bubbleUser { background-color: #2c4a7c; border-radius: 10px; }
QFrame#bubbleAssistant { background-color: #272e39; border-radius: 10px; }
QFrame#bubbleError { background-color: #3a2226; border: 1px solid #7f2b2b; border-radius: 10px; }
QFrame#bubbleTool { background-color: #20303a; border: 1px solid #2d5661; border-radius: 10px; }
QLabel#toolName { color: #7fd4c4; font-size: 13px; font-weight: 600; }
QLabel#toolArgs { color: #8f9aab; font-size: 12px; }
QLabel#toolResult { color: #c7d6e8; font-size: 12px; }
QDialog#approvalDialog { background-color: #262c36; }
QLabel#approvalTitle { color: #ffb86b; font-size: 15px; font-weight: 600; }
QLabel#approvalArgs { color: #c7d6e8; font-family: 'Courier New', monospace; font-size: 12px; background-color: #1f242c; border-radius: 6px; padding: 8px; }
QPushButton#approveButton { background-color: #2f7d4f; color: white; border: none; border-radius: 8px; padding: 9px 18px; font-weight: 600; }
QPushButton#approveButton:hover { background-color: #37955c; }
QPushButton#denyButton { background-color: #7f2b2b; color: white; border: none; border-radius: 8px; padding: 9px 18px; font-weight: 600; }
QPushButton#denyButton:hover { background-color: #963434; }
QLabel#thinkingText { color: #8f9aab; font-style: italic; font-size: 12px; }
QLabel#bubbleMeta { color: #6d7889; font-size: 11px; }
QLabel#chatStatus { color: #8f9aab; font-size: 12px; }
QLabel#emptyHint { color: #6d7889; font-size: 13px; }
QLabel#hintLabel { color: #9aa4b2; font-size: 12px; }
QLabel#statusDot { color: #8f9aab; font-size: 12px; }
QPushButton#sendButton {
    background-color: #4f8cff; color: white; border: none;
    border-radius: 8px; padding: 9px 18px; font-weight: 600;
}
QPushButton#sendButton:hover { background-color: #5f96ff; }
QPushButton { background-color: #2a323e; color: #dfe5ee; border: none; border-radius: 8px; padding: 7px 12px; }
QPushButton:hover { background-color: #333d4b; }
QPlainTextEdit, QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {
    background-color: #232933; color: #e8ecf3; border: 1px solid #39424f;
    border-radius: 8px; padding: 7px;
}
QPlainTextEdit:focus, QLineEdit:focus { border-color: #4f8cff; }
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { background: transparent; width: 9px; margin: 2px; }
QScrollBar::handle:vertical { background: #39424f; border-radius: 4px; min-height: 30px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QStatusBar { background-color: #191d24; color: #aeb8c6; }
"""


def apply_theme(app: QApplication, settings: Any) -> None:
    """اعمال تم و جهت چیدمان بر اساس زبان تنظیمات."""
    app.setStyle("Fusion")
    app.setStyleSheet(DARK_QSS)
    if settings.language == "fa":
        app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    else:
        app.setLayoutDirection(Qt.LayoutDirection.LeftToRight)


class _StatusMonitor(QWidget):
    """پایش دوره‌ای وضعیت Ollama برای نوار وضعیت (بدون هنگ UI)."""

    status_changed = Signal(bool, str)

    def __init__(self, context: AppContext, parent: object | None = None) -> None:
        super().__init__(parent)
        self._context = context
        self._active_workers: list = []
        self._timer = QTimer(self)
        self._timer.setInterval(10_000)
        self._timer.timeout.connect(self._poll)

    def start(self) -> None:
        self._poll()
        self._timer.start()

    def _poll(self) -> None:
        start_worker(self, self._context.provider.is_available, self._emit, lambda _m: None)

    def _emit(self, result: Any) -> None:
        available, detail = result
        self.status_changed.emit(bool(available), str(detail))


class MainWindow(QMainWindow):
    """پنجره اصلی برنامه."""

    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self._context = context
        self.setWindowTitle(f"{d.APP_NAME_FA} — {d.APP_VERSION}")
        self.setMinimumSize(920, 640)
        self.resize(1020, 700)

        rtl = context.settings.language == "fa"
        if rtl:
            self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.setCentralWidget(central)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        layout.addWidget(body, 1)

        shell = QWidget()
        shell.setObjectName("sidebar")
        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)

        app_header = QLabel(d.APP_NAME_FA if rtl else d.APP_NAME)
        app_header.setObjectName("pageHeader")
        app_header.setAlignment(Qt.AlignCenter)
        app_header.setContentsMargins(0, 18, 0, 18)
        shell_layout.addWidget(app_header)

        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        for label in (
            ("چت", "داشبورد", "تنظیمات") if rtl else ("Chat", "Dashboard", "Settings")
        ):
            item = QListWidgetItem(label)
            self.nav.addItem(item)
        self.nav.setCurrentRow(0)
        self.nav.setFixedWidth(190)
        shell_layout.addWidget(self.nav, 1)
        body_layout.addWidget(shell)

        self.pages = QStackedWidget()
        self.chat_page = ChatPage(context)
        self.dashboard_page = DashboardPage(context)
        self.settings_page = SettingsPage(context)
        self.pages.addWidget(self.chat_page)
        self.pages.addWidget(self.dashboard_page)
        self.pages.addWidget(self.settings_page)
        body_layout.addWidget(self.pages, 1)

        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex)

        # نوار وضعیت
        self.status_dot = QLabel("● در حال بررسی…")
        self.status_dot.setObjectName("statusDot")
        self.statusBar().addWidget(self.status_dot)
        self.status_model = QLabel(f"مدل: {context.settings.active_model}")
        self.statusBar().addPermanentWidget(self.status_model)

        self._monitor = _StatusMonitor(context)
        self._monitor.status_changed.connect(self._on_ollama_status)
        self._monitor.start()

    def _on_ollama_status(self, available: bool, detail: str) -> None:
        if available:
            self.status_dot.setText(f"● متصل — Ollama {detail}")
        else:
            self.status_dot.setText("● قطع — Ollama در دسترس نیست")
        self.status_dot.setToolTip(detail)
        self.status_model.setText(f"مدل: {self._context.settings.active_model}")

    def closeEvent(self, event) -> None:  # noqa: N802 - نام Qt
        """لغو عملیات جاری پیش از بستن، تا thread کارگری رها شود."""
        worker = getattr(self.chat_page, "_worker", None)
        if worker is not None and worker.isRunning():
            self._context.orchestrator.request_cancel()
            if not worker.wait(2000):
                logger.warning("کارگر گفت‌وگو پس از ۲ ثانیه نیز پایان نیافت")
        super().closeEvent(event)
