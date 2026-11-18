# کاتالوگ ابزارها (Tool Registry) — طراحی فاز ۲ و ۳

## ۱. قرارداد هر ابزار

هر ابزار باید این مشخصات را داشته باشد (بند ۸ مشخصات):

```python
class Tool:
    name: str                # مثل "filesystem.search"
    description: str         # توضیح برای مدل
    parameters: list[Param]  # فهرست پارامترها
    input_schema: dict       # JSON Schema برای اعتبارسنجی ورودی
    permission_level: int    # L1 / L2 / L3

    def validate(self, args: dict) -> ValidationResult: ...
    def execute(self, args: dict) -> ToolResult: ...
```

## ۲. قالب ساختاریافته نتیجه

```json
{
  "success": true,
  "tool": "filesystem.search",
  "data": {},
  "error": null
}
```

`data` فقط در صورت `success=true` معنا دارد؛ `error` پیام فارسی قابل‌نمایش است.

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

### system.py (بند ۹.۲) — همه فقط-خواندنی (L1)

`get_cpu_usage`, `get_memory_usage`, `get_disk_usage`, `get_system_info`, `list_processes`, `get_process_info`, `get_network_info` — پایه psutil (Test 1).

### terminal.py (بند ۹.۳)

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

## ۴. چرخه فراخوانی (بند ۳.۴)

```
LLM تصمیم → نام+args
  → Registry: ابزار موجود؟
  → اعتبارسنجی args با input_schema (مستقل از LLM)
  → بررسی سطح دسترسی (L1 اجرا؛ L2 سیاست؛ L3 تأیید کاربر — فاز ۵)
  → اجرا در thread جدا با Timeout
  → ToolResult ساختاریافته → بازگشت به LLM → ادامه چرخه (سقف iteration)
```

ورودی نامعتبر **رد** می‌شود و به مدل پیام اصلاح (`build_repair_message`) برمی‌گردد (Test 6) — برنامه کرش نمی‌کند.

## ۵. اعتبارسنجی مستقل

- ابزارها هرگز به LLM اعتماد نمی‌کنند؛ schema خودشان را اعمال می‌کنند.
- اعتبارسنجی مسیر (filesystem): `resolve()` + بررسی پیشوند allowlist + رد symlinks فرار.
- `permission_level` در Registry ثبت و توسط Security (فاز ۵) اعمال می‌شود.
