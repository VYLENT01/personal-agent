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


# ===========================================================================
# فاز ۳ — گیت تأیید L2 در Registry (بخش ۸)
# ===========================================================================

from app.security.permissions import ApprovalError, PermissionManager  # noqa: E402
from app.tools.base import PermissionLevel as PL  # noqa: E402


class _L2EchoTool(_EchoTool):
    """ابزار L2 (نوشتن) — بدون تأیید اجرا نمی‌شود."""

    permission_level = PL.L2_WRITE

    @property
    def name(self) -> str:
        return "test.write_echo"


class TestL2ApprovalGate:
    def _registry_with_l2(self) -> ToolRegistry:
        registry = ToolRegistry()
        registry.register_tool(_EchoTool())
        registry.register_tool(_L2EchoTool())
        return registry

    def test_l2_needs_approval(self) -> None:
        registry = self._registry_with_l2()
        assert registry.needs_approval("test.write_echo") is True
        assert registry.needs_approval("test.echo") is False
        assert registry.needs_approval("test.nobody") is False

    def test_l2_rejected_without_approval(self) -> None:
        registry = self._registry_with_l2()
        result = registry.execute_tool("test.write_echo", {"text": "x"})
        assert result.success is False
        assert "تأیید کاربر" in (result.error or "")
        assert registry.stats()["rejected"] == 1

    def test_l2_executes_with_valid_approval(self) -> None:
        registry = self._registry_with_l2()
        args = {"text": "ها", "times": 2}
        token = PermissionManager().create_token("test.write_echo", args)
        result = registry.execute_tool("test.write_echo", args, approval=token)
        assert result.success is True
        assert result.data == {"echo": "هاها"}

    def test_l2_token_single_use(self) -> None:
        registry = self._registry_with_l2()
        args = {"text": "x"}
        token = PermissionManager().create_token("test.write_echo", args)
        first = registry.execute_tool("test.write_echo", args, approval=token)
        second = registry.execute_tool("test.write_echo", args, approval=token)
        assert first.success is True
        assert second.success is False
        assert "استفاده شده" in (second.error or "")

    def test_l2_token_invalid_on_changed_args(self) -> None:
        registry = self._registry_with_l2()
        token = PermissionManager().create_token(
            "test.write_echo", {"text": "x"}
        )
        result = registry.execute_tool(
            "test.write_echo", {"text": "OTHER"}, approval=token
        )
        assert result.success is False
        assert "تغییر کرده" in (result.error or "")

    def test_l1_still_runs_without_approval(self) -> None:
        registry = self._registry_with_l2()
        result = registry.execute_tool("test.echo", {"text": "x"})
        assert result.success is True

    def test_l3_still_hard_denied_and_hidden(self) -> None:
        registry = self._registry_with_l2()
        registry.register_tool(_SensitiveTool())
        result = registry.execute_tool("test.sensitive", {"text": "x"})
        assert result.success is False
        assert "مجاز نیست" in (result.error or "")
        names = [s["function"]["name"] for s in registry.get_tool_schemas()]
        assert "test.sensitive" not in names
        # L2 در فریم مدل دیده می‌شود (انتخاب با مدل) ولی اجرایش گیت دارد
        assert "test.write_echo" in names

    def test_registry_default_max_permission_is_l2(self) -> None:
        registry = ToolRegistry()
        assert registry._max_permission == PL.L2_WRITE  # noqa: SLF001 - تست مستقیم سیاست

    def test_registry_l3_only_mode_hides_l2(self) -> None:
        registry = ToolRegistry(max_permission=PermissionLevel.L1_READ_ONLY)
        registry.register_tool(_L2EchoTool())
        result = registry.execute_tool("test.write_echo", {"text": "x"})
        assert result.success is False
        names = [s["function"]["name"] for s in registry.get_tool_schemas()]
        assert "test.write_echo" not in names

    def test_expired_token_rejected(self) -> None:
        registry = self._registry_with_l2()
        manager = PermissionManager(approval_ttl=0.01)
        token = manager.create_token("test.write_echo", {"text": "x"})
        time.sleep(0.05)
        result = registry.execute_tool(
            "test.write_echo", {"text": "x"}, approval=token
        )
        assert result.success is False
        assert "منقضی" in (result.error or "")
