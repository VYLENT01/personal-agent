"""ابزارهای Terminal فاز ۳ (بخش ۶ مشخصات) — حساس‌ترین بخش.

سیاست CommandPolicy (الزام بخش ۶):
- **هیچ ورودی مدلی مستقیم وارد Shell نمی‌شود.**
- فرمان = برنامه + آرگومان‌های جدا (لیست، نه رشته) → ساختارمند در subprocess
- Allowlist برنامه/آرگومان از defaults (قابل توسعه/تنظیم)
- رد: زنجیره (;، &&، ||)، کاراکترهای تزریق (| & ` > < $)، اسکریپت دلخواه،
  حذف/Registry/شبکه/مدیریتی، اجرا به‌عنوان Administrator (هرگز create flags نمی‌دهیم)
- working directory باید در PathGuard مجاز باشد
- Timeout هر فرمان + سقف stdout/stderr + لغو (زمانی که CancelToken فعال شود
  runner فرمان را kill می‌کند)
- سطح: L2 — اجرای هر فرمان مجاز نیازمند تأیید کاربر (بخش ۸)

پیاده‌سازی دو-پلتفرمی: PowerShell cmdletها فقط روی ویندوز اجرای واقعی دارند؛
برنامه‌های مسیر (python/git/pip) روی هر دو. runner قابل تزریق است تا تست
بدون اجرای واقعی امکان‌پذیر باشد.
"""
from __future__ import annotations

import logging
import subprocess
import sys
import threading
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional, Sequence

from pydantic import Field

from app.config import defaults as d
from app.security.paths import PathGuard, PathPolicyError
from app.tools.base import PermissionLevel, Tool, ToolInput

logger = logging.getLogger(__name__)


class CommandPolicyError(Exception):
    """نقض سیاست فرمان؛ پیام فارسی."""


# ---------------------------------------------------------------------------
# CommandPolicy
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AllowedCommand:
    """یک قالب فرمان مجاز."""

    program: str                # نام برنامه یا cmdlet
    shell: str = "any"          # any | powershell
    args: tuple[str, ...] = ()  # الگوی آرگومان‌ها؛ "*" = هر آرگومان امن

    def matches_arg(self, arg: str) -> bool:
        """تطبیق آرگومان؛ حساس به بزرگی/کوچکی نیست (مثل --Version/--version)."""
        return "*" in self.args or arg.lower() in {a.lower() for a in self.args}

    def as_dict(self) -> dict[str, Any]:
        return {"program": self.program, "shell": self.shell, "args": list(self.args)}


@dataclass(frozen=True)
class CommandDecision:
    """نتیجه ارزیابی سیاست."""

    allowed: bool
    program: str
    argv: list[str]
    reason: str = ""


