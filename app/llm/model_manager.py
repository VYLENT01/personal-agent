"""مدیریت مدل‌های محلی: انتخاب، اعتبارسنجی و پیشنهاد مدل.

هیچ مدلی در کد قطعی hardcode نمی‌شود؛ مدل فعال از تنظیمات خوانده می‌شود
و در صورت نبود، از میان مدل‌های واقعاً نصب‌شده پیشنهاد می‌گردد.
"""
from __future__ import annotations

import logging
from typing import Optional

from app.config import defaults as d
from app.config.settings import Settings
from app.llm.base import BaseLLMClient, LLMError, ModelInfo

logger = logging.getLogger(__name__)


class ModelManager:
    """مدیریت انتخاب مدل و کش فهرست مدل‌های نصب‌شده."""

    def __init__(self, provider: BaseLLMClient, settings: Settings) -> None:
        self._provider = provider
        self._settings = settings
        self._cache: Optional[list[ModelInfo]] = None

    # --- تزریق وابستگی (برای تغییر تنظیمات در زمان اجرا) ---

    def set_provider(self, provider: BaseLLMClient) -> None:
        self._provider = provider
        self._cache = None

    def set_settings(self, settings: Settings) -> None:
        self._settings = settings

    # --- فهرست و اعتبارسنجی ---

    def list_models(self, refresh: bool = False) -> list[ModelInfo]:
        """فهرست مدل‌های نصب‌شده (با کش). در قطع سرویس LLMConnectionError می‌دهد."""
        if self._cache is None or refresh:
            self._cache = self._provider.list_models()
            logger.debug("فهرست مدل‌ها به‌روز شد: %s", [m.name for m in self._cache])
        return list(self._cache)

    def refresh(self) -> list[ModelInfo]:
        return self.list_models(refresh=True)

    def installed_names(self, refresh: bool = False) -> list[str]:
        return [info.name for info in self.list_models(refresh=refresh)]

    def is_installed(self, name: str, refresh: bool = False) -> bool:
        """تطبیق نام دقیق یا نام پایه (قبل از ':') برای اطمینان از انتخاب مدل."""
        if not name:
            return False
        base = name.split(":", 1)[0].lower()
        for info in self.list_models(refresh=refresh):
            if info.name.lower() == name.lower() or info.name.split(":", 1)[0].lower() == base:
                return True
        return False

    # --- مدل فعال ---

    def active_model(self) -> str:
        """مدل فعال از تنظیمات؛ هیچ فراخوانی شبکه‌ای ندارد (همیشه سریع و امن)."""
        return self._settings.active_model

    def set_active_model(self, name: str) -> None:
        """انتخاب مدل فعال؛ نام نامعتبر رد می‌شود و فهرست نصب‌شده‌ها ارائه می‌گردد."""
        clean = (name or "").strip()
        if not clean:
            raise ValueError("نام مدل نمی‌تواند خالی باشد.")
        if self._cache is not None and clean not in self.installed_names():
            installed = "، ".join(self.installed_names()) or "هیچ مدلی نصب نیست"
            raise ValueError(
                f"مدل «{clean}» نصب نشده است. مدل‌های موجود: {installed}"
            )
        self._settings = self._settings.copy_with(active_model=clean)
        self._settings.save()
        logger.info("مدل فعال به «%s» تغییر کرد", clean)

    def suggest_default(self) -> Optional[str]:
        """بهترین مدل پیشنهادی: مدل فعال اگر نصب باشد، سپس فهرست پیشنهادی سخت‌افزار."""
        try:
            names = self.installed_names(refresh=True)
        except LLMError:
            return None
        if not names:
            return None
        if self.is_installed(self._settings.active_model):
            return self._settings.active_model
        for candidate in d.FALLBACK_MODELS:
            for name in names:
                if name.split(":", 1)[0].lower() == candidate.split(":", 1)[0].lower():
                    return name
        return names[0]

    def ensure_ready(self) -> str:
        """بررسی نهایی مدل فعال؛ اگر نصب نبود، مدل موجود را پیشنهاد/جایگزین می‌کند."""
        names = self.installed_names(refresh=True)
        if not names:
            raise LLMError(
                "هیچ مدلی روی Ollama نصب نیست. با دستور «ollama pull qwen3:8b» یک مدل نصب کنید."
            )
        if not self.is_installed(self._settings.active_model):
            suggestion = self.suggest_default() or names[0]
            logger.warning(
                "مدل فعال «%s» نصب نیست؛ به «%s» تغییر کرد.",
                self._settings.active_model,
                suggestion,
            )
            self.set_active_model(suggestion)
        return self._settings.active_model
