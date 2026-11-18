"""سیستم ابزار ایجنت — قرارداد، Registry و ابزارهای پیاده‌شده.

فاز ۲: Tool Calling با اعتبارسنجی Pydantic و سطح دسترسی.
- base.py: قرارداد Tool (ToolInput/ToolResult/PermissionLevel)
- registry.py: Registry مرکزی (ثبت/اعتبارسنجی/اجرا با timeout و مجوز)
- system_tools.py: سه ابزار فقط-خواندنی سیستم (psutil)
"""
