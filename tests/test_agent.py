"""تست‌های هسته ایجنت: orchestrator، وضعیت مکالمه و ساختار prompts."""
from __future__ import annotations

import time
from typing import Any

import pytest

from app.agent.orchestrator import (
    ChatOrchestrator,
    ContentDelta,
    ThinkingDelta,
    TurnCancelled,
    TurnCompleted,
    TurnFailed,
    TurnStarted,
)
from app.agent.prompts import build_messages, build_system_prompt
from app.agent.state import ConversationState
from app.config.settings import Settings
from app.llm.base import LLMConnectionError, LLMResponseError
from app.llm.model_manager import ModelManager
from tests.fakes import FakeLLMClient


def _orchestrator(script, settings: Settings | None = None) -> tuple[ChatOrchestrator, FakeLLMClient]:
    provider = FakeLLMClient(script=script)
    manager = ModelManager(provider, settings or Settings())
    return ChatOrchestrator(provider, manager, settings or Settings()), provider


def _run(orchestrator: ChatOrchestrator, text: str) -> list[Any]:
    return list(orchestrator.stream_turn(text))


class TestOrchestratorHappyPath:
    def test_event_sequence_and_history(self) -> None:
        orchestrator, provider = _orchestrator(["سلام", "! ", "در خدمتم."])
        events = _run(orchestrator, "درود")
        kinds = [type(e).__name__ for e in events]
        assert kinds[0] == "TurnStarted"
        assert "ContentDelta" in kinds
        assert kinds[-1] == "TurnCompleted"
        final = events[-1]
        assert isinstance(final, TurnCompleted)
        assert final.content == "سلام! در خدمتم."
        assert final.model == "qwen3:8b"
        assert final.duration >= 0.0
        # تاریخچه: پیام کاربر + پاسخ دستیار
        assert orchestrator.state.last_user.content == "درود"
        assert orchestrator.state.last_assistant.content == "سلام! در خدمتم."

    def test_thinking_split_events(self) -> None:
        raw_think_open = "<" + "think" + ">"
        raw_think_close = "</" + "think" + ">"
        script = [
            f"{raw_think_open}تحلیل سوال...{raw_think_close}",
            "پاسخ نهایی: ۴۲.",
        ]
        orchestrator, _ = _orchestrator(script)
        events = _run(orchestrator, "سوال")
        thinking = "".join(e.text for e in events if isinstance(e, ThinkingDelta))
        content = "".join(e.text for e in events if isinstance(e, ContentDelta))
        assert "تحلیل" in thinking
        assert content == "پاسخ نهایی: ۴۲."
        completed = events[-1]
        assert isinstance(completed, TurnCompleted)
        assert completed.thinking and "تحلیل" in completed.thinking
        assert completed.content == "پاسخ نهایی: ۴۲."

    def test_context_window_sent_to_model(self) -> None:
        orchestrator, provider = _orchestrator(["پاسخ"])
        settings = Settings(max_history_messages=4, max_context_chars=10_000)
        orchestrator.set_settings(settings)
        _run(orchestrator, "پیام۱")
        _run(orchestrator, "پیام۲")
        _run(orchestrator, "پیام۳")
        # آخری: سیستم + حداکثر ۴ پیام تاریخچه + پیام جدید
        messages = provider.received[-1]
        assert len(messages) <= 1 + 4 + 1
        assert messages[0].role == "system"
        assert messages[-1].content == "پیام۳"

    def test_new_conversation_resets_state(self) -> None:
        orchestrator, _ = _orchestrator(["پاسخ"])
        _run(orchestrator, "اول")
        first_id = orchestrator.state.conversation_id
        orchestrator.new_conversation()
        assert orchestrator.state.conversation_id != first_id
        assert orchestrator.state.messages == []


