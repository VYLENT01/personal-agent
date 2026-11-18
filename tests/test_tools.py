"""تست‌های Tool Contract و Tool Registry (بخش هفتم فاز ۲).

پوشش الزامی مشخصات:
- ثبت ابزار / جلوگیری از ثبت تکراری
- فراخوانی ابزار موجود / رد ابزار ناشناخته
- اعتبارسنجی پارامترها (Pydantic) / مدیریت خطای ابزار / مدیریت Timeout
- عدم اجرای ابزار غیرمجاز (L2/L3 و غیرفعال)
- get_tool_schemas فقط ابزارهای فعالِ مجاز
- سه ابزار واقعی سیستم (psutil) — فقط-خواندنی
"""
from __future__ import annotations

import time
from typing import Any

import pytest
from pydantic import Field

from app.config import defaults as d
from app.tools.base import (
    PermissionLevel,
    Tool,
    ToolInput,
    ToolResult,
)
from app.tools.registry import (
    DuplicateToolError,
    ToolNotFoundError,
    ToolRegistry,
    build_default_registry,
)


# ---------------------------------------------------------------------------
# ابزارهای ساختگی تست
# ---------------------------------------------------------------------------

class EchoInput(ToolInput):
    text: str = Field(description="متن برای پژواک")
    times: int = Field(default=1, ge=1, le=5, description="تعداد تکرار")


class _EchoTool(Tool):
    input_model = EchoInput
    permission_level = PermissionLevel.L1_READ_ONLY
    category = "test"

    @property
    def name(self) -> str:
        return "test.echo"

    @property
    def description(self) -> str:
        return "پژواک متن."

    def _run(self, args: EchoInput) -> dict:
        return {"echo": args.text * args.times}


class _SlowTool(Tool):
    """ابزار آهسته برای تست Timeout (Registry)."""

    input_model = ToolInput
    permission_level = PermissionLevel.L1_READ_ONLY
    category = "test"

    @property
    def name(self) -> str:
        return "test.slow"

    @property
    def description(self) -> str:
        return "آهسته."

    def _run(self, args: ToolInput) -> dict:
        time.sleep(5.0)
        return {"never": "reached"}


class _BrokenTool(Tool):
    """ابزاری که همیشه خطای داخلی می‌دهد (تست مدیریت خطای ابزار)."""

    input_model = ToolInput
    permission_level = PermissionLevel.L1_READ_ONLY
    category = "test"

    @property
    def name(self) -> str:
        return "test.broken"

    @property
    def description(self) -> str:
        return "شکست‌ناپذیر."

    def _run(self, args: ToolInput) -> Any:
        raise RuntimeError("خرابی عمدی")


class _SensitiveTool(_EchoTool):
    """ابزار سطح L3 — در این فاز نباید اجرا شود."""

    permission_level = PermissionLevel.L3_SENSITIVE

    @property
    def name(self) -> str:
        return "test.sensitive"


def _registry() -> ToolRegistry:
    registry = ToolRegistry(tool_timeout=1.0)
    registry.register_tool(_EchoTool())
    return registry


# ---------------------------------------------------------------------------
# ثبت و جست‌وجو
# ---------------------------------------------------------------------------

class TestRegistration:
    def test_register_tool(self) -> None:
        registry = _registry()
        assert registry.get_tool("test.echo") is not None
        assert "test.echo" in registry.tool_names()

    def test_duplicate_registration_rejected(self) -> None:
        registry = _registry()
        with pytest.raises(DuplicateToolError) as exc_info:
            registry.register_tool(_EchoTool())
        assert "test.echo" in str(exc_info.value)

    def test_invalid_name_format_rejected(self) -> None:
        registry = _registry()

        class _BadName(_EchoTool):
            @property
            def name(self) -> str:
                return "بدون-نقطه"

        with pytest.raises(ValueError):
            registry.register_tool(_BadName())

    def test_unregister_tool(self) -> None:
        registry = _registry()
        assert registry.unregister_tool("test.echo") is True
        assert registry.get_tool("test.echo") is None
        assert registry.unregister_tool("test.echo") is False

    def test_set_enabled_hides_from_model(self) -> None:
        registry = _registry()
        registry.set_enabled("test.echo", False)
        assert "test.echo" not in registry.tool_names(only_enabled=True)
        assert "test.echo" in registry.tool_names(only_enabled=False)
        # مدل ابزار غیرفعال را نمی‌بیند
        schemas = registry.get_tool_schemas()
        assert all(s["function"]["name"] != "test.echo" for s in schemas)

    def test_set_enabled_unknown_tool(self) -> None:
        registry = _registry()
        with pytest.raises(ToolNotFoundError):
            registry.set_enabled("test.nobody", False)


