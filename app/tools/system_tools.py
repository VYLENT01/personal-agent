"""سه ابزار آزمایشی فقط-خواندنی سیستم (بخش پنجم فاز ۲).

- system.get_system_info   → مشخصات سیستم‌عامل/پردازنده/پایتون
- system.get_memory_usage  → مصرف RAM و swap
- system.get_disk_usage    → فضای دیسک مسیرها

همه L1 (فقط-خواندنی): اجرای خودکار مجاز؛ هیچ write/delete/تنظیمات‌سیستمی
در این فاز وجود ندارد. پایه داده‌ها psutil است.
"""
from __future__ import annotations

import platform
import sys
from typing import Optional

from pydantic import BaseModel, Field

from app.tools.base import PermissionLevel, Tool, ToolInput


class _EmptyInput(ToolInput):
    """ورودی بدون پارامتر — ابزارهای اطلاعات کلی."""

    model_config = {"extra": "forbid"}


class DiskPathInput(ToolInput):
    """ورودی get_disk_usage: مسیر دلخواه دیسک (پیش‌فرض ریشه سیستم)."""

    path: Optional[str] = Field(
        default=None,
        description="مسیر دلخواه مثل «C:\\» یا «D:\\data»؛ خالی = همه دیسک‌ها",
    )


class _GetSystemInfo(Tool):
    input_model = _EmptyInput
    permission_level = PermissionLevel.L1_READ_ONLY
    category = "system"

    @property
    def name(self) -> str:
        return "system.get_system_info"

    @property
    def description(self) -> str:
        return (
            "اطلاعات کلی سیستم را برمی‌گرداند: نام سیستم‌عامل و نسخه، معماری پردازنده، "
            "تعداد هسته‌های منطقی، نام ماشین و نسخه پایتون میزبان ایجنت."
        )

    def _run(self, args: _EmptyInput) -> dict:
        import psutil

        return {
            "os": f"{platform.system()} {platform.release()} ({platform.version()})",
            "machine": platform.machine(),
            "hostname": platform.node(),
            "cpu_logical_cores": psutil.cpu_count(logical=True),
            "cpu_physical_cores": psutil.cpu_count(logical=False),
            "python_version": sys.version.split()[0],
            "boot_time": psutil.boot_time(),
        }


class _GetMemoryUsage(Tool):
    input_model = _EmptyInput
    permission_level = PermissionLevel.L1_READ_ONLY
    category = "system"

    @property
    def name(self) -> str:
        return "system.get_memory_usage"

    @property
    def description(self) -> str:
        return (
            "مصرف حافظه اصلی (RAM) و حافظه مبادله (swap) را برمی‌گرداند: "
            "مجموع/مورد استفاده/آزاد به بایت و درصد استفاده."
        )

    def _run(self, args: _EmptyInput) -> dict:
        import psutil

        memory = psutil.virtual_memory()
        swap = psutil.swap_memory()
        gb = 1024**3
        return {
            "ram": {
                "total_bytes": memory.total,
                "used_bytes": memory.used,
                "available_bytes": memory.available,
                "percent": memory.percent,
                "total_gb": round(memory.total / gb, 2),
                "used_gb": round(memory.used / gb, 2),
                "available_gb": round(memory.available / gb, 2),
            },
            "swap": {
                "total_bytes": swap.total,
                "used_bytes": swap.used,
                "free_bytes": swap.free,
                "percent": swap.percent,
            },
        }


class _GetDiskUsage(Tool):
    input_model = DiskPathInput
    permission_level = PermissionLevel.L1_READ_ONLY
    category = "system"

    @property
    def name(self) -> str:
        return "system.get_disk_usage"

    @property
    def description(self) -> str:
        return (
            "فضای دیسک را برمی‌گرداند. بدون path همه پارتیشن‌ها؛ با path فقط همان مسیر "
            "(مثال: «C:\\» یا «/»). خروجی: مجموع/مورد استفاده/آزاد به بایت و درصد."
        )

    def _run(self, args: DiskPathInput) -> dict:
        import psutil

        gb = 1024**3
        if args.path:
            usage = psutil.disk_usage(args.path)
            return {
                "partitions": [
                    {
                        "mountpoint": args.path,
                        "total_bytes": usage.total,
                        "used_bytes": usage.used,
                        "free_bytes": usage.free,
                        "percent": usage.percent,
                        "total_gb": round(usage.total / gb, 2),
                        "used_gb": round(usage.used / gb, 2),
                        "free_gb": round(usage.free / gb, 2),
                    }
                ]
            }
        partitions = []
        for part in psutil.disk_partitions(all=False):
            try:
                usage = psutil.disk_usage(part.mountpoint)
            except (OSError, PermissionError):
                continue  # درایو آماده نیست (مثل کارت‌خوان خالی) — رد بی‌خطر
            partitions.append(
                {
                    "device": part.device,
                    "mountpoint": part.mountpoint,
                    "fstype": part.fstype,
                    "total_bytes": usage.total,
                    "used_bytes": usage.used,
                    "free_bytes": usage.free,
                    "percent": usage.percent,
                    "total_gb": round(usage.total / gb, 2),
                    "used_gb": round(usage.used / gb, 2),
                    "free_gb": round(usage.free / gb, 2),
                }
            )
        return {"partitions": partitions}


def create_default_tools() -> list[Tool]:
    """سه ابزار فاز ۲؛ فهرست منبع واحد برای bootstrap و تست‌ها."""
    return [_GetSystemInfo(), _GetMemoryUsage(), _GetDiskUsage()]


__all__ = ["create_default_tools", "DiskPathInput"]
