"""تست‌های ابزارهای Filesystem فاز ۳ (چک‌لیست بخش ۱۰).

- فهرست‌کردن پوشه / خواندن فایل متنی / فایل بزرگ / فایل باینری
- مسیر نامعتبر / مسیر خارج از محدوده مجاز / Path Traversal
- ایجاد فایل / ویرایش فایل / رد عملیات بدون تأیید / خطای Permission Denied
- درخت / جست‌وجوی نام / جست‌وجوی متن / encoding / نوشتن اتمیک
"""
from __future__ import annotations

import os
import stat as stat_mod
from pathlib import Path

import pytest

from app.security.paths import PathGuard
from app.security.permissions import PermissionManager
from app.tools.filesystem import create_filesystem_tools, decode_text
from app.tools.registry import ToolRegistry


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    """فضای مجاز: ws/{docs,code} با فایل‌های نمونه."""
    docs = tmp_path / "ws" / "docs"
    code = tmp_path / "ws" / "code"
    docs.mkdir(parents=True)
    code.mkdir(parents=True)
    (docs / "readme.md").write_text("# سلام\nخط دوم\n", encoding="utf-8")
    (docs / "notes.txt").write_text("یادداشت A\nیادداشت B\n", encoding="utf-8")
    (code / "app.py").write_text("def main():\n    return 42\n", encoding="utf-8")
    (code / "data.json").write_text('{"k": "v"}\n', encoding="utf-8")
    (tmp_path / "ws" / "binary.bin").write_bytes(b"\x00\x01\x02PNG")
    return tmp_path / "ws"


@pytest.fixture()
def guard(workspace: Path) -> PathGuard:
    return PathGuard([str(workspace)])


@pytest.fixture()
def registry(guard: PathGuard) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in create_filesystem_tools(guard):
        registry.register_tool(tool)
    return registry


# ---------------------------------------------------------------------------
# L1 — مشاهده و جست‌وجو
# ---------------------------------------------------------------------------

class TestListAndInfo:
    def test_list_directory(self, registry: ToolRegistry, workspace: Path) -> None:
        result = registry.execute_tool(
            "filesystem.list_directory", {"path": str(workspace / "docs")}
        )
        assert result.success is True
        names = [e["name"] for e in result.data["entries"]]
        assert "readme.md" in names and "notes.txt" in names
        entry = next(e for e in result.data["entries"] if e["name"] == "readme.md")
        assert entry["type"] == "file" and entry["size_bytes"] > 0

    def test_list_directory_hidden_flag(self, registry: ToolRegistry, workspace: Path) -> None:
        (workspace / "docs" / ".secret").write_text("x", encoding="utf-8")
        visible = registry.execute_tool(
            "filesystem.list_directory", {"path": str(workspace / "docs")}
        )
        hidden = registry.execute_tool(
            "filesystem.list_directory",
            {"path": str(workspace / "docs"), "show_hidden": True},
        )
        assert any(e["name"] == ".secret" for e in hidden.data["entries"])
        assert not any(e["name"] == ".secret" for e in visible.data["entries"])

    def test_list_directory_path_is_file(self, registry: ToolRegistry, workspace: Path) -> None:
        result = registry.execute_tool(
            "filesystem.list_directory", {"path": str(workspace / "docs" / "readme.md")}
        )
        assert result.success is False
        assert "پوشه" in (result.error or "")

    def test_get_file_info(self, registry: ToolRegistry, workspace: Path) -> None:
        result = registry.execute_tool(
            "filesystem.get_file_info", {"path": str(workspace / "docs" / "readme.md")}
        )
        assert result.success is True
        assert result.data["type"] == "file"
        assert result.data["size_bytes"] > 0
        assert result.data["suffix"] == ".md"
        assert result.data["modified"]

    def test_directory_tree(self, registry: ToolRegistry, workspace: Path) -> None:
        (workspace / "code" / "sub").mkdir()
        (workspace / "code" / "sub" / "deep.py").write_text("x", encoding="utf-8")
        result = registry.execute_tool(
            "filesystem.get_directory_tree", {"path": str(workspace)}
        )
        assert result.success is True
        assert result.data["dirs"] >= 3
        assert result.data["files"] >= 4
        # عمق پیش‌فرض ۳: sub دیده می‌شود
        names = str(result.data["tree"])
        assert "code" in names and "sub" in names

    def test_tree_depth_limit(self, registry: ToolRegistry, workspace: Path) -> None:
        deep = workspace / "code"
        for i in range(5):
            deep = deep / f"lvl{i}"
        deep.mkdir(parents=True)
        (deep / "leaf.txt").write_text("x", encoding="utf-8")
        result = registry.execute_tool(
            "filesystem.get_directory_tree", {"path": str(workspace)}
        )
        # عمق ۳ → lvl3/lvl4 نباید در درخت باشد
        assert result.success is True
        assert "lvl3" not in str(result.data["tree"])


