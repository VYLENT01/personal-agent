"""ابزارهای Development فاز ۳ (بخش ۷ مشخصات).

شش ابزار برای تعامل پایه‌ای با پروژه‌های برنامه‌نویسی:
- inspect_project (L1): تشخیص نوع پروژه (python/node/embedded/git) + فایل‌های کلیدی
- get_project_structure (L1): درخت (بازاستفاده از منطق درخت filesystem — نه کپی)
- read_project_file (L1): خواندن فایل پروژه از محدوده مجاز
- run_tests (L2): اجرای pytest در پوشه پروژه مجاز، با timeout و خروجی محدود
- get_test_results (L1): خلاصه آخرین اجرای تست
- get_git_status (L1): وضعیت git از مسیر مجاز (خروجی ساختارمند)

قوانین بخش ۷:
- اجرای تست فقط در پوشه پروژه معتبرِ داخل محدوده مجاز (PathGuard)
- timeout تست‌ها (DEV_TEST_TIMEOUT_SECONDS)؛ خروجی محدود/خوانا
- بدون Administrator؛ اجرای pytest با argv ساختارمند (بدون shell)
- Development دور زدن Terminal ندارد: فقط python -m pytest می‌راند (ثابت در کد)
- تغییر فایل‌های پروژه → فقط از طریق ابزارهای filesystem (این ماژول فقط-خواندنی/اجرا-تست)
"""
from __future__ import annotations

import json
import logging
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Optional

from pydantic import Field

from app.config import defaults as d
from app.security.paths import PathGuard, PathPolicyError
from app.tools.base import PermissionLevel, Tool, ToolInput
from app.tools.filesystem import decode_text, _truncate

logger = logging.getLogger(__name__)

# امضاهای پروژه → نوع
_PROJECT_SIGNATURES = {
    "pyproject.toml": "python",
    "setup.py": "python",
    "requirements.txt": "python",
    "package.json": "node",
    "platformio.ini": "embedded",
    "CMakeLists.txt": "embedded",
}
_TEST_FRAMEWORKS = {
    "pytest": ("tests", "pytest", "pyproject.toml"),
    "unittest": ("tests", "test_"),
}


class _DevToolBase(Tool):
    """پایه مشترک: PathGuard تزریقی."""

    category = "development"
    guard: Optional[PathGuard] = None

    def bind_guard(self, guard: PathGuard) -> None:
        self.guard = guard

    def _resolve_project(self, raw: str) -> Path:
        if self.guard is None:  # pragma: no cover
            raise RuntimeError("PathGuard تزریق نشده است.")
        try:
            project = self.guard.resolve_checked(raw)
        except PathPolicyError as exc:
            raise PermissionError(str(exc)) from exc
        if not project.is_dir():
            raise NotADirectoryError(f"مسیر پروژه پوشه نیست: {project}")
        return project


# ---------------------------------------------------------------------------
# ورودی‌ها
# ---------------------------------------------------------------------------

class ProjectPathInput(ToolInput):
    project_path: str = Field(description="مسیر ریشه پروژه (داخل محدوده مجاز)")


class ReadProjectFileInput(ToolInput):
    project_path: str = Field(description="مسیر ریشه پروژه")
    relative_path: str = Field(
        description="مسیر فایل نسبت به ریشه پروژه (مثل src/main.py)"
    )


class RunTestsInput(ToolInput):
    project_path: str = Field(description="مسیر ریشه پروژه با تست‌ها")
    timeout: float = Field(
        default=d.DEV_TEST_TIMEOUT_SECONDS, ge=5.0, le=600.0,
        description="مهلت اجرای تست (ثانیه)",
    )


class StructureInput(ProjectPathInput):
    max_depth: int = Field(default=d.MAX_TREE_DEPTH, ge=1, le=d.MAX_TREE_DEPTH)


# ---------------------------------------------------------------------------
# ابزارها
# ---------------------------------------------------------------------------

class InspectProjectTool(_DevToolBase):
    input_model = ProjectPathInput
    permission_level = PermissionLevel.L1_READ_ONLY

    @property
    def name(self) -> str:
        return "development.inspect_project"

    @property
    def description(self) -> str:
        return (
            "تشخیص نوع پروژه (python/node/embedded)، ابزار ساخت، فریم‌ورک تست و "
            "وجود git؛ برای برنامه‌ریزی کارهای توسعه."
        )

    def _run(self, args: ProjectPathInput) -> dict:
        project = self._resolve_project(args.project_path)
        kinds: list[str] = []
        files = {item.name for item in project.iterdir() if item.is_file()}
        for signature, kind in _PROJECT_SIGNATURES.items():
            if signature in files:
                if kind not in kinds:
                    kinds.append(kind)
        has_git = (project / ".git").exists()
        test_framework = None
        if (project / "tests").is_dir() or any(f.startswith("test_") for f in files):
            test_framework = "pytest/unittest"
        key_files = sorted(
            f for f in files
            if f in (*_PROJECT_SIGNATURES, "README.md", "README.rst", ".gitignore", "Makefile")
        )
        python_count = sum(1 for f in project.rglob("*.py"))
        return {
            "project": str(project),
            "kinds": kinds or ["unknown"],
            "has_git": has_git,
            "test_framework": test_framework,
            "key_files": key_files,
            "python_files": python_count if python_count < 500 else "500+",
        }