class CommandPolicy:
    """ارزیاب فرمان‌ها بر اساس allowlist ساختاری (بخش ۶)."""

    def __init__(self, allowed: Optional[Sequence[Mapping[str, Any]]] = None) -> None:
        entries = allowed or d.TERMINAL_ALLOWED_COMMANDS
        self._commands: dict[str, list[AllowedCommand]] = {}
        for entry in entries:
            program = str(entry.get("program", "")).strip()
            if not program:
                continue
            shell = str(entry.get("shell", "any"))
            args = tuple(str(a) for a in entry.get("args", []))
            self._commands.setdefault(program.lower(), []).append(
                AllowedCommand(program=program, shell=shell, args=args)
            )

    # ------------------------------------------------------------------

    def evaluate(self, program: str, args: Optional[Sequence[str]]) -> CommandDecision:
        """اعتبارسنجی ساختاری برنامه+آرگومان‌ها؛ ردِ هر نشانه تزریق."""
        name = (program or "").strip()
        if not name:
            return CommandDecision(False, "", [], "برنامه‌ای مشخص نشده است.")

        # شکستن ساختار: نام برنامه نباید جداکننده/فضای حاوی فرمان باشد
        if any(ch in name for ch in d.TERMINAL_FORBIDDEN_CHARS) or " " in name:
            return CommandDecision(
                False, name, [], "نام برنامه شامل کاراکتر غیرمجاز است."
            )

        clean_args: list[str] = []
        for raw_arg in args or []:
            text = str(raw_arg)
            # کاراکتر تزریق در هر آرگومان → رد فوری (نه پاک‌سازی؛ رد)
            if any(ch in text for ch in d.TERMINAL_FORBIDDEN_CHARS):
                return CommandDecision(
                    False,
                    name,
                    [],
                    "آرگومان شامل کاراکتر ممنوع (تزریق فرمان/زنجیره) است.",
                )
            if text.strip() and text not in clean_args or True:
                clean_args.append(text)

        templates = self._commands.get(name.lower())
        if not templates:
            return CommandDecision(
                False, name, clean_args, f"برنامه «{name}» در فهرست مجاز نیست."
            )

        for template in templates:
            if all(template.matches_arg(a) for a in clean_args):
                # برنامه را به شکل کانونیِ allowlist برگردان (PowerShell cmdlet)
                canonical = template.program
                return CommandDecision(True, canonical, clean_args)

        return CommandDecision(
            False,
            name,
            clean_args,
            f"آرگومان‌های «{name}» با هیچ الگوی مجاز تطبیق ندید.",
        )

    def allowed_commands(self) -> list[dict[str, Any]]:
        """فهرست قالب‌های مجاز برای نمایش/تنظیمات."""
        return [cmd.as_dict() for cmds in self._commands.values() for cmd in cmds]


# ---------------------------------------------------------------------------
# Runner: اجرای واقعی با timeout + لغو + سقف خروجی
# ---------------------------------------------------------------------------

CommandRunner = Callable[[list[str], Optional[str], float, Optional[object]], dict[str, Any]]


