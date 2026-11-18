"""تست‌های موتور LLM: کلاینت Ollama (با سرور mock)، ModelManager و parser."""
from __future__ import annotations

import json

import pytest

from app.llm.base import (
    CancelToken,
    ChatMessage,
    LLMConnectionError,
    LLMResponseError,
    LLMTimeoutError,
)
from app.llm.model_manager import ModelManager
from app.llm.ollama_client import OllamaClient
from app.llm.response_parser import (
    THINK_OPEN,
    StreamThinkSplitter,
    build_repair_message,
    extract_content,
    extract_json_candidates,
    try_parse_json,
)
from app.config.settings import Settings
from tests.mock_ollama import MockOllamaServer


def _messages(text: str = "درود") -> list[ChatMessage]:
    return [ChatMessage.system("شما دستیار هستید."), ChatMessage.user(text)]


# ---------------------------------------------------------------------------
# OllamaClient — پروتکل و مدیریت خطا (سرور mock)
# ---------------------------------------------------------------------------

class TestOllamaClientProtocol:
    def test_is_available_returns_version(self) -> None:
        server = MockOllamaServer()
        try:
            client = OllamaClient(server.url)
            ok, detail = client.is_available()
            assert ok is True
            assert detail == "0.12.0"
        finally:
            server.stop()

    def test_is_available_connection_refused(self) -> None:
        # پورت بسته → پیام فارسی و بدون exception (Test 7 در سطح کلاینت)
        client = OllamaClient("http://127.0.0.1:1", connect_timeout=1.0, read_timeout=2.0)
        ok, detail = client.is_available()
        assert ok is False
        assert "Ollama" in detail
        client.close()

    def test_list_models_parses_names(self) -> None:
        server = MockOllamaServer()
        try:
            client = OllamaClient(server.url)
            models = client.list_models()
            names = [m.name for m in models]
            assert "qwen3:8b" in names and "gemma3:4b" in names
            assert models[0].size_gb is not None and models[0].size_gb > 3
        finally:
            server.stop()

    def test_chat_stream_accumulates_and_meta(self) -> None:
        server = MockOllamaServer()
        try:
            client = OllamaClient(server.url)
            options = {"temperature": 0.7, "num_ctx": 8192}
            chunks = list(
                client.chat_stream(_messages(), model="qwen3:8b", options=options)
            )
            content = "".join(c.content for c in chunks)
            assert content == "سلام! در خدمتم."
            assert chunks[-1].done is True
            assert chunks[-1].done_reason == "stop"
            assert chunks[-1].meta.get("eval_count") == 12
            # بدنه درخواست: پیام‌ها و options درست فرستاده شده‌اند
            body = server.received[-1]
            assert body["model"] == "qwen3:8b"
            assert body["messages"][1]["content"] == "درود"
            assert body["stream"] is True
            assert body["options"] == {"temperature": 0.7, "num_ctx": 8192}
        finally:
            server.stop()

    def test_chat_non_stream(self) -> None:
        server = MockOllamaServer(fail_mode="slow")
        try:
            client = OllamaClient(server.url, read_timeout=5.0)
            response = client.chat(_messages(), model="qwen3:8b")
            assert response.content == "دیر"
            assert response.done_reason == "stop" or response.model == "qwen3:8b"
        finally:
            server.stop()

    def test_connection_error_mapped(self) -> None:
        client = OllamaClient("http://127.0.0.1:1", connect_timeout=1.0, read_timeout=2.0)
        with pytest.raises(LLMConnectionError) as exc_info:
            list(client.chat_stream(_messages(), model="qwen3:8b"))
        assert "Ollama" in str(exc_info.value)
        client.close()

    def test_timeout_mapped(self) -> None:
        server = MockOllamaServer(fail_mode="slow")
        try:
            client = OllamaClient(server.url, connect_timeout=2.0, read_timeout=0.4)
            with pytest.raises(LLMTimeoutError):
                list(client.chat_stream(_messages(), model="qwen3:8b"))
        finally:
            server.stop()

    def test_model_error_json_mapped(self) -> None:
        server = MockOllamaServer(fail_mode="error_json")
        try:
            client = OllamaClient(server.url)
            with pytest.raises(LLMResponseError) as exc_info:
                list(client.chat_stream(_messages(), model="nobody:1b"))
            assert "not found" in str(exc_info.value)
        finally:
            server.stop()

    def test_bad_ndjson_mapped(self) -> None:
        server = MockOllamaServer(fail_mode="bad_ndjson")
        try:
            client = OllamaClient(server.url)
            with pytest.raises(LLMResponseError):
                list(client.chat_stream(_messages(), model="qwen3:8b"))
        finally:
            server.stop()

    def test_cancel_active_closes_stream_fast(self) -> None:
        # سرور با تأخیر بین قطعات می‌فرستد؛ لغو باید سریع (بدون انتظار کامل) باشد
        script = [("قطعه۱", 0.0), ("قطعه۲", 1.2), ("قطعه۳", 1.2), ("قطعه۴", 1.2)]
        server = MockOllamaServer(stream_script=script)
        try:
            client = OllamaClient(server.url)
            token = CancelToken()
            received: list[str] = []
            from app.llm.base import LLMCancelledError

            gen = client.chat_stream(_messages(), model="qwen3:8b", cancel=token)
            import threading
            import time

            def _cancel_soon() -> None:
                time.sleep(0.3)
                token.cancel()
                client.cancel_active()

            canceller = threading.Thread(target=_cancel_soon)
            canceller.start()
            start = time.monotonic()
            with pytest.raises(LLMCancelledError):
                for chunk in gen:
                    received.append(chunk.content)
            elapsed = time.monotonic() - start
            canceller.join()
            assert elapsed < 1.0  # نزدیک قطعه دوم و نه پایان کامل اسکریپت (۳.۶ ثانیه)
            assert received == ["قطعه۱"]
        finally:
            server.stop()


