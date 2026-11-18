"""پیام‌های سیستمی و ساخت پیام‌های مدل.

هویت ایجنت به زبان فارسی/انگلیسی تعریف می‌شود و بخش قابلیت‌ها
(فهرست ابزارها) به‌صورت پویا از Tool Registry ساخته می‌شود (فاز ۲).
"""
from __future__ import annotations

from typing import Sequence

from app.llm.base import ROLE_SYSTEM, ROLE_USER, ChatMessage

IDENTITY_FA = """تو «دستیار شخصی» هستی؛ یک ایجنت هوش مصنوعی محلی که روی سیستم کاربر اجرا می‌شود.
قواعد پاسخ‌گویی:
- پاسخ را به زبان پیام کاربر بده (فارسی یا انگلیسی).
- دقیق، صادق و کاربردی باش. اگر چیزی را نمی‌دانی، صریح بگو؛ هیچ واقعیتی را نساز.
- پاسخ‌ها کوتاه ولی کامل باشند؛ از پرگویی و تکرار پرهیز کن.
- اگر درخواست کاربر مبهم است، بهترین برداشت منطقی را انتخاب کن و لازم نیست مدام سؤال بپرسی."""

IDENTITY_EN = """You are "Personal Agent", a local AI agent running on the user's machine.
Rules:
- Answer in the language of the user's message (Persian or English).
- Be accurate, honest and practical. If you do not know something, say so; never fabricate facts.
- Keep answers concise but complete; avoid filler and repetition.
- If the request is ambiguous, pick the most reasonable interpretation instead of asking repeatedly."""

CAPABILITIES_NONE_FA = (
    "وضعیت فعلی: در این نسخه (فاز ۱) ابزارهای سیستم‌عامل هنوز فعال نیستند؛ "
    "فقط به گفت‌وگو پاسخ بده و به هیچ عملیاتی روی فایل‌ها یا برنامه‌ها ادعا نکن."
)
CAPABILITIES_NONE_EN = (
    "Current status: in this version (Phase 1) operating-system tools are not yet "
    "active; answer conversationally and do not claim to have performed any file or "
    "application operations."
)


def build_system_prompt(language: str = "fa", capabilities: Sequence[str] = ()) -> str:
    """ساخت پیام سیستم از هویت + قابلیت‌های پویا (ابزارها در فاز ۲)."""
    if language == "en":
        parts = [IDENTITY_EN]
        parts.extend(c for c in capabilities if c and c.strip())
        if not capabilities:
            parts.append(CAPABILITIES_NONE_EN)
        return "\n".join(parts)
    parts = [IDENTITY_FA]
    parts.extend(c for c in capabilities if c and c.strip())
    if not capabilities:
        parts.append(CAPABILITIES_NONE_FA)
    return "\n".join(parts)


def build_messages(
    system_prompt: str,
    history: Sequence[ChatMessage],
    user_text: str,
) -> list[ChatMessage]:
    """چیدمان نهایی پیام‌ها: سیستم، تاریخچه، پیام جدید کاربر."""
    messages: list[ChatMessage] = [ChatMessage(ROLE_SYSTEM, system_prompt)]
    messages.extend(history)
    messages.append(ChatMessage(ROLE_USER, user_text))
    return messages