class TestSearch:
    def test_search_files(self, registry: ToolRegistry, workspace: Path) -> None:
        result = registry.execute_tool(
            "filesystem.search_files",
            {"directory": str(workspace), "pattern": "read"},
        )
        assert result.success is True
        assert any("readme.md" in m["path"] for m in result.data["matches"])

    def test_search_files_case_insensitive(self, registry: ToolRegistry, workspace: Path) -> None:
        (workspace / "docs" / "README2.md").write_text("x", encoding="utf-8")
        result = registry.execute_tool(
            "filesystem.search_files", {"directory": str(workspace), "pattern": "readme"}
        )
        assert result.data["count"] >= 2

    def test_search_text_finds_line(self, registry: ToolRegistry, workspace: Path) -> None:
        result = registry.execute_tool(
            "filesystem.search_text",
            {"directory": str(workspace / "docs"), "query": "خط دوم"},
        )
        assert result.success is True
        assert result.data["count"] == 1
        hit = result.data["results"][0]
        assert hit["line"] == 2 and "خط دوم" in hit["text"]

    def test_search_text_ignores_binary(self, registry: ToolRegistry, workspace: Path) -> None:
        (workspace / "code" / "x.bin").write_bytes(b"\x00query\x00")
        result = registry.execute_tool(
            "filesystem.search_text", {"directory": str(workspace), "query": "query"}
        )
        assert result.success is True  # کرش نمی‌کند
        assert not any(m["path"].endswith(".bin") for m in result.data["results"])

    def test_search_non_recursive(self, registry: ToolRegistry, workspace: Path) -> None:
        (workspace / "code" / "sub2").mkdir()
        (workspace / "code" / "sub2" / "app.py").write_text("x", encoding="utf-8")
        result = registry.execute_tool(
            "filesystem.search_files",
            {"directory": str(workspace / "code"), "pattern": "app", "recursive": False},
        )
        assert result.data["count"] == 1  # فقط app.py سطح اول

    def test_search_outside_scope(self, registry: ToolRegistry, tmp_path: Path) -> None:
        result = registry.execute_tool(
            "filesystem.search_files", {"directory": str(tmp_path), "pattern": "x"}
        )
        assert result.success is False
        assert "مجاز" in (result.error or "")


# ---------------------------------------------------------------------------
# L1 — خواندن
# ---------------------------------------------------------------------------

class TestRead:
    def test_read_text_file(self, registry: ToolRegistry, workspace: Path) -> None:
        result = registry.execute_tool(
            "filesystem.read_file", {"path": str(workspace / "docs" / "readme.md")}
        )
        assert result.success is True
        assert result.data["encoding"] == "utf-8"
        assert "سلام" in result.data["content"]
        assert result.data["truncated"] is False

    def test_read_latin1_fallback(self, registry: ToolRegistry, workspace: Path) -> None:
        (workspace / "docs" / "latin.txt").write_bytes("café".encode("latin-1"))
        result = registry.execute_tool(
            "filesystem.read_file", {"path": str(workspace / "docs" / "latin.txt")}
        )
        assert result.success is True
        # cp1256 برای بایت‌های فرانسوی هم معتبر است و قبل از latin-1 امتحان می‌شود؛
        # متنِ رمزگشایی‌شده در هر دو یکسان است — انکودینگ باید از مجموعه پذیرفته شود
        assert result.data["encoding"] in ("cp1256", "latin-1")
        assert "café" in result.data["content"]

    def test_read_binary_rejected(self, registry: ToolRegistry, workspace: Path) -> None:
        result = registry.execute_tool(
            "filesystem.read_file", {"path": str(workspace / "binary.bin")}
        )
        assert result.success is False
        assert "باینری" in (result.error or "")

    def test_read_large_file_truncated(self, registry: ToolRegistry, workspace: Path) -> None:
        big = workspace / "docs" / "big.txt"
        big.write_text("a" * 300_000, encoding="utf-8")
        result = registry.execute_tool(
            "filesystem.read_file",
            {"path": str(big), "max_bytes": 1024},
        )
        assert result.success is True
        assert result.data["truncated"] is True
        assert len(result.data["content"]) <= 1100

    def test_read_missing_file(self, registry: ToolRegistry, workspace: Path) -> None:
        result = registry.execute_tool(
            "filesystem.read_file", {"path": str(workspace / "docs" / "نبود.txt")}
        )
        assert result.success is False
        assert "وجود ندارد" in (result.error or "") or "یافت نشد" in (result.error or "")

    def test_read_directory_rejected(self, registry: ToolRegistry, workspace: Path) -> None:
        result = registry.execute_tool(
            "filesystem.read_file", {"path": str(workspace / "docs")}
        )
        assert result.success is False
        assert "پوشه" in (result.error or "")

    def test_read_outside_scope(self, registry: ToolRegistry, tmp_path: Path) -> None:
        (tmp_path / "outside.txt").write_text("x", encoding="utf-8")
        result = registry.execute_tool(
            "filesystem.read_file", {"path": str(tmp_path / "outside.txt")}
        )
        assert result.success is False
        assert "مجاز" in (result.error or "")

    def test_decode_text_binary_null(self) -> None:
        text, enc = decode_text(b"ab\x00cd")
        assert text is None and enc == "binary"
        text2, enc2 = decode_text("سلام".encode("utf-8"))
        assert text2 == "سلام" and enc2 == "utf-8"


