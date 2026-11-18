"""تست‌های Tools Registry — طرح فاز ۲ (این فایل فقط اسکلت معتبر است).

تست واقعی با پیاده‌سازی فاز ۲ (registry/filesystem/system/...) تکمیل می‌شود.
فعلاً قالب ساختاریافته نتیجه ابزار مطابق بند ۸ مشخصات قفل می‌شود.
"""
from __future__ import annotations


def test_tool_result_contract_shape() -> None:
    """قالب ساختاریافته نتیجه اجرای ابزار (بند ۸ مشخصات)."""
    result: dict = {"success": True, "tool": "filesystem.search", "data": {}, "error": None}
    assert set(result) == {"success", "tool", "data", "error"}
    assert isinstance(result["success"], bool)
    failure: dict = {"success": False, "tool": "filesystem.read", "data": None, "error": "فایل یافت نشد"}
    assert failure["error"] is not None
