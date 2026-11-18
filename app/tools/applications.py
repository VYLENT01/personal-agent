"""ابزارهای Applications فاز ۳ (بخش ۵ مشخصات).

چهار ابزار:
- list_installed_apps (L1): کشف از Windows Registry (HKCU/HKLM Uninstall keys)
  + میانبرهای Start Menu — با لایه Finder قابل‌تزریق برای تست
- search_app (L1): جست‌وجوی نام/نام‌انتشاردهنده در همان فهرست
- launch_app (L2): فقط از مسیر «کشف‌شده و معتبر» — نه Shell آزاد؛ نیازمند تأیید کاربر
- get_running_apps (L1): psutil با محدودیت (بدون فرآیند سیستمی حساس)

الزامات بخش ۵:
- شناسایی قابل اعتماد (Registry + Start Menu؛ دو منبع مستقل)
- اجرای فقط از مسیر معتبر (.exe از فهرست کشف‌شده یا در محدوده مجاز)
- «اجرای برنامه از طریق Shell آزاد ممنوع» → subprocess بدون shell با argv=[مسیر]
- نامشناخته/مسیر مشکوک اجرا نمی‌شود (validate قبل از اجرا)
- نیازمند تأیید کاربر (L2)
- بدون Administrator (Registry فقط خوانده می‌شود؛ HKCU اول)
- سازگاری Windows 10/11: کلیدهای استاندارد Uninstall + Start Menu Programs

روی لینوکس/تست: AppFinder ساختگی تزریق می‌شود؛ کشف واقعی فقط ویندوز.
"""
from __future__ import annotations

import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Optional, Protocol

from pydantic import Field

from app.config import defaults as d
from app.tools.base import PermissionLevel, Tool, ToolInput

logger = logging.getLogger(__name__)

# رجیستری ویندوز فقط در این تابع دیده می‌شود (گارد import)
_UNINSTALL_KEYS = (
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
    r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
)
_START_MENU_DIRS = (
    r"%APPDATA%\Microsoft\Windows\Start Menu\Programs",
    r"%PROGRAMDATA%\Microsoft\Windows\Start Menu\Programs",
)


class AppEntry(dict):
    """یک برنامه کشف‌شده: name/path/publisher (dict subclass برای سازگاری)."""


AppFinder = Callable[[], list[dict[str, Any]]]


def windows_registry_finder() -> list[dict[str, Any]]:
    """کشف برنامه‌های نصب‌شده از Registry + Start Menu (فقط ویندوز)."""
    apps: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    try:
        import winreg  # noqa: PLC0415 - فقط ویندوز
    except ImportError:
        logger.debug("winreg در دسترس نیست (غیر-ویندوز)؛ کشف رجیستری skip شد")
        return apps

    for hive, path_template in ((winreg.HKEY_CURRENT_USER, _UNINSTALL_KEYS[0]), (winreg.HKEY_LOCAL_MACHINE, _UNINSTALL_KEYS[0]), (winreg.HKEY_LOCAL_MACHINE, _UNINSTALL_KEYS[1])):
        try:
            root = winreg.OpenKey(hive, path_template)
        except OSError:
            continue
        index = 0
        while True:
            try:
                subkey_name = winreg.EnumKey(root, index)
                index += 1
            except OSError:
                break
            try:
                with winreg.OpenKey(root, subkey_name) as sub:
                    name = winreg.QueryValueEx(sub, "DisplayName")[0]
                    try:
                        location = winreg.QueryValueEx(sub, "InstallLocation")[0]
                    except OSError:
                        location = ""
                    try:
                        publisher = winreg.QueryValueEx(sub, "Publisher")[0]
                    except OSError:
                        publisher = ""
            except OSError:
                continue
            if not name:
                continue
            # مسیر exe مستقیم از UninstallString معمولاً با quote/uninstall است؛
            # فقط به‌عنوان سرنخ ذخیره می‌شود، اجرای مستقیم ممنوع (launch فقط از مسیر معتبر)
            clean_name = str(name)
            clean_loc = str(location)
            key = (clean_name.lower(), clean_loc.lower())
            if key in {(a["name"].lower(), a["install_location"].lower()) for a in apps}:
                continue
            apps.append(
                {
                    "name": clean_name,
                    "publisher": str(publisher),
                    "install_location": clean_loc,
                    "source": "registry",
                    "exe_path": None,
                }
            )

    # Start Menu: میانبرهای .lnk → هدف exe با COM فقط ویندوز؛ مسیر پوشه کافی است
    for template in _START_MENU_DIRS:
        base = Path(os.path.expandvars(template))
        if not base.exists():
            continue
        for lnk in base.rglob("*.lnk"):
            exe = _resolve_shortcut(str(lnk))
            if exe and exe.lower().endswith(d.APP_EXECUTABLE_EXTENSIONS):
                seen_paths.add(exe.lower())
                apps.append(
                    {
                        "name": lnk.stem,
                        "publisher": "",
                        "install_location": str(exe.parent),
                        "source": "start_menu",
                        "exe_path": str(exe),
                    }
                )
    return apps


