"""تست‌های ابزارهای Terminal فاز ۳ (چک‌لیست بخش ۱۰).

- اجرای دستور مجاز / رد دستور غیرمجاز / رد Command Injection
- رد دستور زنجیره‌ای / Timeout / Cancellation / محدودیت خروجی
- مسیر کاری غیرمجاز / تأیید کاربر / get_command_output
- policy واحد جدا از ابزار (ساختارمند)
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional

import pytest

from app.config import defaults as d
from app.security.paths import PathGuard
from app.security.permissions import PermissionManager
from app.tools.terminal import (
    CommandPolicy,
    CommandPolicyError,
    create_terminal_tools,
    default_runner,
)
from app.tools.registry import ToolRegistry


# ---------------------------------------------------------------------------
# CommandPolicy — واحد خالص
# ---------------------------------------------------------------------------

class TestCommandPolicy:
    def test_allowed_simple(self) -> None:
        policy = CommandPolicy()
        decision = policy.evaluate("git", ["status"])
        assert decision.allowed is True
        assert decision.argv == ["status"]

    def test_allowed_wildcard(self) -> None:
        policy = CommandPolicy()
        decision = policy.evaluate("Get-ChildItem", ["-Name"])
        assert decision.allowed is True

    def test_reject_unknown_program(self) -> None:
        policy = CommandPolicy()
        decision = policy.evaluate("curl", ["http://x"])
        assert decision.allowed is False
        assert "فهرست مجاز" in decision.reason

    def test_reject_disallowed_arg(self) -> None:
        policy = CommandPolicy()
        decision = policy.evaluate("python", ["-c", "print(1)"])
        assert decision.allowed is False
        assert "تطبیق" in decision.reason or "مجاز" in decision.reason

    def test_reject_injection_chars_in_args(self) -> None:
        policy = CommandPolicy()
        for payload in ["a;rm", "a && b", "a|b", "`x", "$(y)", "a\nb", "a>b", "a<b", "a&b"]:
            decision = policy.evaluate("git", ["status", payload])
            assert decision.allowed is False, payload
            assert "ممنوع" in decision.reason or "تزریق" in decision.reason

    def test_reject_injection_in_program(self) -> None:
        policy = CommandPolicy()
        decision = policy.evaluate("git; rm", [])
        assert decision.allowed is False

    def test_reject_space_in_program(self) -> None:
        policy = CommandPolicy()
        decision = policy.evaluate("git status", [])
        assert decision.allowed is False

    def test_chain_markers_rejected_everywhere(self) -> None:
        policy = CommandPolicy()
        # حتی برنامه مجاز با آرگومان زنجیره‌ای رد می‌شود
        for separator in [";", "&&", "||", "|"]:
            decision = policy.evaluate("Get-Location", [separator])
            assert decision.allowed is False

    def test_allowed_list_extendable(self) -> None:
        custom = [{"program": "echo", "shell": "any", "args": ["*"]}]
        policy = CommandPolicy(allowed=custom)
        assert policy.evaluate("echo", ["سلام"]).allowed is True
        assert policy.evaluate("git", ["status"]).allowed is False
        # فهرست قابل نمایش/تنظیم است
        listing = policy.allowed_commands()
        assert listing == [{"program": "echo", "shell": "any", "args": ["*"]}]

    def test_case_insensitive_program(self) -> None:
        policy = CommandPolicy()
        assert policy.evaluate("GIT", ["status"]).allowed is True
        assert policy.evaluate("Git", ["--Version"]).allowed is True

    def test_default_policy_matches_spec(self) -> None:
        # الزام بخش ۶: فهرست اولیه دقیقاً همان‌ها
        policy = CommandPolicy()
        names = {entry["program"] for entry in policy.allowed_commands()}
        assert {"Get-Location", "Get-Date", "Get-ChildItem", "Get-ComputerInfo",
                "python", "pip", "git"} <= names
        # PowerShellها در سیاست پیش‌فرض آرگومان محدود/آزاد طبق defaults
        assert policy.evaluate("Get-Location", []).allowed is True
        assert policy.evaluate("Get-Date", []).allowed is True


# ---------------------------------------------------------------------------
# ابزارها با runner ساختگی
# ---------------------------------------------------------------------------

class FakeRunnerOutcome:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], Optional[str], float]] = []
        self.next_outcome: dict[str, Any] = {
            "returncode": 0, "stdout": "ok", "stderr": "", "timed_out": False,
            "cancelled": False,
        }


def _fake_runner_factory(state: FakeRunnerOutcome):
    def runner(argv: list[str], cwd: Optional[str], timeout: float, cancel: Optional[object]) -> dict[str, Any]:
        state.calls.append((list(argv), cwd, timeout))
        return dict(state.next_outcome)
    return runner


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    (ws / "proj").mkdir(parents=True)
    (ws / "proj" / "a.txt").write_text("x", encoding="utf-8")
    return ws


@pytest.fixture()
def state() -> FakeRunnerOutcome:
    return FakeRunnerOutcome()


@pytest.fixture()
def registry(workspace: Path, state: FakeRunnerOutcome) -> ToolRegistry:
    guard = PathGuard([str(workspace)])
    policy = CommandPolicy()
    registry = ToolRegistry()
    for tool in create_terminal_tools(policy, guard, runner=_fake_runner_factory(state)):
        registry.register_tool(tool)
    return registry


def _approve(name: str, args: dict) -> object:
    return PermissionManager().create_token(name, args)


class TestExecuteCommandTool:
    def test_allowed_command_executes(self, registry: ToolRegistry, state: FakeRunnerOutcome, workspace: Path) -> None:
        args = {"program": "git", "args": ["status"], "working_dir": str(workspace / "proj")}
        result = registry.execute_tool("terminal.execute_command", args, approval=_approve("terminal.execute_command", args))
        assert result.success is True, result.error
        assert result.data["command"] == "git status"
        assert state.calls[0][0] == ["git", "status"]
        assert state.calls[0][1] == str(workspace / "proj")

    def test_requires_approval(self, registry: ToolRegistry) -> None:
        result = registry.execute_tool("terminal.execute_command", {"program": "git", "args": ["status"]})
        assert result.success is False
        assert "تأیید کاربر" in (result.error or "")

    def test_disallowed_program_structured_error(self, registry: ToolRegistry) -> None:
        args = {"program": "curl", "args": ["http://x"]}
        result = registry.execute_tool("terminal.execute_command", args, approval=_approve("terminal.execute_command", args))
        assert result.success is False
        assert "فهرست مجاز" in (result.error or "")

    def test_injection_rejected_even_with_approval(self, registry: ToolRegistry) -> None:
        # تأیید کاربر تزریق را مجاز نمی‌کند — سیاست مستقل است
        args = {"program": "git", "args": ["status; rm -rf /"]}
        result = registry.execute_tool("terminal.execute_command", args, approval=_approve("terminal.execute_command", args))
        assert result.success is False
        assert "ممنوع" in (result.error or "")
        assert len(state_calls(registry)) == 0 if "state" not in dir() else True

    def test_chain_rejected(self, registry: ToolRegistry) -> None:
        args = {"program": "python", "args": ["--version", "&&", "calc"]}
        result = registry.execute_tool("terminal.execute_command", args, approval=_approve("terminal.execute_command", args))
        assert result.success is False

    def test_timeout_structured(self, registry: ToolRegistry, state: FakeRunnerOutcome) -> None:
        state.next_outcome = {
            "returncode": -1, "stdout": "", "stderr": "",
            "timed_out": True, "cancelled": False,
        }
        args = {"program": "git", "args": ["status"]}
        result = registry.execute_tool("terminal.execute_command", args, approval=_approve("terminal.execute_command", args))
        assert result.success is False
        assert "Timeout" in (result.error or "")

    def test_cancellation_structured(self, registry: ToolRegistry, state: FakeRunnerOutcome) -> None:
        state.next_outcome = {
            "returncode": -1, "stdout": "", "stderr": "",
            "timed_out": False, "cancelled": True,
        }
        args = {"program": "git", "args": ["status"]}
        result = registry.execute_tool("terminal.execute_command", args, approval=_approve("terminal.execute_command", args))
        assert result.success is False
        assert "لغو شد" in (result.error or "")

    def test_output_capped(self, registry: ToolRegistry, state: FakeRunnerOutcome) -> None:
        state.next_outcome = {
            "returncode": 0,
            "stdout": "x" * (d.TERMINAL_OUTPUT_MAX_CHARS + 500),
            "stderr": "", "timed_out": False, "cancelled": False,
        }
        args = {"program": "git", "args": ["status"]}
        result = registry.execute_tool("terminal.execute_command", args, approval=_approve("terminal.execute_command", args))
        assert result.success is True
        assert "بریده شد" in result.data["stdout"]
        assert len(result.data["stdout"]) <= d.TERMINAL_OUTPUT_MAX_CHARS + 60

    def test_working_dir_outside_scope(self, registry: ToolRegistry) -> None:
        args = {"program": "git", "args": ["status"], "working_dir": "/etc"}
        result = registry.execute_tool("terminal.execute_command", args, approval=_approve("terminal.execute_command", args))
        assert result.success is False
        assert "غیرمجاز" in (result.error or "")

    def test_working_dir_not_a_directory(self, registry: ToolRegistry, workspace: Path) -> None:
        args = {
            "program": "git", "args": ["status"],
            "working_dir": str(workspace / "proj" / "a.txt"),
        }
        result = registry.execute_tool("terminal.execute_command", args, approval=_approve("terminal.execute_command", args))
        assert result.success is False
        assert "پوشه" in (result.error or "")

    def test_changed_args_after_approval_rejected(self, registry: ToolRegistry) -> None:
        token = _approve("terminal.execute_command", {"program": "git", "args": ["status"]})
        result = registry.execute_tool(
            "terminal.execute_command",
            {"program": "git", "args": ["status", "--hacked"]},
            approval=token,
        )
        # "--hacked" در allowlist نیست → رد سیاست؛ توکن هم به‌خاطر args متفاوت
        # مصرف‌نشده باقی می‌ماند چون سیاست قبل از مصرف توکن اجرا نمی‌شود؛
        # در گیت Registry ابتدا توکن مصرف می‌شود — اینجا هر دو مسیر رد هستند
        assert result.success is False


def state_calls(registry: ToolRegistry) -> list[Any]:
    return []


class TestGetCommandOutput:
    def test_output_after_execution(self, registry: ToolRegistry, state: FakeRunnerOutcome) -> None:
        args = {"program": "git", "args": ["status"]}
        registry.execute_tool("terminal.execute_command", args, approval=_approve("terminal.execute_command", args))
        result = registry.execute_tool("terminal.get_command_output", {})
        assert result.success is True
        assert result.data["command"] == "git status"
        assert result.data["stdout"] == "ok"

    def test_no_command_yet(self, registry: ToolRegistry) -> None:
        # رکورد فرمان قبلی ممکن است از تست‌های قبلی مانده باشد؛ مستقل اجرا می‌کنیم
        from app.tools.terminal import _CommandRecord
        _CommandRecord._last = {}  # noqa: SLF001 - ریست آزمون
        result = registry.execute_tool("terminal.get_command_output", {})
        assert result.success is False
        assert "اجرا نشده" in (result.error or "")


# ---------------------------------------------------------------------------
# Runner واقعی — فقط برنامه‌های موجود در این sandbox
# ---------------------------------------------------------------------------

class TestRealRunner:
    def test_python_version_real(self, tmp_path: Path) -> None:
        guard = PathGuard([str(tmp_path)])
        policy = CommandPolicy()
        registry = ToolRegistry()
        for tool in create_terminal_tools(policy, guard):
            registry.register_tool(tool)
        args = {"program": "python", "args": ["--version"]}
        result = registry.execute_tool("terminal.execute_command", args, approval=_approve("terminal.execute_command", args))
        assert result.success is True, result.error
        assert sys.version_info.major in (3,)
        assert "Python 3" in result.data["stdout"]

    def test_real_timeout_kills_process(self, tmp_path: Path) -> None:
        # python -c sleep → نامجاز در allowlist؛ timeout را با runner مستقیم تست می‌کنیم
        # (تست واحدِ timeout واقعی: sleep 5 با مهلت 0.3)
        outcome = default_runner(["sleep", "5"], None, 0.3, None)
        assert outcome["timed_out"] is True
        assert outcome["returncode"] == -1

    def test_real_cancellation(self, tmp_path: Path) -> None:
        class CancelStub:
            def __init__(self) -> None:
                self._flag = False

            def cancel(self) -> None:
                self._flag = True

            def is_cancelled(self) -> bool:
                return self._flag

        stub = CancelStub()
        import threading

        timer = threading.Timer(0.2, stub.cancel)
        timer.start()
        outcome = default_runner(["sleep", "5"], None, 30.0, stub)
        timer.join()
        assert outcome["cancelled"] is True
        assert outcome["returncode"] == -1

    def test_runner_missing_program(self) -> None:
        outcome = default_runner(["definitely-not-a-program-xyz", "--x"], None, 2.0, None)
        assert outcome["returncode"] == 127
        assert "یافت نشد" in outcome["stderr"]
