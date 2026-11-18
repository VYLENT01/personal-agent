# راهنمای توسعه

## ۱. قواعد کد

- **شناسه‌ها انگلیسی، docstrings/پیام‌ها فارسی** (زبان محصول). استثنا: فایل‌های `.ps1` برای جلوگیری از مشکل encoding در PowerShell 5.1 انگلیسی‌اند.
- همه ماژول‌ها `from __future__ import annotations` دارند.
- تایپ‌ها کامل؛ خطاهای هرگز «بلعیده» نمی‌شوند مگر با noqa و دلیل.
- بدون hardcode مدل/آدرس؛ همه از `app/config/defaults.py`.
- هر تغییر رفتاری = تست متناظر در `tests/`.

## ۲. اجرای تست‌ها

```bash
python -m pytest tests -q           # کل مجموعه (سرور mock Ollama خودکار)
python -m pytest tests/test_llm.py -q
QT_QPA_PLATFORM=offscreen python -m pytest tests/test_ui.py -q   # تست UI بدون نمایشگر
```

در محیط بدون نمایشگر لینوکسی، ممکن است `libEGL` لازم باشد (از پکیج `libegl1`).

## ۳. ساختار تست

| فایل | پوشش |
|---|---|
| `test_llm.py` | پروتکل Ollama (mock: استریم NDJSON/timeout/خطا/لغو)، ModelManager، parser |
| `test_agent.py` | Orchestrator: رویدادها، thinking، پنجره Context، لغو، خطاها |
| `test_config.py` | Settings: پیش‌فرض/env/sanitize/ذخیره اتمیک + .env |
| `test_ui.py` | پنجره، چت استریم، خطا بدون کرش، گفت‌وگوی جدید (offscreen) |
| `test_tools.py / test_security.py / test_memory.py` | قرارداد فازهای ۲/۵/۴ — با پیاده‌شان کامل می‌شوند |

## ۴. افزودن Provider جدید (بند ۷)

۱. کلاس جدید با ارث‌بری `BaseLLMClient` (llm/base.py).
۲. در `bootstrap.build_app_context` رشته ساخت provider را شرطی کنید.
۳. تست‌های `test_llm.py` را با FakeClient جدید تکرار کنید.

هسته ایجنت هیچ تغییری نمی‌خواهد — فقط قرارداد.

## ۵. افزودن ابزار (فاز ۲)

۱. کلاس `Tool` در `app/tools/<گروه>.py` با schema و سطح دسترسی (راهنمای کامل: docs/tools.md).
۲. ثبت در Registry + افزودن به پرامپت قابلیت‌ها (`build_system_prompt`).
۳. Orchestrator بین ToolCall و پاسخ متنی تصمیم می‌گیرد؛ رویداد `ToolCallRequested` اضافه خواهد شد.
۴. تست موفق + رد نامعتبر + بدون کرش (Test 6).

## ۶. وضعیت فازها و برنامه

| فاز | خروجی | وضعیت |
|---|---|---|
| ۱ — Local AI Core | چت‌بات محلی: Ollama، استریم، thinking جدا، لغو، مدیریت خطا، UI کامل | ✅ کامل (۶۹ تست سبز) |
| ۲ — Tool Calling | Registry، schema validation، چرخه ابزار | ⏭ بعدی |
| ۳ — Windows Integration | filesystem/system/terminal/applications/development | ⏭ |
| ۴ — Memory & Planning | SQLite + Qdrant اختیاری، Planner/Executor | ⏭ |
| ۵ — Security & Autonomy | Permission/Approval/Audit/Sandbox | ⏭ |
| ۶ — Product | صوت (faster-whisper/Piper)، بسته‌بندی | ⏭ |

## ۷. گزارش فاز ۱

**ساخته شد**: ۳۲ فایل Python واقعی (بدون stub خالی) + ۵ سند + اسکریپت‌ها؛ ساختار دقیق مطابق بند ۵ مشخصات؛ پوشه‌های فازهای بعد ایجاد نشدند تا «کد واقعی یا هیچ» برقرار بماند.

**تست‌ها**: ۶۹ مورد، همه سبز — شامل ۱۰ تست پروتکل روی سرور mock NDJSON، ۶ تست لغو/خطا، ۴ تست UI offscreen و smoke بوت کامل برنامه (exit 0).

**محدودیت شناخته‌شده در سندبox توسعه**: Ollama روی این محیط لینوکسی نصب نیست؛ رفتار پروتکلی با mock تست شد. تست END-TO-END با مدل واقعی (qwen3:8b) باید روی ماشین هدف Windows انجام شود (check_environment خروجی ۲ می‌دهد تا کاربر را راهنمایی کند).

**مسیر صوت/Qdrant** طبق مشخصات در فاز ۶/۴ اضافه می‌شوند.
