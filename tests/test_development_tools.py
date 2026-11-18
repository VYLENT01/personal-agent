"""تست‌های ابزارهای Development فاز ۳ (چک‌لیست بخش ۱۰).

- شناسایی پروژه / خواندن ساختار / خواندن فایل پروژه
- اجرای تست موفق / اجرای تست ناموفق / Timeout تست
- مسیر پروژه نامعتبر / محدودیت دسترسی / get_git_status
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.security.paths import PathGuard
from app.security.permissions import PermissionManager
from app.tools.development import create_development_tools
from app.tools.registry import ToolRegistry


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    proj = ws / "mylib"
    (proj / "tests").mkdir(parents=True)
    (proj / "src").mkdir()
    (proj / "src" / "calc.py").write_text(
        "def add(a, b):\n    return a + b\n", encoding="utf-8"
    )
    (proj / "tests" / "test_calc.py").write_text(
        "import sys; sys.path.insert(0, 'src')\n"
        "from calc import add\n"
        "def test_add():\n    assert add(1, 2) == 3\n",
        encoding="utf-8",
    )
    (proj / "pyproject.toml").write_text(
        '[project]\nname = "mylib"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    # git واقعی برای get_git_status
    subprocess.run(["git", "init", "-q"], cwd=str(proj), check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=str(proj), check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=str(proj), check=True)
    subprocess.run(["git", "add", "."], cwd=str(proj), check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=str(proj), check=True)
    (proj / "src" / "calc.py").write_text(
        "def add(a, b):\n    return a + b  # changed\n", encoding="utf-8"
    )
    return ws


@pytest.fixture()
def registry(workspace: Path) -> ToolRegistry:
    guard = PathGuard([str(workspace)])
    registry = ToolRegistry()
    for tool in create_development_tools(guard):
        registry.register_tool(tool)
    return registry


def _approve(name: str, args: dict) -> object:
    return PermissionManager().create_token(name, args)


# ---------------------------------------------------------------------------
# L1 — بازرسی/ساختار/خواندن
# ---------------------------------------------------------------------------

class TestInspect:
    def test_inspect_python_project(self, registry: ToolRegistry, workspace: Path) -> None:
        result = registry.execute_tool(
            "development.inspect_project", {"project_path": str(workspace / "mylib")}
        )
        assert result.success is True
        assert "python" in result.data["kinds"]
        assert result.data["has_git"] is True
        assert result.data["test_framework"] == "pytest/unittest"
        assert "pyproject.toml" in result.data["key_files"]

    def test_inspect_invalid_path(self, registry: ToolRegistry, tmp_path: Path) -> None:
        result = registry.execute_tool(
            "development.inspect_project", {"project_path": str(tmp_path)}
        )
        assert result.success is False
        assert "مجاز" in (result.error or "")

    def test_inspect_not_a_directory(self, registry: ToolRegistry, workspace: Path) -> None:
        result = registry.execute_tool(
            "development.inspect_project",
            {"project_path": str(workspace / "mylib" / "pyproject.toml")},
        )
        assert result.success is False
        assert "پوشه" in (result.error or "")


class TestStructureAndRead:
    def test_project_structure(self, registry: ToolRegistry, workspace: Path) -> None:
        result = registry.execute_tool(
            "development.get_project_structure", {"project_path": str(workspace / "mylib")}
        )
        assert result.success is True
        assert result.data["dirs"] >= 2
        assert result.data["files"] >= 3
        names = str(result.data["tree"])
        assert "src" in names and "tests" in names

    def test_structure_ignores_dotgit(self, registry: ToolRegistry, workspace: Path) -> None:
        result = registry.execute_tool(
            "development.get_project_structure", {"project_path": str(workspace / "mylib")}
        )
        assert '"name": ".git"' not in str(result.data["tree"])

    def test_read_project_file(self, registry: ToolRegistry, workspace: Path) -> None:
        result = registry.execute_tool(
            "development.read_project_file",
            {
                "project_path": str(workspace / "mylib"),
                "relative_path": "src/calc.py",
            },
        )
        assert result.success is True
        assert "def add" in result.data["content"]
        assert result.data["encoding"] == "utf-8"

    def test_read_project_file_traversal(self, registry: ToolRegistry, workspace: Path) -> None:
        result = registry.execute_tool(
            "development.read_project_file",
            {
                "project_path": str(workspace / "mylib"),
                "relative_path": "../outside.py",
            },
        )
        assert result.success is False
        assert "نامعتبر" in (result.error or "") or "مجاز" in (result.error or "")

    def test_read_project_file_absolute_rejected(self, registry: ToolRegistry, workspace: Path) -> None:
        result = registry.execute_tool(
            "development.read_project_file",
            {
                "project_path": str(workspace / "mylib"),
                "relative_path": "/etc/passwd",
            },
        )
        assert result.success is False

    def test_read_project_file_missing(self, registry: ToolRegistry, workspace: Path) -> None:
        result = registry.execute_tool(
            "development.read_project_file",
            {
                "project_path": str(workspace / "mylib"),
                "relative_path": "src/نبود.py",
            },
        )
        assert result.success is False


# ---------------------------------------------------------------------------
# L2 — run_tests (اجرا واقعی pytest در پروژه واقعی)
# ---------------------------------------------------------------------------

class TestRunTests:
    def test_run_tests_passing_real(self, registry: ToolRegistry, workspace: Path) -> None:
        args = {"project_path": str(workspace / "mylib")}
        result = registry.execute_tool(
            "development.run_tests", args, approval=_approve("development.run_tests", args)
        )
        assert result.success is True, result.error
        assert result.data["status"] == "passed"
        assert "1 passed" in result.data["summary"]
        assert result.data["duration"] >= 0.0

    def test_run_tests_failing_real(self, registry: ToolRegistry, workspace: Path) -> None:
        (workspace / "mylib" / "tests" / "test_fail.py").write_text(
            "def test_boom():\n    assert 1 == 2\n", encoding="utf-8"
        )
        args = {"project_path": str(workspace / "mylib")}
        result = registry.execute_tool(
            "development.run_tests", args, approval=_approve("development.run_tests", args)
        )
        assert result.success is True  # اجرای ابزار موفق؛ وضعیت تست ناموفق
        assert result.data["status"] == "failed"
        assert "failed" in result.data["summary"]

    def test_run_tests_requires_approval(self, registry: ToolRegistry, workspace: Path) -> None:
        result = registry.execute_tool(
            "development.run_tests", {"project_path": str(workspace / "mylib")}
        )
        assert result.success is False
        assert "تأیید کاربر" in (result.error or "")

    def test_run_tests_timeout_real(self, registry: ToolRegistry, workspace: Path) -> None:
        # تست خواب‌آلود با مهلت کوتاه → Timeout ساختاریافته
        (workspace / "mylib" / "tests" / "test_sleep.py").write_text(
            "import time\ndef test_slow():\n    time.sleep(10)\n", encoding="utf-8"
        )
        args = {"project_path": str(workspace / "mylib"), "timeout": 6.0}
        result = registry.execute_tool(
            "development.run_tests", args, approval=_approve("development.run_tests", args)
        )
        assert result.success is False
        assert "Timeout" in (result.error or "") or "فراتر رفت" in (result.error or "")

    def test_run_tests_outside_scope(self, registry: ToolRegistry, tmp_path: Path) -> None:
        other = tmp_path / "other"
        other.mkdir()
        args = {"project_path": str(other)}
        result = registry.execute_tool(
            "development.run_tests", args, approval=_approve("development.run_tests", args)
        )
        assert result.success is False
        assert "مجاز" in (result.error or "")

    def test_get_test_results_after_run(self, registry: ToolRegistry, workspace: Path) -> None:
        from app.tools.development import _TestRunRecord

        _TestRunRecord._last = {}  # noqa: SLF001 - ریست آزمون
        empty = registry.execute_tool("development.get_test_results", {})
        assert empty.success is False
        assert "اجرا نشده" in (empty.error or "")

        args = {"project_path": str(workspace / "mylib")}
        registry.execute_tool(
            "development.run_tests", args, approval=_approve("development.run_tests", args)
        )
        result = registry.execute_tool("development.get_test_results", {})
        assert result.success is True
        assert result.data["status"] == "passed"


class TestGitStatus:
    def test_get_git_status_real(self, registry: ToolRegistry, workspace: Path) -> None:
        result = registry.execute_tool(
            "development.get_git_status", {"project_path": str(workspace / "mylib")}
        )
        assert result.success is True, result.error
        assert "master" in result.data["branch"] or "main" in result.data["branch"]
        assert result.data["changed_count"] == 1
        assert any("calc.py" in c for c in result.data["changes"])

    def test_git_status_not_a_repo(self, registry: ToolRegistry, workspace: Path) -> None:
        plain = workspace / "plain"
        plain.mkdir()
        result = registry.execute_tool(
            "development.get_git_status", {"project_path": str(plain)}
        )
        assert result.success is False
        assert "git نیست" in (result.error or "")


class TestLevelsAndSchemas:
    def test_levels(self, registry: ToolRegistry) -> None:
        for name in (
            "development.inspect_project", "development.get_project_structure",
            "development.read_project_file", "development.get_test_results",
            "development.get_git_status",
        ):
            assert registry.needs_approval(name) is False, name
        assert registry.needs_approval("development.run_tests") is True

    def test_six_tools_registered(self, registry: ToolRegistry) -> None:
        expected = {
            "development.get_git_status", "development.get_project_structure",
            "development.get_test_results", "development.inspect_project",
            "development.read_project_file", "development.run_tests",
        }
        assert {n for n in registry.tool_names() if n.startswith("development.")} == expected
