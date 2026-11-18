"""تجزیه و اعتبارسنجی خروجی مدل.

مدل‌های محلی گاهی خروجی نامرتب تولید می‌کنند؛ این ماژول وظیفه دارد:
- تفکیک بخش thinking (تگ <think>) از محتوای پاسخ — هم پس از اتمام و هم حین استریم
- استخراج بلوک‌های JSON از متن پاسخ (زیرساخت Tool Calling فاز ۲)
- ساخت پیام درخواست اصلاح خروجی نامعتبر (طبق بند ۷ مشخصات)
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

THINK_OPEN = "<" + "think" + ">"
THINK_CLOSE = "</" + "think" + ">"

_THINK_BLOCK = re.compile(re.escape(THINK_OPEN) + r"(.*?)" + re.escape(THINK_CLOSE), re.DOTALL)
_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


# ---------------------------------------------------------------------------
# تفکیک thinking از content
# ---------------------------------------------------------------------------

def extract_content(raw: Optional[str]) -> tuple[str, str]:
    """تفکیک متن خام مدل به (content, thinking).

    حالت‌های پوشش‌داده‌شده:
    - بدون تگ think → همان متن
    - بلوک‌های think کامل (حتی چندتایی)
    - بلوک think باز مانده (استریم قطع شده) → باقی متن به‌عنوان thinking
    """
    if not raw:
        return "", ""

    thinking_parts: list[str] = []

    def _collect(match: "re.Match[str]") -> str:
        thinking_parts.append(match.group(1))
        return ""

    text = _THINK_BLOCK.sub(_collect, raw)

    # بلوک باز مانده (مثلاً لغو استریم حین تفکر)
    open_index = text.find(THINK_OPEN)
    if open_index != -1:
        thinking_parts.append(text[open_index + len(THINK_OPEN):])
        text = text[:open_index]

    thinking = "\n".join(part.strip() for part in thinking_parts if part.strip()).strip()
    return text.strip(), thinking


class StreamThinkSplitter:
    """تفکیک زندهٔ بخش thinking از محتوای پاسخ حین استریم.

    چون Ollama متن thinking را داخل همان content می‌فرستد، این جداکننده
    قطعات را به دو کانال (محتوا / تفکر) هدایت می‌کند تا UI بتواند
    تفکر مدل را زنده و جدا نمایش دهد. مرز تگ ناقص بین دو قطعه هم مدیریت می‌شود.
    """

    def __init__(self) -> None:
        self._buffer = ""
        self._in_think = False

    def feed(self, delta: str) -> tuple[str, str]:
        """دریافت یک قطعه؛ خروجی: (محتوای آماده نمایش، تفکر آماده نمایش)."""
        self._buffer += delta
        content_parts: list[str] = []
        think_parts: list[str] = []

        while self._buffer:
            tag = THINK_CLOSE if self._in_think else THINK_OPEN
            index = self._buffer.find(tag)
            if index == -1:
                # انتهای بافر ممکن است شروعِ ناقصِ تگ باشد؛ آن را نگه می‌داریم
                tail = self._partial_tag_tail(tag)
                emit_len = len(self._buffer) - tail
                if emit_len > 0:
                    emit = self._buffer[:emit_len]
                    self._buffer = self._buffer[emit_len:]
                    (think_parts if self._in_think else content_parts).append(emit)
                break
            if index:
                before = self._buffer[:index]
                (think_parts if self._in_think else content_parts).append(before)
            self._buffer = self._buffer[index + len(tag):]
            self._in_think = not self._in_think

        return "".join(content_parts), "".join(think_parts)

    def flush(self) -> tuple[str, str]:
        """پایان استریم؛ هرچه در بافر مانده به کانال فعلی می‌ریزد."""
        rest, self._buffer = self._buffer, ""
        if not rest:
            return "", ""
        if self._in_think:
            return "", rest
        return rest, ""

    def _partial_tag_tail(self, tag: str) -> int:
        """طول پسوند انتهای بافر که می‌تواند پیشوند ناقص تگ باشد."""
        max_check = min(len(self._buffer), len(tag) - 1)
        for size in range(max_check, 0, -1):
            if tag.startswith(self._buffer[-size:]):
                return size
        return 0


# ---------------------------------------------------------------------------
# استخراج JSON (زیرساخت Tool Calling فاز ۲)
# ---------------------------------------------------------------------------

def try_parse_json(text: str) -> tuple[Optional[Any], Optional[str]]:
    """تلاش برای تفسیر یک رشته به JSON؛ خروجی: (شیء، خطا)."""
    stripped = text.strip()
    if not stripped:
        return None, "متن خالی است"
    try:
        return json.loads(stripped), None
    except json.JSONDecodeError as exc:
        return None, str(exc)


def extract_json_candidates(text: str) -> list[str]:
    """جمع‌آوری نامزدهای JSON از متن پاسخ، به‌ترتیب اولویت.

    ۱) بلوک‌های fenced (```json ... ```)
    ۲) اشیاء/آرایه‌های متوازن‌شده در سطح بالای متن (رشته‌ها در نظر گرفته می‌شوند)
    """
    candidates: list[str] = []
    if not text:
        return candidates

    for match in _JSON_FENCE.finditer(text):
        block = match.group(1).strip()
        if block:
            candidates.append(block)

    for chunk, is_complete, in_string in _scan_balanced(text):
        if is_complete and not in_string and chunk.strip():
            stripped = chunk.strip()
            if stripped not in candidates:
                candidates.append(stripped)
    return candidates


def _scan_balanced(text: str):
    """تولید قطعات متوازن {} یا [] در سطح بالای متن.

    برای هر قطعه: (متن قطعه کامل یا ناقص، آیا متوازن است؟، داخل رشته است؟)
    """
    stack: list[tuple[str, int]] = []  # (کاراکتر باز، شاخص شروع)
    in_string = False
    escape = False
    for index, char in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "{[":
            stack.append((char, index))
        elif char in "}]":
            if stack and (stack[-1][0] == "{" and char == "}" or stack[-1][0] == "[" and char == "]"):
                start = stack.pop()[1]
                if not stack:
                    yield text[start:index + 1], True, in_string
            # پرانتز نامتوازن را نادیده می‌گیریم (متون عادی)
    # قطعه ناقص باقی‌مانده (استریم ناتمام) برای مصرف‌کننده در صورت نیاز
    if stack:
        start = stack[0][1]
        yield text[start:], False, in_string


def build_repair_message(error: str, invalid_output: str, schema_hint: str = "") -> str:
    """پیام «اصلاح خروجی» که به مدل داده می‌شود تا پاسخ نامعتبر را درست کند.

    طبق بند ۷ مشخصات: اگر مدل پاسخ نامعتبر تولید کرد، سیستم باید آن را
    اعتبارسنجی کند و در صورت امکان درخواست اصلاح بدهد.
    """
    snippet = invalid_output.strip()
    if len(snippet) > 400:
        snippet = snippet[:400] + "…"
    hint = f"\nالگوی مورد انتظار:\n{schema_hint.strip()}" if schema_hint.strip() else ""
    return (
        "خروجی قبلی تو معتبر نبود و قابل تفسیر نشد.\n"
        f"خطای اعتبارسنجی: {error}\n"
        f"خروجی نامعتبر:\n{snippet}\n"
        "لطفاً پاسخ را دوباره تولید کن؛ این بار فقط یک شیء JSON معتبر و بدون متن اضافه بنویس"
        f" و همه فیلدهای لازم را کامل پر کن.{hint}"
    )
