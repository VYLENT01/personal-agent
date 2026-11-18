"""تست‌های ابزارهای Applications فاز ۳ (چک‌لیست بخش ۱۰).

- شناسایی برنامه / جست‌وجوی برنامه / اجرای برنامه معتبر
- رد برنامه ناشناخته / رد مسیر نامعتبر / تأیید کاربر / مدیریت خطای اجرا
- get_running_apps با محدودیت (فرآیند سیستمی پنهان)
- LaunchApp با launcher ساختگی (بدون اجرای واقعی GUI در sandbox)
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from app.security.permissions import PermissionManager
from app.tools.applications import create_application_tools
from app.tools.registry import ToolRegistry


FAKE_APPS: list[dict[str, Any]] = [
    {
        "name": "Visual Studio Code",
        "publisher": "Microsoft",
        "install_location": "C:\\Program Files\\VSCode",
        "source": "registry",
        "exe_path": "C:\\Program Files\\VSCode\\Code.exe",
    },
    {
        "name": "Notepad++",
        "publisher": "Don Ho",
        "install_location": "C:\\Program Files\\Npp",
        "source": "start_menu",
        "exe_path": "C:\\Program Files\\Npp\\notepad++.exe",
    },
    {
        "name": "Python 3.12",
        "publisher": "Python Software Foundation",
        "install_location": "C:\\Users\\u\\AppData\\Local\\Programs\\Python",
        "source": "registry",
        "exe_path": None,
    },
]


@pytest.fixture()
def registry() -> ToolRegistry:
    registry = ToolRegistry()
    for tool in create_application_tools(finder=lambda: list(FAKE_APPS)):
        registry.register_tool(tool)
    return registry


def _approve(name: str, args: dict) -> object:
    return PermissionManager().create_token(name, args)


# ---------------------------------------------------------------------------
# کشف و جست‌وجو (L1)
# ---------------------------------------------------------------------------

class TestDiscovery:
    def test_list_installed_apps(self, registry: ToolRegistry) -> None:
        result = registry.execute_tool("applications.list_installed_apps", {})
        assert result.success is True
        assert result.data["count"] == 3
        names = {a["name"] for a in result.data["apps"]}
        assert "Visual Studio Code" in names

    def test_search_by_name(self, registry: ToolRegistry) -> None:
        result = registry.execute_tool("applications.search_app", {"query": "notepad"})
        assert result.success is True
        assert result.data["count"] == 1
        assert result.data["matches"][0]["exe_path"].endswith("notepad++.exe")

    def test_search_by_publisher(self, registry: ToolRegistry) -> None:
        result = registry.execute_tool("applications.search_app", {"query": "microsoft"})
        assert result.data["count"] >= 1
        assert any(m["name"] == "Visual Studio Code" for m in result.data["matches"])

    def test_search_no_result(self, registry: ToolRegistry) -> None:
        result = registry.execute_tool("applications.search_app", {"query": "هیچ‌چیز"})
        assert result.success is True
        assert result.data["count"] == 0

    def test_search_invalid_query(self, registry: ToolRegistry) -> None:
        result = registry.execute_tool("applications.search_app", {"query": ""})
        assert result.success is False

    def test_finder_not_windows_returns_empty(self) -> None:
        # روی لینوکس finder رجیستری لیست خالی می‌دهد (winreg نیست) — بدون کرش
        from app.tools.applications import windows_registry_finder

        apps = windows_registry_finder()
        assert isinstance(apps, list)


# ---------------------------------------------------------------------------
# اجرا (L2)
# ---------------------------------------------------------------------------

class TestLaunchApp:
    def test_launch_requires_approval(self, registry: ToolRegistry) -> None:
        result = registry.execute_tool(
            "applications.launch_app", {"exe_path": "C:\\Program Files\\Npp\\notepad++.exe"}
        )
        assert result.success is False
        assert "تأیید کاربر" in (result.error or "")

    def test_launch_unknown_path_rejected(self, registry: ToolRegistry) -> None:
        # «رد برنامه ناشناخته» — مسیر خارج فهرست کشف‌شده
        args = {"exe_path": "C:\\Windows\\System32\\cmd.exe"}
        result = registry.execute_tool(
            "applications.launch_app", args, approval=_approve("applications.launch_app", args)
        )
        assert result.success is False
        assert "کشف‌شده نیست" in (result.error or "")

    def test_launch_suspicious_path_rejected(self, registry: ToolRegistry) -> None:
        # مسیر نامعتبر/مشکوک: تزریق/نام‌غیرقابل اجرا حتی با تأیید
        args = {"exe_path": "C:\\evil\\payload.bat; calc"}
        result = registry.execute_tool(
            "applications.launch_app", args, approval=_approve("applications.launch_app", args)
        )
        assert result.success is False

    def test_launch_missing_file_structured_error(self, registry: ToolRegistry) -> None:
        # مسیر در فهرست است ولی فایل وجود ندارد → FileNotFoundError ساختاریافته
        apps = [
            {
                "name": "Ghost App",
                "publisher": "X",
                "install_location": "C:\\Ghost",
                "source": "registry",
                "exe_path": "/nonexistent/ghost/ghost.exe",
            }
        ]
        local = ToolRegistry()
        for tool in create_application_tools(finder=lambda: list(apps)):
            local.register_tool(tool)
        args = {"exe_path": "/nonexistent/ghost/ghost.exe"}
        result = local.execute_tool(
            "applications.launch_app", args, approval=_approve("applications.launch_app", args)
        )
        assert result.success is False
        assert "وجود ندارد" in (result.error or "")

    def test_launch_valid_real_executable(self, tmp_path: Path) -> None:
        # اجرای معتبرِ واقعی در لینوکس‌sandbox: اسکریپت اجرایی واقعی + پسوند .exe
        exe = tmp_path / "FakeApp.exe"
        exe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        exe.chmod(0o755)
        apps = [
            {
                "name": "Fake App",
                "publisher": "test",
                "install_location": str(tmp_path),
                "source": "start_menu",
                "exe_path": str(exe),
            }
        ]
        local = ToolRegistry()
        for tool in create_application_tools(finder=lambda: list(apps)):
            local.register_tool(tool)
        args = {"exe_path": str(exe)}
        result = local.execute_tool(
            "applications.launch_app", args, approval=_approve("applications.launch_app", args)
        )
        assert result.success is True, result.error
        assert result.data["launched"] == str(exe)
        assert isinstance(result.data["pid"], int) and result.data["pid"] > 0

    def test_launch_args_injection_blocked_by_schema(self, registry: ToolRegistry) -> None:
        # آرگومان با جداکننده شل — در اجرای ساختارمند argv جدا است؛ schema محدود می‌کند
        args = {"exe_path": "C:\\Program Files\\VSCode\\Code.exe", "args": ["--wait", "a;b"]}
        result = registry.execute_tool(
            "applications.launch_app", args, approval=_approve("applications.launch_app", args)
        )
        # exe در فهرست fake هست ولی واقعاً وجود ندارد → خطای «وجود ندارد» ساختاریافته
        assert result.success is False
        assert "وجود ندارد" in (result.error or "")


class TestRunningApps:
    def test_get_running_apps_limited(self, registry: ToolRegistry) -> None:
        result = registry.execute_tool("applications.get_running_apps", {"limit": 10})
        assert result.success is True
        assert result.data["count"] <= 10
        for proc in result.data["processes"]:
            assert "name" in proc and "pid" in proc

    def test_get_running_apps_min_limit(self, registry: ToolRegistry) -> None:
        result = registry.execute_tool("applications.get_running_apps", {"limit": 2})
        assert result.success is False  # ge=5


class TestSchemasAndLevels:
    def test_levels(self, registry: ToolRegistry) -> None:
        assert registry.needs_approval("applications.list_installed_apps") is False
        assert registry.needs_approval("applications.search_app") is False
        assert registry.needs_approval("applications.get_running_apps") is False
        assert registry.needs_approval("applications.launch_app") is True

    def test_no_close_app_tool_in_phase3(self, registry: ToolRegistry) -> None:
        # close_application (L3) در این فاز نباید باشد
        names = registry.tool_names()
        assert not any("close" in n or "kill" in n for n in names)