class GetProjectStructureTool(_DevToolBase):
    input_model = StructureInput
    permission_level = PermissionLevel.L1_READ_ONLY

    @property
    def name(self) -> str:
        return "development.get_project_structure"

    @property
    def description(self) -> str:
        return (
            f"درخت ساختار پروژه تا عمق {d.MAX_TREE_DEPTH} (پوشه‌ها + تعداد فایل‌ها؛ "
            "node_modules/.git نادیده)."
        )

    def _run(self, args: StructureInput) -> dict:
        project = self._resolve_project(args.project_path)
        tree: dict[str, Any] = {"name": project.name or str(project), "children": []}
        total = {"dirs": 0, "files": 0}
        self._walk(tree, project, args.max_depth, total)
        return {"project": str(project), "dirs": total["dirs"], "files": total["files"], "tree": tree}

    def _walk(self, node: dict[str, Any], folder: Path, depth: int, total: dict[str, int]) -> None:
        children = node["children"]
        try:
            items = sorted(folder.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except (PermissionError, OSError):
            node["error"] = "دسترسی رد شد"
            return
        for item in items:
            if item.name in {".git", "node_modules", "__pycache__", ".venv", "venv"}:
                continue
            if item.name.startswith("."):
                continue
            if item.is_dir():
                total["dirs"] += 1
                child: dict[str, Any] = {"name": item.name, "children": []}
                children.append(child)
                if depth > 1 and total["dirs"] + total["files"] < d.MAX_LIST_ENTRIES:
                    self._walk(child, item, depth - 1, total)
            else:
                total["files"] += 1
                if len(children) < d.MAX_LIST_ENTRIES:
                    children.append({"name": item.name, "file": True})


class ReadProjectFileTool(_DevToolBase):
    input_model = ReadProjectFileInput
    permission_level = PermissionLevel.L1_READ_ONLY

    @property
    def name(self) -> str:
        return "development.read_project_file"

    @property
    def description(self) -> str:
        return (
            f"خواندن فایل پروژه (مسیر نسبی) تا سقف {d.MAX_READ_BYTES // 1024}KB؛ "
            "باینری رد می‌شود."
        )

    def _run(self, args: ReadProjectFileInput) -> dict:
        if self.guard is None:  # pragma: no cover
            raise RuntimeError("PathGuard تزریق نشده است.")
        project = self._resolve_project(args.project_path)
        rel = (args.relative_path or "").strip().replace("\\", "/")
        if not rel or rel.startswith("/") or ".." in Path(rel).parts:
            raise PermissionError("مسیر نسبی نامعتبر است (بدون .. و مطلق).")
        target = project / rel
        try:
            resolved = self.guard.resolve_checked(str(target))
        except PathPolicyError as exc:
            raise PermissionError(str(exc)) from exc
        if resolved.is_dir():
            raise IsADirectoryError("مسیر پوشه است.")
        try:
            data = resolved.read_bytes()
        except PermissionError as exc:
            raise PermissionError(f"دسترسی خواندن رد شد: {resolved} — {exc}") from exc
        if len(data) > d.MAX_READ_BYTES:
            data = data[: d.MAX_READ_BYTES]
        text, encoding = decode_text(data)
        if text is None:
            raise ValueError(f"فایل باینری است: {rel}")
        return {"path": rel, "encoding": encoding, "content": _truncate(text)}


class _TestRunRecord:
    """آخرین اجرای تست برای get_test_results."""

    _lock = threading.Lock()
    _last: dict[str, Any] = {}

    @classmethod
    def record(cls, data: dict[str, Any]) -> None:
        with cls._lock:
            cls._last = data

    @classmethod
    def last(cls) -> dict[str, Any]:
        with cls._lock:
            return dict(cls._last)


class RunTestsTool(_DevToolBase):
    input_model = RunTestsInput
    permission_level = PermissionLevel.L2_WRITE

    @property
    def name(self) -> str:
        return "development.run_tests"

    @property
    def description(self) -> str:
        return (
            "اجرای تست‌های پروژه با pytest در پوشه پروژه (از محدوده مجاز)؛ "
            "با timeout و خروجی خلاصه/محدود. نیازمند تأیید کاربر."
        )

    def _run(self, args: RunTestsInput) -> dict:
        import sys as _sys

        project = self._resolve_project(args.project_path)
        argv = [_sys.executable, "-m", "pytest", "-q", "--no-header", "-x", "--tb=short"]
        # argv کاملاً ثابت است؛ هیچ ورودی مدل وارد فرمان نمی‌شود (بخش ۷:
        # «ابزار Development نباید برای دور زدن محدودیت Terminal استفاده شود»)
        started = time.monotonic()
        try:
            completed = subprocess.run(  # noqa: S603 - argv ثابت ساختارمند
                argv,
                cwd=str(project),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=args.timeout,
            )
        except subprocess.TimeoutExpired:
            _TestRunRecord.record(
                {
                    "project": str(project),
                    "status": "timeout",
                    "summary": f"اجرا از مهلت {args.timeout:.0f} ثانیه فراتر رفت.",
                }
            )
            raise TimeoutError(
                f"اجرای تست از مهلت {args.timeout:.0f} ثانیه فراتر رفت (Timeout)."
            )
        except FileNotFoundError as exc:
            raise OSError(f"پایتون برای اجرای pytest یافت نشد: {exc}") from exc
        except PermissionError as exc:
            raise PermissionError(f"دسترسی اجرا رد شد: {exc}") from exc
        duration = round(time.monotonic() - started, 1)
        tail = _tail(completed.stdout or "", 60)
        summary = _parse_pytest_summary(completed.stdout or "", completed.returncode)
        record = {
            "project": str(project),
            "status": "passed" if completed.returncode == 0 else "failed",
            "returncode": completed.returncode,
            "duration": duration,
            "summary": summary,
            "tail": tail,
        }
        _TestRunRecord.record(record)
        return record


class GetTestResultsTool(_DevToolBase):
    input_model = ToolInput
    permission_level = PermissionLevel.L1_READ_ONLY

    @property
    def name(self) -> str:
        return "development.get_test_results"

    @property
    def description(self) -> str:
        return "خلاصه آخرین اجرای تست (وضعیت/مدت/خلاصه/انتهای خروجی)."

    def _run(self, args: ToolInput) -> dict:
        data = _TestRunRecord.last()
        if not data:
            raise LookupError("هنوز تستی اجرا نشده است.")
        return data


class GetGitStatusTool(_DevToolBase):
    input_model = ProjectPathInput
    permission_level = PermissionLevel.L1_READ_ONLY

    @property
    def name(self) -> str:
        return "development.get_git_status"

    @property
    def description(self) -> str:
        return (
            "وضعیت git پروژه (شاخه، تغییرات خلاصه) از مسیر مجاز؛ فقط-خواندنی و "
            "بدون اجرای دستور دلخواه (git status ثابت)."
        )

    def _run(self, args: ProjectPathInput) -> dict:
        project = self._resolve_project(args.project_path)
        if not (project / ".git").exists():
            raise LookupError("پروژه مخزن git نیست.")
        argv = ["git", "status", "--short", "--branch"]
        try:
            completed = subprocess.run(  # noqa: S603 - argv ثابت ساختارمند
                argv,
                cwd=str(project),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15.0,
            )
        except FileNotFoundError as exc:
            raise OSError(f"git در دسترس نیست: {exc}") from exc
        except subprocess.TimeoutExpired:
            raise TimeoutError("git status از مهلت ۱۵ ثانیه فراتر رفت.")
        output = (completed.stdout or "").strip()
        if completed.returncode != 0:
            raise OSError(
                f"git status ناموفق بود (کد {completed.returncode}): "
                f"{(completed.stderr or '').strip()[:200]}"
            )
        lines = output.splitlines()
        branch = lines[0].replace("##", "").strip() if lines else ""
        changes = [line for line in lines[1:] if line.strip()]
        return {
            "project": str(project),
            "branch": branch,
            "changed_count": len(changes),
            "changes": changes[: d.MAX_LIST_ENTRIES],
        }


# ---------------------------------------------------------------------------
# کمکی‌ها
# ---------------------------------------------------------------------------

def _tail(text: str, lines: int) -> str:
    all_lines = text.splitlines()
    tail = "\n".join(all_lines[-lines:])
    return _truncate(tail, 3000)


def _parse_pytest_summary(stdout: str, returncode: int) -> str:
    for line in reversed(stdout.splitlines()):
        if "passed" in line or "failed" in line or "error" in line:
            return line.strip()[:200]
    if returncode == 0:
        return "OK (بدون خلاصه pytest)"
    return "ناموفق (بدون خلاصه pytest)"


def create_development_tools(guard: PathGuard) -> list[Tool]:
    """شش ابزار development با guard مشترک."""
    tools = [
        InspectProjectTool(),
        GetProjectStructureTool(),
        ReadProjectFileTool(),
        RunTestsTool(),
        GetTestResultsTool(),
        GetGitStatusTool(),
    ]
    for tool in tools:
        tool.bind_guard(guard)
    return tools


__all__ = ["create_development_tools"]
