# کاتالوگ ابزارها (Tool Registry) — پیاده‌سازی فاز ۲ و طراحی فاز ۳

نسخه سند: 1.1 — بخش‌های ۱ و ۲ در فاز ۲ **پیاده و تست‌شده**اند (۱۲۸ تست)؛ ابزارهای گروه filesystem/terminal/… برای فاز ۳ باقی می‌مانند.

## ۱. قرارداد هر ابزار (پیاده‌شده در `app/tools/base.py`)

هر ابزار زیرکلاس `Tool` است و این اعضا را می‌دهد:

```python
class MyTool(Tool):
    input_model: type[ToolInput]       # مدل Pydantic → JSON Schema خودکار
    permission_level = PermissionLevel.L1_READ_ONLY
    category = "system"

    @property
    def name(self) -> str: ...          # قالب «گروه.عمل» مثل system.get_ram
    @property
    def description(self) -> str: ...  # برای پرامپت مدل

    def _run(self, args) -> Any: ...   # فقط داده برمی‌گرداند

# اعضای مشترک خودکار:
# validate()  → اعتبارسنجی Pydantic (extra=forbid؛ خطا فارسی)
# execute()   → validate → _run → ToolResult؛ هر خطا ساختاریافته، بدون exception
# metadata    → {name, description, category, permission_level, parameters}
# input_schema() / to_ollama_tool()  → فریم wire Ollama
```

اعتبارسنجی با **Pydantic v2** انجام می‌شود — مستقل از LLM؛ ورودی نامعتبر (نوع اشتباه/فیلد ناشناخته/خارج بازه) رد می‌شود و پیام فارسی به مدل برمی‌گردد (نیازی به `build_repair_message` در فاز ۲ نیست؛ چون خطا خودش به مدل می‌رسد و تکرار بعد اصلاح می‌شود).

## ۲. قالب ساختاریافته نتیجه (پیاده‌شده)

```json
{
  "success": true,
  "tool": "system.get_memory_usage",
  "data": { "ram": { "total_gb": 15.8, "percent": 47.5 } },
  "error": null,
  "duration": 0.002
}
```

`data` فقط در صورت `success=true` معنا دارد؛ `error` پیام فارسی قابل‌نمایش است. `truncate_for_llm()` نتیجه را تا سقف `TOOL_RESULT_MAX_CHARS` (۴۰۰۰) برای ارسال به مدل می‌بُرد.

## ۲-ب. Registry (پیاده‌شده در `app/tools/registry.py`)

- `register_tool` / `unregister_tool` — **نام تکراری رد می‌شود** (`DuplicateToolError`)
- `get_tool` / `list_tools` / `metadata` / `tool_names`
- `get_tool_schemas` — فریم wire؛ **فقط ابزارهای فعالِ با سطح ≤ L1**؛ سقف ۲۴ ابزار در پرامپت
- `capabilities_prompt(language)` — خطوط ابزار برای پیام سیستم
- `validate_tool_call(name, args)` — وجود + فعال + مجوز + Pydantic (خروجی: ready/tool/خطا)
- `execute_tool` — اجرای کنترل‌شده: **timeout واقعی** (بدون بلاک‌شدن؛ `shutdown(wait=False)`)، **لغو حین اجرا** (`bind_cancel_token`/`request_cancel`)، آمار executed/failed/rejected
- ابزار ثبت‌نشده/غیرفعال/L2+ هرگز اجرا نمی‌شود → `ToolResult.fail` با پیام فارسی

## ۳. فهرست ابزارها و سطح دسترسی

### filesystem.py (بند ۹.۱)

| ابزار | سطح | قوانین |
|---|---|---|
| `filesystem.list_directory` | L1 | فقط مسیرهای allowlist کاربر |
| `filesystem.search_files` | L1 | جلوگیری از Path Traversal |
| `filesystem.read_file` | L1 | سقف حجم؛ خواندن کنترل‌شده فایل‌های حجیم |
| `filesystem.create_file` | L2 | مسیر باید در allowlist باشد |
| `filesystem.write_file` | L2 | overwrite نیازمند تأیید متناسب |
| `filesystem.copy_file` | L2 | — |
| `filesystem.move_file` | L2 | — |
| `filesystem.get_file_info` | L1 | — |
| `filesystem.create_directory` | L2 | Test 4 |
| حذف فایل | — | **غیرفعال در نسخه اولیه** (Test 5 در فاز ۵ با Approval) |

خطاهای `PermissionError` و `FileNotFoundError` باید به `success=false` + پیام فارسی نگاشت شوند.

### system.py (بند ۹.۲) — ✅ سه ابزار پیاده‌شده در فاز ۲ (`app/tools/system_tools.py`)