# ---------------------------------------------------------------------------
# ModelManager
# ---------------------------------------------------------------------------

class TestModelManager:
    def _manager(self, tmp_path, active: str = "qwen3:8b") -> ModelManager:
        from tests.fakes import FakeLLMClient

        settings = Settings(active_model=active)
        settings.save(tmp_path / "settings.json")
        return ModelManager(FakeLLMClient(models=["qwen3:8b", "gemma3:4b"]), settings)

    def test_active_model_from_settings(self, tmp_path) -> None:
        assert self._manager(tmp_path, active="gemma3:4b").active_model() == "gemma3:4b"

    def test_set_active_model_invalid_lists_installed(self, tmp_path) -> None:
        manager = self._manager(tmp_path)
        manager.refresh()  # کش را پر می‌کند تا اعتبارسنجی فعال شود
        with pytest.raises(ValueError) as exc_info:
            manager.set_active_model("unknown:99b")
        assert "qwen3:8b" in str(exc_info.value)  # فهرست نصب‌شده‌ها در پیام آمده

    def test_set_active_model_persists(self, tmp_path) -> None:
        manager = self._manager(tmp_path)
        manager.refresh()
        manager.set_active_model("gemma3:4b")
        saved = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
        assert saved["active_model"] == "gemma3:4b"

    def test_is_installed_matches_base_name(self, tmp_path) -> None:
        manager = self._manager(tmp_path)
        assert manager.is_installed("qwen3") is True       # نام پایه بدون تگ
        assert manager.is_installed("qwen3:latest") is True  # همان نام پایه
        assert manager.is_installed("llama3:70b") is False

    def test_suggest_default_prefers_active_then_fallback(self, tmp_path) -> None:
        manager = self._manager(tmp_path, active="gemma3:4b")
        assert manager.suggest_default() == "gemma3:4b"
        manager2 = self._manager(tmp_path, active="ناموجود:1b")
        assert manager2.suggest_default() == "qwen3:8b"  # اولین fallback نصب‌شده

    def test_ensure_ready_replaces_missing_model(self, tmp_path) -> None:
        manager = self._manager(tmp_path, active="ناموجود:1b")
        chosen = manager.ensure_ready()
        assert chosen == "qwen3:8b"
        assert manager.active_model() == "qwen3:8b"


# ---------------------------------------------------------------------------
# response_parser — تفکیک thinking و استخراج JSON
# ---------------------------------------------------------------------------

class TestExtractContent:
    def test_plain_text(self) -> None:
        content, thinking = extract_content("سلام، در خدمتم.")
        assert content == "سلام، در خدمتم."
        assert thinking == ""

    def test_closed_think_block(self) -> None:
        raw = "<think>در حال تفکر در مورد سوال هستم…</think>پاسخ: ۴۲."
        content, thinking = extract_content(raw)
        assert content == "پاسخ: ۴۲."
        assert "در حال تفکر" in thinking

    def test_unclosed_think_block(self) -> None:
        raw = "شروع واقعی<think>پاسخ متن کامل"
        content, thinking = extract_content(raw)
        assert content == "شروع واقعی"
        assert "متن" in thinking and "پاسخ" in thinking

    def test_multiple_think_blocks(self) -> None:
        raw = "<think>یک</think>مقدمه <think>دومی</think>نهایی"
        content, thinking = extract_content(raw)
        assert content == "مقدمه نهایی"
        assert "یک" in thinking and "دومی" in thinking

    def test_empty(self) -> None:
        assert extract_content("") == ("", "")
        assert extract_content(None) == ("", "")  # type: ignore[arg-type]