# ---------------------------------------------------------------------------
# اجرا و اعتبارسنجی
# ---------------------------------------------------------------------------

class TestExecution:
    def test_execute_known_tool_success(self) -> None:
        registry = _registry()
        result = registry.execute_tool("test.echo", {"text": "ها", "times": 2})
        assert result.success is True
        assert result.tool == "test.echo"
        assert result.data == {"echo": "هاها"}
        assert result.duration >= 0.0

    def test_unknown_tool_rejected(self) -> None:
        registry = _registry()
        result = registry.execute_tool("test.nobody", {})
        assert result.success is False
        assert "ثبت نشده" in (result.error or "")

    def test_validate_tool_call_rejects_bad_args(self) -> None:
        registry = _registry()
        ready, tool, error = registry.validate_tool_call(
            "test.echo", {"text": 123}  # text باید str باشد
        )
        assert ready is False
        assert error and "ورودی نامعتبر" in error

    def test_execute_extra_args_forbidden(self) -> None:
        # extra=forbid → پارامتر ناشناخته رد می‌شود
        registry = _registry()
        result = registry.execute_tool("test.echo", {"text": "x", "surprise": 1})
        assert result.success is False
        assert "surprise" in (result.error or "")

    def test_execute_out_of_range_args(self) -> None:
        registry = _registry()
        result = registry.execute_tool("test.echo", {"text": "x", "times": 99})
        assert result.success is False
        assert "ورودی نامعتبر" in (result.error or "")

    def test_tool_error_structured_not_crash(self) -> None:
        registry = _registry()
        registry.register_tool(_BrokenTool())
        result = registry.execute_tool("test.broken", {})
        assert result.success is False
        assert "خرابی عمدی" in (result.error or "")

    def test_tool_timeout(self) -> None:
        registry = ToolRegistry(tool_timeout=0.3)
        registry.register_tool(_SlowTool())
        started = time.monotonic()
        result = registry.execute_tool("test.slow", {})
        elapsed = time.monotonic() - started
        assert result.success is False
        assert "Timeout" in (result.error or "")
        assert elapsed < 3.0  # ۵ ثانیه خواب؛ زودتر از آن شکست می‌خورد

    def test_permission_denied_for_l3(self) -> None:
        registry = _registry()
        registry.register_tool(_SensitiveTool())
        result = registry.execute_tool("test.sensitive", {"text": "x"})
        assert result.success is False
        assert "مجاز نیست" in (result.error or "")
        # ابزار L3 حتی در schemas مدل هم دیده نمی‌شود
        schemas = registry.get_tool_schemas()
        names = [s["function"]["name"] for s in schemas]
        assert "test.sensitive" not in names
        assert "test.echo" in names

    def test_disabled_tool_rejected_with_message(self) -> None:
        registry = _registry()
        registry.set_enabled("test.echo", False)
        result = registry.execute_tool("test.echo", {"text": "x"})
        assert result.success is False
        assert "غیرفعال" in (result.error or "")


# ---------------------------------------------------------------------------
# فریم wire برای مدل
# ---------------------------------------------------------------------------

