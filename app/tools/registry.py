"""Registry مرکزی ابزارها.

قابلیت‌ها (بخش دوم مشخصات):
- register_tool / unregister_tool / get_tool / list_tools
- get_tool_schemas → فریم wire برای پارامتر tools مدل
- validate_tool_call → اعتبارسنجی مستقل از LLM
- execute_tool → اجرای کنترل‌شده با timeout، مجوز و نگاشت خطا

ضمانت‌ها:
- ابزار تکراری ثبت نمی‌شود (ValueError با نام ابزار موجود)
- ابزار ثبت‌نشده اجرا نمی‌شود (ToolNotFound)
- ابزار غیرفعال/غیرمجاز (L2/L3 در این فاز) اجرا نمی‌شود (PermissionDenied)
- مدل فقط ابزارهای فعالِ مجاز را می‌بیند (get_tool_schemas فیلتر می‌کند)
"""
from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any, Optional

from app.config import defaults as d
from app.tools.base import (
    PermissionLevel,
    Tool,
    ToolResult,
    ToolTimeoutError,
)

if TYPE_CHECKING:  # pragma: no cover - فقط تایپ
    from app.llm.base import CancelToken

logger = logging.getLogger(__name__)


class ToolNotFoundError(Exception):
    """ابزار در Registry ثبت نشده است."""


class DuplicateToolError(Exception):
    """ابزاری با همین نام قبلاً ثبت شده است."""


class PermissionDeniedError(Exception):
    """سطح دسترسی ابزار در این نسخه مجاز نیست (L2/L3 نیاز به فاز ۵ دارند)."""


