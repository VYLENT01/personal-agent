"""ابزارهای Filesystem فاز ۳ (بخش ۴ مشخصات).

۱۱ ابزار در سه سطح:
- L1 (خودکار): list_directory / get_file_info / get_directory_tree /
  search_files / search_text / read_file
- L2 (با تأیید کاربر): create_directory / create_file / write_file / edit_file
- حذف: طبق الزام این فاز پیاده‌سازی نشده (L3 غیرفعال).

امنیت (بخش ۴):
- همه مسیرها از PathGuard می‌گذرند (allowlist قابل‌تنظیم + ضد traversal دو-لایه + symlink)
- فایل بزرگ: سقف MAX_READ_BYTES؛ باینری: تشخیص null-byte و ردِ «متن»
- encoding: تلاش utf-8 → utf-8-sig → cp1256 (فارسی) → latin-1 با گارد
- نوشتن اتمیک (tmp + os.replace)؛ PermissionDenied به خطای ساختاریافته
- خروجی‌ها محدود (MAX_LIST_ENTRIES / MAX_TEXT_RESULT_CHARS)
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

from pydantic import Field

from app.config import defaults as d
from app.security.paths import PathGuard, PathPolicyError
from app.tools.base import PermissionLevel, Tool, ToolInput, ToolResult

logger = logging.getLogger(__name__)

# --- تشخیص encoding ---
_ENCODINGS = ("utf-8", "utf-8-sig", "cp1256", "latin-1")
# پسوندهای متنی رایج برای shortcut سریع
_TEXT_SUFFIXES = {
    ".txt", ".md", ".py", ".js", ".ts", ".json", ".yaml", ".yml", ".toml",
    ".ini", ".cfg", ".csv", ".log", ".html", ".css", ".xml", ".rst", ".sh",
    ".ps1", ".c", ".h", ".cpp", ".hpp", ".java", ".rs", ".go", ".sql",
}


def decode_text(data: bytes) -> tuple[Optional[str], str]:
    """رمزگشایی متن با تشخیص encoding؛ خروجی: (متن یا None اگر باینری، نام encoding)."""
    if b"\x00" in data[:4096]:
        return None, "binary"
    for encoding in _ENCODINGS:
        try:
            return data.decode(encoding), encoding
        except (UnicodeDecodeError, LookupError):
            continue
    return None, "binary"


def _safe_guard(tool: "FilesystemToolBase") -> PathGuard:
    guard = tool.guard
    if guard is None:  # pragma: no cover - bootstrap همیشه تزریق می‌کند
        raise RuntimeError("PathGuard برای ابزار filesystem تزریق نشده است.")
    return guard


class FilesystemToolBase(Tool):
    """پایه مشترک: نگه‌داری PathGuard تزریقی (کپی منطق مشترک ممنوع)."""

    category = "filesystem"
    guard: Optional[PathGuard] = None

    def bind_guard(self, guard: PathGuard) -> None:
        self.guard = guard


def _fs_error(path: str, exc: Exception, action: str) -> str:
    """پیام فارسی ساختاریافته برای خطاهای سیستم فایل."""
    if isinstance(exc, PermissionError):
        return f"دسترسی به مسیر رد شد (Permission Denied): {path}"
    if isinstance(exc, FileNotFoundError):
        return f"مسیر یافت نشد: {path}"
    if isinstance(exc, OSError):
        return f"خطای سیستم فایل هنگام {action}: {path} — {exc.strerror or exc}"
    return f"خطای غیرمنتظره هنگام {action}: {path} — {exc}"


def _truncate(text: str, limit: int = d.MAX_TEXT_RESULT_CHARS) -> str:
    if len(text) > limit:
        return text[:limit] + f"\n…[بریده شد؛ طول کل: {len(text)} کاراکتر]"
    return text


# ---------------------------------------------------------------------------
# ورودی‌ها (Pydantic؛ extra=forbid از ToolInput)
# ---------------------------------------------------------------------------

class PathInput(ToolInput):
    path: str = Field(description="مسیر مطلق یا با ~ (نسبی پذیرفته نمی‌شود)")


class ListDirectoryInput(ToolInput):
    path: str = Field(description="مسیر پوشه")
    show_hidden: bool = Field(default=False, description="نمایش فایل‌های مخفی")


class TreeInput(ToolInput):
    path: str = Field(description="مسیر ریشه درخت")
    max_depth: int = Field(default=d.MAX_TREE_DEPTH, ge=1, le=d.MAX_TREE_DEPTH)


class SearchFilesInput(ToolInput):
    directory: str = Field(description="پوشه شروع جست‌وجو (داخل محدوده مجاز)")
    pattern: str = Field(
        min_length=1, max_length=120, description="بخشی از نام فایل (حساس به بزرگی/کوچکی نیست)"
    )
    recursive: bool = Field(default=True, description="جست‌وجو در زیرپوشه‌ها")


class SearchTextInput(ToolInput):
    directory: str = Field(description="پوشه شروع جست‌وجو (داخل محدوده مجاز)")
    query: str = Field(min_length=1, max_length=200, description="متن مورد جست‌وجو")
    pattern: str = Field(
        default="*", max_length=32, description="الگوی نام فایل مثل *.py"
    )


class ReadFileInput(ToolInput):
    path: str = Field(description="مسیر فایل متنی")
    max_bytes: int = Field(
        default=d.MAX_READ_BYTES, ge=64, le=d.MAX_READ_BYTES,
        description="حداکثر بایت خوانده‌شده",
    )


class WriteFileInput(ToolInput):
    path: str = Field(description="مسیر فایل مقصد")
    content: str = Field(max_length=100_000, description="محتوای کامل فایل")


class CreateFileInput(ToolInput):
    path: str = Field(description="مسیر فایل جدید")
    content: str = Field(default="", max_length=100_000, description="محتوای اولیه (اختیاری)")


class EditFileInput(ToolInput):
    path: str = Field(description="مسیر فایل")
    old_text: str = Field(min_length=1, max_length=2000, description="متن دقیق برای جایگزینی")
    new_text: str = Field(default="", max_length=2000, description="متن جایگزین")
    replace_all: bool = Field(default=False, description="جایگزینی همه تکرارها")


# ---------------------------------------------------------------------------
# ابزارهای L1 — فقط-خواندنی
# ---------------------------------------------------------------------------

class ListDirectoryTool(FilesystemToolBase):
    input_model = ListDirectoryInput
    permission_level = PermissionLevel.L1_READ_ONLY

    @property
    def name(self) -> str:
        return "filesystem.list_directory"

    @property
    def description(self) -> str:
        return (
            "فهرست فایل‌ها و پوشه‌های یک مسیر را با نوع، اندازه و زمان تغییر "
            "می‌دهد. فقط مسیرهای مجاز قابل فهرست‌کردن هستند."
        )

    def _run(self, args: ListDirectoryInput) -> dict:
        guard = _safe_guard(self)
        try:
            folder = guard.resolve_checked(args.path)
        except PathPolicyError as exc:
            raise PermissionError(str(exc)) from exc
        if not folder.is_dir():
            raise NotADirectoryError(f"مسیر پوشه نیست: {folder}")
        entries: list[dict[str, Any]] = []
        try:
            for item in sorted(folder.iterdir(), key=lambda p: p.name.lower()):
                if args.show_hidden is False and item.name.startswith("."):
                    continue
                try:
                    stat = item.stat()
                except OSError:
                    continue  # قفل‌شده/قطع‌شده — رد بی‌خطر
                entries.append(
                    {
                        "name": item.name,
                        "type": "dir" if item.is_dir() else "file",
                        "size_bytes": stat.st_size if not item.is_dir() else None,
                        "modified": time.strftime(
                            "%Y-%m-%d %H:%M", time.localtime(stat.st_mtime)
                        ),
                    }
                )
                if len(entries) >= d.MAX_LIST_ENTRIES:
                    entries.append({"note": f"…فهرست در {d.MAX_LIST_ENTRIES} مورد برش خورد"})
                    break
        except PermissionError as exc:
            raise PermissionError(_fs_error(str(folder), exc, "فهرست‌کردن")) from exc
        except OSError as exc:
            raise OSError(_fs_error(str(folder), exc, "فهرست‌کردن")) from exc
        return {"directory": str(folder), "count": len(entries), "entries": entries}


class GetFileInfoTool(FilesystemToolBase):
    input_model = PathInput
    permission_level = PermissionLevel.L1_READ_ONLY

    @property
    def name(self) -> str:
        return "filesystem.get_file_info"

    @property
    def description(self) -> str:
        return (
            "اطلاعات یک فایل یا پوشه: اندازه، زمان ایجاد/تغییر، نوع، پسوند و مجوزها."
        )

    def _run(self, args: PathInput) -> dict:
        guard = _safe_guard(self)
        try:
            target = guard.resolve_checked(args.path)
        except PathPolicyError as exc:
            raise PermissionError(str(exc)) from exc
        stat = target.stat()
        return {
            "path": str(target),
            "type": "dir" if target.is_dir() else "file",
            "size_bytes": stat.st_size,
            "size_kb": round(stat.st_size / 1024, 1),
            "created": time.strftime("%Y-%m-%d %H:%M", time.localtime(stat.st_ctime)),
            "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(stat.st_mtime)),
            "suffix": target.suffix or None,
            "readonly": not bool(stat.st_mode & 0o200),
        }


class GetDirectoryTreeTool(FilesystemToolBase):
    input_model = TreeInput
    permission_level = PermissionLevel.L1_READ_ONLY

    @property
    def name(self) -> str:
        return "filesystem.get_directory_tree"

    @property
    def description(self) -> str:
        return (
            f"درخت پوشه‌ها تا عمق محدود ({d.MAX_TREE_DEPTH})؛ برای فهم ساختار پروژه. "
            "فایل‌ها فقط شمرده می‌شوند، فهرست کامل نمی‌آیند."
        )

    def _run(self, args: TreeInput) -> dict:
        guard = _safe_guard(self)
        try:
            root = guard.resolve_checked(args.path)
        except PathPolicyError as exc:
            raise PermissionError(str(exc)) from exc
        if not root.is_dir():
            raise NotADirectoryError(f"مسیر پوشه نیست: {root}")
        tree: dict[str, Any] = {"name": root.name or str(root), "children": []}
        total = {"dirs": 0, "files": 0}
        self._walk(tree, root, args.max_depth, total)
        return {
            "root": str(root),
            "dirs": total["dirs"],
            "files": total["files"],
            "tree": tree,
        }

    def _walk(self, node: dict[str, Any], folder: Path, depth: int, total: dict[str, int]) -> None:
        children = node["children"]
        try:
            items = sorted(folder.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except (PermissionError, OSError):
            node["error"] = "دسترسی رد شد"
            return
        for item in items:
            if item.name.startswith("."):
                continue
            if item.is_dir():
                total["dirs"] += 1
                child: dict[str, Any] = {"name": item.name, "children": []}
                children.append(child)
                if depth > 1 and total["dirs"] + total["files"] < d.MAX_LIST_ENTRIES:
                    self._walk(child, item, depth - 1, total)
            else:
                total["files"] += 1
                if len(children) < d.MAX_LIST_ENTRIES:
                    children.append({"name": item.name, "file": True})


class SearchFilesTool(FilesystemToolBase):
    input_model = SearchFilesInput
    permission_level = PermissionLevel.L1_READ_ONLY

    @property
    def name(self) -> str:
        return "filesystem.search_files"

    @property
    def description(self) -> str:
        return (
            "جست‌وجوی نام فایل/پوشه در محدوده مجاز (بخشی از نام، بدون حساسیت به "
            "بزرگی حروف). خروجی محدود است."
        )

    def _run(self, args: SearchFilesInput) -> dict:
        guard = _safe_guard(self)
        try:
            base = guard.resolve_checked(args.directory)
        except PathPolicyError as exc:
            raise PermissionError(str(exc)) from exc
        needle = args.pattern.lower()
        matches: list[dict[str, Any]] = []
        roots = [base]
        while roots and len(matches) < d.MAX_LIST_ENTRIES:
            folder = roots.pop(0)
            try:
                for item in folder.iterdir():
                    if needle in item.name.lower():
                        matches.append(
                            {"path": str(item), "type": "dir" if item.is_dir() else "file"}
                        )
                        if len(matches) >= d.MAX_LIST_ENTRIES:
                            break
                    if args.recursive and item.is_dir():
                        roots.append(item)
            except (PermissionError, OSError):
                continue  # پوشه قفل‌شده — رد بی‌خطر
        return {"base": str(base), "pattern": args.pattern, "count": len(matches), "matches": matches}


class SearchTextTool(FilesystemToolBase):
    input_model = SearchTextInput
    permission_level = PermissionLevel.L1_READ_ONLY

    @property
    def name(self) -> str:
        return "filesystem.search_text"

    @property
    def description(self) -> str:
        return (
            "جست‌وجوی متن داخل فایل‌های متنی محدوده مجاز (مثلاً کد/یادداشت)؛ "
            "فایل‌های باینری نادیده گرفته می‌شوند و نتیجه محدود است."
        )

    def _run(self, args: SearchTextInput) -> dict:
        guard = _safe_guard(self)
        try:
            base = guard.resolve_checked(args.directory)
        except PathPolicyError as exc:
            raise PermissionError(str(exc)) from exc
        needle = args.query
        results: list[dict[str, Any]] = []
        roots = [base]
        scanned = 0
        while roots and len(results) < d.MAX_SEARCH_RESULTS and scanned < 500:
            folder = roots.pop(0)
            try:
                items = list(folder.iterdir())
            except (PermissionError, OSError):
                continue
            for item in items:
                if item.is_dir():
                    roots.append(item)
                    continue
                if item.suffix.lower() not in _TEXT_SUFFIXES:
                    continue
                if item.stat().st_size > d.MAX_READ_BYTES:
                    continue  # فایل بزرگ — جست‌وجوی کامل نه
                scanned += 1
                try:
                    data = item.read_bytes()
                except (PermissionError, OSError):
                    continue
                text, _ = decode_text(data)
                if text is None:
                    continue
                for line_no, line in enumerate(text.splitlines(), start=1):
                    if needle in line:
                        snippet = line.strip()[:200]
                        results.append(
                            {"path": str(item), "line": line_no, "text": snippet}
                        )
                        if len(results) >= d.MAX_SEARCH_RESULTS:
                            break
        return {
            "base": str(base),
            "query": args.query,
            "files_scanned": scanned,
            "count": len(results),
            "results": results,
        }


class ReadFileTool(FilesystemToolBase):
    input_model = ReadFileInput
    permission_level = PermissionLevel.L1_READ_ONLY

    @property
    def name(self) -> str:
        return "filesystem.read_file"

    @property
    def description(self) -> str:
        return (
            f"خواندن فایل متنی تا سقف {d.MAX_READ_BYTES // 1024}KB با تشخیص encoding؛ "
            "فایل باینری رد می‌شود."
        )

    def _run(self, args: ReadFileInput) -> dict:
        guard = _safe_guard(self)
        try:
            target = guard.resolve_checked(args.path)
        except PathPolicyError as exc:
            raise PermissionError(str(exc)) from exc
        if target.is_dir():
            raise IsADirectoryError(f"مسیر پوشه است نه فایل: {target}")
        try:
            data = target.read_bytes()
        except PermissionError as exc:
            raise PermissionError(_fs_error(args.path, exc, "خواندن")) from exc
        except OSError as exc:
            raise OSError(_fs_error(args.path, exc, "خواندن")) from exc
        truncated = False
        if len(data) > args.max_bytes:
            data = data[: args.max_bytes]
            truncated = True
        text, encoding = decode_text(data)
        if text is None:
            raise ValueError(
                f"فایل باینری است و نمی‌توان آن را به‌عنوان متن خواند: {target.name}"
            )
        return {
            "path": str(target),
            "size_bytes": target.stat().st_size,
            "encoding": encoding,
            "truncated": truncated,
            "content": _truncate(text),
        }


# ---------------------------------------------------------------------------
# ابزارهای L2 — ایجاد/نوشتن/ویرایش (با تأیید کاربر؛ Registry گیت می‌کند)
# ---------------------------------------------------------------------------

class CreateDirectoryTool(FilesystemToolBase):
    input_model = PathInput
    permission_level = PermissionLevel.L2_WRITE

    @property
    def name(self) -> str:
        return "filesystem.create_directory"

    @property
    def description(self) -> str:
        return (
            "ایجاد پوشه (والدها هم ساخته می‌شوند) در محدوده مجاز؛ "
            "نیازمند تأیید کاربر."
        )

    def _run(self, args: PathInput) -> dict:
        guard = _safe_guard(self)
        target = guard.ensure_parent(args.path)
        if target.exists():
            raise FileExistsError(f"مسیر از قبل وجود دارد: {target}")
        try:
            target.mkdir(parents=True, exist_ok=False)
        except PermissionError as exc:
            raise PermissionError(_fs_error(args.path, exc, "ایجاد پوشه")) from exc
        except OSError as exc:
            raise OSError(_fs_error(args.path, exc, "ایجاد پوشه")) from exc
        return {"created": str(target)}


class CreateFileTool(FilesystemToolBase):
    input_model = CreateFileInput
    permission_level = PermissionLevel.L2_WRITE

    @property
    def name(self) -> str:
        return "filesystem.create_file"

    @property
    def description(self) -> str:
        return (
            "ایجاد فایل متنی جدید با محتوای اولیه (اگر وجود نداشته باشد)؛ "
            "نوشتن اتمیک؛ نیازمند تأیید کاربر."
        )

    def _run(self, args: CreateFileInput) -> dict:
        guard = _safe_guard(self)
        target = guard.ensure_parent(args.path)
        if target.exists():
            raise FileExistsError(f"فایل از قبل وجود دارد: {target}")
        _atomic_write(target, args.content)
        return {"created": str(target), "size_bytes": len(args.content.encode("utf-8"))}


class WriteFileTool(FilesystemToolBase):
    input_model = WriteFileInput
    permission_level = PermissionLevel.L2_WRITE

    @property
    def name(self) -> str:
        return "filesystem.write_file"

    @property
    def description(self) -> str:
        return (
            "نوشتن/بازنویسی کامل محتوای یک فایل متنی (اتمیک؛ اگر نباشد ساخته می‌شود)؛ "
            "نیازمند تأیید کاربر."
        )

    def _run(self, args: WriteFileInput) -> dict:
        guard = _safe_guard(self)
        target = guard.ensure_parent(args.path)
        existed = target.exists()
        if existed and target.is_dir():
            raise IsADirectoryError(f"مسیر پوشه است نه فایل: {target}")
        _atomic_write(target, args.content)
        return {
            "written": str(target),
            "overwrote": existed,
            "size_bytes": len(args.content.encode("utf-8")),
        }


class EditFileTool(FilesystemToolBase):
    input_model = EditFileInput
    permission_level = PermissionLevel.L2_WRITE

    @property
    def name(self) -> str:
        return "filesystem.edit_file"

    @property
    def description(self) -> str:
        return (
            "ویرایش جراحی‌وار متن: جایگزینی دقیق old_text با new_text "
            "(اولین تکرار یا همه تکرارها)؛ نیازمند تأیید کاربر."
        )

    def _run(self, args: EditFileInput) -> dict:
        guard = _safe_guard(self)
        try:
            target = guard.resolve_checked(args.path)
        except PathPolicyError as exc:
            raise PermissionError(str(exc)) from exc
        if target.is_dir():
            raise IsADirectoryError(f"مسیر پوشه است نه فایل: {target}")
        try:
            data = target.read_bytes()
        except PermissionError as exc:
            raise PermissionError(_fs_error(args.path, exc, "خواندن برای ویرایش")) from exc
        text, encoding = decode_text(data)
        if text is None:
            raise ValueError(f"فایل باینری است و ویرایش متنی ممکن نیست: {target.name}")
        count = text.count(args.old_text)
        if count == 0:
            raise ValueError(
                "متن مورد جست‌وجو در فایل پیدا نشد؛ old_text باید دقیقاً باشد."
            )
        if args.replace_all:
            new_content = text.replace(args.old_text, args.new_text)
        else:
            new_content = text.replace(args.old_text, args.new_text, 1)
        _atomic_write(target, new_content)
        return {
            "edited": str(target),
            "replacements": count if args.replace_all else 1,
            "encoding": encoding,
        }


def _atomic_write(target: Path, content: str) -> None:
    """نوشتن اتمیک: فایل موقت در همان پوشه سپس os.replace (بخش ۴)."""
    try:
        tmp = target.with_name(target.name + ".agenttmp")
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, target)
    except PermissionError as exc:
        raise PermissionError(_fs_error(str(target), exc, "نوشتن")) from exc
    except OSError as exc:
        raise OSError(_fs_error(str(target), exc, "نوشتن")) from exc


def create_filesystem_tools(guard: PathGuard) -> list[Tool]:
    """۹+۲ ابزار filesystem آماده ثبت؛ guard مشترک تزریق می‌شود."""
    tools = [
        ListDirectoryTool(),
        GetFileInfoTool(),
        GetDirectoryTreeTool(),
        SearchFilesTool(),
        SearchTextTool(),
        ReadFileTool(),
        CreateDirectoryTool(),
        CreateFileTool(),
        WriteFileTool(),
        EditFileTool(),
    ]
    for tool in tools:
        tool.bind_guard(guard)
    return tools


__all__ = ["create_filesystem_tools", "decode_text"]
