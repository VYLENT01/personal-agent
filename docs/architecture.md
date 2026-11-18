# معماری نرم‌افزار — Personal Agent

نسخه سند: 1.2 (هم‌راستا با فاز ۳ — Windows Integration؛ بخش‌های قبلی همچنان معتبرند)

## ۱. هدف معماری

ساخت یک **Local-First Personal AI Agent** برای Windows که با مدل‌های محلی Ollama کار می‌کند، بدون هیچ API پولی، و قابلیت تعامل کنترل‌شده با سیستم‌عامل دارد (از فاز ۲ به بعد). سه اصل حاکم:

1. **Correctness**: هر رفتار تست‌شده و قابل‌تکرار؛ هیچ ادعایی بدون اجرای واقعی.
2. **Modularity**: اجزا فقط از طریق interfaceهای مشخص هم‌کار می‌کنند.
3. **Separation of concerns**: هسته ایجنت هیچ چیزی درباره Ollama نمی‌داند.

## ۲. نمای کلی اجزا

```
┌──────────────────────────────────────────────────────────────┐
│                     User Interface (PySide6)                  │
│   window.py ─ chat.py ─ dashboard.py ─ settings.py           │
│   (workers.py: هر عملیات طولانی در QThread جدا)               │
└───────────────────────────┬──────────────────────────────────┘
                            │ رویدادها/فراخوانی متدها
┌───────────────────────────▼──────────────────────────────────┐
│                 Agent Core (app.agent)                         │
│  orchestrator.py — چرخه اصلی تصمیم‌گیری                       │
│  state.py — حافظه کوتاه‌مدت مکالمه + پنجره Context            │
│  prompts.py — هویت ایجنت و ساخت پیام‌های مدل                  │
│  (planner / executor / task_manager: فاز ۴)                  │
└──────────┬──────────────────────────────┬─────────────────────┘
           │ قرارداد BaseLLMClient        │ (فاز ۲+: Tool Registry)
┌──────────▼──────────────┐  ┌───────────▼─────────────────────┐
│    LLM Engine           │  │      Tools (فاز ۲–۳)            │
│  base.py (قرارداد)      │  │   registry / filesystem /        │
│  ollama_client.py       │  │   system / terminal / …          │
│  model_manager.py       │  │   Security (فاز ۵) اعتبارسنجی    │
│  response_parser.py     │  │   Integrations (فاز ۳)           │
└─────────────────────────┘  └──────────────────────────────────┘
           │
┌──────────▼───────────────────────────────────────────────────┐
│        Config (app.config) + Logging + Memory (فاز ۴)         │
│   settings.py / defaults.py — اولویت: env > فایل > پیش‌فرض   │
└──────────────────────────────────────────────────────────────┘
```

## ۳. جریان یک درخواست (فاز ۱)

```
User Request
  → ChatPage.submit()                    (UI، بدون هنگ)
  → ChatWorker(QThread)                 (اجرا در thread جدا)
  → ChatOrchestrator.stream_turn()
      → ConversationState پنجره Context (سقف پیام/کاراکتر)
      → build_messages(سیستم + تاریخچه + پیام)
      → OllamaClient.chat_stream()       (NDJSON، chunk_size=1)
      → StreamThinkSplitter               (تفکیک زنده thinking از محتوا)
      → AgentEvent stream به UI          (ContentDelta / ThinkingDelta)
  → TurnCompleted / TurnFailed / TurnCancelled
```

هر خطای LLM به `TurnFailed` با پیام فارسی قابل‌نمایش نگاشت می‌شود؛ هیچ استثنایی به UI نمی‌رسد. قطع Ollama = پیام «اتصال به Ollama برقرار نشد» در حباب خطا + نوار وضعیت قرمز، بدون کرش (Test 7).

## ۴. مدل Threading

| Thread | کار | لغو |
|---|---|---|
| Main (Qt event loop) | UI، NEVER blocking | — |
| ChatWorker (QThread) | پیمایش `stream_turn` | `CancelToken` + `cancel_active()` |
| FnWorker (QThread) | داشبورد/تنظیمات: psutil، is_available | بدون نیاز |
| Ollama serve (خارجی) | استنتاج مدل | بستن socket |

**لغو فوری**: `cancel_active` علاوه بر `response.close()`، socket زیرین را با `shutdown(fd)` می‌بندد تا `recv` بلاک‌شده فوری با خطا برگردد (تست‌شده: لغو در <0.1s بدون انتظار برای read timeout).

## ۵. قرارداد LLM (بند ۷ مشخصات)

`BaseLLMClient` تنها واسط هسته ایجنت است:

- `chat()` / `chat_stream()` — پاسخ کامل / استریم
- `list_models()` / `is_available()` — کشف سرویس و مدل‌ها
- `cancel_active()` / `close()` — لغو و پاک‌سازی
- خطاهای استاندارد: `LLMConnectionError / LLMTimeoutError / LLMResponseError / LLMCancelledError`

پیام‌های سیستمی هنگام پاسخ نامعتبر مدل از طریق `build_repair_message` در فاز ۲ استفاده می‌شوند (زیرساختش در `response_parser.py` آماده و تست‌شده است).

## ۶. سطوح دسترسی (پیش‌نگر فاز ۵)

| سطح | عملیات | سیاست |
|---|---|---|
| L1 | خواندن فایل/جست‌وجو/وضعیت سیستم | اجرای مستقل |
| L2 | ایجاد/تغییر فایل، اجرای برنامه | محدودیت + سیاست تأیید متناسب |
| L3 | حذف، Registry، دستور مدیریتی | تأیید صریح کاربر |