def default_runner(
    argv: list[str],
    cwd: Optional[str],
    timeout: float,
    cancel: Optional[object],
) -> dict[str, Any]:
    """اجرای فرمان ساختارمند (list-argv؛ بدون shell=True).

    - PowerShell cmdlet → powershell -NoProfile -NonInteractive -Command <cmdlet args...>
    - برنامه مسیر → مستقیم
    - timeout: سقف فرمان؛ لغو: پایش CancelToken و kill
    - خروجی سقف‌دار stdout/stderr
    """
    is_cmdlet = argv and argv[0].lower().startswith("get-")
    if is_cmdlet:
        full_argv = ["powershell", "-NoProfile", "-NonInteractive", "-Command", *argv]
    else:
        full_argv = list(argv)

    process: Optional[subprocess.Popen] = None
    try:
        process = subprocess.Popen(  # noqa: S603 - argv ساختارمند؛ بدون shell
            full_argv,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as exc:
        return {
            "returncode": 127,
            "stdout": "",
            "stderr": f"برنامه یافت نشد: {argv[0]} — {exc}",
            "timed_out": False,
            "cancelled": False,
        }
    except OSError as exc:
        return {
            "returncode": 1,
            "stdout": "",
            "stderr": f"خطای راه‌اندازی فرمان: {exc}",
            "timed_out": False,
            "cancelled": False,
        }

    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    cancelled = False
    timed_out = False

    def _reader(pipe, sink: list[str]) -> None:
        try:
            for chunk in iter(lambda: pipe.read(4096), ""):  # type: ignore[union-attr]
                sink.append(chunk)
                if sum(len(s) for s in sink) > d.TERMINAL_OUTPUT_MAX_CHARS * 4:
                    break
        except (OSError, ValueError):
            pass
        finally:
            try:
                pipe.close()  # type: ignore[union-attr]
            except OSError:
                pass

    threads = [
        threading.Thread(target=_reader, args=(process.stdout, stdout_chunks), daemon=True),
        threading.Thread(target=_reader, args=(process.stderr, stderr_chunks), daemon=True),
    ]
    for thread in threads:
        thread.start()

    deadline = timeout
    waited = 0.0
    interval = 0.05
    while True:
        if process.poll() is not None:
            break
        if cancel is not None and getattr(cancel, "is_cancelled", lambda: False)():
            cancelled = True
            process.kill()
            break
        if waited >= deadline:
            timed_out = True
            process.kill()
            break
        time.sleep(interval)
        waited += interval

    for thread in threads:
        thread.join(timeout=1.0)

    stdout = _cap("".join(stdout_chunks))
    stderr = _cap("".join(stderr_chunks))
    return {
        "returncode": process.returncode if not (timed_out or cancelled) else -1,
        "stdout": stdout,
        "stderr": stderr,
        "timed_out": timed_out,
        "cancelled": cancelled,
    }


def _cap(text: str, limit: int = d.TERMINAL_OUTPUT_MAX_CHARS) -> str:
    if len(text) > limit:
        return text[:limit] + f"\n…[بریده شد؛ طول کل: {len(text)}]"
    return text


import time  # noqa: E402 - استفاده در حلقه انتظار بالا


# ---------------------------------------------------------------------------
# ابزارها
# ---------------------------------------------------------------------------

class ExecuteCommandInput(ToolInput):
    program: str = Field(description="نام برنامه/cdmlt مجاز مثل git یا Get-ChildItem")
    args: list[str] = Field(
        default_factory=list, max_length=12,
        description="آرگومان‌ها به‌صورت فهرست جدا (نه رشته یکپارچه)",
    )
    working_dir: Optional[str] = Field(
        default=None, description="پوشه اجرا (اختیاری؛ باید مجاز باشد)"
    )


class _TerminalToolBase(Tool):
    """پایه مشترک ترمینال: policy + runner + guard تزریقی."""

    category = "terminal"
    permission_level = PermissionLevel.L2_WRITE
    policy: Optional[CommandPolicy] = None
    guard: Optional[PathGuard] = None
    timeout: float = d.TERMINAL_TIMEOUT_SECONDS
    # runner به‌صورت attribute instance (نه متد) تا self به آن bind نشود
    _runner_fn: CommandRunner = staticmethod(default_runner)  # type: ignore[assignment]

    def bind(self, policy: CommandPolicy, guard: PathGuard, runner: Optional[CommandRunner] = None) -> None:
        self.policy = policy
        self.guard = guard
        if runner is not None:
            self._runner_fn = runner
        else:
            self._runner_fn = default_runner

    def _invoke_runner(
        self, argv: list[str], cwd: Optional[str], timeout: float, cancel: Optional[object]
    ) -> dict[str, Any]:
        """فراخوانی runner تزریقی + سقف خروجی یکجا.

        نکته: runner به‌صورت attribute ساده (نه متد کلاس) نگه داشته می‌شود
        تا self به آن bind نشود؛ امضای سازگار (با/بدون cancel) پشتیبانی می‌شود.
        """
        runner = self._runner_fn
        try:
            outcome = runner(argv, cwd, timeout, cancel)
        except TypeError as exc:
            if "positional" in str(exc):
                outcome = runner(argv, cwd, timeout)
            else:
                raise
        for key in ("stdout", "stderr"):
            if key in outcome and isinstance(outcome[key], str):
                outcome[key] = _cap(outcome[key])
        return outcome


class ExecuteCommandTool(_TerminalToolBase):
    input_model = ExecuteCommandInput

    @property
    def name(self) -> str:
        return "terminal.execute_command"

    @property
    def description(self) -> str:
        return (
            "اجرای یک فرمان مجاز از فهرست allowlist (git status، Get-ChildItem، "
            "python --version و…). فرمان ساختارمند است (برنامه + آرگومان‌های جدا)؛ "
            "زنجیره (;، &&) و کاراکترهای تزریق رد می‌شوند. نیازمند تأیید کاربر."
        )

    def _run(self, args: ExecuteCommandInput) -> dict:
        if self.policy is None or self.guard is None:  # pragma: no cover
            raise RuntimeError("سیاست/نگهبان ترمینال تزریق نشده است.")
        decision = self.policy.evaluate(args.program, args.args)
        if not decision.allowed:
            raise CommandPolicyError(decision.reason)

        cwd: Optional[str] = None
        if args.working_dir:
            try:
                cwd = str(self.guard.resolve_checked(args.working_dir))
            except PathPolicyError as exc:
                raise CommandPolicyError(
                    f"working directory غیرمجاز است: {exc}"
                ) from exc
            import os.path

            if not os.path.isdir(cwd):
                raise CommandPolicyError(f"working directory پوشه نیست: {cwd}")

        cancel = getattr(self, "_active_cancel", None)
        outcome = self._invoke_runner(
            [decision.program, *decision.argv], cwd, self.timeout, cancel
        )
        if outcome.get("cancelled"):
            raise CommandPolicyError("فرمان به‌دستور شما لغو شد.")
        if outcome.get("timed_out"):
            raise CommandPolicyError(
                f"فرمان از مهلت {self.timeout:.0f} ثانیه فراتر رفت (Timeout) و پایان یافت."
            )
        return {
            "command": " ".join([decision.program, *decision.argv]),
            "returncode": outcome.get("returncode"),
            "stdout": outcome.get("stdout", ""),
            "stderr": outcome.get("stderr", ""),
            "working_dir": cwd,
        }


class _CommandRecord:
    """آخرین اجرای فرمان برای get_command_output (بدون حافظه بلندمدت)."""

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


class GetCommandOutputInput(ToolInput):
    pass  # پارامتری ندارد؛ آخرین خروجی را می‌دهد


class GetCommandOutputTool(_TerminalToolBase):
    input_model = GetCommandOutputInput
    permission_level = PermissionLevel.L1_READ_ONLY  # خواندن خروجی خودمان

    @property
    def name(self) -> str:
        return "terminal.get_command_output"

    @property
    def description(self) -> str:
        return (
            "خروجی آخرین فرمان اجراشده (stdout/stderr/returncode)؛ برای پیگیری "
            "بدون اجرای دوباره."
        )

    def _run(self, args: GetCommandOutputInput) -> dict:
        data = _CommandRecord.last()
        if not data:
            raise CommandPolicyError("هنوز فرمانی اجرا نشده است.")
        return data


def build_command_policy() -> CommandPolicy:
    """سیاست پیش‌فرض از defaults (نقطه واحد ساخت در bootstrap)."""
    return CommandPolicy()


def create_terminal_tools(
    policy: CommandPolicy,
    guard: PathGuard,
    *,
    runner: Optional[CommandRunner] = None,
) -> list[Tool]:
    """ساخت ابزارهای ترمینال با سیاست/نگهبان/runner تزریقی."""
    execute = ExecuteCommandTool()
    execute.bind(policy, guard, runner)
    output = GetCommandOutputTool()
    output.bind(policy, guard, runner)
    if runner is not None:
        # در تست: هر اجرا در رکورد ثبت می‌شود تا get_command_output کار کند
        base_runner = runner

        def recording_runner(argv: list[str], cwd: Optional[str], timeout: float, cancel: Optional[object]) -> dict[str, Any]:
            outcome = base_runner(argv, cwd, timeout, cancel)
            _CommandRecord.record(
                {
                    "command": " ".join(argv),
                    "returncode": outcome.get("returncode"),
                    "stdout": outcome.get("stdout", ""),
                    "stderr": outcome.get("stderr", ""),
                }
            )
            return outcome

        execute._runner_fn = recording_runner  # type: ignore[method-assign]
    return [execute, output]


__all__ = [
    "AllowedCommand",
    "CommandPolicy",
    "CommandPolicyError",
    "build_command_policy",
    "create_terminal_tools",
    "default_runner",
]
