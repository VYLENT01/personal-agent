"""داشبورد: وضعیت Ollama، مدل فعال، مصرف CPU/RAM، GPU و وظایف.

نمونه‌برداری‌ها در thread جدا انجام می‌شوند تا UI هنگ نکند.
GPU فقط در صورت وجود nvidia-smi نمایش داده می‌شود (بند ۱۴: «در صورت دسترسی»).
"""
from __future__ import annotations

import logging
import subprocess
from typing import Any, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QFrame, QGridLayout, QLabel, QVBoxLayout, QWidget

from app.bootstrap import AppContext
from app.ui.workers import start_worker

logger = logging.getLogger(__name__)


def sample_system() -> dict[str, Any]:
    """نمونه‌برداری CPU/RAM با psutil (در thread کارگری)."""
    import psutil  # import محلی: نباید در سیستم بدون psutil، UI را بکشد

    memory = psutil.virtual_memory()
    return {
        "cpu_percent": psutil.cpu_percent(interval=0.15),
        "cpu_cores": psutil.cpu_count(logical=True),
        "ram_percent": memory.percent,
        "ram_used_gb": round(memory.used / 1024**3, 1),
        "ram_total_gb": round(memory.total / 1024**3, 1),
    }


def sample_gpu() -> Optional[dict[str, Any]]:
    """نمونه‌برداری GPU با nvidia-smi؛ اگر در دسترس نبود None می‌دهد."""
    try:
        output = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.used",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=3,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    if not output:
        return None
    parts = [part.strip() for part in output.splitlines()[0].split(",")]
    if len(parts) < 3:
        return None
    return {"name": parts[0], "vram_total_mb": parts[1], "vram_used_mb": parts[2]}


def sample_ollama(context: AppContext) -> dict[str, Any]:
    """بررسی اتصال Ollama و شمارش مدل‌های نصب‌شده."""
    available, detail = context.provider.is_available()
    result: dict[str, Any] = {"available": available, "detail": detail, "model_count": 0}
    if available:
        try:
            result["model_count"] = len(context.model_manager.list_models(refresh=True))
        except Exception:  # noqa: BLE001 - فهرست مدل نباید داشبورد را بکشد
            result["model_count"] = 0
    return result


class _Card(QFrame):
    """کارت نمایش یک شاخص."""

    def __init__(self, title: str, parent: object | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(6)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("cardTitle")
        self.value_label = QLabel("…")
        self.value_label.setObjectName("cardValue")
        self.value_label.setWordWrap(True)
        layout.addWidget(self.title_label)
        layout.addWidget(self.value_label, 1)

    def set_value(self, text: str) -> None:
        self.value_label.setText(text)


class DashboardPage(QWidget):
    """صفحه داشبورد."""

    def __init__(self, context: AppContext, parent: object | None = None) -> None:
        super().__init__(parent)
        self._context = context
        self._active_workers: list = []

        rtl = context.settings.language == "fa"
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(12)

        title = QLabel("داشبورد" if rtl else "Dashboard")
        title.setObjectName("pageHeader")
        root.addWidget(title)

        grid = QGridLayout()
        grid.setSpacing(12)
        self.card_ollama = _Card("وضعیت Ollama" if rtl else "Ollama Status")
        self.card_model = _Card("مدل فعال" if rtl else "Active Model")
        self.card_cpu = _Card("پردازنده (CPU)" if rtl else "CPU Usage")
        self.card_ram = _Card("حافظه (RAM)" if rtl else "RAM Usage")
        self.card_gpu = _Card("کارت گرافیک (GPU)" if rtl else "GPU")
        self.card_tasks = _Card("وظایف فعال" if rtl else "Active Tasks")
        self.card_tools = _Card("وضعیت ابزارها" if rtl else "Tools Status")
        cards = [
            self.card_ollama, self.card_model, self.card_cpu,
            self.card_ram, self.card_gpu, self.card_tasks,
        ]
        for index, card in enumerate(cards):
            grid.addWidget(card, index // 3, index % 3)
        grid.addWidget(self.card_tools, 2, 0, 1, 3)
        root.addLayout(grid)
        root.addStretch(1)

        # مقدار این نسخه — فهرست واقعی ابزارهای ثبت‌شده
        self.card_tasks.set_value("۰ (Planner در فاز ۴)")
        names = context.tool_registry.tool_names()
        if names:
            self.card_tools.set_value(
                f"{len(names)} ابزار فقط-خواندنی فعال:\n"
                + "\n".join(f"• {name}" for name in names)
            )
        else:
            self.card_tools.set_value("هیچ ابزاری ثبت نشده است")

        # تازه‌سازی دوره‌ای
        self._timer = QTimer(self)
        self._timer.setInterval(3000)
        self._timer.timeout.connect(self._refresh)
        self._timer.start()
        self._refresh()

    def _refresh(self) -> None:
        start_worker(self, sample_system, self._on_system, self._on_error)
        start_worker(self, sample_gpu, self._on_gpu, self._on_error)
        start_worker(
            self, sample_ollama, self._on_ollama, self._on_error, self._context
        )

    def _on_system(self, data: dict[str, Any]) -> None:
        self.card_cpu.set_value(f"{data['cpu_percent']:.0f}% ({data['cpu_cores']} هسته)")
        self.card_ram.set_value(
            f"{data['ram_used_gb']} / {data['ram_total_gb']} GB — {data['ram_percent']:.0f}%"
        )

    def _on_gpu(self, data: Optional[dict[str, Any]]) -> None:
        if data:
            self.card_gpu.set_value(
                f"{data['name']} — VRAM {data['vram_used_mb']}/{data['vram_total_mb']} MB"
            )
        else:
            self.card_gpu.set_value("در دسترس نیست")

    def _on_ollama(self, data: dict[str, Any]) -> None:
        if data["available"]:
            self.card_ollama.set_value(f"متصل (نسخه {data['detail']}) — {data['model_count']} مدل")
        else:
            self.card_ollama.set_value("قطع — Ollama را اجرا کنید")
        self.card_model.set_value(self._context.settings.active_model)

    def _on_error(self, message: str) -> None:
        logger.warning("خطای نمونه‌برداری داشبورد: %s", message)