| ابزار | سطح | وضعیت |
|---|---|---|
| `system.get_system_info` | L1 | ✅ پیاده و تست‌شده (OS/هسته‌ها/hostname/نسخه پایتون) |
| `system.get_memory_usage` | L1 | ✅ پیاده و تست‌شده (RAM+swap: بایت/GB/درصد) |
| `system.get_disk_usage` | L1 | ✅ پیاده و تست‌شده (همه پارتیشن‌ها یا `path` خاص) |

بقیه ابزارهای گروه system (`get_cpu_usage`, `list_processes`, `get_process_info`, `get_network_info`) در فاز ۳ باقی‌اند — پایه psutil (Test 1).

### terminal.py (بند ۹.۳) — فاز ۳؛ طبق الزام بخش پنجم فاز ۲، ابزارهای اجرای دستور/تغییر سیستم در این فاز عمداً پیاده **نشده‌اند**

| ابزار | سطح | قوانین |
|---|---|---|
| `terminal.execute_powershell` | **L3** | اعتبارسنجی دستور، blocklist مدیریتی، Timeout اجباری، محدودسازی خروجی، تأیید صریح |
| `terminal.execute_python` | **L2–L3** | اجرای کد مدل فقط در Sandbox |
| `terminal.get_command_output` | L1 | خروجی در-جریان |
| `terminal.get_command_status` | L1 | — |

**ممنوع**: دستور دلخواه مدل بدون اعتبارسنجی؛ حذف/فرمت؛ تغییر Registry/کاربر/سرویس؛ pipes به shell خطرناک. کلمات کلیدی blocklist: `rm -rf`, `Remove-Item -Recurse`, `format`, `reg add`, `net user`, `shutdown`, `taskkill /F` روی پردازش‌های سیستمی و….

### applications.py (بند ۹.۴)

| ابزار | سطح |
|---|---|
| `applications.list_installed_applications` | L1 |
| `applications.find_application` | L1 |
| `applications.launch_application` | L2 (Test 3) |
| `applications.close_application` | **L3** — پردازش سیستمی/حساس ممنوع بدون تأیید |
| `applications.get_running_applications` | L1 |

### development.py (بند ۹.۵) — برای پروژه‌های Python و Embedded

`inspect_python_project`, `inspect_platformio_project`, `inspect_git_repository`, `run_pytest` (L2), `run_platformio_build` (L2), `inspect_project_dependencies`, `analyze_project_structure` (L1). سناریوی مرجع بند ۳.۳: «بررسی پروژه ESP32» = زنجیره‌ای از همین ابزارها.

### browser.py (بند ۹.۶) — فازهای بعد با Playwright

ورود به سایت/ارسال فرم/تراکنش = **L3** با مجوز ویژه؛ فقط-خواندن صفحه = L1/L2.

## ۴. چرخه فراخوانی (بند ۳.۴) — ✅ پیاده‌شده در `app/agent/orchestrator.py` (فاز ۲)

```
دریافت درخواست کاربر
  → ساخت Context (سیستم + تاریخچه + کاربر)
  → ارسال ابزارهای مجاز به مدل:
      native  ← قابلیت tools از /api/show → پارامتر tools + message.tool_calls
      fallback← پروتکل JSON در پیام سیستم → استخراج از متن پاسخ
  → دریافت پاسخ (استریم؛ thinking جدا)
  → تشخیص Tool Call (native: tool_calls / fallback: بلوک JSON)
  → اعتبارسنجی ابزار و پارامترها (Registry + Pydantic، مستقل از LLM)
  → بررسی مجوز (L1 اجرا؛ L2/L3 رد — فاز ۵ کامل می‌شود)
  → اجرا در thread جدا با Timeout + لغو
  → ToolResult ساختاریافته
  → ارسال نتیجه به مدل (native: پیام role=tool / fallback: پیام کاربر با [نتیجه ابزار])
  → تولید پاسخ نهایی (یا ادامه چرخه تا سقف)
```

سقف‌های پیاده‌شده: `MAX_TOOL_CALLS_PER_TURN=5` و `MAX_AGENT_ITERATIONS=8` — حلقه بی‌نهایت ممکن نیست. ورودی نامعتبر رد می‌شود و پیام خطای فارسی خودش به مدل می‌رسد (Test 6) — برنامه کرش نمی‌کند.

## ۵. اعتبارسنجی مستقل

- ابزارها هرگز به LLM اعتماد نمی‌کنند؛ schema خودشان را اعمال می‌کنند.
- اعتبارسنجی مسیر (filesystem): `resolve()` + بررسی پیشوند allowlist + رد symlinks فرار.
- `permission_level` در Registry ثبت و توسط Security (فاز ۵) اعمال می‌شود.
