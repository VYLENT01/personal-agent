# Personal Agent — دستیار هوش مصنوعی شخصی و محلی برای Windows

**Local-First Personal AI Agent** — نسخه 0.2.0 (فاز ۲: Tool Calling)

چت‌بات محلی نیست؛ ایجنت واقعی است که مرحله‌به‌مرحله قابلیت تعامل کنترل‌شده با ویندوز را کسب می‌کند. همه هوش مصنوعی روی مدل‌های محلی Ollama اجرا می‌شود — بدون هیچ API پولی.

## قابلیت‌های فعلی (فاز ۲)

- 💬 **گفت‌وگوی فارسی/انگلیسی** با استریم پاسخ توکن‌به‌توکن و تفکر مدل جدا از پاسخ (Qwen3)
- 🔧 **Tool Calling واقعی**: مدل خودش ابزار را انتخاب و پارامتر تولید می‌کند؛ نتیجه اجرا به مدل برمی‌گردد و پاسخ نهایی بر پایه داده واقعی ساخته می‌شود
  - مسیر **بومی** (پارامتر `tools` + `message.tool_calls` در API رسمی Ollama) — تشخیص خودکار قابلیت از `/api/show`
  - مسیر **fallback** با پروتکل JSON برای مدل‌های بدون قابلیت (مثل gemma3)
- 🛠 **۳ ابزار فقط-خواندنی** با اعتبارسنجی Pydantic: `system.get_system_info` / `system.get_memory_usage` / `system.get_disk_usage`
- 🛡 **سطح دسترسی**: فقط L1 (خواندن) مجاز؛ ابزار L2/L3 در این فاز اصلاً ثبت نمی‌شود
- ⚡ **لغو فوری** در میانه پاسخ یا حین اجرای ابزار (سه لایه: توکن مشترک + بستن socket + قطع اجرای ابزار)
- 🩹 **مدیریت خطای کامل**: قطع Ollama / timeout / پاسخ نامعتبر / خطای ابزار → پیام فارسی ساختاریافته، بدون کرش
- 🧵 **سقف‌های ضد حلقه بی‌نهایت**: حداکثر ۵ فراخوانی ابزار و ۸ مرحله Agent در هر نوبت
- 📊 **داشبورد**: وضعیت Ollama، مدل فعال، CPU/RAM، GPU و فهرست ابزارهای فعال
- 🌗 **UI تیره RTL** (PySide6) با حباب مخصوص ابزار (نام، پارامترها، وضعیت اجرا، نتیجه/خطا) و شمارش مراحل اجرا — UI هرگز هنگ نمی‌کند
- 🔌 **مدل‌محور و بدون hardcode**: تعویض مدل از تنظیمات؛ پیشنهاد خودکار از نصب‌شده‌ها

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

تست‌ها: `python -m pytest tests -q` — بدون نیاز به Ollama واقعی (سرور mock)؛ **۱۲۸ تست سبز**.

تست integration با Ollama واقعی (اختیاری):

```powershell
$env:PERSONAL_AGENT_INTEGRATION_MODEL="qwen3:8b"
python -m pytest tests/test_integration_ollama.py -v
```

## مدل‌های پیشنهادی (RTX 4060 / 8GB VRAM)

| مدل | حجم | نکته |
|---|---|---|
| `qwen3:8b` | ~5GB | پیش‌فرض؛ thinking + Tool Calling بومی |
| `gemma3:4b` | ~3GB | سبک؛ مسیر fallback JSON |
| `qwen2.5:7b-instruct` | ~4.7GB | بدون thinking |

## نقشه فازها

| فاز | خروجی | وضعیت |
|---|---|---|
| ۱ | هسته AI محلی + UI کامل | ✅ |
| ۲ | Tool Calling (Registry + Schema + چرخه کامل) | ✅ |
| ۳ | Windows Integration (فایل/سیستم/ترمینال/برنامه) | ⏭ |
| ۴ | Memory (SQLite + Qdrant اختیاری) + Planning | ⏭ |
| ۵ | Security (Approval + Audit + Sandbox) | ⏭ |
| ۶ | Product (صوت، بسته‌بندی) | ⏭ |

مستندات کامل: `docs/architecture.md` · `docs/installation.md` · `docs/tools.md` · `docs/development.md`

## ساختار

```
personal-agent/
├── app/            # main, bootstrap, ui/, agent/, llm/, config/, tools/
├── data/           # config/ database/ memory/ conversations/ cache/
├── logs/           # agent.log چرخشی
├── tests/          # ۱۲۸ تست (pytest) + سرور mock Ollama + integration
├── scripts/        # setup.ps1 / run.ps1 / check_environment.py
└── docs/           # معماری، نصب، ابزارها، توسعه
```

## امنیت و حریم خصوصی

- هیچ داده‌ای از ماشین شما خارج نمی‌شود؛ استنتاج کاملاً محلی است.
- `.env` در `.gitignore`؛ هیچ کلید/توکنی در سورس نیست.
- سطوح دسترسی L1/L2/L3 (خواندن/نوشتن/حساس): فاز ۲ فقط ابزارهای L1 دارد و Registry ابزارهای L2/L3 را نه ثبت و نه اجرا می‌کند؛ اجرای کنترل‌شده آن‌ها در فاز ۵ (Approval) کامل می‌شود.
