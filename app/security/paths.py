"""PathGuard — مزدوج مسیرهای مجاز ابزارها (بخش ۴ فاز ۳).

تمام ابزارهای filesystem/development/terminal فقط از این ماژول مسیر می‌گیرند:

- allowlist از Settings (قابل تنظیم؛ پیش‌فرض ~/Desktop، ~/Documents، ...)
- expanduser + resolve کامل → سپس بررسی پیشوند یکی از ریشه‌های مجاز
- ضد Path Traversal: «../» قبل از resolve بررسی می‌شود و پس از resolve
  مسیر خروجی دوباره با ریشه‌ها تطبیق داده می‌شود (دو-لایه)
- symlink فرار: resolve آن را به مسیر واقعی می‌برد؛ اگر خارج ریشه‌ها باشد رد می‌شود
- مسیر ریشه خودِ allowlist (مثل ~/Documents) مجاز است
- مسیر ناموجود: برای «خواندن» رد؛ برای «ایجاد» والد باید مجاز باشد (ensure_parent)

هیچ ابزاری حق ندارد مسیری را بدون عبور از PathGuard باز کند.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable, Optional

logger = logging.getLogger(__name__)


class PathPolicyError(Exception):
    """نقض سیاست مسیر؛ پیام فارسی برای مدل/کاربر."""

    def __init__(self, message: str, *, requested: str = "", root: Optional[Path] = None) -> None:
        super().__init__(message)
        self.requested = requested
        self.root = root


class PathGuard:
    """نگهبان مسیرهای مجاز؛ thread-safe (بدون حالت جهانی)."""

    def __init__(self, allowed_roots: Iterable[str]) -> None:
        roots: list[Path] = []
        for raw in allowed_roots:
            if not str(raw).strip():
                continue
            root = Path(str(raw)).expanduser()
            try:
                root = root.resolve(strict=False)
            except OSError:
                continue
            roots.append(root)
        if not roots:
            logger.warning("هیچ ریشه مجازی برای PathGuard ساخته نشد")
        self._roots = sorted(roots, key=lambda p: len(str(p)), reverse=True)
        logger.info("PathGuard ریشه‌های مجاز: %s", [str(r) for r in self._roots])

    @property
    def roots(self) -> list[Path]:
        return list(self._roots)

    def _matching_root(self, resolved: Path) -> Optional[Path]:
        for root in self._roots:
            try:
                if resolved == root or resolved.is_relative_to(root):
                    return root
            except (OSError, ValueError):
                continue
        return None

    # ------------------------------------------------------------------
    # بررسی مسیر
    # ------------------------------------------------------------------

    def resolve_checked(
        self,
        raw_path: str,
        *,
        must_exist: bool = True,
        allow_root_itself: bool = True,
    ) -> Path:
        """اعتبارسنجی کامل یک مسیر و بازگرداندن مسیر امن‌شده.

        must_exist=True برای عملیات خواندن؛ False برای ایجاد/نوشتن.
        raises PathPolicyError با پیام فارسی در صورت نقض.
        """
        text = (raw_path or "").strip()
        if not text:
            raise PathPolicyError("مسیر خالی است.", requested=raw_path)

        # لایه ۱: الگوی traversal خام قبل از resolve (Paths مثل a/../../etc)
        normalized = text.replace("\\", "/")
        if ".." in Path(normalized).parts:
            raise PathPolicyError(
                "مسیر شامل پیمایش به بالای دایرکتوری (..) است؛ مجاز نیست.",
                requested=raw_path,
            )

        candidate = Path(text).expanduser()
        try:
            resolved = candidate.resolve(strict=must_exist)
        except (OSError, RuntimeError) as exc:
            if must_exist:
                raise PathPolicyError(
                    f"مسیر وجود ندارد یا در دسترس نیست: {raw_path}",
                    requested=raw_path,
                ) from exc
            try:
                resolved = candidate.resolve(strict=False)
            except OSError as exc2:  # pragma: no cover - حالت خیلی نادر
                raise PathPolicyError(
                    f"مسیر قابل‌تفسیر نیست: {raw_path}", requested=raw_path
                ) from exc2

        # لایه ۲: پس از resolve (شامل رفع symlink) دوباره با ریشه‌ها تطبیق
        root = self._matching_root(resolved)
        if root is None:
            raise PathPolicyError(
                "مسیر خارج از محدوده مجاز است. ریشه‌های مجاز: "
                + "، ".join(str(r) for r in self._roots),
                requested=raw_path,
            )
        if not allow_root_itself and resolved == root:
            raise PathPolicyError(
                "عملیات روی خودِ ریشه مجاز تعریف نشده است.", requested=raw_path, root=root
            )
        if must_exist and not resolved.exists():
            raise PathPolicyError(f"مسیر وجود ندارد: {raw_path}", requested=raw_path)
        return resolved

    def ensure_parent(self, raw_path: str) -> Path:
        """برای ایجاد فایل: والد باید مجاز و (در صورت وجود) پوشه باشد."""
        text = (raw_path or "").strip()
        if not text:
            raise PathPolicyError("مسیر خالی است.", requested=raw_path)
        normalized = text.replace("\\", "/")
        if ".." in Path(normalized).parts:
            raise PathPolicyError(
                "مسیر شامل پیمایش به بالای دایرکتوری (..) است؛ مجاز نیست.",
                requested=raw_path,
            )
        candidate = Path(text).expanduser()
        parent = candidate.parent if str(candidate) else None
        if parent is None:  # pragma: no cover - گارد
            raise PathPolicyError("مسیر والد قابل‌تعیین نیست.", requested=raw_path)
        # والد باید در محدوده مجاز باشد (خودِ ریشه هم والد مشروع است)
        parent_resolved = self.resolve_checked(str(parent), must_exist=False)
        if parent_resolved.exists() and not parent_resolved.is_dir():
            raise PathPolicyError(
                f"والدِ مسیر پوشه نیست: {parent}", requested=raw_path
            )
        try:
            return candidate.resolve(strict=False)
        except OSError as exc:  # pragma: no cover
            raise PathPolicyError(
                f"مسیر قابل‌تفسیر نیست: {raw_path}", requested=raw_path
            ) from exc

    def is_allowed(self, raw_path: str, *, must_exist: bool = True) -> bool:
        """بررسی بدون raise (برای تست‌ها و تصمیم‌های داخلی)."""
        try:
            self.resolve_checked(raw_path, must_exist=must_exist)
            return True
        except PathPolicyError:
            return False

    def describe(self) -> dict[str, object]:
        return {"roots": [str(root) for root in self._roots]}


def build_path_guard(settings: object) -> PathGuard:
    """ساخت PathGuard از Settings؛ نقطه واحد در bootstrap."""
    return PathGuard(settings.resolved_allowed_paths())


__all__ = ["PathGuard", "PathPolicyError", "build_path_guard"]
