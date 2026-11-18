#!/usr/bin/env python3
"""بررسی سلامت محیط اجرای Personal Agent.

به‌صورت مستقل اجرا می‌شود (نیازی به Ollama واقعی برای اجرای خود اسکریپت ندارد):
    python scripts/check_environment.py

بررسی‌ها:
- نسخه Python (حداقل 3.12)
- CPU / RAM / GPU (psutil و nvidia-smi در صورت وجود)
- در دسترس بودن Ollama و فهرست مدل‌های نصب‌شده
- نصب بودن مدل فعال پیکربندی‌شده
- قابل نوشتن بودن پوشه‌های داده و لاگ

کد خروج: 0 = سالم | 1 = هشدار | 2 = مشکل جدی (مانند قطع Ollama)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REQUIRED_PYTHON = (3, 12)
DEFAULT_OLLAMA_URL = os.environ.get("PERSONAL_AGENT_OLLAMA_URL", "http://127.0.0.1:11434")
DEFAULT_MODEL = os.environ.get("PERSONAL_AGENT_MODEL", "qwen3:8b")
FALLBACK_MODELS = ("gemma3:4b", "qwen2.5:7b-instruct")

EXIT_OK, EXIT_WARN, EXIT_CRITICAL = 0, 1, 2


def _line(title: str, value: str, ok: bool | None = None) -> None:
    mark = {True: "  [OK]  ", False: "  [!!]  ", None: "        "}[ok]
    print(f"{mark}{title:<32} {value}")


def check_python() -> bool:
    version = sys.version_info[:3]
    ok = version >= REQUIRED_PYTHON
    _line(
        "نسخه Python",
        f"{version[0]}.{version[1]}.{version[2]} (لازم: >={REQUIRED_PYTHON[0]}.{REQUIRED_PYTHON[1]})",
        ok,
    )
    return ok


def check_hardware() -> None:
    try:
        import psutil
    except ImportError:
        _line("اطلاعات سخت‌افزار", "psutil نصب نیست (pip install psutil)", False)
        return
    memory = psutil.virtual_memory()
    _line(
        "CPU",
        f"{psutil.cpu_count(logical=True)} هسته منطقی — {psutil.cpu_percent(interval=0.2):.0f}% استفاده",
    )
    _line("RAM", f"{memory.total / 1024**3:.1f} GB کل — {memory.available / 1024**3:.1f} GB آزاد")
    try:
        import subprocess

        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=4, check=True,
        ).stdout.strip()
        _line("GPU", out if out else "شناسایی نشد")
    except Exception:
        _line("GPU", "nvidia-smi در دسترس نیست (روی ویندوز با درایور NVIDIA فعال می‌شود)")


def check_requests() -> bool:
    try:
        import requests  # noqa: F401

        _line("کتابخانه requests", "نصب است", True)
        return True
    except ImportError:
        _line("کتابخانه requests", "نصب نیست (pip install requests)", False)
        return False


def check_ollama() -> tuple[bool, list[str]]:
    import requests

    try:
        version = requests.get(f"{DEFAULT_OLLAMA_URL}/api/version", timeout=(2, 4)).json()
        _line("Ollama", f"در حال اجراست — نسخه {version.get('version', 'نامشخص')}", True)
    except Exception as exc:
        _line("Ollama", f"قطع است ({DEFAULT_OLLAMA_URL}) — جزئیات: {exc}", False)
        print()
        print("  راهنما: Ollama را از https://ollama.com/download نصب و اجرا کنید (ollama serve)")
        return False, []

    try:
        tags = requests.get(f"{DEFAULT_OLLAMA_URL}/api/tags", timeout=(2, 5)).json()
        names = sorted(item.get("name", "?") for item in tags.get("models", []))
    except Exception as exc:
        _line("فهرست مدل‌ها", f"دریافت ناموفق: {exc}", False)
        return True, []

    if not names:
        _line("مدل‌های نصب‌شده", "هیچ مدلی نصب نیست", False)
        print()
        print(f"  راهنما: یک مدل نصب کنید — مثال:  ollama pull {DEFAULT_MODEL}")
        return True, []
    _line("مدل‌های نصب‌شده", "، ".join(names))
    return True, names


def check_model_installed(names: list[str]) -> bool:
    if not names:
        return False
    base = DEFAULT_MODEL.split(":")[0].lower()
    installed_bases = {name.split(":")[0].lower() for name in names}
    ok = base in installed_bases
    if not ok:
        hint = " یا ".join(FALLBACK_MODELS)
        _line(f"مدل پیکربندی ({DEFAULT_MODEL})", "نصب نیست", False)
        print(f"  راهنما:  ollama pull {DEFAULT_MODEL}   یا یکی از {hint}")
    else:
        _line(f"مدل پیکربندی ({DEFAULT_MODEL})", "نصب است", True)
    return ok


def check_dirs() -> bool:
    root = Path(__file__).resolve().parent.parent
    ok = True
    for rel in ("data", "data/config", "logs"):
        target = root / rel
        try:
            target.mkdir(parents=True, exist_ok=True)
            probe = target / ".write_test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            _line(f"پوشه {rel}/", "قابل نوشتن", True)
        except OSError as exc:
            ok = False
            _line(f"پوشه {rel}/", f"قابل نوشتن نیست: {exc}", False)
    return ok


def main() -> int:
    print("=" * 64)
    print("بررسی محیط Personal Agent — Local-First Personal AI Assistant")
    print("=" * 64)
    print()

    problems = 0
    warnings = 0

    if not check_python():
        warnings += 1
    check_hardware()
    if not check_requests():
        problems += 1
    print()

    available, names = check_ollama()
    if not available:
        problems += 1
    elif not names:
        warnings += 1
    elif not check_model_installed(names):
        warnings += 1
    print()

    if not check_dirs():
        problems += 1

    print()
    print("=" * 64)
    if problems:
        print(f"نتیجه: {problems} مشکل جدی — ابتدا موارد [!!] را رفع کنید.")
        return EXIT_CRITICAL
    if warnings:
        print(f"نتیجه: محیط قابل اجراست با {warnings} هشدار (موارد [!!] اختیاری هستند).")
        return EXIT_WARN
    print("نتیجه: محیط کاملاً آماده اجراست.")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