def _resolve_shortcut(lnk_path: str) -> Optional[Path]:
    """حل هدف میانبر .lnk با pywin32 در صورت وجود؛ وگرنه None (بدون خطا)."""
    try:
        from win32com.client import Dispatch  # noqa: PLC0415 - فقط ویندوز

        shell = Dispatch("WScript.Shell")
        target = shell.CreateShortcut(lnk_path).Targetpath
        if target:
            return Path(str(target))
    except ImportError:
        logger.debug("pywin32 نصب نیست؛ هدف میانبر %s حل نشد", lnk_path)
    except Exception as exc:  # noqa: BLE001 - هدف حل‌نشده نباید کشف را بکشد
        logger.debug("حل میانبر ناموفق (%s): %s", lnk_path, exc)
    return None


class _ApplicationsToolBase(Tool):
    """پایه مشترک: finder تزریقی + کش با TTL."""

    category = "applications"
    _finder: Optional[AppFinder] = None
    _cache: Optional[tuple[float, list[dict[str, Any]]]] = None
    _cache_ttl: float = 60.0

    def bind_finder(self, finder: AppFinder) -> None:
        self._finder = finder
        self._cache = None

    def _list_apps(self, refresh: bool = False) -> list[dict[str, Any]]:
        if self._finder is None:  # pragma: no cover - bootstrap تزریق می‌کند
            raise RuntimeError("AppFinder تزریق نشده است.")
        now = time.monotonic()
        if self._cache is None or refresh or (now - self._cache[0]) > self._cache_ttl:
            apps = self._finder()
            self._cache = (now, apps)
            return list(apps)
        return list(self._cache[1])


# ---------------------------------------------------------------------------
# ورودی‌ها
# ---------------------------------------------------------------------------

class SearchAppInput(ToolInput):
    query: str = Field(min_length=1, max_length=80, description="نام یا بخشی از نام برنامه")
    limit: int = Field(default=10, ge=1, le=50, description="حداکثر نتایج")


class ListAppsInput(ToolInput):
    refresh: bool = Field(default=False, description="تازه‌سازی کش برنامه‌ها")


class LaunchAppInput(ToolInput):
    exe_path: str = Field(description="مسیر کامل exe از فهرست کشف‌شده")
    args: list[str] = Field(default_factory=list, max_length=8, description="آرگومان‌های امن")


class RunningAppsInput(ToolInput):
    limit: int = Field(default=30, ge=5, le=100, description="حداکثر فرآیند‌ها")


# ---------------------------------------------------------------------------
# ابزارها
# ---------------------------------------------------------------------------

class ListInstalledAppsTool(_ApplicationsToolBase):
    input_model = ListAppsInput
    permission_level = PermissionLevel.L1_READ_ONLY

    @property
    def name(self) -> str:
        return "applications.list_installed_apps"

    @property
    def description(self) -> str:
        return (
            "فهرست برنامه‌های نصب‌شده ویندوز (رجیستری + Start Menu) با نام، "
            "ناشر و مسیر نصب؛ محدود و بدون نیاز به Administrator."
        )

    def _run(self, args: ListAppsInput) -> dict:
        apps = self._list_apps(refresh=args.refresh)
        entries = apps[: d.MAX_APPS_LISTED]
        return {
            "count": len(apps),
            "truncated": len(apps) > len(entries),
            "apps": [
                {
                    "name": a.get("name"),
                    "publisher": a.get("publisher") or None,
                    "install_location": a.get("install_location") or None,
                    "exe_path": a.get("exe_path"),
                    "source": a.get("source"),
                }
                for a in entries
            ],
        }


