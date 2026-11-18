"""Orchestrator — مرکز تصمیم‌گیری و مدیریت اجرای درخواست کاربر.

چرخه اصلی (بخش چهارم فاز ۲؛ ده مرحله):
۱. دریافت درخواست کاربر
۲. ارسال Context و ابزارهای مجاز به مدل (native یا fallback بر اساس قابلیت واقعی مدل)
۳. دریافت پاسخ (استریم؛ تفکیک thinking)
۴. تشخیص Tool Call (tool_calls بومی یا بلوک JSON در متن — فقط در مسیر fallback)
۵. اعتبارسنجی ابزار و پارامترها (Registry + Pydantic، مستقل از LLM)
۶. بررسی مجوز (L1 اجرا؛ L2/L3 رد — فاز ۵)
۷. اجرای ابزار (timeout؛ در همان thread کارگری)
۸. دریافت نتیجه ساختاریافته
۹. ارسال نتیجه به مدل (پیام role=tool در native / پیام کاربر با پیشوند در fallback)
۱۰. تولید پاسخ نهایی

محدودیت‌ها (بخش چهارم):
- سقف MAX_TOOL_CALLS_PER_TURN و سقف iteration کل → جلوگیری از Loop بی‌نهایت
- Timeout ابزار در Registry؛ Timeout مدل در کلاینت
- Cancel در هر لحظه (توکن + بستن socket)
- خطای ابزار به ToolResult.fail → به مدل برمی‌گردد (نه کرش)
- Tool Callهای متعدد در یک پاسخ: به‌ترتیب اجرا می‌شوند
- وضعیت مکالمه کامل حفظ می‌شود (پیام‌های ابزار در state)

نکته‌های معماری:
- stream_turn یک generator است و در Thread کارگری UI اجرا می‌شود؛ UI هنگ نمی‌کند.
- هیچ وابستگی Agent Core به فرمت اختصاصی مدل نیست: تفکیک native/fallback
  با قرارداد BaseLLMClient و supports_native_tools انجام می‌شود.
- در مسیر native، tools به کلاینت داده می‌شود و مدل از message.tool_calls
  پاسخ می‌دهد؛ در fallback، پروتکل JSON در پیام سیستم تعریف و از متن پاسخ
  استخراج می‌شود (extract_json_candidates — تست‌شده فاز ۱).
- رفع باگ فاز ۱: پیام کاربر دیگر هم در پنجره Context و هم جداگانه ارسال
  نمی‌شود؛ پنجره قبل از append_user گرفته می‌شود.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Iterator, Optional, Sequence, Union

from app.agent.prompts import (
    build_messages,
    build_system_prompt,
    tool_result_user_message,
)
from app.agent.state import ConversationState
from app.config import defaults as d
from app.config.settings import Settings
from app.llm.base import (
    BaseLLMClient,
    CancelToken,
    ChatMessage,
    LLMCancelledError,
    LLMError,
    ModelInfo,
    ToolCall,
)
from app.llm.model_manager import ModelManager
from app.llm.response_parser import (
    StreamThinkSplitter,
    extract_content,
    extract_json_candidates,
    try_parse_json,
)
from app.tools.base import ToolResult
from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# رویدادهای یک نوبت گفت‌وگو (AgentEvent)
# ---------------------------------------------------------------------------

@dataclass
class TurnStarted:
    """شروع نوبت پاسخ‌گویی؛ مدل و مسیر ابزار (native/fallback/none)."""

    model: Optional[str] = None
    tool_mode: str = "none"          # none | native | fallback


@dataclass
class ThinkingDelta:
    """قطعه‌ای از تفکر مدل (جدا از محتوای اصلی)."""

    text: str


@dataclass
class ContentDelta:
    """قطعه‌ای از محتوای پاسخ."""

    text: str


@dataclass
class ToolCallRequested:
    """مدل یک ابزار را خواسته (بعد از اعتبارسنجی اولیه نام)."""

    call: ToolCall
    index: int                        # شماره فراخوانی در این نوبت
    iteration: int                    # شماره تکرار حلقه


@dataclass
class ApprovalRequested:
    """درخواست تأیید کاربر برای عملیات L2 (بخش ۸ فاز ۳).

    UI پس از نمایش دیالوگ، یکی از resolve_approve/resolve_deny را صدا می‌زند
    (thread-safe). orchestrator منتظرِ قابل‌قطع (لغو نوبت/انقضا) می‌ماند.
    """

    tool_name: str
    args: dict[str, Any]
    index: int
    # پرشدن توسط UI: (تصمیم کاربر، توکن تأیید در صورت قبول)
    _decision: Optional[bool] = None          # None=در انتظار | True=تأیید | False=رد
    _approval: Optional["ApprovalToken"] = None
    _event: Any = None                          # threading.Event (سازنده می‌سازد)

    def resolve_approve(self, approval: "ApprovalToken") -> None:
        """کاربر عملیات را تأیید کرد؛ توکن متناسب پیوست می‌شود."""
        self._approval = approval
        self._decision = True
        if self._event is not None:
            self._event.set()

    def resolve_deny(self) -> None:
        """کاربر عملیات را رد کرد."""
        self._decision = False
        if self._event is not None:
            self._event.set()

    @property
    def decision(self) -> Optional[bool]:
        return self._decision

    @property
    def approval(self) -> Optional["ApprovalToken"]:
        return self._approval


@dataclass
class ToolCallStarted:
    """اجرای ابزار آغاز شد (پارامترها معتبر و مجاز)."""

    name: str
    args: dict[str, Any]
    index: int


@dataclass
class ToolCallFinished:
    """نتیجه اجرای ابزار (موفق یا خطا) — ساختاریافته."""

    result: ToolResult
    args: dict[str, Any]
    index: int


@dataclass
class TurnCompleted:
    """پایان موفق نوبت."""

    content: str
    thinking: str = ""
    model: Optional[str] = None
    duration: float = 0.0
    tool_calls: int = 0               # تعداد کل فراخوانی‌های ابزار این نوبت
    iterations: int = 1               # تعداد مراحل اجرای Agent


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


AgentEvent = Union[
    TurnStarted,
    ThinkingDelta,
    ContentDelta,
    ToolCallRequested,
    ApprovalRequested,
    ToolCallStarted,
    ToolCallFinished,
    TurnCompleted,
    TurnCancelled,
    TurnFailed,
]


class ChatOrchestrator:
    """هماهنگ‌کننده اصلی گفت‌وگو بین UI و موتور مدل و ابزارها."""

    def __init__(
        self,
        provider: BaseLLMClient,
        model_manager: ModelManager,
        settings: Settings,
        tool_registry: Optional[ToolRegistry] = None,
    ) -> None:
        self.provider = provider
        self.model_manager = model_manager
        self.settings = settings
        self.tool_registry = tool_registry or ToolRegistry()
        self.state = ConversationState()
        self.iterations_limit = d.MAX_AGENT_ITERATIONS
        self.max_tool_calls = d.MAX_TOOL_CALLS_PER_TURN
        self._cancel: Optional[CancelToken] = None

    # --- تزریق مجدد اجزا هنگام تغییر تنظیمات ---

    def set_provider(self, provider: BaseLLMClient) -> None:
        self.provider = provider

    def set_settings(self, settings: Settings) -> None:
        self.settings = settings

    def set_tool_registry(self, registry: ToolRegistry) -> None:
        self.tool_registry = registry

    # ------------------------------------------------------------------
    # نوبت گفت‌وگو — حلقه کامل Agent
    # ------------------------------------------------------------------

    def stream_turn(self, user_text: str) -> Iterator[AgentEvent]:
        """اجرای یک نوبت کامل با حلقه ابزار؛ رویدادها استریم به UI می‌رسند.

        هر خطا به TurnFailed با پیام فارسی تبدیل می‌شود؛ هیچ exception
        از این generator به UI نمی‌رسد.
        """
        text = (user_text or "").strip()
        if not text:
            yield TurnFailed(error="پیام خالی است.", kind="ValidationError", recoverable=False)
            return

        token = CancelToken()
        self._cancel = token
        # اجرای ابزار هم همان توکن لغو نوبت را می‌بیند (بخش چهارم: امکان Cancel)
        try:
            self.tool_registry.bind_cancel_token(token)
        except Exception:  # noqa: BLE001 - سیم‌کیری توکن هرگز نوبت را نباید بکشد
            logger.debug("اتصال توکن لغو به Registry ناموفق", exc_info=True)
        model = self.model_manager.active_model()
        started = perf_counter()

        # رفع باگ فاز ۱: پنجره Context «قبل» از افزودن پیام جدید گرفته می‌شود
        history_window = self.state.window(
            self.settings.max_history_messages,
            self.settings.max_context_chars,
        )
        self.state.append_user(text)

        # --- تعیین مسیر ابزار بر اساس قابلیت واقعی مدل ---
        registry = self.tool_registry
        schemas = registry.get_tool_schemas()
        tool_mode = self._resolve_tool_mode(model, schemas)

        yield TurnStarted(model=model, tool_mode=tool_mode)

        turn_tool_calls = 0
        iteration = 0
        pending_result_messages: list[ChatMessage] = []  # پیام‌های نتیجه این تکرار

        while iteration < self.iterations_limit:
            iteration += 1
            if token.is_cancelled():
                yield TurnCancelled()
                return

            # --- ۲. ساخت پیام‌ها (سیستم + تاریخچه + کاربر + نتایج ابزار) ---
            system_prompt = build_system_prompt(
                self.settings.language,
                registry.capabilities_prompt(self.settings.language) if schemas else (),
                tool_mode=tool_mode,
            )
            if tool_mode == "fallback" and iteration > 1:
                # در مسیر fallback نتیجه ابزار به‌عنوان پیام کاربر می‌رود
                user_text_for_model = tool_result_user_message(
                    pending_result_messages[-1].meta.get("tool_name", "tool"),
                    pending_result_messages[-1].content,
                    self.settings.language,
                )
                messages = build_messages(system_prompt, history_window, user_text_for_model)
            else:
                messages = build_messages(system_prompt, history_window, text)
                if tool_mode == "native":
                    # نتایج ابزار تکرارهای قبلی به‌عنوان پیام‌های role=tool
                    messages.extend(pending_result_messages)

            options = self.settings.generation.to_options()
            tools_param = schemas if tool_mode == "native" else None

            # --- ۳/۴. دریافت پاسخ و تشخیص Tool Call ---
            # outcome همیشه دیکشنری نتیجه است؛ رویدادهای پایان (TurnFailed/
            # TurnCancelled) داخل آن با کلیدهای "failed"/"cancelled" می‌آیند.
            outcome = yield from self._stream_llm_call(
                messages, model, options, tools_param, token
            )
            if outcome.get("failed") is not None:
                yield outcome["failed"]
                return
            if outcome.get("cancelled") is not None:
                yield outcome["cancelled"]
                return

            content: str = outcome["content"]
            thinking: str = outcome["thinking"]
            native_calls: list[ToolCall] = outcome["tool_calls"]

            # --- Tool Callهای مسیر fallback از متن پاسخ ---
            fallback_calls: list[ToolCall] = []
            if tool_mode == "fallback" and content:
                fallback_calls = self._extract_fallback_calls(content)
                if fallback_calls:
                    # پاسخ فقط بلوک ابزار است؛ محتوای متنی نمایشی باقی نمی‌ماند
                    content = ""

            calls = native_calls if tool_mode == "native" else fallback_calls

            if not calls:
                # --- بدون ابزار: پاسخ نهایی ---
                if not content and not thinking:
                    yield TurnFailed(
                        error="مدل پاسخی تولید نکرد (خروجی خالی بود). دوباره تلاش کنید.",
                        kind="LLMResponseError",
                    )
                    return
                self.state.append_assistant(content, thinking=thinking)
                yield TurnCompleted(
                    content=content,
                    thinking=thinking,
                    model=model,
                    duration=round(perf_counter() - started, 3),
                    tool_calls=turn_tool_calls,
                    iterations=iteration,
                )
                return

            # --- ۵–۸. فراخوانی ابزارها ---
            if turn_tool_calls + len(calls) > self.max_tool_calls:
                logger.info(
                    "سقف فراخوانی ابزار این نوبت (%d) پر شد؛ باقی رد شد.",
                    self.max_tool_calls,
                )
                calls = calls[: self.max_tool_calls - turn_tool_calls]
                if not calls:
                    self.state.append_assistant(content, thinking=thinking)
                    yield TurnCompleted(
                        content=content or "به سقف فراخوانی ابزار رسیدم؛ پاسخ بر پایهٔ داده‌های موجود:",
                        thinking=thinking,
                        model=model,
                        duration=round(perf_counter() - started, 3),
                        tool_calls=turn_tool_calls,
                        iterations=iteration,
                    )
                    return

            # ثبت پیام دستیار همراه فراخوانی‌ها (قبل از اجرا؛ تا Context دقیق بماند)
            self.state.append_assistant_with_calls(content, calls, thinking=thinking)

            pending_result_messages = []
            for index, call in enumerate(calls, start=turn_tool_calls + 1):
                yield ToolCallRequested(call=call, index=index, iteration=iteration)

                # اعتبارسنجی + مجوز (مرحله ۵ و ۶)
                ready, tool, validation_error = registry.validate_tool_call(
                    call.name, call.args
                )
                if not ready:
                    result = ToolResult.fail(
                        call.name, validation_error or "فراخوانی رد شد."
                    )
                else:
                    assert tool is not None
                    # --- گیت تأیید کاربر برای L2 (بخش ۸ فاز ۳) ---
                    approval = None
                    if registry.needs_approval(call.name):
                        approval = yield from self._request_approval(
                            call.name, call.args, index, token
                        )
                        if approval is None:
                            # رد/انقضا/لغو → نتیجه ساختاریافته رد به مدل
                            result = ToolResult.fail(
                                call.name,
                                "عملیات به‌دستور شما رد شد یا مهلت تأیید تمام شد.",
                            )
                            yield ToolCallFinished(
                                result=result, args=call.args, index=index
                            )
                            turn_tool_calls += 1
                            result_text = result.truncate_for_llm()
                            self._record_tool_result(
                                call, result_text, tool_mode, pending_result_messages
                            )
                            continue

                    yield ToolCallStarted(name=call.name, args=call.args, index=index)
                    # --- ۷/۸. اجرا با timeout داخلی Registry + تأیید ---
                    result = registry.execute_tool(
                        call.name, call.args, approval=approval
                    )

                yield ToolCallFinished(result=result, args=call.args, index=index)
                turn_tool_calls += 1

                # --- ۹. آماده‌سازی نتیجه برای مدل (تکرار بعد) + ثبت در تاریخچه ---
                result_text = result.truncate_for_llm()
                self._record_tool_result(call, result_text, tool_mode, pending_result_messages)

            # حلقه ادامه می‌یابد: مدل نتیجه را می‌بیند و پاسخ نهایی می‌سازد
            if iteration >= self.iterations_limit:
                # سقف مراحل Agent؛ بدون پاسخ نهایی نوبت را محترمانه می‌بندیم
                yield TurnFailed(
                    error=(
                        f"به سقف مراحل اجرای Agent ({self.iterations_limit}) رسیدم "
                        "بدون پاسخ نهایی. درخواست را ساده‌تر یا مشخص‌تر بپرسید."
                    ),
                    kind="IterationLimit",
                    recoverable=True,
                )
                return

        # pragma: no cover - حلقه بالا همیشه return می‌کند
        yield TurnFailed(error="حلقه ایجنت به پایان غیرمنتظره رسید.", kind="InternalError")

    # ------------------------------------------------------------------
    # تأیید کاربر برای عملیات L2 (بخش ۸ فاز ۳)
    # ------------------------------------------------------------------

    def _request_approval(
        self,
        tool_name: str,
        args: dict[str, Any],
        index: int,
        token: "CancelToken",
    ) -> Iterator[AgentEvent]:
        """رویداد ApprovalRequested + انتظار قابل‌قطع تا تصمیم/انقضا/لغو.

        خروجی: ApprovalToken در صورت تأیید؛ None در صورت رد/انقضا/لغو.
        توکن فقط از پاسخ کاربر (UI) می‌آید — مدل هرگز آن نمی‌سازد.
        """
        import threading as _threading

        from app.config import defaults as _d
        from app.security.permissions import ApprovalToken, PermissionManager

        request = ApprovalRequested(tool_name=tool_name, args=args, index=index)
        request._event = _threading.Event()  # noqa: SLF001 - سیم‌کشی داخلی رویداد
        yield request

        timeout = getattr(self.settings, "approval_timeout", None) or _d.APPROVAL_TIMEOUT_SECONDS
        deadline = time.monotonic() + min(float(timeout), _d.APPROVAL_TIMEOUT_SECONDS)
        while not request._event.wait(timeout=0.1):  # noqa: SLF001 - انتظار قابل‌قطع
            if token.is_cancelled():
                logger.info("نوبت حین انتظار تأیید لغو شد (%s)", tool_name)
                return
            if time.monotonic() >= deadline:
                logger.info("مهلت تأیید %s تمام شد", tool_name)
                return
        if request.decision is True and request.approval is not None:
            return request.approval
        return None

    def _record_tool_result(
        self,
        call: ToolCall,
        result_text: str,
        tool_mode: str,
        pending_result_messages: list[ChatMessage],
    ) -> None:
        """ثبت نتیجه ابزار در تاریخچه + آماده‌سازی پیام تکرار بعد (هر دو مسیر)."""
        if tool_mode == "native":
            state_message = self.state.append_tool_result(
                call.name, result_text, tool_call_id=call.id
            )
            pending_result_messages.append(state_message)
        else:
            self.state.append_tool_result(call.name, result_text)
            pending_result_messages.append(_fallback_pending(call.name, result_text))

    # ------------------------------------------------------------------
    # زیر-چرخه: یک فراخوانی استریم LLM
    # ------------------------------------------------------------------
    def _stream_llm_call(
        self,
        messages: Sequence[ChatMessage],
        model: str,
        options: dict[str, Any],
        tools: Optional[Sequence[dict[str, Any]]],
        token: CancelToken,
    ) -> Iterator[AgentEvent]:
        """یک فراخوانی استریم LLM؛ رویدادها را yield می‌کند و dict نتیجه برمی‌گرداند.

        الگو: «yield برای رویدادهای نمایشی + return برای نتیجه» — چون در
        Python نتیجهٔ `yield from` مقدار return زیر-ژنراتور است، کلیدهای
        failed/cancelled هم داخل dict نتیجه حمل می‌شوند.
        """
        splitter = StreamThinkSplitter()
        content_parts: list[str] = []
        thinking_parts: list[str] = []
        native_calls: list[ToolCall] = []

        try:
            for chunk in self.provider.chat_stream(
                messages, model=model, options=options, tools=tools, cancel=token
            ):
                if token.is_cancelled():
                    raise LLMCancelledError("پیش از ادامه پاسخ، عملیات لغو شد.")
                for call in chunk.tool_calls:
                    native_calls.append(call)
                if chunk.done and chunk.done_reason == "length":
                    logger.info("پاسخ به سقف توکن تولید رسید (done_reason=length)")
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
                self.state.append_assistant(partial, cancelled=True)
            return {"cancelled": TurnCancelled(partial=partial)}

        except LLMError as exc:
            logger.warning("نوبت با خطا پایان یافت (%s): %s", type(exc).__name__, exc)
            return {"failed": TurnFailed(error=str(exc), kind=type(exc).__name__)}
        except Exception as exc:  # noqa: BLE001 - هیچ خطایی نباید از حلقه به UI برسد
            logger.exception("خطای غیرمنتظره در فراخوانی مدل")
            return {
                "failed": TurnFailed(
                    error=f"خطای داخلی: {exc}", kind="InternalError", recoverable=False
                )
            }

        return {
            "content": "".join(content_parts).strip(),
            "thinking": "".join(thinking_parts).strip(),
            "tool_calls": native_calls,
        }

    # ------------------------------------------------------------------
 # ابزارهای داخلی
    # ------------------------------------------------------------------

    def _resolve_tool_mode(self, model: str, schemas: list[dict[str, Any]]) -> str:
        """none (بدون ابزار) | native | fallback — بر اساس قابلیت واقعی مدل."""
        if not schemas:
            return "none"
        try:
            if self.model_manager.supports_native_tools(model):
                return "native"
        except Exception:  # noqa: BLE001 - تصمیم مسیر هرگز نوبت را نباید بکشد
            logger.debug("بررسی قابلیت ابزار مدل ناموفق؛ fallback انتخاب شد", exc_info=True)
        return "fallback"

    def _extract_fallback_calls(self, content: str) -> list[ToolCall]:
        """استخراج فراخوانی JSON از متن پاسخ (مسیر fallback).

        فقط بلوک اول که «شکل درخواست ابزار» را دارد پذیرفته می‌شود؛
        قالب باید دقیقاً {"tool": ..., "args": {...}} باشد (در پروتکل تعریف شده).
        """
        if len(content.strip()) < d.FALLBACK_MIN_JSON_LEN:
            return []
        candidates = extract_json_candidates(content)
        for candidate in candidates:
            parsed, error = try_parse_json(candidate)
            if error is not None or not isinstance(parsed, dict):
                continue
            if "tool" not in parsed:
                continue  # JSON اما نه درخواست ابزار → متن عادی است
            name = parsed.get("tool")
            args = parsed.get("args", {})
            if not isinstance(name, str) or not name.strip():
                continue
            if not isinstance(args, dict):
                args = {}
            return [
                ToolCall(
                    name=name.strip(),
                    args=args,
                    id=None,
                    source="fallback",
                    raw=candidate,
                )
            ]
        return []

    # ------------------------------------------------------------------
    # لغو و کنترل مکالمه
    # ------------------------------------------------------------------

    def request_cancel(self) -> None:
        """لغو نوبت در-حال-اجرا: توکن ست می‌شود، socket مدل بسته می‌شود و
        اجرای ابزار در-جریان هم قطع می‌گردد."""
        if self._cancel is None:
            return
        self._cancel.cancel()
        try:
            self.tool_registry.request_cancel()
        except Exception:  # noqa: BLE001 - لغو نباید هیچ‌وقت خطا تولید کند
            logger.debug("لغو اجرای ابزار با خطا مواجه شد", exc_info=True)
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


def _fallback_pending(tool_name: str, result_text: str) -> ChatMessage:
    """پیام placeholder نتیجه برای مسیر fallback (به‌عنوان آخرین پیام کاربر)."""
    message = ChatMessage.user(tool_result_user_message(tool_name, result_text))
    message.meta = {"tool_name": tool_name, "is_tool_result": True}
    return message