class TestOrchestratorFailure:
    def test_connection_error_yields_turn_failed(self) -> None:
        orchestrator, _ = _orchestrator([LLMConnectionError("اتصال نشد")])
        events = _run(orchestrator, "درود")
        failed = [e for e in events if isinstance(e, TurnFailed)]
        assert len(failed) == 1
        assert "اتصال نشد" in failed[0].error
        assert failed[0].kind == "LLMConnectionError"
        assert failed[0].recoverable is True

    def test_empty_response_yields_turn_failed(self) -> None:
        orchestrator, _ = _orchestrator(["", "  "])
        events = _run(orchestrator, "درود")
        assert isinstance(events[-1], TurnFailed)
        assert "خروجی خالی" in events[-1].error

    def test_empty_user_text_rejected(self) -> None:
        orchestrator, _ = _orchestrator(["پاسخ"])
        events = _run(orchestrator, "   ")
        assert isinstance(events[0], TurnFailed)
        assert events[0].kind == "ValidationError"

    def test_model_error_yields_turn_failed_not_crash(self) -> None:
        # Test 6: خروجی نامعتبر مدل نباید برنامه را بکشد
        orchestrator, _ = _orchestrator([LLMResponseError("پاسخ نامعتبر مدل")])
        events = _run(orchestrator, "درود")
        assert isinstance(events[-1], TurnFailed)
        # پیام کاربر در تاریخچه مانده تا تلاش مجدد Context داشته باشد
        assert orchestrator.state.last_user is not None


class TestOrchestratorCancellation:
    def test_cancel_mid_stream(self) -> None:
        # Test 8-بُعد: لغو باید سریع باشد و بخش ناتمام ذخیره شود
        provider = FakeLLMClient(script=["بخش۱", "بخش۲"], delay=0.15)
        manager = ModelManager(provider, Settings())
        orchestrator = ChatOrchestrator(provider, manager, Settings())

        events: list[Any] = []

        def consume() -> None:
            for event in orchestrator.stream_turn("درود"):
                events.append(event)

        import threading

        thread = threading.Thread(target=consume)
        thread.start()
        time.sleep(0.2)  # بعد از اولین قطعه
        orchestrator.request_cancel()
        thread.join(timeout=5)
        assert not thread.is_alive()
        kinds = [type(e).__name__ for e in events]
        assert "TurnCancelled" in kinds
        cancelled = events[-1]
        assert isinstance(cancelled, TurnCancelled)
        assert cancelled.partial == "بخش۱"
        assert orchestrator.state.last_assistant is not None  # ناتمام ثبت شد
        assert orchestrator.state.last_assistant.meta.get("cancelled") is True

    def test_turn_started_has_model(self) -> None:
        orchestrator, _ = _orchestrator(["پاسخ"])
        events = _run(orchestrator, "درود")
        assert isinstance(events[0], TurnStarted)
        assert events[0].model == "qwen3:8b"


class TestConversationState:
    def test_window_respects_char_budget(self) -> None:
        state = ConversationState()
        state.append_user("ا" * 5_000)
        state.append_assistant("ب" * 5_000)
        state.append_user("ج" * 5_000)
        window = state.window(max_messages=10, max_chars=11_000)
        assert len(window) == 2  # دو پیام اول سقف را پر می‌کنند
        assert window[0].content.startswith("ب")

    def test_stats(self) -> None:
        state = ConversationState()
        state.append_user("سلام")
        stats = state.stats()
        assert stats["message_count"] == 1
        assert stats["total_chars"] == 4
        assert stats["conversation_id"]


class TestPrompts:
    def test_fa_prompt_announces_phase1_no_tools(self) -> None:
        prompt = build_system_prompt("fa")
        assert "دستیار شخصی" in prompt
        assert "ابزارهای سیستم‌عامل هنوز فعال نیستند" in prompt

    def test_en_prompt(self) -> None:
        prompt = build_system_prompt("en")
        assert "Personal Agent" in prompt
        assert "not yet" in prompt

    def test_capabilities_injected_when_present(self) -> None:
        prompt = build_system_prompt("fa", capabilities=["ابزار تست: system.get_ram"])
        assert "system.get_ram" in prompt

    def test_build_messages_layout(self) -> None:
        state = ConversationState()
        state.append_user("قبلی")
        messages = build_messages("سیستم", state.window(10, 1000), "جدید")
        assert messages[0].role == "system"
        assert messages[-1].content == "جدید"
        assert [m.role for m in messages] == ["system", "user", "user"]
