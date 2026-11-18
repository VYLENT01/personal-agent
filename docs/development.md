# راهنمای توسعه

## ۱. قواعد کد

- **شناسه‌ها انگلیسی، docstrings/پیام‌ها فارسی** (زبان محصول). استثنا: فایل‌های `.ps1` برای جلوگیری از مشکل encoding در PowerShell 5.1 انگلیسی‌اند.
- همه ماژول‌ها `from __future__ import annotations` دارند.
- تایپ‌ها کامل؛ خطاهای هرگز «بلعیده» نمی‌شوند مگر با noqa و دلیل.
- بدون hardcode مدل/آدرس؛ همه از `app/config/defaults.py`.
- هر تغییر رفتاری = تست متناظر در `tests/`.

## ۲. اجرای تست‌ها

```bash
python -m pytest tests -q --ignore=tests/test_ui.py   # هسته (سرور mock) → ۲۶۸ passed
QT_QPA_PLATFORM=offscreen python -m pytest tests/test_ui.py -q   # UI جدا (جلوگیری از تداخل شناخته‌شده PySide6+subprocess در یک process)
python -m pytest tests/test_tools.py -q
QT_QPA_PLATFORM=offscreen python -m pytest tests/test_ui.py -q   # تست UI بدون نمایشگر

# integration با Ollama واقعی (اختیاری):
PERSONAL_AGENT_INTEGRATION_MODEL=qwen3:8b python -m pytest tests/test_integration_ollama.py -v
```

در محیط بدون نمایشگر لینوکسی، ممکن است `libEGL` لازم باشد (از پکیج `libegl1`).

## ۳. ساختار تست

| فایل | پوشش |
|---|---|
| `test_tools.py` | قرارداد Tool/Pydantic، Registry (ثبت/تکراری/رد ناشناخته/غیرفعال/L3)، timeout واقعی، **گیت L2/ApprovalToken**، سه ابزار psutil |
| `test_permissions.py` | PathGuard (traversal/symlink/خارج از محدوده/env) + ApprovalToken (یک‌بارمصرف/تغییر پارامتر/انقضا) |
| `test_filesystem_tools.py` | ۱۰ ابزار filesystem: فهرست/خواندن/بزرگ/باینری/تراوِرسال/ایجاد/ویرایش/رد بدون تأیید/PermissionDenied/اتمیک |
| `test_terminal_tools.py` | CommandPolicy (تزریق/زنجیره/allowlist توسعه‌پذیر) + timeout/لغو/سقف خروجی واقعی + python --version واقعی |
| `test_app_apps.py` → `test_app_tools.py` | کشف/جست‌وجو/اجرا معتبر/رد ناشناخته/تأیید/PID واقعی/فرآیند سیستمی پنهان |
| `test_development_tools.py` | ۶ ابزار توسعه: بازرسی/ساختار/خواندن/pytest موفق-ناموفق-timeout واقعی/git status واقعی |
| `test_phase3_integration.py` | چرخه کامل: مدل→Registry→نتیجه→LLM→پاسخ نهایی؛ تأیید/رد/انقضا/لغوی UI؛ L3 رد؛ حفظ فاز ۲ |
| `test_llm.py` | پروتکل Ollama (mock: استریم NDJSON/timeout/خطا/لغو)، **native tools + /api/show capabilities**، ModelManager، parser |
| `test_agent.py` | حلقه کامل Agent: **چرخه native و fallback**، ارسال نتیجه به مدل، سقف‌ها/حلقه بی‌نهایت، لغو حین ابزار، پیام‌های state |
| `test_config.py` | Settings: پیش‌فرض/env/sanitize/ذخیره اتمیک + .env |
| `test_ui.py` | پنجره، چت استریم، خطا بدون کرش، **حباب ابزار موفق/خطا** (offscreen) |
| `test_memory.py / test_security.py` | قرارداد فازهای ۴/۵ — با پیاده‌شان کامل می‌شوند |
| `test_integration_ollama.py` | **integration واقعی**؛ بدون env skip می‌شود |

## ۴. افزودن Provider جدید (بند ۷)

۱. کلاس جدید با ارث‌بری `BaseLLMClient` (llm/base.py).
۲. در `bootstrap.build_app_context` رشته ساخت provider را شرطی کنید.
۳. تست‌های `test_llm.py` را با FakeClient جدید تکرار کنید.

هسته ایجنت هیچ تغییری نمی‌خواهد — فقط قرارداد.

## ۵. افزودن ابزار (فاز ۲ به بعد)

۱. کلاس `Tool` در `app/tools/<گروه>.py` با `input_model` (Pydantic) و `permission_level` (راهنمای کامل: docs/tools.md).
۲. ثبت در `ToolRegistry.register_tool` (bootstrap یا گروه ابزار جدید در `create_default_tools`).
۳. پرامپت قابلیت‌ها خودکار از Registry ساخته می‌شود (`capabilities_prompt`)؛ مسیر native/fallback خودکار انتخاب می‌شود.
۴. سه تست الزامی: موفق + رد ورودی نامعتبر + بدون کرش.