class TestStreamThinkSplitter:
    def test_split_across_chunk_boundaries(self) -> None:
        # تگ باز THINK_OPEN بین دو قطعه می‌شکند: "<thi" + "nk>"
        head, rest = THINK_OPEN[:4], THINK_OPEN[4:]
        splitter = StreamThinkSplitter()
        content1, thinking1 = splitter.feed("سلام " + head)
        content2, thinking2 = splitter.feed(rest + "درون تفکر ادامه")
        c3, t3 = splitter.feed("است.\n")
        assert (content1 + content2 + c3) == "سلام "
        assert thinking1 == ""
        assert "درون تفکر" in (thinking2 + t3)  # بعد از تگ باز، متن به کانال تفکر می‌رود

    def test_no_think_passthrough_with_tail_holdback(self) -> None:
        splitter = StreamThinkSplitter()
        out = splitter.feed("متن بدون تگ، ولی انتهایش <ta")
        out2 = splitter.feed("g نیست.")
        content = out[0] + out2[0]
        assert content == "متن بدون تگ، ولی انتهایش <tag نیست."

    def test_flush_releases_pending(self) -> None:
        # «<ne» پیشوند تگ think نیست، پس بی‌درنگ در content می‌ماند
        splitter = StreamThinkSplitter()
        content, _ = splitter.feed("پایان جمله <ne")
        tail_content, _ = splitter.flush()
        assert content == "پایان جمله <ne"
        assert tail_content == ""

    def test_flush_releases_pending_true_partial(self) -> None:
        # «<t» پیشوند واقعی تگ است → نگه داشته می‌شود تا flush
        splitter = StreamThinkSplitter()
        content, _ = splitter.feed("پایان جمله <t")
        assert content == "پایان جمله "
        tail_content, _ = splitter.flush()
        assert tail_content == "<t"

    def test_full_cycle_matches_extract_content(self) -> None:
        raw = "ابتدا مقدمه است."
        splitter = StreamThinkSplitter()
        # به قطعات ۱۰ کاراکتری می‌شکنیم تا مرز تگ واقعی آزمایش شود
        pieces = [raw[i:i + 10] for i in range(0, len(raw), 10)]
        content_parts: list[str] = []
        thinking_parts: list[str] = []
        for piece in pieces:
            c, t = splitter.feed(piece)
            content_parts.append(c)
            thinking_parts.append(t)
        c_tail, t_tail = splitter.flush()
        content_parts.append(c_tail)
        thinking_parts.append(t_tail)
        streamed = ("".join(content_parts).strip(), "".join(thinking_parts).strip())
        assert streamed == extract_content(raw)


class TestJsonExtraction:
    def test_valid_fenced_json(self) -> None:
        text = 'پاسخ:\n```json\n{"tool": "system.get_ram", "args": {}}\n```\nموفق'
        candidates = extract_json_candidates(text)
        assert candidates
        obj, error = try_parse_json(candidates[0])
        assert error is None
        assert obj == {"tool": "system.get_ram", "args": {}}

    def test_braces_inside_strings(self) -> None:
        text = '{"s": "داخل رشته {خرابی} هست"}'
        candidates = extract_json_candidates(text)
        assert len(candidates) >= 1
        obj, error = try_parse_json(candidates[0])
        assert error is None
        assert obj["s"].endswith("هست")

    def test_invalid_json_reported(self) -> None:
        _, error = try_parse_json("{خراب}")
        assert error is not None

    def test_empty_text_no_candidates(self) -> None:
        assert extract_json_candidates("") == []

    def test_build_repair_message_contains_error_and_snippet(self) -> None:
        message = build_repair_message("Expecting value", '{"tool": }', schema_hint='{"tool": "…"}')
        assert "Expecting value" in message
        assert '{"tool": }' in message
        assert "JSON" in message


# ===========================================================================
# فاز ۲ — Tool Calling بومی روی سرور mock
# ===========================================================================

