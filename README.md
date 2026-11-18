# Personal Agent — دستیار هوش مصنوعی شخصی و محلی برای Windows

**Local-First Personal AI Agent** — نسخه 0.1.0 (فاز ۱: Local AI Core)

چت‌بات محلی نیست؛ ایجنت واقعی است که مرحله‌به‌مرحله قابلیت تعامل کنترل‌شده با ویندوز را کسب می‌کند. همه هوش مصنوعی روی مدل‌های محلی Ollama اجرا می‌شود — بدون هیچ API پولی.

## قابلیت‌های فعلی (فاز ۱)

- 💬 **گفت‌وگوی فارسی/انگلیسی** با استریم پاسخ توکن‌به‌توکن
- 🧠 **تفکر مدل جدا از پاسخ** (پشتیبانی مدل‌های thinking مثل Qwen3) — زنده نمایش داده می‌شود
- 🔌 **مدل‌محور و بدون hardcode**: تعویض مدل (Gemma 3 / Qwen3 / Qwen2.5) از تنظیمات؛ پیشنهاد خودکار از نصب‌شده‌ها
- ⚡ **لغو فوری** در میانه تولید پاسخ (بستن socket زیرین)
- 🩹 **مدیریت خطای کامل**: قطع Ollama/timeout/پاسخ نامعتبر → پیام فارسی، بدون کرش UI
- 📊 **داشبورد**: وضعیت Ollama، مدل فعال، CPU/RAM، GPU (در صورت وجود nvidia-smi)
- 🌗 **UI مدرن تیره RTL** (PySide6) — UI هرگز هنگ نمی‌کند (همه عملیات در thread جدا)
- 🧵 **مدیریت Context**: پنجره تاریخچه با سقف پیام/کاراکتر

## شروع سریع (Windows)

```powershell
# ۱) نصب Ollama از https://ollama.com/download سپس:
ollama pull qwen3:8b

# ۲) راه‌اندازی پروژه:
cd personal-agent
.\scripts\setup.ps1
.\scripts\run.ps1
```

اجرای دستی: `python -m app.main` از ریشه پروژه.

تست‌ها: `python -m pytest tests -q` (بدون نیاز به Ollama واقعی — سرور mock).

## مدل‌های پیشنهادی (RTX 4060 / 8GB VRAM)

| مدل | حجم | نکته |
|---|---|---|
| `qwen3:8b` | ~5GB | پیش‌فرض؛ thinking |
| `gemma3:4b` | ~3GB | سبک و سریع |
| `qwen2.5:7b-instruct` | ~4.7GB | بدون thinking |

## نقشه فازها

| فاز | خروجی | وضعیت |
|---|---|---|
| ۱ | هسته AI محلی + UI کامل | ✅ |
| ۲ | Tool Calling (Registry + Schema) | ⏭ |
| ۳ | Windows Integration (فایل/سیستم/ترمینال/برنامه) | ⏭ |
| ۴ | Memory (SQLite + Qdrant اختیاری) + Planning | ⏭ |
| ۵ | Security (L1/L2/L3 + Approval + Audit) | ⏭ |
| ۶ | Product (صوت، بسته‌بندی) | ⏭ |

مستندات کامل: `docs/architecture.md` · `docs/installation.md` · `docs/tools.md` · `docs/development.md`

## ساختار

```
personal-agent/
├── app/            # main, bootstrap, ui/, agent/, llm/, config/
├── data/           # config/ database/ memory/ conversations/ cache/
├── logs/           # agent.log چرخشی
├── tests/          # ۶۹ تست (pytest) + سرور mock Ollama
├── scripts/        # setup.ps1 / run.ps1 / check_environment.py
└── docs/           # معماری، نصب، ابزارها، توسعه
```

## امنیت و حریم خصوصی

- هیچ داده‌ای از ماشین شما خارج نمی‌شود؛ استنتاج کاملاً محلی است.
- `.env` در `.gitignore`؛ هیچ کلید/توکنی در سورس نیست.
- طرح سطوح دسترسی L1/L2/L3 (خواندن/نوشتن/حساس) از فاز ۵ به بالا اعمال می‌شود؛ فاز ۱ فقط گفت‌وگو است و هیچ عملی روی سیستم انجام نمی‌دهد.