## ۶. وضعیت فازها و برنامه

| فاز | خروجی | وضعیت |
|---|---|---|
| ۱ — Local AI Core | چت‌بات محلی: Ollama، استریم، thinking جدا، لغو، مدیریت خطا، UI کامل | ✅ کامل |
| ۲ — Tool Calling | Registry، Pydantic schema، چرخه ۱۰مرحله‌ای، native+fallback، UI ابزار | ✅ کامل |
| ۳ — Windows Integration | ۲۵ ابزار ۵ گروه + PathGuard + ApprovalToken + CommandPolicy + ApprovalDialog | ✅ کامل (۲۷۷ تست سبز؛ ویندوز-تست‌های دستی مستند) |
| ۳ — Windows Integration | filesystem/system/terminal/applications/development | ⏭ بعدی |
| ۴ — Memory & Planning | SQLite + Qdrant اختیاری، Planner/Executor | ⏭ |
| ۵ — Security & Autonomy | Permission/Approval/Audit/Sandbox | ⏭ |
| ۶ — Product | صوت (faster-whisper/Piper)، بسته‌بندی | ⏭ |

## ۷. گزارش فاز ۱

**ساخته شد**: ۳۲ فایل Python واقعی (بدون stub خالی) + ۵ سند + اسکریپت‌ها؛ ساختار دقیق مطابق بند ۵ مشخصات؛ پوشه‌های فازهای بعد ایجاد نشدند تا «کد واقعی یا هیچ» برقرار بماند.

**تست‌ها**: ۶۹ مورد، همه سبز — شامل ۱۰ تست پروتکل روی سرور mock NDJSON، ۶ تست لغو/خطا، ۴ تست UI offscreen و smoke بوت کامل برنامه (exit 0).

**محدودیت شناخته‌شده در سندبox توسعه**: Ollama روی این محیط لینوکسی نصب نیست؛ رفتار پروتکلی با mock تست شد. تست END-TO-END با مدل واقعی (qwen3:8b) باید روی ماشین هدف Windows انجام شود (check_environment خروجی ۲ می‌دهد تا کاربر را راهنمایی کند).

## ۸. گزارش فاز ۲ — Tool Calling

**فایل‌های جدید**: `app/tools/{__init__,base,registry,system_tools}.py` + `tests/{test_tools,test_integration_ollama}.py`.

**فایل‌های تغییرکرده**: `llm/base.py` (ToolCall/پیام tool/tools param/show_model)، `llm/ollama_client.py` (ارسال tools + انباشت استریمی tool_calls + /api/show)، `llm/model_manager.py` (کش قابلیت)، `agent/orchestrator.py` (حلقه ۱۰مرحله‌ای + دو مسیر + لغو سه‌لایه)، `agent/prompts.py` (پروتکل fallback + هدایت native)، `agent/state.py` (پیام‌های ابزار)، `bootstrap.py` (Registry)، `ui/{chat,window,dashboard}.py` (حباب ابزار/تم/داشبورد)، defaults/pyproject/requirements (نسخه + pydantic + سقف‌ها)، mock_ollama.py + fakes.py (پشتیبانی tools/capabilities/مرحله‌ای)، تست‌های agent/llm/ui به‌روز.

**تست‌ها**: ۱۲۸ passed + ۲ skipped (integration عمدی تا تنظیم env) + smoke بوت با ۳ ابزار ثبت‌شده (exit 0).

**باگ‌های یافت‌و‌رفع‌شده حین توسعه**: (۱) `ThreadPoolExecutor` با context-manager تا اتمام thread بلاک می‌شد → timeout بی‌اثار؛ (۲) لغو فقط socket مدل را می‌بست، اجرای ابزار ادامه می‌یافت → توکن مشترک Registry؛ (۳) باگ باقی‌مانده فاز ۱: پیام کاربر دوبار به مدل می‌رفت (پنجره Context + append جداگانه).


## ۹. گزارش فاز ۳ — Windows Integration (خلاصه)

**فایل‌های جدید**: `app/tools/{filesystem,terminal,applications,development}.py`، `app/security/{__init__,paths,permissions}.py`، تست‌های `test_permissions/test_filesystem_tools/test_terminal_tools/test_app_tools/test_development_tools/test_phase3_integration.py`.

**تغییرات**: defaults (v0.3.0 + سقف‌ها/allowlist)، settings (`allowed_paths` + env)، registry (سقف L2 + گیت ApprovalToken + needs_approval)، orchestrator (رویداد ApprovalRequested + انتظار قابل‌قطع + timeout تأیید)، bootstrap (PathGuard/PermissionManager + ثبت ۲۲ ابزار جدید)، UI chat (ApprovalDialog + وضعیت انتظار تأیید + قالب)، window (QSS دیالوگ).

**وضعیت تست**: ۲۷۷ passed + ۲ skipped (integration Ollama واقعی) + smoke بوت ۲۵ ابزار (exit 0). ویندوز-تست‌های دستی: کشف رجیستری واقعی، cmdletهای PowerShell، pywin32 میانبرها — در گزارش فاز ۳ مستند شده‌اند.