هویت‌سنجی ابزار مستقل از LLM انجام می‌شود؛ مدل هرگز permission policy را نمی‌بیند تا بتواند تغییرش دهد.

## ۷. نگاشت ساختار مشخصات به فازها

| مسیر | فاز | وضعیت |
|---|---|---|
| `app/main.py, bootstrap.py, ui/*, llm/*, config/*, agent/{orchestrator,state,prompts}` | ۱ | ✅ پیاده و تست‌شده |
| `app/tools/{base,registry,system_tools}.py` + حلقه Tool Calling در orchestrator + UI ابزار | ۲ | ✅ پیاده و تست‌شده |
| `app/tools/{filesystem,terminal,applications,development}.py` + `app/security/{paths,permissions}.py` + گیت L2 در Registry/orchestrator + ApprovalDialog UI | ۳ | ✅ پیاده و تست‌شده (۲۷۷ تست) |
| `app/tools/browser.py` | ۳+ | برنامه‌ریزی‌شده |
| `app/memory/*`, `agent/{planner,executor,task_manager}` | ۴ | برنامه‌ریزی‌شده |
| `app/security/*`, `ui/{tool_activity,approval_dialog}` | ۵ | برنامه‌ریزی‌شده |

فایل‌های فازهای ۴–۵ عمداً **ایجاد نشده‌اند** (نه پوشه خالی، نه فایل stub) تا «کد واقعی یا هیچ» رعایت شود. `app/security/` طبق گزارش فاز ۳ از فاز ۵ جلو کشیده شد (حداقل PathGuard + ApprovalToken).

## ۸. تصمیم‌های کلیدی طراحی

1. **HTTP API رسمی Ollama با requests** (بدون کتابخانه ollama): کنترل کامل روی استریم، timeout دوقلوه و لغو در سطح socket.
2. **iter_lines(chunk_size=1)**: استریم توکن‌به‌توکن واقعی؛ بافر پیش‌فرض ۵۱۲بایتی کل پاسخ را یک‌جا می‌کشد.
3. **StreamThinkSplitter**: تفکیک زنده thinking (تگ think) با مدیریت مرز تگ ناقص بین دو قطعه؛ مدل‌های Qwen3 بدون آن، تفکر خام را داخل پاسخ نشان می‌دهند.
4. **مدل هرگز hardcode نیست**: `ModelManager.suggest_default()` از نصب‌شده‌های واقعی انتخاب می‌کند (اولویت: مدل فعال → فهرست fallback مشخصات → اولین مدل موجود).
5. **Settings immutable-style**: هر تغییر `copy_with` + `save` اتمیک (tmp + replace)؛ `_path` به ارث می‌رسد.
6. **دو مسیر Tool Calling (فاز ۲)**:
   - **native**: تشخیص قابلیت واقعی از `POST /api/show` (`capabilities`) → پارامتر `tools` به `/api/chat` و `message.tool_calls` در قطعه پایانی استریم؛ انباشت قطعه‌ای با کلید id/name.
   - **fallback**: مدل بدون قابلیت → پروتکل JSON صریح (`{"tool","args"}`) در پیام سیستم؛ استخراج از متن پاسخ با `extract_json_candidates` (تست‌شده فاز ۱)؛ نتیجه به‌عنوان پیام کاربر با پیشوند `[نتیجه ابزار]`.
   - تصمیم مسیر در `_resolve_tool_mode`؛ Agent Core به هیچ فرمت اختصاصی مدل وابسته نیست (قرارداد `BaseLLMClient`/`ToolCall` neutral).
7. **لغو در سه لایه (فاز ۲)**: توکن CancelToken مشترک بین LLM و Registry؛ `cancel_active` برای socket؛ حلقه انتظارِ قابل‌قطع در `_run_with_timeout` — لغو حین اجرای ابزار در <۰.۱s.
8. **اجرای ابزار با timeout واقعی**: `ThreadPoolExecutor` بدون context-manager (`shutdown(wait=False)`) — چون `with` تا اتمام thread بلاک می‌شود و timeout را بی‌اثار می‌کند (باگ یافت‌شده و تست‌شده).


## ۹. تصمیم‌های کلیدی فاز ۳ (Windows Integration)

1. **گیت L2 با ApprovalToken یک‌بارمصرف** (بخش ۸): توکن به هش SHA-256 از (نام ابزار + پارامترهای کانونی) گره خورده؛ تغییر هر پارامتر پس از تأیید، اجرا را رد می‌کند؛ انقضا ۱۲۰ ثانیه؛ مدل هیچ راهی برای ساخت/دستکاری توکن ندارد.
2. **PathGuard دو-لایه**: رد الگوی `..` قبل از resolve + تطبیق پس از resolve (شامل رفع symlink فرار) با allowlist قابل‌تنظیم از Settings/env.
3. **CommandPolicy ساختارمند**: فرمان = برنامه + argv جدا (هیچ رشته‌ای وارد shell نمی‌شود)؛ allowlist قابل‌ توسعه؛ رد کاراکترهای تزریق/زنجیره؛ PowerShell فقط با -NoProfile -NonInteractive؛ runner با timeout + kill + سقف خروجی.
4. **اجرای برنامه‌ها فقط از مسیر کشف‌شده** (Registry HKCU/HKLM + Start Menu) — «Shell آزاد ممنوع» با Popen(argv) بدون shell.
5. **Development دور-زدن Terminal ندارد**: run_tests فقط `python -m pytest` ثابت را در پوشه مجاز می‌راند؛ هیچ ورودی مدل وارد argv نمی‌شود.
6. **L3 در دو لایه سخت**: پنهان از پرامپت مدل (get_tool_schemas) + رد همیشگی در validate/execute حتی با توکن.
