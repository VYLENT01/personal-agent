"""تست‌های هسته ایجنت: orchestrator، وضعیت مکالمه و ساختار prompts.

فاز ۲: چرخه کامل Tool Calling (native + fallback)، ارسال نتیجه ابزار به
مدل، سقف‌ها، لغو حین ابزار، و مدیریت خطای ابزار — همه با FakeLLMClient.
"""
from __future__ import annotations

import time
from typing import Any

import pytest

from app.agent.orchestrator import (
    ChatOrchestrator,
    ContentDelta,
    ThinkingDelta,
    ToolCallFinished,
    ToolCallRequested,
    ToolCallStarted,
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
from app.tools.registry import ToolRegistry, build_default_registry
from tests.fakes import FakeLLMClient


def _orchestrator(
    script,
    settings: Settings | None = None,
    *,
    registry: ToolRegistry | None = None,
    native_tools: bool = False,
) -> tuple[ChatOrchestrator, FakeLLMClient]:
    provider = FakeLLMClient(script=script, native_tools=native_tools)
    settings = settings or Settings()
    manager = ModelManager(provider, settings)
    registry = registry or build_default_registry()
    return (
        ChatOrchestrator(provider, manager, settings, registry),
        provider,
    )


def _run(orchestrator: ChatOrchestrator, text: str) -> list[Any]:
    return list(orchestrator.stream_turn(text))


class TestOrchestratorHappyPath:
    def test_event_sequence_and_history(self) -> None:
        # یک مرحله = لیستی از قطعات (رفتار استریم واقعی در یک پاسخ)
        orchestrator, provider = _orchestrator([["سلام", "! ", "در خدمتم."]])
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
            [f"{raw_think_open}تحلیل سوال...{raw_think_close}", "پاسخ نهایی: ۴۲."],
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
        orchestrator, _ = _orchestrator([["", "  "]])
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
        # لغو باید سریع باشد و بخش ناتمام ذخیره شود.
        # فاز ۲: فیک مرحله‌ای است؛ یک مرحله با دو قطعهٔ تأخیری می‌سازیم
        provider = FakeLLMClient(script=[["بخش۱", "بخش۲", "بخش۳"]], delay=0.15)
        manager = ModelManager(provider, Settings())
        registry = build_default_registry()
        orchestrator = ChatOrchestrator(provider, manager, Settings(), registry)

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
    def test_fa_prompt_announces_no_tools(self) -> None:
        prompt = build_system_prompt("fa")
        assert "دستیار شخصی" in prompt
        assert "هیچ ابزاری فعال نیست" in prompt

    def test_en_prompt(self) -> None:
        prompt = build_system_prompt("en")
        assert "Personal Agent" in prompt
        assert "no tools are active" in prompt

    def test_fa_prompt_native_mode(self) -> None:
        prompt = build_system_prompt(
            "fa", capabilities=["- system.get_memory_usage(): ..."], tool_mode="native"
        )
        assert "system.get_memory_usage" in prompt
        assert "استفاده از ابزارها" in prompt

    def test_fa_prompt_fallback_mode_has_protocol(self) -> None:
        prompt = build_system_prompt(
            "fa", capabilities=["- system.get_disk_usage(): ..."], tool_mode="fallback"
        )
        assert "پروتکل فراخوانی ابزار" in prompt
        assert '"tool"' in prompt
        assert '"args"' in prompt

    def test_en_prompt_fallback_mode_has_protocol(self) -> None:
        prompt = build_system_prompt(
            "en", capabilities=["- system.get_memory_usage(): ..."], tool_mode="fallback"
        )
        assert "Tool-call protocol" in prompt
        assert "[TOOL RESULT]" in prompt

    def test_capabilities_injected_when_present(self) -> None:
        prompt = build_system_prompt(
            "fa", capabilities=["ابزار تست: system.get_ram"], tool_mode="native"
        )
        assert "system.get_ram" in prompt

    def test_tool_result_user_message_format(self) -> None:
        from app.agent.prompts import tool_result_user_message

        message = tool_result_user_message("system.get_ram", "ok", "fa")
        assert message.startswith("[نتیجه ابزار]")
        assert "system.get_ram" in message
        message_en = tool_result_user_message("system.get_ram", "ok", "en")
        assert message_en.startswith("[TOOL RESULT]")

    def test_build_messages_layout(self) -> None:
        state = ConversationState()
        state.append_user("قبلی")
        messages = build_messages("سیستم", state.window(10, 1000), "جدید")
        assert messages[0].role == "system"
        assert messages[-1].content == "جدید"
        assert [m.role for m in messages] == ["system", "user", "user"]


# ===========================================================================
# فاز ۲ — چرخه کامل Tool Calling (بخش هفتم: تست‌های الزامی)
# ===========================================================================

from app.llm.base import ChatMessage, ROLE_TOOL  # noqa: E402


class TestNativeToolCallingCycle:
    """چرخه کامل native: مدل با capabilities=tools → tools فرستاده می‌شود؛
    tool_calls در قطعه پایانی می‌آید؛ نتیجه به‌عنوان پیام role=tool برمی‌گردد."""

    def test_full_cycle_native(self) -> None:
        # اسکریپت دو-مرحله‌ای: اول ابزار می‌خواهد، بعد پاسخ نهایی می‌دهد
        script = [
            {"tool_calls": [{"name": "system.get_memory_usage", "args": {}}]},
            "RAM سیستم: متصل و سالم است.",
        ]
        orchestrator, provider = _orchestrator(script, native_tools=True)
        events = _run(orchestrator, "وضعیت رم را بگو")

        kinds = [type(e).__name__ for e in events]
        # توالی کامل: شروع → درخواست ابزار → شروع اجرا → پایان اجرا → دلتا → پایان
        assert kinds[0] == "TurnStarted"
        assert "ToolCallRequested" in kinds and "ToolCallStarted" in kinds
        assert "ToolCallFinished" in kinds
        assert kinds[-1] == "TurnCompleted"

        # ۱) tools واقعاً به مدل فرستاده شد (مسیر native)
        assert provider.received_tools[0] is not None
        sent_names = [t["function"]["name"] for t in provider.received_tools[0]]
        assert "system.get_memory_usage" in sent_names

        # ۲) نتیجه ابزار در فراخوانی دوم به‌عنوان پیام role=tool فرستاده شد
        second_messages = provider.received[1]
        tool_messages = [m for m in second_messages if m.role == ROLE_TOOL]
        assert tool_messages, "پیام نتیجه ابزار به مدل فرستاده نشد"
        assert "system.get_memory_usage" in tool_messages[0].content
        assert tool_messages[0].meta.get("tool_name") == "system.get_memory_usage"

        # ۳) اجرای واقعی ابزار (psutil) — نتیجه ساختاریافته
        finished = [e for e in events if isinstance(e, ToolCallFinished)][0]
        assert finished.result.success is True
        assert finished.result.data["ram"]["total_gb"] > 0

        # ۴) پاسخ نهایی از داده واقعی
        completed = events[-1]
        assert isinstance(completed, TurnCompleted)
        assert completed.tool_calls == 1
        assert completed.iterations == 2
        assert "RAM" in completed.content

        # ۵) وضعیت مکالمه حفظ شد: user + assistant(با calls) + tool + assistant
        roles = [m.role for m in orchestrator.state.messages]
        assert roles == ["user", "assistant", "tool", "assistant"]

    def test_turn_started_reports_native_mode(self) -> None:
        script = [{"tool_calls": [{"name": "system.get_system_info", "args": {}}]}, "پاسخ"]
        orchestrator, _ = _orchestrator(script, native_tools=True)
        events = _run(orchestrator, "سیستم")
        assert events[0].tool_mode == "native"

    def test_model_without_capability_gets_no_tools_param(self) -> None:
        # مدل بدون قابلیت بومی → ابزارها در پارامتر tools فرستاده نمی‌شود
        orchestrator, provider = _orchestrator(["پاسخ عادی"], native_tools=False)
        events = _run(orchestrator, "درود")
        assert events[0].tool_mode == "fallback"
        assert provider.received_tools[0] is None
        assert isinstance(events[-1], TurnCompleted)

    def test_unknown_tool_call_rejected_but_cycle_continues(self) -> None:
        # مدل ابزار ساختگی خواست → رد ساختاریافته → مدل پاسخ نهایی می‌دهد
        script = [
            {"tool_calls": [{"name": "system.nonsense", "args": {}}]},
            "چنین ابزاری نداشتم؛ پاسخ بدون آن.",
        ]
        orchestrator, _ = _orchestrator(script, native_tools=True)
        events = _run(orchestrator, "چیزی بگو")
        finished = [e for e in events if isinstance(e, ToolCallFinished)][0]
        assert finished.result.success is False
        assert "ثبت نشده" in finished.result.error
        # نتیجه خطای ابزار هم به مدل برگشت (پیام role=tool)
        assert isinstance(events[-1], TurnCompleted)

    def test_invalid_args_structured_failure(self) -> None:
        # پارامتر نامعتبر → Pydantic رد می‌کند؛ بدون کرش
        script = [
            {"tool_calls": [{"name": "system.get_disk_usage", "args": {"path": 12345}}]},
            "پارامتر اشتباه بود؛ با مسیر درست.",
        ]
        orchestrator, _ = _orchestrator(script, native_tools=True)
        events = _run(orchestrator, "دیسک")
        finished = [e for e in events if isinstance(e, ToolCallFinished)][0]
        assert finished.result.success is False
        assert "ورودی نامعتبر" in finished.result.error
        assert isinstance(events[-1], TurnCompleted)

    def test_multiple_tool_calls_sequential(self) -> None:
        # دو فراخوانی در یک پاسخ: هر دو اجرا و هر دو نتیجه به مدل
        script = [
            {
                "tool_calls": [
                    {"name": "system.get_system_info", "args": {}},
                    {"name": "system.get_memory_usage", "args": {}},
                ]
            },
            "هر دو را گرفتم.",
        ]
        orchestrator, provider = _orchestrator(script, native_tools=True)
        events = _run(orchestrator, "سیستم و رم")
        finished = [e for e in events if isinstance(e, ToolCallFinished)]
        assert len(finished) == 2
        assert all(f.result.success for f in finished)
        tool_messages = [m for m in provider.received[1] if m.role == ROLE_TOOL]
        assert len(tool_messages) == 2
        completed = events[-1]
        assert isinstance(completed, TurnCompleted)
        assert completed.tool_calls == 2


class TestFallbackToolCallingCycle:
    """مدل بدون قابلیت بومی: پروتکل JSON در متن → استخراج → اجرا → نتیجه به‌عنوان پیام کاربر."""

    def test_full_cycle_fallback(self) -> None:
        # پاسخ اول: بلوک JSON (پروتکل)؛ پاسخ دوم: نهایی
        request_json = '{"tool": "system.get_memory_usage", "args": {}}'
        script = ["```json\n" + request_json + "\n```", "رم: سالم."]
        orchestrator, provider = _orchestrator(script, native_tools=False)
        events = _run(orchestrator, "وضعیت رم")

        kinds = [type(e).__name__ for e in events]
        assert "ToolCallRequested" in kinds and "ToolCallFinished" in kinds
        assert kinds[-1] == "TurnCompleted"

        # نتیجه به‌عنوان پیام کاربر با پیشوند برگشت
        second_messages = provider.received[1]
        last_user = [m for m in second_messages if m.role == "user"][-1]
        assert last_user.content.startswith("[نتیجه ابزار]")
        assert "system.get_memory_usage" in last_user.content

        # پروتکل JSON در پیام سیستم تکرار اول موجود بود
        first_messages = provider.received[0]
        assert '"tool"' in first_messages[0].content  # پیام سیستم

        finished = [e for e in events if isinstance(e, ToolCallFinished)][0]
        assert finished.result.success is True
        assert finished.result.data["ram"]["total_gb"] > 0

        # در fallback هم درخواست ابزار و نتیجه در state ثبت می‌شوند:
        # user → assistant(درخواست) → tool(نتیجه) → assistant(نهایی)
        roles = [m.role for m in orchestrator.state.messages]
        assert roles == ["user", "assistant", "tool", "assistant"]

    def test_fallback_plain_text_no_tool(self) -> None:
        # متن عادی بدون JSON → بدون فراخوانی، پاسخ مستقیم
        orchestrator, _ = _orchestrator(["سلام، در خدمتم."], native_tools=False)
        events = _run(orchestrator, "درود")
        kinds = [type(e).__name__ for e in events]
        assert "ToolCallRequested" not in kinds
        assert kinds[-1] == "TurnCompleted"

    def test_fallback_json_without_tool_key_ignored(self) -> None:
        # JSON که ابزار نیست (مثلاً {"answer": ...}) → متن عادی تلقی می‌شود
        script = ['پاسخ کمیسی: ```json\n{"answer": 42}\n``` تمام.']
        orchestrator, _ = _orchestrator(script, native_tools=False)
        events = _run(orchestrator, "حساب کن")
        kinds = [type(e).__name__ for e in events]
        assert "ToolCallRequested" not in kinds
        assert kinds[-1] == "TurnCompleted"


class TestToolLimitsAndLoopProtection:
    """سقف‌ها: جلوگیری از حلقه بی‌نهایت و سقف فراخوانی در هر نوبت."""

    def test_iteration_limit_stops_infinite_tool_loop(self) -> None:
        # مدل در هر تکرار دوباره ابزار می‌خواهد و هیچ‌وقت پاسخ نهایی نمی‌دهد
        frame = {"tool_calls": [{"name": "system.get_system_info", "args": {}}]}
        script: list[Any] = [frame] * 8  # از سقف iteration (۸) فراتر نمی‌گذرد
        orchestrator, _ = _orchestrator(script, native_tools=True)
        events = _run(orchestrator, "حلقه بی‌نهایت")
        finished = [e for e in events if isinstance(e, ToolCallFinished)]
        # سقف فراخوانی ابزار (۵) زودتر از سقف iteration می‌نشیند
        assert len(finished) == 5
        final = events[-1]
        assert isinstance(final, (TurnCompleted, TurnFailed))
        # هیچ حلقه بی‌نهایت رخ نداد: مجموع رویدادها متناسب با سقف است
        requested = [e for e in events if isinstance(e, ToolCallRequested)]
        assert len(requested) == 5

    def test_max_tool_calls_truncated_batch(self) -> None:
        # مدل ۴ فراخوانی یکجا می‌خواهد (زیر سقف) و در تکرار بعد دوباره؛ جمع از سقف رد می‌شود
        batch = {"tool_calls": [{"name": "system.get_system_info", "args": {}} for _ in range(4)]}
        script = [batch, batch, "پایان"]
        orchestrator, _ = _orchestrator(script, native_tools=True)
        events = _run(orchestrator, "چندتایی")
        finished = [e for e in events if isinstance(e, ToolCallFinished)]
        assert len(finished) <= 5  # سقف هر نوبت
        final = events[-1]
        assert isinstance(final, (TurnCompleted, TurnFailed))


class TestToolErrorAndCancellation:
    """خطای ابزار و لغو حین فراخوانی ابزار."""

    def test_tool_failure_reported_to_model_and_final_answer(self) -> None:
        # دیسک با مسیر ناموجود → خطا ساختاریافته → مدل پاسخ نهایی می‌دهد
        script = [
            {"tool_calls": [{"name": "system.get_disk_usage", "args": {"path": "/no/such/dir/x"}}]},
            "مسیر دیسک در دسترس نبود.",
        ]
        orchestrator, _ = _orchestrator(script, native_tools=True)
        events = _run(orchestrator, "دیسک ناموجود")
        finished = [e for e in events if isinstance(e, ToolCallFinished)][0]
        assert finished.result.success is False
        # پیام خطا هم role=tool به مدل رفت
        assert isinstance(events[-1], TurnCompleted)

    def test_cancel_during_tool_execution(self) -> None:
        # ابزار آهسته + لغو از thread دیگر → نوبت با TurnCancelled تمام می‌شود
        import threading

        class _HangingInput:
            pass

        class _HangingTool:
            from app.tools.base import PermissionLevel, Tool, ToolInput

        from app.tools.base import Tool, ToolInput, PermissionLevel

        class HangingTool(Tool):
            input_model = ToolInput
            permission_level = PermissionLevel.L1_READ_ONLY
            category = "test"

            @property
            def name(self) -> str:
                return "test.hang"

            @property
            def description(self) -> str:
                return "آویزان."

            def _run(self, args: ToolInput) -> dict:
                time.sleep(3.0)
                return {}

        registry = build_default_registry()
        registry.register_tool(HangingTool())
        script = [
            {"tool_calls": [{"name": "test.hang", "args": {}}]},
            "پاسخ",
        ]
        provider = FakeLLMClient(script=script, native_tools=True, delay=0.05)
        settings = Settings()
        manager = ModelManager(provider, settings)
        orchestrator = ChatOrchestrator(provider, manager, settings, registry)

        events: list[Any] = []
        started = time.monotonic()

        def consume() -> None:
            for event in orchestrator.stream_turn("کار آهسته"):
                events.append(event)

        thread = threading.Thread(target=consume)
        thread.start()
        # صبر تا شروع اجرای ابزار سپس لغو
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if any(isinstance(e, ToolCallStarted) for e in events):
                break
            time.sleep(0.02)
        orchestrator.request_cancel()
        thread.join(timeout=6)
        elapsed = time.monotonic() - started
        assert not thread.is_alive(), "thread پس از لغو آزاد نشد"
        assert elapsed < 2.5, f"لغو بیش از حد طول کشید ({elapsed:.1f}s)"
        kinds = [type(e).__name__ for e in events]
        assert "TurnCancelled" in kinds, kinds


class TestConversationStateTools:
    """حفظ وضعیت مکالمه با پیام‌های ابزار."""

    def test_append_tool_messages(self) -> None:
        from app.llm.base import ToolCall

        state = ConversationState()
        state.append_user("سوال")
        state.append_assistant_with_calls("", [ToolCall(name="a.b")])
        state.append_tool_result("a.b", "ok")
        roles = [m.role for m in state.messages]
        assert roles == ["user", "assistant", "tool"]
        # پیام tool فریم wire درست دارد
        frame = state.messages[2].to_api()
        assert frame["role"] == "tool"
        assert frame.get("tool_name") == "a.b"

    def test_tool_messages_in_context_window(self) -> None:
        from app.llm.base import ToolCall

        state = ConversationState()
        state.append_user("س")
        state.append_assistant_with_calls("", [ToolCall(name="a.b")], thinking="t")
        state.append_tool_result("a.b", "نتیجه اینجا")
        window = state.window(10, 10_000)
        assert any(m.role == ROLE_TOOL for m in window)
        assert any("نتیجه اینجا" in m.content for m in window)

    def test_user_message_not_duplicated_after_phase1_fix(self) -> None:
        # رفع باگ فاز ۱: پیام کاربر فقط یک‌بار به مدل می‌رود
        orchestrator, provider = _orchestrator(["پاسخ"])
        _run(orchestrator, "سلام")
        first_call = provider.received[0]
        user_texts = [m.content for m in first_call if m.role == "user"]
        assert user_texts == ["سلام"]
