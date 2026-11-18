# راهنمای نصب و اجرا

## ۱. پیش‌نیازها

| مورد | حداقل | توضیح |
|---|---|---|
| Python | **3.12** | `python --version` را بررسی کنید |
| Ollama | هر نسخه جدید | از ollama.com/download نصب کنید (Windows) |
| GPU | اختیاری | NVIDIA + nvidia-smi برای نمایش در داشبورد |
| RAM | 16GB توصیه | برای مدل‌های 8B quantized |

## ۲. نصب Ollama و مدل‌ها

```powershell
# پس از نصب Ollama از https://ollama.com/download
ollama serve            # معمولاً خودکار اجرا می‌شود

# مدل‌های پیشنهادی برای RTX 4060 / 8GB VRAM:
ollama pull qwen3:8b             # ~5GB (q4) — مدل پیش‌فرض پیکربندی
ollama pull gemma3:4b            # ~3GB — سبک‌تر
ollama pull qwen2.5:7b-instruct   # ~4.7GB — بدون thinking

ollama list             # بررسی نصب
```

## ۳. راه‌اندازی پروژه

### روش ۱ — اسکریپت (پیشنهادی روی ویندوز)

```powershell
cd personal-agent
.\scripts\setup.ps1                 # venv + وابستگی‌ها + بررسی محیط
.\scripts\setup.ps1 -PullModel      # همین + pull مدل پیش‌فرض qwen3:8b
.\scripts\run.ps1                   # اجرای برنامه
```

### روش ۲ — دستی

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m app.main        # اجرا از ریشه پروژه
```

## ۴. تنظیمات

اولویت: **متغیر محیطی > `data/config/settings.json` > پیش‌فرض کد**.

فایل `.env.example` را به `.env` کپی و مطابق نیاز تنظیم کنید:

```
PERSONAL_AGENT_OLLAMA_URL=http://127.0.0.1:11434
PERSONAL_AGENT_MODEL=qwen3:8b
PERSONAL_AGENT_LANGUAGE=fa
PERSONAL_AGENT_LOG_LEVEL=INFO
```

همه تنظیمات از داخل برنامه (صفحه «تنظیمات») هم قابل تغییرند و بی‌درنگ اعمال می‌شوند (تغییر آدرس Ollama، کلاینت را بازسازی می‌کند).

## ۵. اجرای تست‌ها

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

تست‌های پروتکل Ollama با سرور mock اجرا می‌شوند؛ نیازی به Ollama واقعی ندارند.

## ۶. عیب‌یابی

| نشانه | علت / راه‌حل |
|---|---|
| نوار وضعیت: «قطع — Ollama در دسترس نیست» | سرویس اجرا نیست → `ollama serve` یا اجرای برنامه Ollama |
| خطای `model 'x' not found` | مدل نصب نیست → `ollama pull x` یا از تنظیمات مدل موجود انتخاب کنید |
| پاسخ خیلی کند / محیط تنگ VRAM | `num_ctx` را در تنظیمات کاهش دهید (مثلاً 4096) |
| فایل تنظیمات خراب شد | حذف `data/config/settings.json`؛ برنامه با پیش‌فرض بوت می‌شود |
| پورت 11434 اشغال | در `.env` آدرس دیگری بگذارید و Ollama را با `OLLAMA_HOST` هم‌راستا کنید |

## ۷. ملاحظات امنیتی

- هیچ اطلاعات حساسی در سورس نیست؛ `.env` در `.gitignore` است.
- لاگ‌ها در `logs/agent.log` (چرخشی، حداکثر ۵MB×۳) — برای Audit فاز ۵ پایه است.
