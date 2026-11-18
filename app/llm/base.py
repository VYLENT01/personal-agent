"""قرارداد مشترک موتور مدل زبانی (LLM Engine Interface).

Agent Core فقط با این واسط کار می‌کند. هر بک‌اند جدید (Ollama،
LLM.cpp، سرور API داخلی و...) فقط کافی است این قرارداد را پیاده کند.

اجزای قرارداد:
- ChatMessage / StreamChunk / LLMResponse: مدل‌های داده پیام و پاسخ
- CancelToken: لغو درخواست از سمت کاربر (thread-safe)
- خطاهای استاندارد: LLMConnectionError / LLMTimeoutError / LLMResponseError / LLMCancelledError
- BaseLLMClient: واسط کلاینت (chat، chat_stream، list_models، is_available، cancel_active)
"""
from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Iterator, Mapping, Optional, Sequence

# --- نقش‌های پیام ---
ROLE_SYSTEM = "system"
ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"
ROLE_TOOL = "tool"


@dataclass
class ChatMessage:
    """یک پیام مکالمه در قالب مدل-ناشناخته (backend agnostic)."""

    role: str
    content: str
    meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def system(cls, content: str) -> "ChatMessage":
        return cls(role=ROLE_SYSTEM, content=content)

    @classmethod
    def user(cls, content: str) -> "ChatMessage":
        return cls(role=ROLE_USER, content=content)

    @classmethod
    def assistant(cls, content: str) -> "ChatMessage":
        return cls(role=ROLE_ASSISTANT, content=content)

    def to_api(self) -> dict[str, str]:
        """تبدیل به فریم wire مربوط به API مدل."""
        return {"role": self.role, "content": self.content}


@dataclass
class ModelInfo:
    """اطلاعات یک مدل نصب‌شده."""

    name: str
    size_bytes: Optional[int] = None
    modified_at: Optional[str] = None
    digest: Optional[str] = None
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def parameter_size(self) -> Optional[str]:
        value = self.details.get("parameter_size")
        return str(value) if value else None

    @property
    def size_gb(self) -> Optional[float]:
        if self.size_bytes:
            return round(self.size_bytes / (1024**3), 2)
        return None


@dataclass
class StreamChunk:
    """یک قطعه از پاسخ استریم."""

    content: str = ""                 # قطعه متن (ممکن است شامل تگ thinking خام باشد)
    done: bool = False                # پایان استریم
    done_reason: Optional[str] = None # stop / length / ...
    meta: dict[str, Any] = field(default_factory=dict)  # آمار پایانی (eval_count و...)


@dataclass
class LLMResponse:
    """پاسخ کامل یک فراخوانی غیر-استریم."""

    content: str = ""                 # محتوای نهایی بدون بخش thinking
    thinking: str = ""                 # بخش تفکر مدل (تگ think) در صورت وجود
    model: Optional[str] = None
    done_reason: Optional[str] = None
    prompt_eval_count: Optional[int] = None
    eval_count: Optional[int] = None
    total_duration_ns: Optional[int] = None
    meta: dict[str, Any] = field(default_factory=dict)


# --- خطاهای استاندارد موتور LLM ---

class LLMError(Exception):
    """خطای پایه؛ همه خطاهای موتور LLM از این کلاس مشتق می‌شوند."""


class LLMConnectionError(LLMError):
    """اتصال به سرویس مدل (Ollama) برقرار نشد یا حین پاسخ قطع شد."""


class LLMTimeoutError(LLMError):
    """زمان انتظار پاسخ مدل به پایان رسید."""


class LLMResponseError(LLMError):
    """پاسخ دریافتی از مدل نامعتبر است (خرابی ساختار / خطای گزارش‌شده مدل)."""


class LLMCancelledError(LLMError):
    """درخواست به‌دستور کاربر لغو شد."""


class CancelToken:
    """توکن لغو thread-safe؛ بین UI و کارگر پردازش به اشتراک گذاشته می‌شود."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def reset(self) -> None:
        self._event.clear()


class BaseLLMClient(ABC):
    """واسط مشترک همه کلاینت‌های مدل زبانی.

    پیاده‌سازی‌ها باید thread-safe باشند: chat_stream ممکن است از یک thread
    کارگری خوانده شود و هم‌زمان cancel_active از thread رابط کاربری فراخوانی شود.
    """

    @abstractmethod
    def chat(
        self,
        messages: Sequence[ChatMessage],
        model: Optional[str] = None,
        *,
        options: Optional[Mapping[str, Any]] = None,
        cancel: Optional[CancelToken] = None,
    ) -> LLMResponse:
        """ارسال پیام‌ها و دریافت پاسخ کامل (غیر-استریم)."""

    @abstractmethod
    def chat_stream(
        self,
        messages: Sequence[ChatMessage],
        model: Optional[str] = None,
        *,
        options: Optional[Mapping[str, Any]] = None,
        cancel: Optional[CancelToken] = None,
    ) -> Iterator[StreamChunk]:
        """ارسال پیام‌ها و دریافت پاسخ به‌صورت استریم (قطعه‌قطعه)."""

    @abstractmethod
    def list_models(self) -> list[ModelInfo]:
        """فهرست مدل‌های نصب‌شده. در صورت قطع سرویس LLMConnectionError می‌دهد."""

    @abstractmethod
    def is_available(self) -> tuple[bool, str]:
        """بررسی در دسترس بودن سرویس؛ خروجی: (در دسترس است؟، توضیح)."""

    @abstractmethod
    def cancel_active(self) -> int:
        """بستن جریان‌های در-حال-اجرا؛ تعداد جریان‌های بسته‌شده را برمی‌گرداند."""

    def close(self) -> None:
        """آزادسازی منابع (پیش‌فرض: هیچ کاری لازم نیست)."""
