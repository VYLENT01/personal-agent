"""کلاینت LLM ساختگی برای تست‌های بدون Ollama واقعی.

اسکریپت پاسخ (دنباله‌ای از متن‌ها/خطاها/فریم tool_calls) را شبیه‌سازی می‌کند؛
رفتار لغو (CancelToken) را هم دقیق پیاده می‌کند تا مسیر لغو واقعی آزمایش شود.

فاز ۲: پشتیبانی پارامتر tools و انتشار tool_calls در قطعه پایانی —
هم مسیر native (فریم OpenAI-style) و هم شمارش فراخوانی‌های دریافتی برای تست.
"""
from __future__ import annotations

import json
from typing import Any, Iterator, Mapping, Optional, Sequence

from app.llm.base import (
    BaseLLMClient,
    CancelToken,
    ChatMessage,
    LLMConnectionError,
    LLMError,
    LLMResponse,
    ModelInfo,
    StreamChunk,
    ToolCall,
)


class FakeLLMClient(BaseLLMClient):
    """کلاینت ساختگی با اسکریپت قابل تنظیم.

    script: آیتم‌ها می‌توانند:
    - str → قطعه متن پاسخ
    - Exception → خطای شبیه‌سازی‌شده در همان نقطه
    - dict با کلید tool_calls → قطعه پایانی با فراخوانی ابزار
      قالب: {"tool_calls": [{"name":..., "args": {...}}]}
    """

    def __init__(
        self,
        script: Optional[Sequence[str | Exception | dict[str, Any]]] = None,
        *,
        models: Optional[Sequence[str]] = None,
        delay: float = 0.0,
        native_tools: bool = False,
        capabilities: Optional[dict[str, list[str]]] = None,
    ) -> None:
        self.script: list[str | Exception | dict[str, Any]] = list(
            script or ["سلام! در خدمتم."]
        )
        self.models: list[str] = list(models or ["qwen3:8b", "gemma3:4b"])
        self.delay = delay
        self.native_tools = native_tools
        # قابلیت‌های اعلام‌شده هر مدل برای supports_native_tools
        self.capabilities: dict[str, list[str]] = capabilities or {}
        self.received: list[list[ChatMessage]] = []
        self.received_tools: list[Optional[list[dict[str, Any]]]] = []
        self.last_options: Optional[dict[str, Any]] = None
        self.last_model: Optional[str] = None
        self.cancelled_streams = 0
        self.show_calls: list[str] = []

    def chat(
        self,
        messages: Sequence[ChatMessage],
        model: Optional[str] = None,
        *,
        options: Optional[Mapping[str, Any]] = None,
        tools: Optional[Sequence[Mapping[str, Any]]] = None,
        cancel: Optional[CancelToken] = None,
    ) -> LLMResponse:
        content = "".join(item for item in self.script if isinstance(item, str))
        calls = [
            ToolCall(name=frame["name"], args=frame.get("args", {}))
            for item in self.script
            if isinstance(item, dict)
            for frame in item.get("tool_calls", [])
        ]
        return LLMResponse(content=content, tool_calls=calls, model=model or "fake")

    def chat_stream(
        self,
        messages: Sequence[ChatMessage],
        model: Optional[str] = None,
        *,
        options: Optional[Mapping[str, Any]] = None,
        tools: Optional[Sequence[Mapping[str, Any]]] = None,
        cancel: Optional[CancelToken] = None,
    ) -> Iterator[StreamChunk]:
        self.received.append(list(messages))
        self.received_tools.append(list(tools) if tools else None)
        self.last_options = dict(options or {})
        self.last_model = model
        # هر فراخوانی فقط یک «مرحله» از اسکریپت را مصرف می‌کند (رفتار مکالمه
        # واقعی: پاسخ بعدی به تکرار بعدی می‌رسد). اگر آیتم لیست باشد، همهٔ
        # اعضایش در همان مرحله پخش می‌شوند (چند قطعه در یک پاسخ).
        step = len(self.received) - 1
        item: Any = self.script[step] if step < len(self.script) else "پایان."
        collected_calls: list[ToolCall] = []
        if cancel is not None and cancel.is_cancelled():
            self.cancelled_streams += 1
            from app.llm.base import LLMCancelledError

            raise LLMCancelledError("توسط تست لغو شد.")
        if isinstance(item, Exception):
            raise item
        pieces = item if isinstance(item, list) else [item]
        for piece in pieces:
            if isinstance(piece, Exception):
                raise piece
            if isinstance(piece, dict):
                for frame in piece.get("tool_calls", []):
                    collected_calls.append(
                        ToolCall(name=str(frame.get("name")), args=frame.get("args", {}) or {})
                    )
                continue  # فریم ابزار متن تولید نمی‌کند
            if self.delay:
                import time

                time.sleep(self.delay)
            yield StreamChunk(content=str(piece))
        yield StreamChunk(
            content="",
            done=True,
            done_reason="stop",
            tool_calls=collected_calls,
            meta={"eval_count": 42},
        )

    def list_models(self) -> list[ModelInfo]:
        return [
            ModelInfo(name=name, size_bytes=1_000_000 * (index + 1))
            for index, name in enumerate(self.models)
        ]

    def show_model(self, model: str) -> ModelInfo:
        """capabilities ساختگی بر اساس تنظیم تست؛ native_tools=True یعنی همه مدل‌ها."""
        self.show_calls.append(model)
        caps = self.capabilities.get(
            model, ["tools"] if self.native_tools else None
        )
        return ModelInfo(name=model, capabilities=caps)

    def supports_native_tools(self, model: str) -> bool:
        return self.show_model(model).supports_tools

    def is_available(self) -> tuple[bool, str]:
        return True, "fake-1.0"

    def cancel_active(self) -> int:
        return 0

    def close(self) -> None:
        pass


def make_native_tool_script(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """ابزار کمکی تست: ساخت اسکریپت native از فریم‌های ساده.

    frames: [{"name": "system.get_memory_usage", "args": {}}]
    """
    return [{"tool_calls": [dict(frame) for frame in frames]}]


__all__ = ["FakeLLMClient", "make_native_tool_script"]
