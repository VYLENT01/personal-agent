"""Orchestrator — مرکز تصمیم‌گیری و مدیریت اجرای درخواست کاربر.

چرخه اصلی (طبق بند ۱۰ مشخصات):
دریافت درخواست → بررسی Context → ارسال به LLM (استریم) → تفکیک thinking
→ ثبت پاسخ در وضعیت مکالمه → ارائه پاسخ نهایی.

از فاز ۲ به بعد، همین چرخه بین «پاسخ متنی» و «Tool Call» تصمیم می‌گیرد؛
ساختار رویدادها (AgentEvent) از هم‌اکنون برای آن آماده است.

نکته‌های معماری:
- stream_turn یک generator است و در Thread کارگری UI اجرا می‌شود؛ UI هنگ نمی‌کند.
- لغو عملیات با CancelToken + بستن socket از طریق provider (سریع، بدون انتظار).
- سقف iteration برای جلوگیری از حلقه بی‌نهایت (MAX_AGENT_ITERATIONS).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from time import perf_counter
from typing import Iterator, Optional, Union

from app.agent.prompts import build_messages, build_system_prompt
from app.agent.state import ConversationState
from app.config import defaults as d
from app.config.settings import Settings
from app.llm.base import (
    BaseLLMClient,
    CancelToken,
    LLMCancelledError,
    LLMError,
    ModelInfo,
)
from app.llm.model_manager import ModelManager
from app.llm.response_parser import StreamThinkSplitter

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# رویدادهای یک نوبت گفت‌وگو (AgentEvent)
# ---------------------------------------------------------------------------

@dataclass
class TurnStarted:
    """شروع نوبت پاسخ‌گویی؛ مدل انتخاب‌شده را اعلام می‌کند."""

    model: Optional[str] = None


@dataclass
class ThinkingDelta:
    """قطعه‌ای از تفکر مدل (جدا از محتوای اصلی)."""

    text: str


@dataclass
class ContentDelta:
    """قطعه‌ای از محتوای پاسخ."""

    text: str


@dataclass
class TurnCompleted:
    """پایان موفق نوبت."""

    content: str
    thinking: str = ""
    model: Optional[str] = None
    duration: float = 0.0


@dataclass
class TurnCancelled:
    """لغو نوبت به‌دستور کاربر؛ متن جزئی در صورت وجود."""

    partial: str = ""


@dataclass
class TurnFailed:
    """شکست نوبت با پیام قابل نمایش به کاربر."""

    error: str
    kind: str = "LLMError"
    recoverable: bool = True


AgentEvent = Union[TurnStarted, ThinkingDelta, ContentDelta, TurnCompleted, TurnCancelled, TurnFailed]


class ChatOrchestrator:
    """هماهنگ‌کننده اصلی گفت‌وگو بین UI و موتور مدل."""

    def __init__(
        self,
        provider: BaseLLMClient,
        model_manager: ModelManager,
        settings: Settings,
    ) -> None:
        self.provider = provider
        self.model_manager = model_manager
        self.settings = settings
        self.state = ConversationState()
        self.iterations_limit = d.MAX_AGENT_ITERATIONS
        self._cancel: Optional[CancelToken] = None

    # --- تزریق مجدد اجزا هنگام تغییر تنظیمات ---

    def set_provider(self, provider: BaseLLMClient) -> None:
        self.provider = provider

    def set_settings(self, settings: Settings) -> None:
        self.settings = settings

    # --- نوبت گفت‌وگو ---

    def stream_turn(self, user_text: str) -> Iterator[AgentEvent]:
        """اجرای یک نوبت کامل؛ رویدادها به‌صورت استریم به UI می‌رسند.

        هر خطا به TurnFailed با پیام فارسی قابل‌نمایش تبدیل می‌شود؛
        هیچ exception از این generator به UI نمی‌رسد.
        """
        text = (user_text or "").strip()
        if not text:
            yield TurnFailed(error="پیام خالی است.", kind="ValidationError", recoverable=False)
            return

        token = CancelToken()
        self._cancel = token
        model = self.model_manager.active_model()

        self.state.append_user(text)
        yield TurnStarted(model=model)

        messages = build_messages(
            build_system_prompt(self.settings.language),
            self.state.window(
                self.settings.max_history_messages,
                self.settings.max_context_chars,
            ),
            text,
        )
        options = self.settings.generation.to_options()

        splitter = StreamThinkSplitter()
        content_parts: list[str] = []
        thinking_parts: list[str] = []
        done_reason: Optional[str] = None
        started = perf_counter()

        try:
            for chunk in self.provider.chat_stream(
                messages, model=model, options=options, cancel=token
            ):
                if token.is_cancelled():
                    raise LLMCancelledError("پیش از ادامه پاسخ، عملیات لغو شد.")
                if chunk.done and chunk.done_reason:
                    done_reason = chunk.done_reason
                if not chunk.content:
                    continue
                content_out, thinking_out = splitter.feed(chunk.content)
                if content_out:
                    content_parts.append(content_out)
                    yield ContentDelta(content_out)
                if thinking_out:
                    thinking_parts.append(thinking_out)
                    yield ThinkingDelta(thinking_out)

            tail_content, tail_thinking = splitter.flush()
            if tail_content:
                content_parts.append(tail_content)
                yield ContentDelta(tail_content)
            if tail_thinking:
                thinking_parts.append(tail_thinking)
                yield ThinkingDelta(tail_thinking)

        except LLMCancelledError:
            partial = "".join(content_parts).strip()
            if partial:
                # پاسخ ناتمام را در تاریخچه نگه می‌داریم تا Context گمراه‌کننده نشود
                self.state.append_assistant(partial, cancelled=True)
            yield TurnCancelled(partial=partial)
            return

        except LLMError as exc:
            logger.warning("نوبت با خطا پایان یافت (%s): %s", type(exc).__name__, exc)
            yield TurnFailed(error=str(exc), kind=type(exc).__name__)
            return

        content = "".join(content_parts).strip()
        thinking = "".join(thinking_parts).strip()
        if not content and not thinking:
            yield TurnFailed(
                error="مدل پاسخی تولید نکرد (خروجی خالی بود). دوباره تلاش کنید.",
                kind="LLMResponseError",
            )
            return

        if done_reason == "length":
            logger.info("پاسخ به سقف توکن تولید رسید (done_reason=length)")

        self.state.append_assistant(content, thinking=thinking)
        yield TurnCompleted(
            content=content,
            thinking=thinking,
            model=model,
            duration=round(perf_counter() - started, 3),
        )

    # --- لغو و کنترل مکالمه ---

    def request_cancel(self) -> None:
        """لغو نوبت در-حال-اجرا: هم توکن ست می‌شود هم socket مدل بسته می‌شود."""
        if self._cancel is None:
            return
        self._cancel.cancel()
        try:
            closed = self.provider.cancel_active()
            logger.debug("لغو درخواست شد؛ %d جریان بسته شد.", closed)
        except Exception:  # noqa: BLE001 - لغو نباید هیچ‌وقت خطا تولید کند
            logger.debug("بستن جریان‌ها هنگام لغو با خطا مواجه شد", exc_info=True)

    def new_conversation(self) -> None:
        self.state.clear()

    # --- کمکی ---

    def installed_models(self) -> list[ModelInfo]:
        """فهرست مدل‌ها برای UI؛ خطای اتصال به پیام خالی تبدیل نمی‌شود."""
        return self.model_manager.list_models()