class SearchAppTool(_ApplicationsToolBase):
    input_model = SearchAppInput
    permission_level = PermissionLevel.L1_READ_ONLY

    @property
    def name(self) -> str:
        return "applications.search_app"

    @property
    def description(self) -> str:
        return "جست‌وجوی برنامه نصب‌شده بر اساس نام یا ناشر؛ برای یافتن مسیر قبل از اجرا."

    def _run(self, args: SearchAppInput) -> dict:
        apps = self._list_apps()
        needle = args.query.lower()
        matches = [
            {
                "name": a.get("name"),
                "publisher": a.get("publisher") or None,
                "exe_path": a.get("exe_path"),
                "install_location": a.get("install_location") or None,
            }
            for a in apps
            if needle in str(a.get("name", "")).lower()
            or needle in str(a.get("publisher", "")).lower()
        ]
        return {"query": args.query, "count": len(matches), "matches": matches[: args.limit]}


class LaunchAppTool(_ApplicationsToolBase):
    input_model = LaunchAppInput
    permission_level = PermissionLevel.L2_WRITE

    @property
    def name(self) -> str:
        return "applications.launch_app"

    @property
    def description(self) -> str:
        return (
            "اجرای یک برنامه از مسیر exe معتبر (از فهرست کشف‌شده)؛ بدون Shell آزاد، "
            "بدون Administrator. نیازمند تأیید کاربر."
        )

    def _run(self, args: LaunchAppInput) -> dict:
        # ۱) مسیر باید در فهرست کشف‌شده باشد (یا suffix .exe داشته باشد) — «نامشناخته اجرا نشود»
        target = Path(args.exe_path)
        known = None
        for app in self._list_apps():
            if app.get("exe_path") and str(Path(app["exe_path"])).lower() == str(target).lower():
                known = app
                break
        if known is None:
            raise PermissionError(
                "مسیر exe در فهرست برنامه‌های کشف‌شده نیست؛ ابتدا با "
                "applications.search_app مسیر معتبر را پیدا کنید."
            )
        exe = Path(known["exe_path"])
        if exe.suffix.lower() not in d.APP_EXECUTABLE_EXTENSIONS:
            raise PermissionError(f"پسوند قابل اجرا نیست: {exe.suffix}")
        if not exe.exists():
            raise FileNotFoundError(f"مسیر اجرایی وجود ندارد: {exe}")
        # ۲) اجرای ساختارمند (بدون shell) — «Shell آزاد ممنوع»
        argv = [str(exe), *args.args]
        try:
            process = subprocess.Popen(  # noqa: S603 - مسیر معتبر بدون shell
                argv, close_fds=True
            )
        except PermissionError as exc:
            raise PermissionError(f"دسترسی اجرا رد شد: {exe} — {exc}") from exc
        except OSError as exc:
            raise OSError(f"خطای اجرای برنامه: {exe} — {exc}") from exc
        return {
            "launched": str(exe),
            "app": known.get("name"),
            "pid": process.pid,
        }


class GetRunningAppsTool(_ApplicationsToolBase):
    input_model = RunningAppsInput
    permission_level = PermissionLevel.L1_READ_ONLY

    @property
    def name(self) -> str:
        return "applications.get_running_apps"

    @property
    def description(self) -> str:
        return (
            "فرآیند‌های در حال اجرای کاربر (نام، pid، حافظه)؛ فرآیند‌های سیستمی "
            "حساس نمایش داده نمی‌شوند و خروجی محدود است."
        )

    def _run(self, args: RunningAppsInput) -> dict:
        import psutil

        current_user = psutil.Process().username()
        processes: list[dict[str, Any]] = []
        for proc in psutil.process_iter(["name", "pid", "username", "memory_info"]):
            try:
                info = proc.info
                if info.get("username") != current_user:
                    continue
                name = str(info.get("name") or "").lower()
                if name.rstrip(".exe") in d.APP_FORBIDDEN_TARGETS:
                    continue  # فرآیند سیستمی حساس — پنهان
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            memory = info.get("memory_info")
            processes.append(
                {
                    "name": info.get("name"),
                    "pid": info.get("pid"),
                    "memory_mb": round(memory.rss / (1024 * 1024), 1) if memory else None,
                }
            )
            if len(processes) >= args.limit:
                break
        return {"count": len(processes), "processes": processes}


def create_application_tools(finder: Optional[AppFinder] = None) -> list[Tool]:
    """چهار ابزار applications؛ finder پیش‌فرض = رجیستری ویندوز."""
    tools = [
        ListInstalledAppsTool(),
        SearchAppTool(),
        LaunchAppTool(),
        GetRunningAppsTool(),
    ]
    resolved = finder or windows_registry_finder
    for tool in tools:
        tool.bind_finder(resolved)
    return tools


__all__ = ["create_application_tools", "windows_registry_finder"]