class ToolRegistry:
    """ثبت مرکزی ابزارها؛ thread-safe (فراخوانی از UI و worker)."""

    def __init__(
        self,
        *,
        max_permission: PermissionLevel = PermissionLevel.L1_READ_ONLY,
        tool_timeout: float = d.TOOL_TIMEOUT_SECONDS,
    ) -> None:
        self._tools: dict[str, Tool] = {}
        self._enabled: dict[str, bool] = {}
        self._lock = threading.RLock()
        self._max_permission = max_permission
        self._tool_timeout = tool_timeout
        self._stats = {"executed": 0, "failed": 0, "rejected": 0}
        # توکن لغو جاری: در حین اجرای ابزار، request_cancel آن را فعال می‌کند
        self._active_cancel: Optional["CancelToken"] = None

    # ------------------------------------------------------------------
    # لغو اجرای در-جریان
    # ------------------------------------------------------------------

    def bind_cancel_token(self, token: "CancelToken") -> None:
        """اتصال توکن لغو نوبت جاری؛ orchestrator در شروع هر نوبت صدا می‌زند."""
        self._active_cancel = token

    def request_cancel(self) -> None:
        """لغو اجرای در-جریان: توکن فعال ست می‌شود و اجرای ابزار قطع می‌گردد."""
        token = self._active_cancel
        if token is not None:
            token.cancel()

    # ------------------------------------------------------------------
    # ثبت / حذف
    # ------------------------------------------------------------------

    def register_tool(self, tool: Tool, *, enabled: bool = True) -> Tool:
        """ثبت ابزار؛ نام تکراری رد می‌شود. نام باید «گروه.عمل» باشد."""
        if not isinstance(tool, Tool):
            raise TypeError("فقط نمونه‌های Tool قابل ثبت هستند.")
        name = tool.name
        if not name or "." not in name:
            raise ValueError(f"نام ابزار باید قالب «گروه.عمل» داشته باشد: {name!r}")
        with self._lock:
            if name in self._tools:
                raise DuplicateToolError(
                    f"ابزار «{name}» قبلاً ثبت شده است؛ ثبت تکراری مجاز نیست."
                )
            self._tools[name] = tool
            self._enabled[name] = enabled
        logger.debug("ابزار ثبت شد: %s (سطح %s)", name, int(tool.permission_level))
        return tool

    def unregister_tool(self, name: str) -> bool:
        """حذف ابزار؛ True اگر موجود بود و حذف شد."""
        with self._lock:
            removed = self._tools.pop(name, None) is not None
            self._enabled.pop(name, None)
        return removed

    def set_enabled(self, name: str, enabled: bool) -> None:
        """فعال/غیرفعال‌سازی بدون حذف (مدل ابزار غیرفعال را نمی‌بیند)."""
        with self._lock:
            if name not in self._tools:
                raise ToolNotFoundError(f"ابزار «{name}» ثبت نشده است.")
            self._enabled[name] = enabled

    # ------------------------------------------------------------------
    # جست‌وجو
    # ------------------------------------------------------------------

    def get_tool(self, name: str) -> Optional[Tool]:
        with self._lock:
            return self._tools.get(name)

    def list_tools(self, *, only_enabled: bool = True) -> list[Tool]:
        with self._lock:
            items = list(self._tools.values())
            if only_enabled:
                items = [t for t in items if self._enabled.get(t.name, True)]
            return items

    def tool_names(self, *, only_enabled: bool = True) -> list[str]:
        return [tool.name for tool in self.list_tools(only_enabled=only_enabled)]

    def metadata(self) -> list[dict[str, Any]]:
        """متادیتای همه ابزارها برای داشبورد UI."""
        with self._lock:
            return [
                {**tool.metadata, "enabled": self._enabled.get(tool.name, True)}
                for tool in self._tools.values()
            ]

    def stats(self) -> dict[str, int]:
        with self._lock:
            return dict(self._stats)

    # ------------------------------------------------------------------
    # فریم مدل
    # ------------------------------------------------------------------

    def get_tool_schemas(
        self, *, only_enabled: bool = True, limit: int = d.MAX_TOOLS_IN_PROMPT
    ) -> list[dict[str, Any]]:
        """فریم wire ابزارها برای پارامتر tools.

        فقط ابزارهای فعالِ با سطح مجاز این نسخه (L1) دیده می‌شوند؛
        سقف limit از num_ctx محافظت می‌کند.
        """
        schemas: list[dict[str, Any]] = []
        for tool in self.list_tools(only_enabled=only_enabled):
            if tool.permission_level > self._max_permission:
                continue
            schemas.append(tool.to_ollama_tool())
            if len(schemas) >= limit:
                break
        return schemas

    def capabilities_prompt(self, language: str = "fa") -> list[str]:
        """بلوک‌های قابلیت برای پیام سیستمی (فهرست ابزار برای مدل)."""
        lines: list[str] = []
        for tool in self.list_tools(only_enabled=True):
            if tool.permission_level > self._max_permission:
                continue
            params = ", ".join(tool.input_schema().get("properties", {}).keys())
            signature = f"- {tool.name}({params})"
            lines.append(f"{signature}: {tool.description}")
        if not lines:
            return []
        if language == "en":
            return ["Available tools (use tool_calls; never invent a tool):", *lines]
        return ["ابزارهای در دسترس (فقط همین‌ها؛ ابزار دیگری نساز):", *lines]

    # ------------------------------------------------------------------
    # اعتبارسنجی / اجرا
    # ------------------------------------------------------------------

    def validate_tool_call(
        self, name: str, args: Optional[dict[str, Any]]
    ) -> tuple[bool, Optional[Tool], Optional[str]]:
        """بررسی وجود + فعال بودن + مجوز + اعتبارسنجی پارامترها (مستقل از LLM).

        خروجی: (قابل اجراست؟، ابزار، خطای فارسی)
        """
        if not name or not isinstance(name, str):
            return False, None, "نام ابزار نامعتبر است."
        with self._lock:
            tool = self._tools.get(name)
            enabled = self._enabled.get(name, False)
        if tool is None:
            self._bump("rejected")
            return False, None, f"ابزار «{name}» ثبت نشده است."
        if not enabled:
            self._bump("rejected")
            return False, None, f"ابزار «{name}» غیرفعال است."
        if tool.permission_level > self._max_permission:
            self._bump("rejected")
            return False, None, (
                f"ابزار «{name}» سطح دسترسی {int(tool.permission_level)} دارد که "
                "در این نسخه مجاز نیست."
            )
        valid, parsed, error = tool.validate(args or {})
        if not valid:
            self._bump("rejected")
            return False, tool, error
        return True, tool, None

    def execute_tool(self, name: str, args: Optional[dict[str, Any]]) -> ToolResult:
        """اجرای کامل کنترل‌شده: اعتبارسنجی → مجوز → timeout → ToolResult.

        هر شکست (وجود/مجوز/اعتبارسنجی/timeout/خطای داخلی) به ToolResult
        ساختاریافته با پیام فارسی تبدیل می‌شود؛ exception بیرون نمی‌رود.
        """
        ready, tool, error = self.validate_tool_call(name, args)
        if not ready:
            # ابزار ناموجود → نام معتبر برای گزارش
            return ToolResult.fail(name, error or "اجرا ممکن نیست.")

        assert tool is not None  # برای type-checker؛ ready=True تضمینش می‌کند
        if tool.permission_level > self._max_permission:  # pragma: no cover - گارد مضاعف
            return ToolResult.fail(name, "سطح دسترسی مجاز نیست.")

        result = self._run_with_timeout(tool, args or {})
        self._bump("executed" if result.success else "failed")
        return result

    def _run_with_timeout(self, tool: Tool, args: dict[str, Any]) -> ToolResult:
        """اجرای ابزار در thread جدا با سقف زمان + پایش لغو.

        - فراتر از مهلت → ToolResult شکست با پیام Timeout
        - لغو کاربر حین اجرا → اجرا رها می‌شود و شکست «لغو شد» برمی‌گردد؛
          thread ابزار در پس‌زمینه تمام می‌شود (daemon) و caller بلاک نمی‌ماند.
        """
        import concurrent.futures

        # بدون with: خروج از context-manager تا اتمام thread بلاک می‌شود و
        # timeout واقعی را بی‌اثار می‌کند؛ shutdown(wait=False) فوری برمی‌گردد.
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = executor.submit(tool.execute, args)
        token = self._active_cancel
        try:
            if token is not None:
                # result با انتظارِ قابل‌قطع: اگر لغو شد زودتر از timeout برمی‌گردیم
                deadline = self._tool_timeout
                interval = 0.05
                waited = 0.0
                while waited < deadline:
                    if token.is_cancelled():
                        raise concurrent.futures.TimeoutError  # مسیر لغو پایین
                    try:
                        return future.result(timeout=interval)
                    except concurrent.futures.TimeoutError:
                        waited += interval
                        continue
                raise concurrent.futures.TimeoutError
            return future.result(timeout=self._tool_timeout)
        except concurrent.futures.TimeoutError:
            reason = "لغو شد" if (token is not None and token.is_cancelled()) else "Timeout"
            future.cancel()
            if token is not None and token.is_cancelled():
                return ToolResult.fail(tool.name, "اجرای ابزار به‌دستور شما لغو شد.", 0.0)
            return ToolResult.fail(
                tool.name,
                f"اجرای ابزار از مهلت {self._tool_timeout:.0f} ثانیه فراتر رفت (Timeout).",
                self._tool_timeout,
            )
        except ToolTimeoutError:
            return ToolResult.fail(tool.name, "اجرای ابزار به زمان مقرر نرسید.", self._tool_timeout)
        finally:
            # daemon-thread executor؛ thread در پس‌زمینه تمام می‌شود و در شات‌داون
            # برنامه، ماژول concurrent خودش پاک‌سازی می‌کند.
            executor.shutdown(wait=False)

    def _bump(self, key: str) -> None:
        with self._lock:
            self._stats[key] = self._stats.get(key, 0) + 1

    def __len__(self) -> int:
        with self._lock:
            return len(self._tools)


def build_default_registry() -> ToolRegistry:
    """Registry آماده با سه ابزار فاز ۲؛ نقطه واحد ساخت برای bootstrap."""
    from app.tools.system_tools import create_default_tools

    registry = ToolRegistry()
    for tool in create_default_tools():
        registry.register_tool(tool)
    return registry


__all__ = [
    "DuplicateToolError",
    "PermissionDeniedError",
    "ToolNotFoundError",
    "ToolRegistry",
    "build_default_registry",
]