class TestNativeToolsProtocol:
    """بخش سوم: tools در /api/chat و tool_calls در قطعه پایانی NDJSON."""

    def test_tools_sent_in_payload(self) -> None:
        server = MockOllamaServer()
        try:
            client = OllamaClient(server.url)
            tools = [
                {
                    "type": "function",
                    "function": {
                        "name": "system.get_memory_usage",
                        "description": "…",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ]
            list(client.chat_stream(_messages(), model="qwen3:8b", tools=tools))
            body = server.received[-1]
            assert body["tools"] == tools  # فریم wire دست‌نخورده
            assert body["stream"] is True
        finally:
            server.stop()

    def test_no_tools_key_without_tools(self) -> None:
        server = MockOllamaServer()
        try:
            client = OllamaClient(server.url)
            list(client.chat_stream(_messages(), model="qwen3:8b"))
            assert "tools" not in server.received[-1]
        finally:
            server.stop()

    def test_stream_delivers_tool_calls_on_final_chunk(self) -> None:
        server = MockOllamaServer(
            tool_calls_script=[
                [{"name": "system.get_memory_usage", "args": {}, "id": "call_9"}]
            ]
        )
        try:
            client = OllamaClient(server.url)
            tools = [
                {
                    "type": "function",
                    "function": {
                        "name": "system.get_memory_usage",
                        "description": "…",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ]
            chunks = list(
                client.chat_stream(_messages(), model="qwen3:8b", tools=tools)
            )
            final = chunks[-1]
            assert final.done is True
            assert len(final.tool_calls) == 1
            call = final.tool_calls[0]
            assert call.name == "system.get_memory_usage"
            assert call.args == {}
            assert call.id == "call_9"
            assert call.source == "native"
            # متن پاسخ هم مثل قبل استریم شد
            assert "".join(c.content for c in chunks[:-1]) == "سلام! در خدمتم."
        finally:
            server.stop()

    def test_chat_non_stream_parses_tool_calls(self) -> None:
        # chat() غیر-استریم هم tool_calls را برمی‌گرداند (از parse_tool_calls)
        from app.llm.ollama_client import parse_tool_calls

        frames = [
            {"function": {"name": "system.get_disk_usage", "arguments": {"path": "C:"}}}
        ]
        calls = parse_tool_calls(frames)
        assert calls[0].name == "system.get_disk_usage"
        assert calls[0].args == {"path": "C:"}

    def test_tool_messages_wire_format(self) -> None:
        # پیام role=tool و پیام assistant با tool_calls در to_api درست serialize می‌شوند
        from app.llm.base import ChatMessage, ToolCall

        assistant = ChatMessage.assistant(
            "", tool_calls=[ToolCall(name="a.b", args={"x": 1}, id="c1")]
        )
        frame = assistant.to_api()
        assert frame["tool_calls"][0]["function"]["name"] == "a.b"
        assert frame["tool_calls"][0]["function"]["arguments"] == {"x": 1}
        assert frame["tool_calls"][0]["id"] == "c1"

        tool = ChatMessage.tool("نتیجه", tool_call_id="c1")
        tool.meta = {"tool_name": "a.b"}
        tool_frame = tool.to_api()
        assert tool_frame["role"] == "tool"
        assert tool_frame["tool_name"] == "a.b"
        assert tool_frame["tool_call_id"] == "c1"


class TestShowModelCapabilities:
    """تشخیص قابلیت واقعی مدل از /api/show."""

    def test_supports_native_tools_true(self) -> None:
        server = MockOllamaServer(capabilities={"qwen3:8b": ["completion", "tools"]})
        try:
            client = OllamaClient(server.url)
            assert client.supports_native_tools("qwen3:8b") is True
        finally:
            server.stop()

    def test_supports_native_tools_false(self) -> None:
        server = MockOllamaServer(capabilities={"gemma3:4b": ["completion"]})
        try:
            client = OllamaClient(server.url)
            assert client.supports_native_tools("gemma3:4b") is False
        finally:
            server.stop()

    def test_no_capabilities_key_means_false(self) -> None:
        # Ollama قدیمی / بدون capabilities → False (fallback می‌ماند)
        server = MockOllamaServer()
        try:
            client = OllamaClient(server.url)
            assert client.supports_native_tools("qwen3:8b") is False
        finally:
            server.stop()

    def test_show_model_connection_failure_returns_minimal(self) -> None:
        # قطع سرویس → ModelInfo حداقلی، بدون exception
        client = OllamaClient("http://127.0.0.1:1", connect_timeout=1.0, read_timeout=2.0)
        info = client.show_model("qwen3:8b")
        assert info.name == "qwen3:8b"
        assert info.capabilities is None
        client.close()


class TestModelManagerToolsCapability:
    def test_supports_tools_cached(self) -> None:
        from app.llm.base import ModelInfo
        from tests.fakes import FakeLLMClient

        provider = FakeLLMClient(native_tools=True)
        settings = Settings()
        manager = ModelManager(provider, settings)
        assert manager.supports_native_tools("qwen3:8b") is True
        assert provider.show_calls.count("qwen3:8b") == 1
        manager.supports_native_tools("qwen3:8b")  # کش — فراخوانی مجدد شبکه نیست
        assert provider.show_calls.count("qwen3:8b") == 1

    def test_supports_tools_default_false_without_capability(self) -> None:
        from tests.fakes import FakeLLMClient

        provider = FakeLLMClient(native_tools=False)
        manager = ModelManager(provider, Settings())
        assert manager.supports_native_tools("qwen3:8b") is False


import json  # noqa: E402 - مورد نیاز تست ذخیره تنظیمات
