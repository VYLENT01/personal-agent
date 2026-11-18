"""قرارداد استاندارد ابزار (Tool Contract).

هر ابزار باید این قرارداد را پیاده کند:
- name / description / input_schema (JSON Schema از مدل Pydantic)
- permission_level (L1 فقط-خواندنی … L3 حساس)
- validate() با Pydantic — مستقل از LLM؛ ورودی نامعتبر رد می‌شود
- execute() با خروجی ساختاریافته ToolResult

چرا Pydantic: schema تولیدی استاندارد JSON Schema برای Ollama است
(``tools=[{"type": "function", ...}]``) و اعتبارسنجی ورودی مدلِ ساختاریافته —
دو نیازمند فاز ۲ با یک مدل داده واحد.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from enum import IntEnum
from typing import Any, Optional

from pydantic import BaseModel, Field, ValidationError

from app.config import defaults as d


class PermissionLevel(IntEnum):
    """سطح دسترسی ابزار (بند ۱۳ مشخصات).

    L1 فقط-خواندنی → اجرای خودکار.
    L2 نوشتن/تغییر و L3 حساس/حذف → در این فاز ابزاری با این سطح ثبت نمی‌شود؛
    اجرای کنترل‌شده آن‌ها در فاز ۵ (Approval) کامل می‌شود.
    """

    L1_READ_ONLY = d.PERMISSION_L1
    L2_WRITE = d.PERMISSION_L2
    L3_SENSITIVE = d.PERMISSION_L3


class ToolInput(BaseModel):
    """مدل پایه ورودی ابزار؛ زیرکلاس‌ها فیلدهای خاص خود را تعریف می‌کنند.

    مدل باید خالی (بدون فیلد) را هم مجاز بداند تا ابزارهای بدون پارامتر
    با args={} صدا شوند؛ PermitEmpty اجازه این را صراحتاً می‌دهد.
    """

    model_config = {"extra": "forbid"}


class ToolResult(BaseModel):
    """خروجی ساختاریافته هر اجرای ابزار (بند ۸ مشخصات)."""

    success: bool
    tool: str
    data: Optional[Any] = None
    error: Optional[str] = None          # پیام فارسی قابل‌نمایش
    duration: float = 0.0                # ثانیه

    @classmethod
    def ok(cls, tool: str, data: Any, duration: float = 0.0) -> "ToolResult":
        return cls(success=True, tool=tool, data=data, error=None, duration=round(duration, 3))

    @classmethod
    def fail(cls, tool: str, error: str, duration: float = 0.0) -> "ToolResult":
        return cls(success=False, tool=tool, data=None, error=error, duration=round(duration, 3))

    def truncate_for_llm(self, max_chars: int = d.TOOL_RESULT_MAX_CHARS) -> str:
        """نمای متنی برای ارسال به مدل؛ سقف طول با برش ایمن."""
        payload = self.data if self.success else self.error
        if payload is None:
            text = ""
        elif not isinstance(payload, str):
            import json

            try:
                text = json.dumps(payload, ensure_ascii=False, default=str)
            except (TypeError, ValueError):
                text = str(payload)
        else:
            text = payload
        if len(text) > max_chars:
            text = text[:max_chars] + "…[بریده شد]"
        header = f"tool={self.tool} success={self.success}"
        if self.success:
            return f"{header}\n{text}"
        return f"{header} error={self.error}\n{text}"


class Tool(ABC):
    """قرارداد پایه هر ابزار؛ زیرکلاس‌ها فقط ۴ عضو انتزاعی را می‌دهند."""

    #: مدل Pydantic ورودی (زیرکلاس ToolInput)
    input_model: type[ToolInput]
    #: سطح دسترسی
    permission_level: PermissionLevel = PermissionLevel.L1_READ_ONLY
    #: برچسب انسانی گروه ابزار (برای UI)
    category: str = "system"

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def description(self) -> str: ...

    @abstractmethod
    def _run(self, args: ToolInput) -> Any:
        """اجرای واقعی؛ فقط داده برمی‌گرداند (به ToolResult.ok می‌رسد)."""

    # ------------------------------------------------------------------
    # اعضای مشترک (نه انتزاعی)
    # ------------------------------------------------------------------

    @property
    def metadata(self) -> dict[str, Any]:
        """متادیتای ثابت ابزار برای UI و لاگ."""
        return {
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "permission_level": int(self.permission_level),
            "permission_level_name": self.permission_level.name,
            "parameters": list(self.input_model.model_json_schema().get("properties", {}).keys()),
        }

    def input_schema(self) -> dict[str, Any]:
        """JSON Schema پارامترها — فرمت Ollama/OpenAI function calling."""
        schema = self.input_model.model_json_schema()
        # حذف کلیدهای متا Pydantic که مدل زبانی لازم ندارد
        for noise in ("title", "model_config"):
            schema.pop(noise, None)
        for prop in schema.get("properties", {}).values():
            prop.pop("title", None)
        return schema

    def to_ollama_tool(self) -> dict[str, Any]:
        """فریم wire ابزار برای پارامتر `tools` در /api/chat."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema() or {"type": "object", "properties": {}},
            },
        }

    def validate(self, args: dict[str, Any]) -> tuple[bool, Optional[ToolInput], Optional[str]]:
        """اعتبارسنجی ورودی با Pydantic؛ مستقل از LLM.

        خروجی: (معتبر است؟، نمونه پارس‌شده، پیام خطای فارسی)
        """
        if not isinstance(args, dict):
            return False, None, "آرگومان‌های ابزار باید یک شیء JSON باشند."
        try:
            parsed = self.input_model(**args)
        except ValidationError as exc:
            lines = []
            for problem in exc.errors():
                where = ".".join(str(part) for part in problem.get("loc", ()))
                what = problem.get("msg", "نامعتبر")
                lines.append(f"«{where}»: {what}" if where else what)
            return False, None, "ورودی نامعتبر — " + "؛ ".join(lines)
        return True, parsed, None

    def execute(self, args: dict[str, Any]) -> ToolResult:
        """اجرای کنترل‌شده: اعتبارسنجی → اجرا → نگاشت خطا به ToolResult.

        هر exception پیاده‌سازی به failure ساختاریافته تبدیل می‌شود؛
        هیچ exception از execute بیرون نمی‌رود (Test «مدیریت خطای ابزار»).
        """
        started = time.perf_counter()
        valid, parsed, error = self.validate(args)
        if not valid:
            return ToolResult.fail(self.name, error or "ورودی نامعتبر", time.perf_counter() - started)
        try:
            data = self._run(parsed)
        except Exception as exc:  # noqa: BLE001 - خطای ابزار باید ساختاریافته برگردد
            return ToolResult.fail(
                self.name, f"خطا در اجرای ابزار: {exc}", time.perf_counter() - started
            )
        return ToolResult.ok(self.name, data, time.perf_counter() - started)


class ToolTimeoutError(Exception):
    """اجرای ابزار از مهلت مقرر فراتر رفت (در Registry مدیریت می‌شود)."""


__all__ = [
    "PermissionLevel",
    "Tool",
    "ToolInput",
    "ToolResult",
    "ToolTimeoutError",
    "ValidationError",
]
