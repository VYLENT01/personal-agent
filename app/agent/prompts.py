"""پیام‌های سیستمی و ساخت پیام‌های مدل.

هویت ایجنت به زبان فارسی/انگلیسی تعریف می‌شود و بخش قابلیت‌ها
(فهرست ابزارها) به‌صورت پویا از Tool Registry ساخته می‌شود (فاز ۲).

دو حالت ابزار وجود دارد:
- native: مدل قابلیت Tool Calling بومی دارد؛ فقط هدایت کوتاه (نه آموزش پروتکل)
- fallback: مدل بدون قابلیت بومی؛ پروتکل JSON صریح در پیام سیستم تعریف می‌شود
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
    "وضعیت فعلی: در این نسخه هیچ ابزاری فعال نیست؛ فقط به گفت‌وگو پاسخ بده و "
    "به هیچ عملیاتی روی فایل‌ها یا برنامه‌ها ادعا نکن."
)
CAPABILITIES_NONE_EN = (
    "Current status: no tools are active in this version; answer conversationally "
    "and do not claim to have performed any file or application operations."
)

# --- هدایت حالت native (Tool Calling بومی) ---
TOOLS_NATIVE_FA = """استفاده از ابزارها:
- وقتی ابزار مناسبِ درخواست کاربر وجود دارد، آن را صدا بزن؛ اطلاعات واقعی سیستم را از ابزار بگیر.
- هر فراخوانی فقط با نام و پارامترهای تعریف‌شدهٔ همان ابزار.
- بعد از دریافت نتیجه ابزار، پاسخ نهایی را با همان داده‌های واقعی بنویس.
- اگر ابزار مناسبی نیست، مستقیم پاسخ متنی بده؛ ابزار را بی‌دلیل فراخوانی نکن."""

TOOLS_NATIVE_EN = """Tool usage:
- When a listed tool fits the user's request, call it; take real system data from tools.
- Each call must use exactly that tool's name and defined parameters.
- After receiving a tool result, write the final answer using that real data.
- If no tool fits, answer in plain text; do not call tools without reason."""

# --- پروتکل fallback (مدل بدون قابلیت بومی) ---
TOOLS_FALLBACK_FA = """پروتکل فراخوانی ابزار (این مدل از Tool Calling بومی پشتیبانی نمی‌کند؛ پس این پروتکل را دقیق رعایت کن):
برای استفاده از ابزار، پاسخ خود را «فقط» با این قالب بده و هیچ متن دیگری اضافه نکن:
```json
{"tool": "<نام دقیق ابزار>", "args": {<پارامترها یا {}>}}
```
قواعد:
- نام ابزار را فقط از فهرست مجاز انتخاب کن؛ نام دیگری نساز.
- args باید یک شیء JSON باشد؛ اگر ابزار پارامتری ندارد، {} بگذار.
- پس از دریافت نتیجه ابزار (به‌عنوان پیام کاربر با پیشوند «[نتیجه ابزار]»)، پاسخ نهایی را با داده‌های واقعی بنویس.
- اگر هیچ ابزاری مناسب نیست، به‌طور عادی به فارسی/انگلیسی پاسخ بده و بلوک json نفرست.
- در هر پیام فقط یک فراخوانی ابزار بفرست."""

TOOLS_FALLBACK_EN = """Tool-call protocol (this model has no native tool calling; follow exactly):
To use a tool, reply with ONLY this format and no other text:
```json
{"tool": "<exact tool name>", "args": {<parameters or {}>}}
```
Rules:
- Pick the tool name only from the allowed list; never invent names.
- args must be a JSON object; use {} when the tool takes no parameters.
- After receiving the tool result (as a user message prefixed "[TOOL RESULT]"), write the final answer using the real data.
- If no tool fits, answer normally in plain text and send no json block.
- Send at most one tool call per message."""


def build_system_prompt(
    language: str = "fa",
    capabilities: Sequence[str] = (),
    *,
    tool_mode: str = "none",  # "none" | "native" | "fallback"
) -> str:
    """ساخت پیام سیستم از هویت + قابلیت‌های پویا + هدایت/پروتکل ابزار.

    tool_mode تعیین می‌کند بلوک هدایت ابزار کدام باشد؛ capabilities
    خطوط ابزار (از Registry.capabilities_prompt) است.
    """
    fa = language != "en"
    parts = [IDENTITY_FA if fa else IDENTITY_EN]
    capability_lines = [c for c in capabilities if c and c.strip()]

    if not capability_lines or tool_mode == "none":
        parts.append(CAPABILITIES_NONE_FA if fa else CAPABILITIES_NONE_EN)
        return "\n".join(parts)

    parts.extend(capability_lines)
    if tool_mode == "native":
        parts.append(TOOLS_NATIVE_FA if fa else TOOLS_NATIVE_EN)
    elif tool_mode == "fallback":
        parts.append(TOOLS_FALLBACK_FA if fa else TOOLS_FALLBACK_EN)
    return "\n".join(parts)


def build_messages(
    system_prompt: str,
    history: Sequence[ChatMessage],
    user_text: str,
) -> list[ChatMessage]:
    """چیدمان نهایی پیام‌ها: سیستم، تاریخچه، پیام جدید کاربر.

    پیام کاربر نباید هم در تاریخچه باشد (رفع دوبار فرستادن)؛
    فراخواننده مسئول است که پنجره Context را قبل از append بگیرد.
    """
    messages: list[ChatMessage] = [ChatMessage(ROLE_SYSTEM, system_prompt)]
    messages.extend(history)
    messages.append(ChatMessage(ROLE_USER, user_text))
    return messages


TOOL_RESULT_PREFIX_FA = "[نتیجه ابزار]"
TOOL_RESULT_PREFIX_EN = "[TOOL RESULT]"


def tool_result_user_message(tool_name: str, result_text: str, language: str = "fa") -> str:
    """قالب پیام نتیجه ابزار در مسیر fallback (به‌عنوان پیام کاربر)."""
    prefix = TOOL_RESULT_PREFIX_FA if language != "en" else TOOL_RESULT_PREFIX_EN
    return f"{prefix} {tool_name}\n{result_text}"