# ---------------------------------------------------------------------------
# امنیت مسیر (چک‌لیست بخش ۱۰)
# ---------------------------------------------------------------------------

class TestPathSecurity:
    def test_traversal_rejected(self, registry: ToolRegistry, workspace: Path) -> None:
        result = registry.execute_tool(
            "filesystem.read_file",
            {"path": str(workspace / "docs" / ".." / ".." / "etc" / "passwd")},
        )
        assert result.success is False
        assert "پیمایش" in (result.error or "") or "مجاز" in (result.error or "")

    def test_absolute_outside_rejected(self, registry: ToolRegistry) -> None:
        result = registry.execute_tool(
            "filesystem.get_file_info", {"path": "/etc/passwd"}
        )
        assert result.success is False

    def test_relative_path_rejected(self, registry: ToolRegistry) -> None:
        result = registry.execute_tool(
            "filesystem.read_file", {"path": "docs/readme.md"}
        )
        assert result.success is False

    def test_write_outside_scope_rejected(self, registry: ToolRegistry, tmp_path: Path) -> None:
        token = PermissionManager().create_token(
            "filesystem.write_file", {"path": str(tmp_path / "x.txt"), "content": "x"}
        )
        result = registry.execute_tool(
            "filesystem.write_file",
            {"path": str(tmp_path / "x.txt"), "content": "x"},
            approval=token,
        )
        assert result.success is False
        assert "مجاز" in (result.error or "")


# ---------------------------------------------------------------------------
# L2 — نوشتن با تأیید
# ---------------------------------------------------------------------------

def _approve(name: str, args: dict) -> object:
    return PermissionManager().create_token(name, args)


