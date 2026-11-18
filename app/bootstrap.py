"""راه‌اندازی برنامه: بارگذاری .env، تنظیمات، لاگ و سیم‌کشی اجزا.

build_app_context تنها نقطه ساخت اجزاست؛ در تست‌ها امکان تزریق
provider و settings ساختگی وجود دارد تا بدون Ollama واقعی قابل آزمایش باشد.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from app.agent.orchestrator import ChatOrchestrator
from app.config import defaults as d
from app.config.settings import Settings
from app.llm.base import BaseLLMClient
from app.llm.model_manager import ModelManager
from app.llm.ollama_client import OllamaClient

logger = logging.getLogger(__name__)

# ریشه پروژه: personal-agent (دو سطح بالاتر از این فایل)
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_env_file(path: Path) -> dict[str, str]:
    """بارگذاری ساده فایل .env (KEY=VALUE) بدون وابستگی خارجی.

    مقادیر موجود در محیط اصلی بازنویسی نمی‌شوند (اولویت محیط بالاتر است).
    """
    applied: dict[str, str] = {}
    if not path.exists():
        return applied
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key:
            continue
        if key not in os.environ:
            os.environ[key] = value
            applied[key] = value
    return applied


def configure_logging(level: str, log_dir: Path) -> None:
    """پیکربندی لاگ: فایل چرخشی + کنسول؛ idempotent (بدون هندلر تکراری)."""
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(level)

    for handler in list(root.handlers):
        if getattr(handler, "_personal_agent", False):
            root.removeHandler(handler)
            handler.close()

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler = RotatingFileHandler(
        log_dir / d.LOG_FILE_NAME,
        maxBytes=d.LOG_MAX_BYTES,
        backupCount=d.LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    stream_handler = logging.StreamHandler()
    for handler in (file_handler, stream_handler):
        handler.setFormatter(formatter)
        handler._personal_agent = True  # noqa: SLF001 - نشانگر هندلر خودمان
        root.addHandler(handler)


@dataclass
class AppContext:
    """ظرف اجزای برنامه؛ UI فقط با این آبجکت کار می‌کند."""

    root: Path
    data_dir: Path
    log_dir: Path
    settings_path: Path
    settings: Settings
    provider: BaseLLMClient
    model_manager: ModelManager
    orchestrator: ChatOrchestrator

    def apply_settings(self, new_settings: Settings) -> None:
        """اعمال تنظیمات جدید در زمان اجرا + ذخیره + بازسازی اجزای وابسته."""
        old = self.settings
        url_changed = (
            new_settings.ollama_base_url != old.ollama_base_url
            or new_settings.connect_timeout != old.connect_timeout
            or new_settings.read_timeout != old.read_timeout
        )
        if url_changed:
            old_provider = self.provider
            provider = OllamaClient(
                new_settings.ollama_base_url,
                new_settings.connect_timeout,
                new_settings.read_timeout,
            )
            self.provider = provider
            self.orchestrator.set_provider(provider)
            self.model_manager.set_provider(provider)
            try:
                old_provider.close()
            except Exception:  # noqa: BLE001
                logger.debug("بستن provider قبلی با خطا مواجه شد", exc_info=True)
        self.settings = new_settings
        self.model_manager.set_settings(new_settings)
        self.orchestrator.set_settings(new_settings)
        self.model_manager.refresh()
        new_settings.save(self.settings_path)
        logger.info("تنظیمات جدید اعمال شد (مدل: %s)", new_settings.active_model)

    def shutdown(self) -> None:
        """آزادسازی منابع در خروج از برنامه."""
        try:
            self.provider.close()
        except Exception:  # noqa: BLE001
            logger.debug("بستن provider در shutdown با خطا مواجه شد", exc_info=True)
        logger.info("برنامه بسته شد")


def build_app_context(
    root: Optional[Path] = None,
    *,
    settings: Optional[Settings] = None,
    provider: Optional[BaseLLMClient] = None,
    configure_log: bool = True,
) -> AppContext:
    """ساخت و سیم‌کشی همه اجزای برنامه."""
    root = (root or PROJECT_ROOT).resolve()
    data_dir = root / d.DATA_DIR_NAME
    log_dir = root / d.LOG_DIR_NAME

    # ساختار پوشه‌های زمان-اجرا (خودکار در هر بوت)
    for subdir in (d.CONFIG_SUBDIR, "database", "memory", "conversations", "cache"):
        (data_dir / subdir).mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

    load_env_file(root / d.ENV_FILE_NAME)

    settings_path = data_dir / d.CONFIG_SUBDIR / d.SETTINGS_FILE_NAME
    if settings is None:
        settings = Settings.load(settings_path)
    else:
        settings._path = settings_path  # noqa: SLF001 - تزریق مسیر ذخیره در تست

    if configure_log:
        configure_logging(settings.log_level, log_dir)

    provider = provider or OllamaClient(
        settings.ollama_base_url,
        settings.connect_timeout,
        settings.read_timeout,
    )
    model_manager = ModelManager(provider, settings)
    orchestrator = ChatOrchestrator(provider, model_manager, settings)

    context = AppContext(
        root=root,
        data_dir=data_dir,
        log_dir=log_dir,
        settings_path=settings_path,
        settings=settings,
        provider=provider,
        model_manager=model_manager,
        orchestrator=orchestrator,
    )
    logger.info(
        "برنامه راه‌اندازی شد (نسخه %s) — Ollama: %s | مدل فعال: %s",
        d.APP_VERSION,
        settings.ollama_base_url,
        settings.active_model,
    )
    return context
