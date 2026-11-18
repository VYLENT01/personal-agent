"""کارگرهای پس‌زمینه برای UI.

هر عملیات طولانی (پاسخ مدل، نمونه‌برداری سیستم، بررسی اتصال) در
QThread جدا اجرا می‌شود تا رابط کاربری هرگز هنگ نکند (بند ۱۴ و ۱۰ مشخصات).
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from PySide6.QtCore import QThread, Signal

logger = logging.getLogger(__name__)


class FnWorker(QThread):
    """اجرای یک تابع پایتونی در thread جدا با رویدادهای ok/fail."""

    ok = Signal(object)
    fail = Signal(str)

    def __init__(self, fn: Callable[..., Any], *args: Any, parent: object = None) -> None:
        super().__init__(parent)
        self._fn = fn
        self._args = args

    def run(self) -> None:
        try:
            result = self._fn(*self._args)
        except Exception as exc:  # noqa: BLE001 - خطا باید به UI برسد نه کرش thread
            logger.exception("کارگر پس‌زمینه با خطا مواجه شد")
            self.fail.emit(f"{type(exc).__name__}: {exc}")
            return
        self.ok.emit(result)


def start_worker(
    owner: Any,
    fn: Callable[..., Any],
    on_ok: Callable[..., None],
    on_fail: Callable[[str], None],
    *args: Any,
) -> FnWorker:
    """راه‌اندازی کارگر + مدیریت مرجع و پاک‌سازی خودکار پس از اتمام.

    owner باید صاحب کارگر باشد (برای جلوگیری از GC حین اجرا).
    """
    worker = FnWorker(fn, *args)
    worker.ok.connect(on_ok)
    worker.fail.connect(on_fail)
    workers: list = getattr(owner, "_active_workers", None) or []
    if not hasattr(owner, "_active_workers"):
        owner._active_workers = workers  # noqa: SLF001 - الگوی نگهداری مرجع کارگرها
    workers.append(worker)

    def _cleanup() -> None:
        try:
            workers.remove(worker)
        except ValueError:
            pass
        worker.deleteLater()

    worker.finished.connect(_cleanup)
    worker.start()
    return worker
