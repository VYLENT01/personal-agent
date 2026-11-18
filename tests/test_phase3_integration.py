"""تست‌های Integration چرخه کامل فاز ۳ (چک‌لیست بخش ۱۰-Integration).

- فراخوانی ابزار توسط مدل (native) → Registry → نتیجه به LLM → پاسخ نهایی واقعی
- عملیات L2 بدون تأیید انجام نمی‌شود؛ با تأیید انجام می‌شود
- رد عملیات L3 (در فاز ۳ ابزار L3 نداریم؛ سطح Registry سخت رد می‌کند)
- نمایش عملیات در UI (رویدادها)
- حفظ عملکرد قابلیت‌های فاز ۲ (چرخه ابزار سیستم همچنان کار می‌کند)

با FakeLLMClient (بدون مدل واقعی) — مستقل و تکرارپذیر؛ اجرا با Ollama
واقعی طبق تست‌های integration موجود فاز ۲ (test_integration_ollama).
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import pytest

from app.agent.orchestrator import (
    ApprovalRequested,
    ChatOrchestrator,
    ToolCallFinished,
    ToolCallStarted,
    TurnCompleted,
)
from app.config.settings import Settings
from app.llm.model_manager import ModelManager
from app.security.paths import PathGuard
from app.security.permissions import ApprovalToken, PermissionManager
from app.tools.applications import create_application_tools
from app.tools.development import create_development_tools
from app.tools.filesystem import create_filesystem_tools
from app.tools.registry import ToolRegistry, build_default_registry
from app.tools.terminal import CommandPolicy, create_terminal_tools
from tests.fakes import FakeLLMClient


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    docs = ws / "docs"
    docs.mkdir(parents=True)
    (docs / "note.txt").write_text("محتوای واقعی فایل برای مدل\n", encoding="utf-8")
    return ws


@pytest.fixture()
def full_registry(workspace: Path) -> ToolRegistry:
    """Registry کامل فاز ۳: سیستم + فایل‌سیستم + ترمینال + برنامه‌ها + توسعه."""
    registry = ToolRegistry()  # سقف پیش‌فرض L2
    for tool in build_default_registry().list_tools():
        registry.register_tool(tool)
    guard = PathGuard([str(workspace)])
    for tool in create_filesystem_tools(guard):
        registry.register_tool(tool)
    for tool in create_terminal_tools(CommandPolicy(), guard):
        registry.register_tool(tool)
    for tool in create_application_tools(finder=lambda: []):
        registry.register_tool(tool)
    for tool in create_development_tools(guard):
        registry.register_tool(tool)
    return registry


def _orchestrator(script: list[Any], registry: ToolRegistry) -> tuple[ChatOrchestrator, FakeLLMClient]:
    provider = FakeLLMClient(script=script, native_tools=True)
    settings = Settings(active_model="qwen3:8b")
    manager = ModelManager(provider, settings)
    return ChatOrchestrator(provider, manager, settings, registry), provider


# ---------------------------------------------------------------------------
# چرخه کامل L1 — ابزار filesystem توسط مدل
# ---------------------------------------------------------------------------

class TestFullCycleL1:
    def test_model_reads_real_file_content(self, full_registry: ToolRegistry, workspace: Path) -> None:
        # مدل read_file می‌خواهد → فایل واقعی خوانده می‌شود → نتیجه به مدل → پاسخ نهایی
        script = [
            {
                "tool_calls": [
                    {
                        "name": "filesystem.read_file",
                        "args": {"path": str(workspace / "docs" / "note.txt")},
                    }
                ]
            },
            "محتوای فایل را خواندم؛ متن آن دربارهٔ «محتوای واقعی» است.",
        ]
        orchestrator, provider = _orchestrator(script, full_registry)
        events = list(orchestrator.stream_turn("فایل note.txt را بخوان"))
        kinds = [type(e).__name__ for e in events]

        # اجرای واقعی: نتیجه حاوی متن واقعی فایل است
        finished = [e for e in events if isinstance(e, ToolCallFinished)][0]
        assert finished.result.success is True
        assert "محتوای واقعی فایل" in finished.result.data["content"]

        # نتیجه به مدل فرستاده شد (پیام role=tool در فراخوانی دوم)
        second_messages = provider.received[1]
        tool_messages = [m for m in second_messages if m.role == "tool"]
        assert tool_messages and "محتوای واقعی" in tool_messages[0].content

        # پاسخ نهایی بر اساس خروجی واقعی
        completed = events[-1]
        assert isinstance(completed, TurnCompleted)
        assert completed.tool_calls == 1
        assert "خواندم" in completed.content
        assert kinds.count("ApprovalRequested") == 0  # L1 بدون تأیید

    def test_model_lists_directory(self, full_registry: ToolRegistry, workspace: Path) -> None:
        script = [
            {
                "tool_calls": [
                    {
                        "name": "filesystem.list_directory",
                        "args": {"path": str(workspace / "docs")},
                    }
                ]
            },
            "در پوشه یک فایل به نام note.txt هست.",
        ]
        orchestrator, _ = _orchestrator(script, full_registry)
        events = list(orchestrator.stream_turn("چه فایلی در docs هست؟"))
        finished = [e for e in events if isinstance(e, ToolCallFinished)][0]
        assert finished.result.success is True
        names = [e["name"] for e in finished.result.data["entries"]]
        assert "note.txt" in names


# ---------------------------------------------------------------------------
# چرخه L2 — گیت تأیید واقعی در جریان Agent
# ---------------------------------------------------------------------------

class TestApprovalFlowInCycle:
    def test_l2_denied_when_user_rejects(self, full_registry: ToolRegistry, workspace: Path) -> None:
        script = [
            {
                "tool_calls": [
                    {
                        "name": "filesystem.create_file",
                        "args": {"path": str(workspace / "docs" / "new.txt"), "content": "x"},
                    }
                ]
            },
            "فایل ساخته نشد؛ کاربر رد کرد.",
        ]
        orchestrator, _ = _orchestrator(script, full_registry)
        events: list[Any] = []

        def consume() -> None:
            for event in orchestrator.stream_turn("فایل new.txt بساز"):
                events.append(event)
                # شبیه‌سازی UI: به رویداد تأیید «رد» پاسخ می‌دهیم
                if isinstance(event, ApprovalRequested):
                    event.resolve_deny()

        thread = threading.Thread(target=consume)
        thread.start()
        thread.join(timeout=10)
        assert not thread.is_alive()

        kinds = [type(e).__name__ for e in events]
        assert "ApprovalRequested" in kinds
        # بدون تأیید → اجرا نشد؛ فایل واقعاً ساخته نشد
        assert not (workspace / "docs" / "new.txt").exists()
        finished = [e for e in events if isinstance(e, ToolCallFinished)][0]
        assert finished.result.success is False
        assert "رد شد" in (finished.result.error or "")
        # نتیجه رد به مدل رفت و پاسخ نهایی آمد
        assert isinstance(events[-1], TurnCompleted)

    def test_l2_executes_when_user_approves(self, full_registry: ToolRegistry, workspace: Path) -> None:
        manager = PermissionManager(approval_ttl=30.0)
        script = [
            {
                "tool_calls": [
                    {
                        "name": "filesystem.create_file",
                        "args": {"path": str(workspace / "docs" / "approved.txt"), "content": "تأیید شد"},
                    }
                ]
            },
            "فایل با تأیید کاربر ساخته شد.",
        ]
        orchestrator, _ = _orchestrator(script, full_registry)
        events: list[Any] = []

        def consume() -> None:
            for event in orchestrator.stream_turn("فایل بساز"):
                events.append(event)
                if isinstance(event, ApprovalRequested):
                    # شبیه‌سازی UI: توکن متناسب همان نام+args ساخته و تأیید می‌شود
                    token = manager.create_token(event.tool_name, event.args)
                    event.resolve_approve(token)

        thread = threading.Thread(target=consume)
        thread.start()
        thread.join(timeout=10)
        assert not thread.is_alive()

        kinds = [type(e).__name__ for e in events]
        assert "ApprovalRequested" in kinds and "ToolCallStarted" in kinds
        # فایل واقعاً ساخته شد
        assert (workspace / "docs" / "approved.txt").read_text(encoding="utf-8") == "تأیید شد"
        finished = [e for e in events if isinstance(e, ToolCallFinished)][0]
        assert finished.result.success is True
        assert isinstance(events[-1], TurnCompleted)

    def test_approval_times_out_rejects_operation(
        self, full_registry: ToolRegistry, workspace: Path
    ) -> None:
        # هیچ UIای پاسخ نمی‌دهد → انقضا → رد ساختاریافته (بدون قفل برای همیشه)
        import app.config.defaults as d

        d.APPROVAL_TIMEOUT_SECONDS = 0.3  # مهلت کوتاه آزمون
        try:
            script = [
                {
                    "tool_calls": [
                        {
                            "name": "filesystem.create_directory",
                            "args": {"path": str(workspace / "docs" / "timedout")},
                        }
                    ]
                },
                "مهلت تأیید تمام شد.",
            ]
            orchestrator, _ = _orchestrator(script, full_registry)
            started = time.monotonic()
            events = list(orchestrator.stream_turn("پوشه بساز"))
            elapsed = time.monotonic() - started
            assert elapsed < 5.0  # انقضای واقعی، نه قفل ابدی
            assert not (workspace / "docs" / "timedout").exists()
            finished = [e for e in events if isinstance(e, ToolCallFinished)][0]
            assert finished.result.success is False
            assert "مهلت" in (finished.result.error or "") or "رد شد" in (finished.result.error or "")
            assert isinstance(events[-1], TurnCompleted)
        finally:
            d.APPROVAL_TIMEOUT_SECONDS = 120.0

    def test_cancel_during_approval_wait(self, full_registry: ToolRegistry, workspace: Path) -> None:
        script = [
            {
                "tool_calls": [
                    {
                        "name": "filesystem.create_file",
                        "args": {"path": str(workspace / "docs" / "canceled.txt"), "content": "x"},
                    }
                ]
            },
            "پاسخ",
        ]
        orchestrator, _ = _orchestrator(script, full_registry)
        events: list[Any] = []

        def consume() -> None:
            for event in orchestrator.stream_turn("بساز"):
                events.append(event)

        thread = threading.Thread(target=consume)
        thread.start()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if any(isinstance(e, ApprovalRequested) for e in events):
                break
            time.sleep(0.02)
        orchestrator.request_cancel()  # لغو حین انتظار تأیید
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert not (workspace / "docs" / "canceled.txt").exists()
        kinds = [type(e).__name__ for e in events]
        assert "TurnCancelled" in kinds


# ---------------------------------------------------------------------------
# L3 و محافظت‌ها
# ---------------------------------------------------------------------------

class TestL3AndProtections:
    def test_l3_tool_never_executed_or_offered(self, full_registry: ToolRegistry) -> None:
        from app.tools.base import PermissionLevel, Tool, ToolInput

        class DangerousTool(Tool):
            input_model = ToolInput
            permission_level = PermissionLevel.L3_SENSITIVE

            @property
            def name(self) -> str:
                return "danger.wipe"

            @property
            def description(self) -> str:
                return "خطرناک."

            def _run(self, args: ToolInput) -> dict:
                return {}

        full_registry.register_tool(DangerousTool())
        # مدل اصلاً آن را نمی‌بیند
        names = [t["function"]["name"] for t in full_registry.get_tool_schemas()]
        assert "danger.wipe" not in names
        # و اجرای مستقیم هم رد می‌شود (سخت)
        result = full_registry.execute_tool("danger.wipe", {})
        assert result.success is False
        assert "مجاز نیست" in (result.error or "")

    def test_terminal_injection_via_model_rejected(
        self, full_registry: ToolRegistry, workspace: Path
    ) -> None:
        # دو لایه مستقل دفاع:
        # ۱) در چرخه agent: رد تأیید کاربر → عملیات اجرا نمی‌شود اصلاً
        script = [
            {
                "tool_calls": [
                    {
                        "name": "terminal.execute_command",
                        "args": {"program": "git", "args": ["status; rm -rf /"]},
                    }
                ]
            },
            "دستور رد شد.",
        ]
        orchestrator, _ = _orchestrator(script, full_registry)
        events: list[Any] = []

        def consume() -> None:
            for event in orchestrator.stream_turn("گیت را اجرا کن"):
                events.append(event)
                if isinstance(event, ApprovalRequested):
                    event.resolve_deny()

        thread = threading.Thread(target=consume)
        thread.start()
        thread.join(timeout=10)
        finished = [e for e in events if isinstance(e, ToolCallFinished)]
        assert finished
        assert finished[0].result.success is False
        assert "رد شد" in (finished[0].result.error or "")

        # ۲) لایه سیاست مستقل از تأیید: حتی با توکن معتبرِ کاربر، تزریق رد می‌شود
        manager = PermissionManager()
        bad_args = {"program": "git", "args": ["status; rm -rf /"]}
        token = manager.create_token("terminal.execute_command", bad_args)
        direct = full_registry.execute_tool(
            "terminal.execute_command", bad_args, approval=token
        )
        assert direct.success is False
        assert "ممنوع" in (direct.error or "") or "تزریق" in (direct.error or "")

    def test_phase2_system_tools_still_work(self, full_registry: ToolRegistry) -> None:
        # حفظ عملکرد فاز ۲: ابزار سیستم همچنان از همان چرخه کار می‌کند
        script = [
            {"tool_calls": [{"name": "system.get_memory_usage", "args": {}}]},
            "RAM گزارش شد.",
        ]
        orchestrator, _ = _orchestrator(script, full_registry)
        events = list(orchestrator.stream_turn("وضعیت رم"))
        finished = [e for e in events if isinstance(e, ToolCallFinished)][0]
        assert finished.result.success is True
        assert finished.result.data["ram"]["total_gb"] > 0
        assert isinstance(events[-1], TurnCompleted)


class TestRegistryInventory:
    def test_full_inventory_and_levels(self, full_registry: ToolRegistry) -> None:
        names = full_registry.tool_names()
        # فاز ۲: ۳ ابزار سیستم
        assert {"system.get_disk_usage", "system.get_memory_usage", "system.get_system_info"} <= set(names)
        # فاز ۳: چهار گروه
        for prefix in ("filesystem.", "terminal.", "applications.", "development."):
            assert any(n.startswith(prefix) for n in names), prefix
        # هیچ ابزار حذف/بستن (L3) وجود ندارد
        assert not any(("delete" in n or "remove" in n or "close" in n or "kill" in n) for n in names)
        # L2ها همه گیت تأیید دارند
        l2 = [n for n in names if full_registry.needs_approval(n)]
        assert "filesystem.write_file" in l2
        assert "terminal.execute_command" in l2
        assert "applications.launch_app" in l2
        assert "development.run_tests" in l2
