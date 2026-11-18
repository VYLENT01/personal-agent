"""وضعیت مکالمه و مدیریت Context.

حافظه کوتاه‌مدت درون-برنامه: پیام‌های مکالمه فعلی نگهداری و
بر اساس سقف پیام/کاراکتر، پنجره‌ای از آخرین پیام‌ها ساخته می‌شود.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional
from uuid import uuid4

from app.llm.base import ROLE_ASSISTANT, ROLE_USER, ChatMessage


@dataclass
class ConversationState:
    """پیام‌های مکالمه فعلی به همراه شناسه و زمان شروع."""

    messages: list[ChatMessage] = field(default_factory=list)
    conversation_id: str = field(default_factory=lambda: uuid4().hex)
    created_at: float = field(default_factory=time.time)

    # ---------- افزودن پیام ----------

    def append_user(self, content: str) -> ChatMessage:
        message = ChatMessage.user(content)
        self.messages.append(message)
        return message

    def append_assistant(
        self, content: str, *, thinking: str = "", **meta: Any
    ) -> ChatMessage:
        message = ChatMessage.assistant(content)
        message.meta = {"thinking": thinking, **meta}
        self.messages.append(message)
        return message

    # ---------- پنجره Context ----------

    def window(self, max_messages: int, max_chars: int) -> list[ChatMessage]:
        """آخرین پیام‌ها تا سقف تعداد و حجم؛ ترتیب زمانی حفظ می‌شود.

        پیام سیستم جداگانه توسط orchestrator اضافه می‌شود و در این سقف نمی‌گیرد.
        """
        selected: list[ChatMessage] = []
        total = 0
        for message in reversed(self.messages):
            if len(selected) >= max_messages or total >= max_chars:
                break
            if total + len(message.content) > max_chars and selected:
                break  # این پیام کل سقف را رد می‌کند؛ بدون آن ادامه می‌دهیم
            selected.append(message)
            total += len(message.content)
        selected.reverse()
        return selected

    # ---------- ابزارهای دسترسی ----------

    @property
    def last_assistant(self) -> Optional[ChatMessage]:
        for message in reversed(self.messages):
            if message.role == ROLE_ASSISTANT:
                return message
        return None

    @property
    def last_user(self) -> Optional[ChatMessage]:
        for message in reversed(self.messages):
            if message.role == ROLE_USER:
                return message
        return None

    def stats(self) -> dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "message_count": len(self.messages),
            "total_chars": sum(len(m.content) for m in self.messages),
            "created_at": self.created_at,
        }

    def clear(self) -> None:
        """شروع گفت‌وگوی جدید (شناسه جدید)."""
        self.messages.clear()
        self.conversation_id = uuid4().hex
        self.created_at = time.time()