class TestWriteTools:
    def test_create_directory(self, registry: ToolRegistry, workspace: Path) -> None:
        args = {"path": str(workspace / "docs" / "newdir")}
        result = registry.execute_tool("filesystem.create_directory", args, approval=_approve("filesystem.create_directory", args))
        assert result.success is True
        assert (workspace / "docs" / "newdir").is_dir()

    def test_create_file(self, registry: ToolRegistry, workspace: Path) -> None:
        args = {"path": str(workspace / "docs" / "new.txt"), "content": "متن جدید"}
        result = registry.execute_tool("filesystem.create_file", args, approval=_approve("filesystem.create_file", args))
        assert result.success is True
        assert (workspace / "docs" / "new.txt").read_text(encoding="utf-8") == "متن جدید"

    def test_create_file_existing_rejected(self, registry: ToolRegistry, workspace: Path) -> None:
        args = {"path": str(workspace / "docs" / "readme.md"), "content": "x"}
        result = registry.execute_tool("filesystem.create_file", args, approval=_approve("filesystem.create_file", args))
        assert result.success is False
        assert "وجود دارد" in (result.error or "")

    def test_write_file_overwrite(self, registry: ToolRegistry, workspace: Path) -> None:
        args = {"path": str(workspace / "docs" / "notes.txt"), "content": "بازنویسی کامل"}
        result = registry.execute_tool("filesystem.write_file", args, approval=_approve("filesystem.write_file", args))
        assert result.success is True
        assert result.data["overwrote"] is True
        assert (workspace / "docs" / "notes.txt").read_text(encoding="utf-8") == "بازنویسی کامل"

    def test_edit_file_single(self, registry: ToolRegistry, workspace: Path) -> None:
        args = {"path": str(workspace / "docs" / "readme.md"), "old_text": "خط دوم", "new_text": "خط ویرایش‌شده"}
        result = registry.execute_tool("filesystem.edit_file", args, approval=_approve("filesystem.edit_file", args))
        assert result.success is True
        assert result.data["replacements"] == 1
        text = (workspace / "docs" / "readme.md").read_text(encoding="utf-8")
        assert "خط ویرایش‌شده" in text and "خط دوم" not in text

    def test_edit_file_all(self, registry: ToolRegistry, workspace: Path) -> None:
        (workspace / "docs" / "rep.txt").write_text("x=1\nx=2\n", encoding="utf-8")
        args = {
            "path": str(workspace / "docs" / "rep.txt"),
            "old_text": "x=", "new_text": "y=", "replace_all": True,
        }
        result = registry.execute_tool("filesystem.edit_file", args, approval=_approve("filesystem.edit_file", args))
        assert result.data["replacements"] == 2

    def test_edit_file_not_found_text(self, registry: ToolRegistry, workspace: Path) -> None:
        args = {"path": str(workspace / "docs" / "readme.md"), "old_text": "این‌متن‌نیست", "new_text": "x"}
        result = registry.execute_tool("filesystem.edit_file", args, approval=_approve("filesystem.edit_file", args))
        assert result.success is False
        assert "پیدا نشد" in (result.error or "")

    def test_edit_binary_rejected(self, registry: ToolRegistry, workspace: Path) -> None:
        args = {"path": str(workspace / "binary.bin"), "old_text": "PNG", "new_text": "X"}
        result = registry.execute_tool("filesystem.edit_file", args, approval=_approve("filesystem.edit_file", args))
        assert result.success is False
        assert "باینری" in (result.error or "")

    def test_all_l2_rejected_without_approval(self, registry: ToolRegistry, workspace: Path) -> None:
        # چک‌لیست: «رد عملیات بدون تأیید» برای هر چهار ابزار نوشتنی
        cases = [
            ("filesystem.create_directory", {"path": str(workspace / "d1")}),
            ("filesystem.create_file", {"path": str(workspace / "f.txt"), "content": "x"}),
            ("filesystem.write_file", {"path": str(workspace / "f2.txt"), "content": "x"}),
            ("filesystem.edit_file", {"path": str(workspace / "docs" / "readme.md"), "old_text": "x", "new_text": "y"}),
        ]
        for name, args in cases:
            result = registry.execute_tool(name, args)
            assert result.success is False, name
            assert "تأیید کاربر" in (result.error or ""), name
            # هیچ اثر جانبی نباید مانده باشد
        assert not (workspace / "d1").exists()
        assert not (workspace / "f.txt").exists()

    def test_permission_denied_structured(self, registry: ToolRegistry, workspace: Path) -> None:
        # پوشه فقط-خواندن → نوشتن → PermissionError ساختاریافته (نه کرش)
        locked = workspace / "docs" / "locked"
        locked.mkdir()
        os.chmod(locked, stat_mod.S_IRUSR | stat_mod.S_IXUSR)  # r-x
        try:
            args = {"path": str(locked / "out.txt"), "content": "x"}
            result = registry.execute_tool(
                "filesystem.write_file", args, approval=_approve("filesystem.write_file", args)
            )
            assert result.success is False
            assert "دسترسی" in (result.error or "") or "Permission" in (result.error or "")
        finally:
            os.chmod(locked, 0o755)

    def test_atomic_write_no_tmp_left(self, registry: ToolRegistry, workspace: Path) -> None:
        args = {"path": str(workspace / "docs" / "atomic.txt"), "content": "اتمیک"}
        result = registry.execute_tool("filesystem.write_file", args, approval=_approve("filesystem.write_file", args))
        assert result.success is True
        assert not any(p.name.endswith(".agenttmp") for p in (workspace / "docs").iterdir())
        assert (workspace / "docs" / "atomic.txt").read_text(encoding="utf-8") == "اتمیک"


class TestSchemasAndMeta:
    def test_all_eleven_tools_registered(self, registry: ToolRegistry) -> None:
        names = registry.tool_names()
        expected = [
            "filesystem.create_directory", "filesystem.create_file",
            "filesystem.edit_file", "filesystem.get_directory_tree",
            "filesystem.get_file_info", "filesystem.list_directory",
            "filesystem.read_file", "filesystem.search_files",
            "filesystem.search_text", "filesystem.write_file",
        ]
        assert sorted(n for n in names if n.startswith("filesystem.")) == expected

    def test_l1_vs_l2_levels(self, registry: ToolRegistry) -> None:
        for name in (
            "filesystem.list_directory", "filesystem.read_file",
            "filesystem.search_text", "filesystem.get_file_info",
        ):
            assert registry.needs_approval(name) is False
        for name in (
            "filesystem.create_directory", "filesystem.create_file",
            "filesystem.write_file", "filesystem.edit_file",
        ):
            assert registry.needs_approval(name) is True

    def test_no_delete_tool_registered(self, registry: ToolRegistry) -> None:
        # الزام بخش ۴: حذف در این فاز پیاده‌سازی نمی‌شود
        assert not any("delete" in n or "remove" in n for n in registry.tool_names())
