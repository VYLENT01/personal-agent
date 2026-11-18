"""مدل تنظیمات برنامه و ذخیره/بازیابی آن.

اولویت مقدارها: متغیر محیطی > فایل data/config/settings.json > مقادیر پیش‌فرض.
تنظیمات به‌صورت dataclass تغییرناپذیر-به-روایت (نمونه جدید هنگام ذخیره ساخته می‌شود)
استفاده می‌شود تا اجزای برنامه همیشه از یک نمونه هماهنگ بخوانند.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional

from app.config import defaults as d

logger = logging.getLogger(__name__)


@dataclass
class GenerationSettings:
    """پارامترهای تولید متن مدل."""

    temperature: float = d.DEFAULT_TEMPERATURE
    top_p: float = d.DEFAULT_TOP_P
    num_ctx: int = d.DEFAULT_NUM_CTX
    num_predict: int = d.DEFAULT_NUM_PREDICT

    def sanitize(self) -> None:
        self.temperature = _clamp(self.temperature, 0.0, 2.0, d.DEFAULT_TEMPERATURE)
        self.top_p = _clamp(self.top_p, 0.0, 1.0, d.DEFAULT_TOP_P)
        self.num_ctx = int(_clamp(self.num_ctx, 512, 65536, d.DEFAULT_NUM_CTX))
        self.num_predict = int(
            _clamp(self.num_predict, 64, 32768, d.DEFAULT_NUM_PREDICT)
        )

    def to_options(self) -> dict[str, Any]:
        """تبدیل به فریم options مربوط به API مدل."""
        return {
            "temperature": self.temperature,
            "top_p": self.top_p,
            "num_ctx": self.num_ctx,
            "num_predict": self.num_predict,
        }


@dataclass
class Settings:
    """تنظیمات کل برنامه."""

    ollama_base_url: str = d.DEFAULT_OLLAMA_BASE_URL
    active_model: str = d.DEFAULT_MODEL
    language: str = d.DEFAULT_LANGUAGE
    theme: str = d.DEFAULT_THEME
    generation: GenerationSettings = field(default_factory=GenerationSettings)
    connect_timeout: float = d.DEFAULT_CONNECT_TIMEOUT
    read_timeout: float = d.DEFAULT_READ_TIMEOUT
    max_history_messages: int = d.DEFAULT_MAX_HISTORY_MESSAGES
    max_context_chars: int = d.DEFAULT_MAX_CONTEXT_CHARS
    log_level: str = d.DEFAULT_LOG_LEVEL
    # فاز ۳ — مسیرهای مجاز ابزارها (String؛ None یعنی پیش‌فرض‌های defaults.py)
    allowed_paths: Optional[list[str]] = None
    _path: Optional[Path] = field(default=None, repr=False, compare=False)

    # ---------- ساخت/بارگذاری ----------

    @classmethod
    def create_default(cls) -> "Settings":
        return cls()

    @classmethod
    def load(cls, path: Path) -> "Settings":
        """بارگذاری از فایل JSON؛ در صورت خرابی یا نبود، مقادیر پیش‌فرض."""
        settings = cls.from_dict({})
        settings._path = path
        if not path.exists():
            logger.info("فایل تنظیمات یافت نشد؛ از مقادیر پیش‌فرض استفاده شد: %s", path)
            return settings
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("ساختار فایل تنظیمات معتبر نیست")
            settings = cls.from_dict(data)
        except (json.JSONDecodeError, ValueError, OSError) as exc:
            logger.warning("فایل تنظیمات قابل خواندن نبود (%s)؛ به پیش‌فرض برمی‌گردیم: %s", exc, path)
            settings = cls.from_dict({})
        settings._path = path
        return settings

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Settings":
        """ساخت نمونه از دیکشنری؛ کلیدهای ناشناخته نادیده گرفته می‌شوند."""
        gen_data = data.get("generation") or {}
        settings = cls(
            ollama_base_url=str(data.get("ollama_base_url", d.DEFAULT_OLLAMA_BASE_URL)),
            active_model=str(data.get("active_model", d.DEFAULT_MODEL)),
            language=str(data.get("language", d.DEFAULT_LANGUAGE)),
            theme=str(data.get("theme", d.DEFAULT_THEME)),
            generation=GenerationSettings(
                temperature=_to_float(gen_data.get("temperature"), d.DEFAULT_TEMPERATURE),
                top_p=_to_float(gen_data.get("top_p"), d.DEFAULT_TOP_P),
                num_ctx=_to_int(gen_data.get("num_ctx"), d.DEFAULT_NUM_CTX),
                num_predict=_to_int(gen_data.get("num_predict"), d.DEFAULT_NUM_PREDICT),
            ),
            connect_timeout=_to_float(data.get("connect_timeout"), d.DEFAULT_CONNECT_TIMEOUT),
            read_timeout=_to_float(data.get("read_timeout"), d.DEFAULT_READ_TIMEOUT),
            max_history_messages=_to_int(
                data.get("max_history_messages"), d.DEFAULT_MAX_HISTORY_MESSAGES
            ),
            max_context_chars=_to_int(data.get("max_context_chars"), d.DEFAULT_MAX_CONTEXT_CHARS),
            log_level=str(data.get("log_level", d.DEFAULT_LOG_LEVEL)).upper(),
            allowed_paths=_clean_allowed_paths(data.get("allowed_paths")),
        )
        settings.apply_env_overrides()
        settings.sanitize()
        return settings

    def apply_env_overrides(self) -> None:
        """اعمال متغیرهای محیطی (بالاترین اولویت)."""
        if (url := os.environ.get(d.ENV_KEY_OLLAMA_URL)) :
            self.ollama_base_url = url.strip()
        if (model := os.environ.get(d.ENV_KEY_MODEL)) :
            self.active_model = model.strip()
        if (lang := os.environ.get(d.ENV_KEY_LANGUAGE)) :
            self.language = lang.strip().lower()
        if (level := os.environ.get(d.ENV_KEY_LOG_LEVEL)) :
            self.log_level = level.strip().upper()
        if (raw_paths := os.environ.get(d.ENV_KEY_ALLOWED_PATHS)) :
            parts = [part.strip() for part in raw_paths.split("|") if part.strip()]
            if parts:
                self.allowed_paths = parts

    def resolved_allowed_paths(self) -> list[str]:
        """فهرست نهایی مسیرهای مجاز (پیش‌فرض اگر تنظیم نشده باشد)."""
        if self.allowed_paths:
            return [str(path) for path in self.allowed_paths]
        return list(d.DEFAULT_ALLOWED_PATHS)

    # ---------- اعتبارسنجی ----------

    def sanitize(self) -> None:
        """اصلاح مقادیر نامعتبر به بازه‌های امن."""
        url = self.ollama_base_url.strip().rstrip("/")
        if not (url.startswith("http://") or url.startswith("https://")):
            logger.warning("آدرس Ollama نامعتبر بود؛ به پیش‌فرض برگردانده شد: %r", self.ollama_base_url)
            url = d.DEFAULT_OLLAMA_BASE_URL
        self.ollama_base_url = url

        if not self.active_model.strip():
            self.active_model = d.DEFAULT_MODEL

        if self.language not in d.VALID_LANGUAGES:
            self.language = d.DEFAULT_LANGUAGE
        if self.theme not in ("dark",):
            self.theme = d.DEFAULT_THEME
        if self.log_level not in d.VALID_LOG_LEVELS:
            self.log_level = d.DEFAULT_LOG_LEVEL

        self.connect_timeout = _clamp(self.connect_timeout, 1.0, 30.0, d.DEFAULT_CONNECT_TIMEOUT)
        self.read_timeout = _clamp(self.read_timeout, 10.0, 3600.0, d.DEFAULT_READ_TIMEOUT)
        self.max_history_messages = int(
            _clamp(self.max_history_messages, 2, 200, d.DEFAULT_MAX_HISTORY_MESSAGES)
        )
        self.max_context_chars = int(
            _clamp(self.max_context_chars, 1000, 200000, d.DEFAULT_MAX_CONTEXT_CHARS)
        )
        self.generation.sanitize()

    # ---------- ذخیره ----------

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("_path", None)
        return data

    def save(self, path: Optional[Path] = None) -> Path:
        """ذخیره اتمیک (نوشتن فایل موقت سپس جایگزینی) برای جلوگیری از فایل نیمه‌کاره."""
        target = Path(path or self._path or Path(d.SETTINGS_FILE_NAME))
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(tmp, target)
        self._path = target
        logger.debug("تنظیمات در %s ذخیره شد", target)
        return target

    def copy_with(self, **changes: Any) -> "Settings":
        """نمونه جدید با تغییرات داده‌شده (بدون جهش نمونه فعلی).

        مسیر ذخیره (_path) به ارث می‌رسد تا ذخیره‌های بعدی همان فایل را به‌روز کنند.
        """
        data = self.to_dict()
        data.update(changes)
        new_settings = Settings.from_dict(data)
        new_settings._path = self._path  # noqa: SLF001 - انتقال مسیر ذخیره
        return new_settings

    @property
    def path(self) -> Optional[Path]:
        return self._path


def _clean_allowed_paths(value: Any) -> Optional[list[str]]:
    """نرمال‌سازی مسیرهای مجاز: لیست یا رشته «|»-جدا → لیست تمیز یا None."""
    if isinstance(value, str):
        value = value.split("|")
    if not isinstance(value, list):
        return None
    cleaned = [str(item).strip() for item in value if str(item).strip()]
    return cleaned or None


# ---------- ابزارهای کمکی ----------

def _clamp(value: float, low: float, high: float, default: float) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    if value < low or value > high:
        return default
    return value


def _to_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
