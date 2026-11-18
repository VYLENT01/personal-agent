"""صفحه تنظیمات: مدل، Ollama، حافظه Context، UI و لاگ.

تغییرات با دکمه «ذخیره» اعمال و از طریق AppContext به همه اجزا
(بازسازی provider در صورت تغییر آدرس) تزریق می‌شود.
"""
from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.bootstrap import AppContext
from app.config.settings import Settings
from app.ui.workers import start_worker

logger = logging.getLogger(__name__)


class SettingsPage(QWidget):
    """صفحه تنظیمات برنامه."""

    def __init__(self, context: AppContext, parent: object | None = None) -> None:
        super().__init__(parent)
        self._context = context
        self._active_workers: list = []

        rtl = context.settings.language == "fa"
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(12)

        title = QLabel("تنظیمات" if rtl else "Settings")
        title.setObjectName("pageHeader")
        root.addWidget(title)

        # --- Ollama ---
        self.url_edit = _make_line_edit(context.settings.ollama_base_url)
        self.model_combo = QComboBox()
        self.model_combo.setMinimumWidth(240)
        self.model_combo.addItem(context.settings.active_model)

        refresh_models_button = QPushButton("به‌روزرسانی فهرست مدل‌ها")
        refresh_models_button.clicked.connect(self._refresh_models)
        test_button = QPushButton("تست اتصال")
        test_button.clicked.connect(self._test_connection)

        # --- زبان و ظاهر ---
        self.language_combo = QComboBox()
        self.language_combo.addItem("فارسی (RTL)", "fa")
        self.language_combo.addItem("English (LTR)", "en")
        self.language_combo.setCurrentIndex(0 if context.settings.language == "fa" else 1)

        # --- تولید متن ---
        self.temperature_spin = _make_double_spin(0.0, 2.0, 1, context.settings.generation.temperature)
        self.top_p_spin = _make_double_spin(0.0, 1.0, 2, context.settings.generation.top_p)
        self.num_ctx_spin = _make_spin(512, 65536, 512, int(context.settings.generation.num_ctx))
        self.num_predict_spin = _make_spin(64, 32768, 64, int(context.settings.generation.num_predict))

        # --- Context / حافظه مکالمه ---
        self.history_spin = _make_spin(
            2, 200, 1, int(context.settings.max_history_messages)
        )
        self.context_chars_spin = _make_spin(
            1000, 200000, 500, int(context.settings.max_context_chars)
        )

        # --- Timeout ---
        self.connect_timeout_spin = _make_double_spin(
            1.0, 30.0, 1, context.settings.connect_timeout
        )
        self.read_timeout_spin = _make_double_spin(
            10.0, 3600.0, 10, context.settings.read_timeout
        )

        # --- لاگ ---
        self.log_level_combo = QComboBox()
        for level in ("DEBUG", "INFO", "WARNING", "ERROR"):
            self.log_level_combo.addItem(level)
        self.log_level_combo.setCurrentText(context.settings.log_level)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight if rtl else Qt.AlignLeft)
        form.addRow("آدرس Ollama:", self.url_edit)
        form.addRow("مدل فعال:", self._wrap_row(self.model_combo, refresh_models_button))
        form.addRow("زبان رابط کاربری:", self.language_combo)
        form.addRow("دما (Temperature):", self.temperature_spin)
        form.addRow("Top P:", self.top_p_spin)
        form.addRow("اندازه Context (num_ctx):", self.num_ctx_spin)
        form.addRow("حداکثر توکن پاسخ (num_predict):", self.num_predict_spin)
        form.addRow("حداکثر پیام‌های تاریخچه:", self.history_spin)
        form.addRow("حداکثر کاراکترهای Context:", self.context_chars_spin)
        form.addRow("Timeout اتصال (ثانیه):", self.connect_timeout_spin)
        form.addRow("Timeout پاسخ (ثانیه):", self.read_timeout_spin)
        form.addRow("سطح لاگ:", self.log_level_combo)
        root.addLayout(form)

        self.test_status = QLabel("")
        self.test_status.setObjectName("hintLabel")
        self.test_status.setWordWrap(True)
        root.addWidget(self.test_status)

        save_button = QPushButton("ذخیره تنظیمات")
        save_button.setObjectName("sendButton")
        save_button.clicked.connect(self._on_save)
        root.addWidget(save_button)
        root.addStretch(1)

    # ------------------------------------------------------------------
    # عملیات
    # ------------------------------------------------------------------

    def _wrap_row(self, widget: QWidget, button: QPushButton) -> QWidget:
        from PySide6.QtWidgets import QHBoxLayout

        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(widget, 1)
        layout.addWidget(button)
        return row

    def _refresh_models(self) -> None:
        self.test_status.setText("در حال دریافت فهرست مدل‌ها…")

        def _fetch() -> Any:
            return self._context.model_manager.installed_names(refresh=True)

        start_worker(self, _fetch, self._on_models, self._on_models_error)

    def _on_models(self, names: Any) -> None:
        names = list(names or [])
        if not names:
            self.test_status.setText("هیچ مدلی نصب نشده است. مثال: ollama pull qwen3:8b")
            return
        self.model_combo.clear()
        self.model_combo.addItems(names)
        active = self._context.settings.active_model
        if active in names:
            self.model_combo.setCurrentText(active)
        self.test_status.setText(f"{len(names)} مدل نصب‌شده پیدا شد.")

    def _on_models_error(self, message: str) -> None:
        self.test_status.setText(f"دریافت فهرست مدل‌ها ناموفق بود.\n{message}")

    def _test_connection(self) -> None:
        self.test_status.setText("در حال بررسی اتصال…")

        def _check() -> tuple[bool, str]:
            return self._context.provider.is_available()

        start_worker(self, _check, self._on_test, self._on_models_error)

    def _on_test(self, result: Any) -> None:
        available, detail = result
        if available:
            self.test_status.setText(f"اتصال برقرار است — نسخه Ollama: {detail}")
        else:
            self.test_status.setText(f"اتصال برقرار نیست:\n{detail}")

    # ------------------------------------------------------------------
    # ذخیره
    # ------------------------------------------------------------------

    def _on_save(self) -> None:
        current = self._context.settings
        new_settings = current.copy_with(
            ollama_base_url=self.url_edit.text().strip(),
            active_model=self.model_combo.currentText().strip() or current.active_model,
            language=self.language_combo.currentData(),
            connect_timeout=self.connect_timeout_spin.value(),
            read_timeout=self.read_timeout_spin.value(),
            max_history_messages=self.history_spin.value(),
            max_context_chars=self.context_chars_spin.value(),
            log_level=self.log_level_combo.currentText(),
            generation={
                "temperature": self.temperature_spin.value(),
                "top_p": self.top_p_spin.value(),
                "num_ctx": self.num_ctx_spin.value(),
                "num_predict": self.num_predict_spin.value(),
            },
        )
        try:
            self._context.apply_settings(new_settings)
        except Exception as exc:  # noqa: BLE001 - خطای ذخیره باید نمایش داده شود
            logger.exception("ذخیره تنظیمات ناموفق بود")
            self.test_status.setText(f"ذخیره تنظیمات ناموفق بود: {exc}")
            return
        # سطح لاگ باید بی‌درنگ اعمال شود
        if new_settings.log_level != current.log_level:
            from app.bootstrap import configure_logging

            configure_logging(new_settings.log_level, self._context.log_dir)
        self.test_status.setText("تنظیمات ذخیره و اعمال شد.")


# ---------------------------------------------------------------------------
# سازنده‌های ویجت (کاهش تکرار)
# ---------------------------------------------------------------------------

def _make_line_edit(text: str) -> Any:
    from PySide6.QtWidgets import QLineEdit

    edit = QLineEdit(text)
    edit.setMinimumWidth(280)
    return edit


def _make_spin(minimum: int, maximum: int, step: int, value: int) -> QSpinBox:
    spin = QSpinBox()
    spin.setRange(minimum, maximum)
    spin.setSingleStep(step)
    spin.setValue(value)
    return spin


def _make_double_spin(minimum: float, maximum: float, decimals: int, value: float) -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setRange(minimum, maximum)
    spin.setDecimals(decimals)
    spin.setSingleStep(0.1)
    spin.setValue(value)
    return spin


def settings_from_page(page: "SettingsPage") -> Settings:  # pragma: no cover - کمکی تست
    """ساخت Settings از مقادیر فعلی صفحه (برای تست)."""
    return page._context.settings.copy_with(  # noqa: SLF001
        active_model=page.model_combo.currentText().strip()
    )
