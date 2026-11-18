"""تست UI با PySide6 در حالت offscreen.

در محیط‌های بدون نمایشگر اجرا می‌شود (QT_QPA_PLATFORM=offscreen)؛
اگر کتابخانه/کتابخانه‌های سیستم موجود نباشند، کل فایل skip می‌شود.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pyside = pytest.importorskip("PySide6.QtWidgets", reason="PySide6 در دسترس نیست")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.agent.orchestrator import TurnCompleted  # noqa: E402
from app.bootstrap import build_app_context  # noqa: E402
from app.config.settings import Settings  # noqa: E402
from app.llm.model_manager import ModelManager  # noqa: E402
from app.agent.orchestrator import ChatOrchestrator  # noqa: E402
from tests.fakes import FakeLLMClient  # noqa: E402


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    return app


def _wait_until(condition, timeout: float = 5.0) -> bool:
    """پردازش حلقه رویداد تا برقراری شرط یا پایان مهلت."""
    app = QApplication.instance()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        app.processEvents()
        time.sleep(0.02)
    app.processEvents()
    return condition()


def _make_context(tmp_path: Path, script) -> object:
    provider = FakeLLMClient(script=script)
    settings = Settings(active_model="qwen3:8b")
    return build_app_context(
        root=tmp_path, settings=settings, provider=provider, configure_log=False
    )


class TestMainWindow:
    def test_window_constructs_and_pages_exist(self, qapp, tmp_path) -> None:
        from app.ui.window import MainWindow

        context = _make_context(tmp_path, ["پاسخ"])
        window = MainWindow(context)
        assert window.pages.count() == 3
        assert window.chat_page is not None
        assert window.dashboard_page is not None
        assert window.settings_page is not None
        window.close()

    def test_chat_send_shows_streamed_reply(self, qapp, tmp_path) -> None:
        from app.ui.window import MainWindow

        context = _make_context(tmp_path, ["سلام", " از ", "دستیار."])
        window = MainWindow(context)
        page = window.chat_page
        page.submit("درود")
        finished = _wait_until(
            lambda: page._worker is None and page._bubbles and page._bubbles[-1].content_label.text(),
            timeout=6.0,
        )
        assert finished, "پاسخ در مهلت مقرر نمایش داده نشد"
        bubble = page._bubbles[-1]
        assert bubble.content_label.text() == "سلام از دستیار."
        assert page.status_label.text() == "انجام شد"
        assert page.send_button.text() == "ارسال"  # بازگشت از حالت لغو
        # حباب کاربر هم هست
        assert page._bubbles[0].content_label.text() == "درود"
        window.close()

    def test_chat_error_shows_error_bubble_not_crash(self, qapp, tmp_path) -> None:
        # Test 7: قطع Ollama نباید UI را بکشد
        from app.llm.base import LLMConnectionError
        from app.ui.window import MainWindow

        context = _make_context(tmp_path, [LLMConnectionError("اتصال به Ollama برقرار نشد.")])
        window = MainWindow(context)
        page = window.chat_page
        page.submit("درود")
        finished = _wait_until(lambda: page._worker is None, timeout=6.0)
        assert finished
        assert page._bubbles[-1].objectName() == "bubbleError"
        assert "Ollama" in page._bubbles[-1].content_label.text()
        # UI همچنان پاسخ‌گوست: ارسال دوباره مجاز است
        assert page.send_button.text() == "ارسال"
        window.close()

    def test_new_conversation_clears_bubbles(self, qapp, tmp_path) -> None:
        from app.ui.window import MainWindow

        context = _make_context(tmp_path, ["پاسخ"])
        window = MainWindow(context)
        page = window.chat_page
        page.submit("درود")
        _wait_until(lambda: page._worker is None, timeout=6.0)
        page.new_conversation()
        assert page._bubbles == []
        assert not page.empty_hint.isHidden()  # ویجت والد offscreen؛ isVisible به پدر وابسته است
        window.close()
