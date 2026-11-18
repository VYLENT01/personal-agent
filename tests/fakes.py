"""کلاینت LLM ساختگی برای تست‌های بدون Ollama واقعی.

اسکریپت پاسخ (دنباله‌ای از متن‌ها/خطاها) را شبیه‌سازی می‌کند؛
رفتار لغو (CancelToken) را هم دقیق پیاده می‌کند تا مسیر لغو واقعی آزمایش شود.
"""
from __future__ import annotations

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
)


class FakeLLMClient(BaseLLMClient):
    """کلاینت ساختگی با اسکریپت قابل تنظیم."""

    def __init__(
        self,
        script: Optional[Sequence[str | Exception]] = None,
        *,
        models: Optional[Sequence[str]] = None,
        delay: float = 0.0,
    ) -> None:
        self.script: list[str | Exception] = list(script or ["سلام! در خدمتم."])
        self.models: list[str] = list(models or ["qwen3:8b", "gemma3:4b"])
        self.delay = delay
        self.received: list[list[ChatMessage]] = []
        self.last_options: Optional[dict[str, Any]] = None
        self.last_model: Optional[str] = None
        self.cancelled_streams = 0

    def chat(
        self,
        messages: Sequence[ChatMessage],
        model: Optional[str] = None,
        *,
        options: Optional[Mapping[str, Any]] = None,
        cancel: Optional[CancelToken] = None,
    ) -> LLMResponse:
        content = "".join(item for item in self.script if isinstance(item, str))
        return LLMResponse(content=content, model=model or "fake")

    def chat_stream(
        self,
        messages: Sequence[ChatMessage],
        model: Optional[str] = None,
        *,
        options: Optional[Mapping[str, Any]] = None,
        cancel: Optional[CancelToken] = None,
    ) -> Iterator[StreamChunk]:
        self.received.append(list(messages))
        self.last_options = dict(options or {})
        self.last_model = model
        for item in self.script:
            if cancel is not None and cancel.is_cancelled():
                self.cancelled_streams += 1
                from app.llm.base import LLMCancelledError

                raise LLMCancelledError("توسط تست لغو شد.")
            if isinstance(item, Exception):
                raise item
            if self.delay:
                import time

                time.sleep(self.delay)
            yield StreamChunk(content=str(item))
        yield StreamChunk(content="", done=True, done_reason="stop", meta={"eval_count": 42})

    def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(name=name, size_bytes=1_000_000 * (index + 1)) for index, name in enumerate(self.models)]

    def is_available(self) -> tuple[bool, str]:
        return True, "fake-1.0"

    def cancel_active(self) -> int:
        return 0

    def close(self) -> None:
        pass
