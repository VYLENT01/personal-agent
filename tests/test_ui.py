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


def _make_context(tmp_path: Path, script, native_tools: bool = False) -> object:
    provider = FakeLLMClient(script=script, native_tools=native_tools)
    # فاز ۳: tmp_path به‌عنوان مسیر مجاز ابزارهای filesystem/development
    settings = Settings(active_model="qwen3:8b", allowed_paths=[str(tmp_path)])
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

        context = _make_context(tmp_path, [["سلام", " از ", "دستیار."]])
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

    def test_tool_bubble_shown_for_native_tool_call(self, qapp, tmp_path) -> None:
        # بخش ششم: نام ابزار + پارامترها + نتیجه در حباب جدا
        from app.ui.window import MainWindow
        from app.ui.chat import ToolBubble

        script = [
            {"tool_calls": [{"name": "system.get_memory_usage", "args": {}}]},
            "رم سالم است.",
        ]
        context = _make_context(tmp_path, script, native_tools=True)
        window = MainWindow(context)
        page = window.chat_page
        page.submit("وضعیت رم را بگو")

        finished = _wait_until(lambda: page._worker is None, timeout=8.0)
        assert finished, "چرخه ابزار در مهلت مقرر تمام نشد"

        tool_bubbles = [b for b in page._bubbles if isinstance(b, ToolBubble)]
        assert len(tool_bubbles) == 1
        bubble = tool_bubbles[0]
        assert "system.get_memory_usage" in bubble.tool_name_label.text()
        assert "پارامترها" in bubble.args_label.text()
        # نتیجه موفق psutil (RAM) در حباب آمده
        assert "نتیجه" in bubble.result_label.text()
        assert not bubble.result_label.text().startswith("⛔")
        # مراحل اجرا در نوار وضعیت دیده شد و بازگشت به حالت عادی
        assert page.status_label.text() == "انجام شد"
        assert page.send_button.text() == "ارسال"
        # پاسخ نهایی هم حباب دارد
        assert any(
            b.objectName() == "bubbleAssistant" for b in page._bubbles
        )
        window.close()

    def test_tool_error_shown_in_bubble(self, qapp, tmp_path) -> None:
        # ابزار ناموجود → حباب ابزار با پیام خطا؛ بدون کرش UI
        from app.ui.window import MainWindow
        from app.ui.chat import ToolBubble

        script = [
            {"tool_calls": [{"name": "system.nonsense", "args": {}}]},
            "چنین ابزاری ندارم؛ پاسخ متنی.",
        ]
        context = _make_context(tmp_path, script, native_tools=True)
        window = MainWindow(context)
        page = window.chat_page
        page.submit("کاری بکن")
        finished = _wait_until(lambda: page._worker is None, timeout=8.0)
        assert finished
        tool_bubbles = [b for b in page._bubbles if isinstance(b, ToolBubble)]
        assert tool_bubbles, "حباب ابزار خطا نمایش داده نشد"
        assert "⛔" in tool_bubbles[0].result_label.text()
        assert "ثبت نشده" in tool_bubbles[0].result_label.text()
        # UI همچنان پاسخ‌گوست
        assert page.send_button.text() == "ارسال"
        window.close()


# ===========================================================================
# فاز ۳ — UI تأیید عملیات L2 و حباب وضعیت (بخش ۹)
# ===========================================================================

class TestApprovalUI:
    def test_l2_shows_approval_dialog_and_denies(self, qapp, tmp_path, monkeypatch) -> None:
        # دیالوگ به‌جای تعامل واقعی، خودکار «رد» می‌شود (تست offscreen)
        from PySide6.QtWidgets import QDialog

        from app.ui.chat import ToolBubble
        from app.ui.window import MainWindow

        monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
        script = [
            {
                "tool_calls": [
                    {"name": "filesystem.write_file",
                     "args": {"path": str(tmp_path / "x.txt"), "content": "x"}}
                ]
            },
            "عملیات رد شد.",
        ]
        # مسیر مجاز: tmp_path خودِ تست
        context = _make_context(tmp_path, script, native_tools=True)
        window = MainWindow(context)
        page = window.chat_page
        page.submit("فایل بساز")
        finished = _wait_until(lambda: page._worker is None, timeout=8.0)
        assert finished
        tool_bubbles = [b for b in page._bubbles if isinstance(b, ToolBubble)]
        assert tool_bubbles, "حباب ابزار نمایش داده نشد"
        bubble = tool_bubbles[0]
        # وضعیت رد در حباب
        assert "⛔" in bubble.result_label.text() or "رد" in bubble.result_label.text()
        # فایل ساخته نشد
        assert not (tmp_path / "x.txt").exists()
        # گفت‌وگو همچنان پاسخ‌گو
        assert page.send_button.text() == "ارسال"
        window.close()

    def test_l2_approval_dialog_approves_and_executes(self, qapp, tmp_path, monkeypatch) -> None:
        from PySide6.QtWidgets import QDialog

        from app.ui.chat import ToolBubble
        from app.ui.window import MainWindow

        monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
        target = tmp_path / "approved.txt"
        script = [
            {
                "tool_calls": [
                    {"name": "filesystem.write_file",
                     "args": {"path": str(target), "content": "تأیید UI"}}
                ]
            },
            "فایل ساخته شد.",
        ]
        context = _make_context(tmp_path, script, native_tools=True)
        window = MainWindow(context)
        page = window.chat_page
        page.submit("بنویس")
        finished = _wait_until(lambda: page._worker is None, timeout=8.0)
        assert finished
        # فایل واقعاً با تأیید UI ساخته شد (توکن از PermissionManager)
        assert target.exists()
        assert target.read_text(encoding="utf-8") == "تأیید UI"
        tool_bubbles = [b for b in page._bubbles if isinstance(b, ToolBubble)]
        assert "✅" in tool_bubbles[0].result_label.text()
        window.close()

    def test_approval_status_texts_and_steps(self, qapp, tmp_path, monkeypatch) -> None:
        from PySide6.QtWidgets import QDialog

        from app.ui.window import MainWindow

        monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
        script = [
            {"tool_calls": [{"name": "filesystem.create_directory",
                            "args": {"path": str(tmp_path / "d1")}}]},
            "پاسخ",
        ]
        context = _make_context(tmp_path, script, native_tools=True)
        window = MainWindow(context)
        page = window.chat_page
        page.submit("پوشه بساز")
        finished = _wait_until(lambda: page._worker is None, timeout=8.0)
        assert finished
        # نوار وضعیت در پایان به حالت عادی برگشت و مراحل اجرا ثبت شد
        assert page.status_label.text() in ("انجام شد", "خطا")
        assert page._agent_steps >= 2  # درخواست + نتیجه
        window.close()