class TestSchemas:
    def test_tool_schema_shape(self) -> None:
        registry = _registry()
        schema = registry.get_tool_schemas()[0]
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "test.echo"
        params = schema["function"]["parameters"]
        assert params["type"] == "object"
        assert "text" in params["properties"]
        assert "text" in params.get("required", [])
        assert "title" not in params  # متا Pydantic حذف شده

    def test_metadata_shape(self) -> None:
        registry = _registry()
        meta = registry.metadata()[0]
        assert meta["name"] == "test.echo"
        assert meta["permission_level"] == 1
        assert meta["enabled"] is True
        assert set(meta["parameters"]) == {"text", "times"}

    def test_capabilities_prompt_lists_tools(self) -> None:
        registry = _registry()
        lines = registry.capabilities_prompt("fa")
        assert any("test.echo" in line for line in lines)
        assert lines[0].startswith("ابزارهای در دسترس")

    def test_schemas_limit(self) -> None:
        registry = ToolRegistry()
        for index in range(30):
            registry.register_tool(_make_numbered_tool(index))
        assert len(registry.get_tool_schemas()) == d.MAX_TOOLS_IN_PROMPT

    def test_registry_stats(self) -> None:
        registry = _registry()
        registry.execute_tool("test.echo", {"text": "x"})
        registry.execute_tool("test.nobody", {})
        stats = registry.stats()
        assert stats["executed"] == 1
        assert stats["rejected"] == 1


def _make_numbered_tool(index: int) -> Tool:
    class _Numbered(_EchoTool):
        @property
        def name(self) -> str:
            return f"test.tool{index}"

    return _Numbered()


# ---------------------------------------------------------------------------
# سه ابزار واقعی فاز ۲ (psutil) — فقط-خواندنی
# ---------------------------------------------------------------------------

class TestSystemTools:
    def test_default_registry_has_three_read_only_tools(self) -> None:
        registry = build_default_registry()
        assert sorted(registry.tool_names()) == [
            "system.get_disk_usage",
            "system.get_memory_usage",
            "system.get_system_info",
        ]
        for tool in registry.list_tools():
            assert tool.permission_level == PermissionLevel.L1_READ_ONLY

    def test_get_system_info_runs(self) -> None:
        registry = build_default_registry()
        result = registry.execute_tool("system.get_system_info", {})
        assert result.success is True
        data = result.data
        assert "os" in data and "cpu_logical_cores" in data

    def test_get_memory_usage_runs(self) -> None:
        registry = build_default_registry()
        result = registry.execute_tool("system.get_memory_usage", {})
        assert result.success is True
        assert result.data["ram"]["total_gb"] > 0
        assert 0 <= result.data["ram"]["percent"] <= 100

    def test_get_disk_usage_runs(self) -> None:
        registry = build_default_registry()
        result = registry.execute_tool("system.get_disk_usage", {})
        assert result.success is True
        # در sandbox شاید پارتیشن قابل‌خواندنی نباشد؛ نتیجه باید ساختاری معتبر بماند
        assert isinstance(result.data["partitions"], list)
        for partition in result.data["partitions"]:
            assert partition["total_bytes"] > 0
            assert 0 <= partition["percent"] <= 100

    def test_get_disk_usage_with_path(self) -> None:
        registry = build_default_registry()
        result = registry.execute_tool("system.get_disk_usage", {"path": "/"})
        assert result.success is True
        assert result.data["partitions"][0]["mountpoint"] == "/"
        assert result.data["partitions"][0]["total_gb"] > 0

    def test_disk_usage_bad_path_structured_error(self) -> None:
        registry = build_default_registry()
        result = registry.execute_tool("system.get_disk_usage", {"path": "/مسیر/ناموجود/قطعاً"})
        assert result.success is False
        assert result.error is not None

    def test_tool_result_truncate(self) -> None:
        ok = ToolResult.ok("x.y", {"big": "a" * 10_000})
        text = ok.truncate_for_llm(max_chars=100)
        assert len(text) < 200
        assert "[بریده شد]" in text
        fail = ToolResult.fail("x.y", "خطای تست")
        assert "error=خطای تست" in fail.truncate_for_llm()
