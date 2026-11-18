"""مقادیر پیش‌فرض پیکربندی برنامه.

همه ثابت‌های قابل‌تنظیم در یک نقطه مرکزی تعریف می‌شوند تا
جست‌وجو و تغییر آن‌ها ساده بماند. هیچ مقداری در سایر ماژول‌ها hardcode نمی‌شود.
"""
from __future__ import annotations

APP_NAME = "Personal Agent"
APP_NAME_FA = "دستیار هوشمند شخصی"
APP_VERSION = "0.2.0"
ORG_NAME = "PersonalAgent"

# --- Ollama / LLM ---
DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen3:8b"
# مدل‌های جایگزین پیشنهادی برای سخت‌افزار هدف (RTX 4060 / 8GB VRAM)
FALLBACK_MODELS = ["qwen3:8b", "gemma3:4b", "qwen2.5:7b-instruct"]
DEFAULT_CONNECT_TIMEOUT = 5.0    # ثانیه
DEFAULT_READ_TIMEOUT = 180.0      # ثانیه؛ حداکثر انتظار بین قطعات پاسخ مدل
DEFAULT_TEMPERATURE = 0.7
DEFAULT_TOP_P = 0.9
DEFAULT_NUM_CTX = 8192            # متناسب با 8GB VRAM برای مدل‌های 8B
DEFAULT_NUM_PREDICT = 2048        # سقف توکن تولید پاسخ

# --- مدیریت Context مکالمه ---
DEFAULT_MAX_HISTORY_MESSAGES = 20
DEFAULT_MAX_CONTEXT_CHARS = 12000

# --- UI / زبان ---
DEFAULT_LANGUAGE = "fa"           # "fa" | "en"
DEFAULT_THEME = "dark"

# --- Logging ---
DEFAULT_LOG_LEVEL = "INFO"
LOG_FILE_NAME = "agent.log"
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 3

# --- مسیرها (نسبی به ریشه پروژه؛ در bootstrap ساخته می‌شوند) ---
DATA_DIR_NAME = "data"
LOG_DIR_NAME = "logs"
CONFIG_SUBDIR = "config"
SETTINGS_FILE_NAME = "settings.json"
ENV_FILE_NAME = ".env"

# --- Agent ---
# سقف تکرار حلقه agent در هر درخواست؛ جلوگیری از حلقه بی‌نهایت.
MAX_AGENT_ITERATIONS = 8

# --- Tool Calling (فاز ۲) ---
# سقف تعداد فراخوانی ابزار در یک نوبت (چند Tool Call مجاز، حلقه بی‌نهایت ممنوع)
MAX_TOOL_CALLS_PER_TURN = 5
# حداکثر طول متن نتیجه ابزار که به مدل فرستاده می‌شود (کاراکتر) — محافظ Context
TOOL_RESULT_MAX_CHARS = 4000
# زمان اجرای هر ابزار (ثانیه)؛ فراتر از آن ToolTimeoutError
TOOL_TIMEOUT_SECONDS = 10.0
# تأخیر پیش از ثبت رویداد شروع ابزار در UI (ثانیه) — فقط برای شفافیت لاگ
TOOL_LOG_DELAY = 0.0
# سقف تعداد ابزار در پرامپت مدل؛ بیش از این، ابزارها برش می‌خورند (محافظت از num_ctx)
MAX_TOOLS_IN_PROMPT = 24
# حداقل اندازه تفکیک برای اعتبار پاسخ JSON در مسیر fallback
FALLBACK_MIN_JSON_LEN = 6

# --- Permission levels (فاز ۲؛ اجرای L2/L3 در فاز ۵ کامل می‌شود) ---
PERMISSION_L1 = 1  # فقط-خواندنی: اجرای خودکار
PERMISSION_L2 = 2  # نوشتن/تغییر: در این فاز ابزاری ثبت نمی‌شود
PERMISSION_L3 = 3  # حساس/حذف/ترمینال: در این فاز ابزاری ثبت نمی‌شود

# --- مقادیر مجاز ---
VALID_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
VALID_LANGUAGES = ("fa", "en")

# --- متغیرهای محیطی (.env) ---
ENV_PREFIX = "PERSONAL_AGENT_"
ENV_KEY_OLLAMA_URL = ENV_PREFIX + "OLLAMA_URL"
ENV_KEY_MODEL = ENV_PREFIX + "MODEL"
ENV_KEY_LANGUAGE = ENV_PREFIX + "LANGUAGE"
ENV_KEY_LOG_LEVEL = ENV_PREFIX + "LOG_LEVEL"

# --- متغیرهای محیطی تست Integration واقعی ---
# اگر ست شده باشد، تست‌های integration واقعی Ollama اجرا می‌شوند:
# PERSONAL_AGENT_INTEGRATION_MODEL=qwen3:8b pytest tests/test_integration_ollama.py
ENV_KEY_INTEGRATION_MODEL = ENV_PREFIX + "INTEGRATION_MODEL"
