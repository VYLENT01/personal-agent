"""تست‌های امنیت فاز ۳: PathGuard + PermissionManager/ApprovalToken.

مطابق بند ۸ و چک‌لیست بخش ۱۰ (Path Traversal / مسیر خارج از محدوده /
رد عملیات بدون تأیید / بی‌اعتباری با تغییر پارامتر).
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from app.config.settings import Settings
from app.security.paths import PathGuard, PathPolicyError
from app.security.permissions import (
    ApprovalError,
    ApprovalToken,
    PermissionManager,
    canonicalize,
    fingerprint,
)


@pytest.fixture()
def sandbox(tmp_path: Path) -> Path:
    """درخت آزمون: root/{docs,projects,secret.txt, link→/etc}"""
    docs = tmp_path / "docs"
    projects = tmp_path / "projects"
    docs.mkdir()
    projects.mkdir()
    (docs / "a.txt").write_text("سلام", encoding="utf-8")
    (docs / "b.txt").write_text("world", encoding="utf-8")
    (projects / "app").mkdir()
    (projects / "app" / "main.py").write_text("print('x')", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("بیرون", encoding="utf-8")
    return tmp_path


@pytest.fixture()
def guard(sandbox: Path) -> PathGuard:
    return PathGuard([str(sandbox / "docs"), str(sandbox / "projects")])


# ---------------------------------------------------------------------------
# PathGuard
# ---------------------------------------------------------------------------

class TestPathGuardAllowed:
    def test_path_inside_root_allowed(self, guard: PathGuard, sandbox: Path) -> None:
        resolved = guard.resolve_checked(str(sandbox / "docs" / "a.txt"))
        assert resolved == (sandbox / "docs" / "a.txt").resolve()

    def test_root_itself_allowed(self, guard: PathGuard, sandbox: Path) -> None:
        resolved = guard.resolve_checked(str(sandbox / "docs"))
        assert resolved == (sandbox / "docs").resolve()

    def test_is_allowed_false_outside(self, guard: PathGuard, sandbox: Path) -> None:
        assert guard.is_allowed(str(sandbox / "secret.txt")) is False

    def test_home_style_paths(self, tmp_path: Path) -> None:
        g = PathGuard([f"~/{tmp_path.name}"])  # ریشه با ~ — expanduser اجرا می‌شود
        # NOTE: ~/X وابسته به HOME محیط است؛ فقط شکل expand را تست می‌کنیم
        assert g.roots and "~" not in str(g.roots[0])

    def test_relative_path_against_root(self, guard: PathGuard, sandbox: Path) -> None:
        # مسیر نسبی نسبت به CWD مستقل از ریشه‌ها resolve می‌شود → رد
        assert guard.is_allowed("docs/a.txt") is False


class TestPathGuardTraversal:
    def test_dotdot_traversal_rejected(self, guard: PathGuard, sandbox: Path) -> None:
        with pytest.raises(PathPolicyError) as exc:
            guard.resolve_checked(str(sandbox / "docs" / ".." / "secret.txt"))
        assert "پیمایش" in str(exc.value) or "مجاز" in str(exc.value)

    def test_windows_style_traversal_rejected(self, guard: PathGuard, sandbox: Path) -> None:
        with pytest.raises(PathPolicyError):
            guard.resolve_checked(str(sandbox / "docs") + "\\..\\secret.txt")

    def test_absolute_path_outside_root(self, guard: PathGuard) -> None:
        with pytest.raises(PathPolicyError) as exc:
            guard.resolve_checked("/etc/passwd")
        assert "مجاز" in str(exc.value)

    def test_nonexistent_path_for_read(self, guard: PathGuard, sandbox: Path) -> None:
        with pytest.raises(PathPolicyError):
            guard.resolve_checked(str(sandbox / "docs" / "نبود.txt"), must_exist=True)

    def test_missing_ok_for_create(self, guard: PathGuard, sandbox: Path) -> None:
        target = guard.ensure_parent(str(sandbox / "docs" / "جدید.txt"))
        assert target == (sandbox / "docs" / "جدید.txt").resolve()

    def test_ensure_parent_outside_root(self, guard: PathGuard, sandbox: Path) -> None:
        with pytest.raises(PathPolicyError):
            guard.ensure_parent(str(sandbox / "خارج" / "x.txt"))

    def test_symlink_escape_rejected(self, sandbox: Path, tmp_path_factory) -> None:
        # symlink داخل ریشه → مقصد بیرون ریشه؛ resolve باید آن را رد کند
        docs = sandbox / "docs"
        try:
            link = docs / "escape"
            link.symlink_to("/etc")
        except (OSError, NotImplementedError):  # sandbox بدون اجازه symlink
            pytest.skip("symlink در این محیط پشتیبانی نمی‌شود")
        g = PathGuard([str(docs)])
        with pytest.raises(PathPolicyError):
            g.resolve_checked(str(link))

    def test_empty_path(self, guard: PathGuard) -> None:
        with pytest.raises(PathPolicyError):
            guard.resolve_checked("   ")


class TestPathGuardFromSettings:
    def test_build_from_settings(self, sandbox: Path, monkeypatch) -> None:
        from app.security.paths import build_path_guard

        settings = Settings(allowed_paths=[str(sandbox / "docs")])
        guard = build_path_guard(settings)
        assert guard.is_allowed(str(sandbox / "docs" / "a.txt"))

    def test_settings_env_override(self, sandbox: Path, monkeypatch) -> None:
        monkeypatch.setenv("PERSONAL_AGENT_ALLOWED_PATHS", f"{sandbox / 'projects'}")
        settings = Settings.load(sandbox / "missing.json")
        assert settings.resolved_allowed_paths() == [str(sandbox / "projects")]


# ---------------------------------------------------------------------------
# PermissionManager / ApprovalToken
# ---------------------------------------------------------------------------

class TestApprovalToken:
    def test_token_match_and_consume(self) -> None:
        manager = PermissionManager()
        token = manager.create_token("filesystem.write_file", {"path": "a.txt", "content": "x"})
        token.consume("filesystem.write_file", {"path": "a.txt", "content": "x"})
        # یک‌بارمصرف: مصرف دوم رد می‌شود
        with pytest.raises(ApprovalError):
            token.consume("filesystem.write_file", {"path": "a.txt", "content": "x"})

    def test_changed_args_invalidate_token(self) -> None:
        token = PermissionManager().create_token(
            "filesystem.write_file", {"path": "a.txt", "content": "x"}
        )
        with pytest.raises(ApprovalError) as exc:
            token.consume("filesystem.write_file", {"path": "a.txt", "content": "MALICIOUS"})
        assert "تغییر کرده" in str(exc.value)

    def test_different_tool_invalidates(self) -> None:
        token = PermissionManager().create_token(
            "filesystem.create_file", {"path": "a.txt"}
        )
        with pytest.raises(ApprovalError):
            token.consume("terminal.execute_command", {"path": "a.txt"})

    def test_expired_token(self) -> None:
        manager = PermissionManager(approval_ttl=0.01)
        token = manager.create_token("t", {})
        time.sleep(0.05)
        with pytest.raises(ApprovalError) as exc:
            token.consume("t", {})
        assert "منقضی" in str(exc.value)

    def test_verify_without_consume(self) -> None:
        token = PermissionManager().create_token("t", {"x": 1})
        assert PermissionManager.verify(token, "t", {"x": 1}) is True
        # هنوز مصرف نشده؛ verify دوباره هم درست است
        assert PermissionManager.verify(token, "t", {"x": 1}) is True
        assert PermissionManager.verify(token, "t", {"x": 2}) is False
        assert PermissionManager.verify(None, "t", {}) is False

    def test_verify_false_after_consume(self) -> None:
        token = PermissionManager().create_token("t", {})
        token.consume("t", {})
        assert PermissionManager.verify(token, "t", {}) is False


class TestFingerprint:
    def test_canonicalize_order_insensitive(self) -> None:
        # ترتیب کلیدها فرق نتیجه نمی‌دهد
        a = canonicalize("t", {"x": 1, "y": 2})
        b = canonicalize("t", {"y": 2, "x": 1})
        assert a == b

    def test_fingerprint_differs_on_value(self) -> None:
        assert fingerprint("t", {"x": 1}) != fingerprint("t", {"x": 2})

    def test_fingerprint_differs_on_tool(self) -> None:
        assert fingerprint("a", {}) != fingerprint("b", {})

    def test_nested_args_change_matters(self) -> None:
        assert fingerprint("t", {"path": "a", "opts": {"k": 1}}) != fingerprint(
            "t", {"path": "a", "opts": {"k": 2}}
        )
