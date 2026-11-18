"""PermissionManager + ApprovalToken — حداقل حفاظت فاز ۳ (بخش ۸).

قواعد پیاده‌شده:
- L1: اجرای مستقل
- L2: اجرا فقط با ApprovalToken یک‌بارمصرف
- L3: رد همیشگی (در Registry سطح سخت اعمال می‌شود)

خواص ApprovalToken (الزام بخش ۸):
- به «نام ابزار + پارامترهای دقیق» گره می‌خورد؛ تغییر پارامتر → تأیید بی‌اعتبار
- یک‌بارمصرف: مصرف دوم با ConsumptionError رد می‌شود
- مدل هرگز نمی‌تواند توکن بسازد؛ فقط UI (کاربر) از طریق PermissionManager
- انقضا: هر توکن مهلت APPROVAL_TIMEOUT_SECONDS دارد

جریان (بخش ۸): orchestrator برای ابزار L2 رویداد ApprovalRequested می‌دهد؛
UI دیالوگ تأیید نشان می‌دهد؛ در صورت تأیید، UI برای همان (نام+args) توکن
می‌سازد و اجرا ادامه می‌یابد؛ در غیر این صورت عملیات رد می‌شود.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from typing import Any, Optional

from app.config import defaults as d

logger = logging.getLogger(__name__)


class ApprovalError(Exception):
    """بی‌اعتباری یا نبودِ تأیید؛ پیام فارسی برای مدل/کاربر."""


def canonicalize(tool_name: str, args: dict[str, Any]) -> str:
    """نسخه کانونی پارامترها؛ تغییر هر مقدار/کلید = رشتهٔ متفاوت.

    sort_keys + ensure_ascii=False؛ ورودی مدل مستقیماً وارد shell نمی‌شود
    (برای ترمینال از همین نسخه کانونی برای قفل تأیید استفاده می‌شود).
    """
    try:
        return json.dumps(
            {"tool": str(tool_name), "args": args},
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
    except (TypeError, ValueError):  # pragma: no cover - dict غیرقابل serialize
        return f"{tool_name}:{args!r}"


def fingerprint(tool_name: str, args: dict[str, Any]) -> str:
    """انگشت‌دست تأیید: هش SHA-256 از نسخه کانونی (نام ابزار + پارامترها)."""
    return hashlib.sha256(canonicalize(tool_name, args).encode("utf-8")).hexdigest()


class ApprovalToken:
    """تأیید یک‌بارمصرف کاربر برای یک عملیات L2 مشخص."""

    __slots__ = ("_fingerprint", "_created", "_ttl", "_consumed", "_lock")

    def __init__(self, fingerprint_value: str, ttl: float) -> None:
        self._fingerprint = fingerprint_value
        self._created = time.monotonic()
        self._ttl = ttl
        self._consumed = False
        self._lock = threading.Lock()

    @property
    def fingerprint(self) -> str:
        return self._fingerprint

    def _expired(self) -> bool:
        return (time.monotonic() - self._created) > self._ttl

    def consume(self, tool_name: str, args: dict[str, Any]) -> None:
        """مصرف توکن برای همان عملیات تأییدشده؛ رد در صورت عدم تطابق/انقضا/مصرف."""
        with self._lock:
            if self._consumed:
                raise ApprovalError("این تأیید قبلاً استفاده شده است؛ دوباره تأیید بگیرید.")
            if self._expired():
                raise ApprovalError("مهلت تأیید منقضی شده است؛ دوباره تأیید بگیرید.")
            expected = fingerprint(tool_name, args)
            if expected != self._fingerprint:
                raise ApprovalError(
                    "پارامترهای عملیات پس از تأیید تغییر کرده‌اند؛ تأیید قبلی معتبر نیست."
                )
            self._consumed = True


class PermissionManager:
    """سازنده و داور توکن‌های تأیید؛ بدون وابستگی به UI."""

    def __init__(self, approval_ttl: float = d.APPROVAL_TIMEOUT_SECONDS) -> None:
        self._ttl = float(approval_ttl)

    def create_token(self, tool_name: str, args: dict[str, Any]) -> ApprovalToken:
        """ساخت توکن برای عملیات مشخص — فقط UI/کاربر این را صدا می‌زند.

        مدل زبانی به این متد دسترسی ندارد؛ orchestrator توکن را فقط
        از پاسخ کاربر می‌گیرد (الزام «تأیید به نام ابزار و پارامترهای دقیق
        همان درخواست متصل باشد»).
        """
        token = ApprovalToken(fingerprint(tool_name, args), self._ttl)
        logger.info("تأیید کاربر برای %s ثبت شد", tool_name)
        return token

    @staticmethod
    def verify(
        token: Optional[ApprovalToken], tool_name: str, args: dict[str, Any]
    ) -> bool:
        """آیا توکنِ داده‌شده برای این عملیات معتبر است؟ (مصرف‌کننده نیست).

        خواندنِ وضعیت بدون قفلِ مصرف؛ فقط ذی‌نفعِ همان fingerprint جواب True می‌گیرد.
        """
        if token is None:
            return False
        try:
            expected = fingerprint(tool_name, args)
        except (TypeError, ValueError):  # pragma: no cover
            return False
        return (
            token.fingerprint == expected
            and not token._expired()  # noqa: SLF001 - داور داخلی
            and not token._consumed  # noqa: SLF001 - داور داخلی
        )

    @staticmethod
    def describe_request(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        """شرح قابل‌نمایش درخواست تأیید برای UI (فارسی/RTL-safe)."""
        return {
            "tool": tool_name,
            "args": args,
            "fingerprint": fingerprint(tool_name, args),
        }


__all__ = [
    "ApprovalError",
    "ApprovalToken",
    "PermissionManager",
    "canonicalize",
    "fingerprint",
]
