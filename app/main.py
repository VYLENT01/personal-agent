"""نقطه ورود برنامه.

اجرا از ریشه پروژه:
    python -m app.main
یا روی ویندوز:
    scripts\\run.ps1
"""
from __future__ import annotations

import logging
import sys

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    try:
        from PySide6.QtWidgets import QApplication, QMessageBox

        from app.bootstrap import build_app_context
        from app.config import defaults as d
        from app.ui.window import MainWindow, apply_theme
    except ImportError as exc:  # PySide6 نصب نیست
        print(f"خطا: وابستگی‌های UI در دسترس نیست ({exc}).", file=sys.stderr)
        print("ابتدا وابستگی‌ها را نصب کنید:  pip install -r requirements.txt", file=sys.stderr)
        return 2

    app = QApplication(argv)
    app.setApplicationName(d.APP_NAME)
    app.setApplicationVersion(d.APP_VERSION)
    app.setOrganizationName(d.ORG_NAME)

    try:
        context = build_app_context()
    except Exception as exc:  # noqa: BLE001 - خطای راه‌اندازی باید به کاربر نمایش داده شود
        logger.exception("راه‌اندازی برنامه با خطا مواجه شد")
        box = QMessageBox()
        box.setIcon(QMessageBox.Icon.Critical)
        box.setWindowTitle(d.APP_NAME_FA)
        box.setText("راه‌اندازی برنامه با خطا مواجه شد.")
        box.setInformativeText(str(exc))
        box.exec()
        return 1

    apply_theme(app, context.settings)
    window = MainWindow(context)
    window.show()

    exit_code = 0
    try:
        exit_code = app.exec()
    finally:
        context.shutdown()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
