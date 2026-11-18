"""مقادیر پیش‌فرض پیکربندی برنامه.

همه ثابت‌های قابل‌تنظیم در یک نقطه مرکزی تعریف می‌شوند تا
جست‌وجو و تغییر آن‌ها ساده بماند. هیچ مقداری در سایر ماژول‌ها hardcode نمی‌شود.
"""
from __future__ import annotations

APP_NAME = "Personal Agent"
APP_NAME_FA = "دستیار هوشمند شخصی"
APP_VERSION = "0.3.0"
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

# --- Permission levels (فاز ۲؛ فاز ۳: L2 با تأیید کاربر اجرا می‌شود) ---
PERMISSION_L1 = 1  # فقط-خواندنی: اجرای خودکار
PERMISSION_L2 = 2  # نوشتن/اجرا/فرمان: فقط با ApprovalToken (بخش ۸ فاز ۳)
PERMISSION_L3 = 3  # حذف/Registry/مدیریتی: غیرفعال در این فاز

# --- Windows Integration (فاز ۳) ---
# مسیرهای مجاز پیش‌فرض (فقط-خواندنی برای ابزارها) — در Settings قابل تنظیم.
DEFAULT_ALLOWED_PATHS = [
    "~/Desktop",
    "~/Documents",
    "~/Downloads",
    "~/projects",
]
# سقف تأیید کاربر برای عملیات L2 (ثانیه) — پس از آن درخواست رد می‌شود
APPROVAL_TIMEOUT_SECONDS = 120.0
# سقف عمق درخت پوشه برای filesystem.get_directory_tree
MAX_TREE_DEPTH = 3
# سقف تعداد فایل/پوشه در هر فهرست/جست‌وجو
MAX_LIST_ENTRIES = 200
# سقف حجم خواندن فایل (بایت) — فایل بزرگ‌تر رد یا برش می‌خورد
MAX_READ_BYTES = 512 * 1024
# سقف طول متن بازگردانده‌شده از read/search (کاراکتر)
MAX_TEXT_RESULT_CHARS = 8000
# سقف تعداد نتایج جست‌وجوی متن
MAX_SEARCH_RESULTS = 50
# زمان اجرای فرمان ترمینال (ثانیه)
TERMINAL_TIMEOUT_SECONDS = 30.0
# سقف حجم stdout/stderr هر فرمان (کاراکتر)
TERMINAL_OUTPUT_MAX_CHARS = 8000
# زمان اجرای تست پروژه (ثانیه)
DEV_TEST_TIMEOUT_SECONDS = 120.0
# سقف تعداد برنامه‌های فهرست‌شده
MAX_APPS_LISTED = 200
# نام فرمان‌های اجرایی مجاز terminal — سیاست با ساختار (بخش ۶ مشخصات)
# هر ورودی: (برنامه، آرگومان‌های مجاز به‌عنوان الگو؛ "*"" یعنی هر آرگومان امن)
# دستورات زنجیره‌ای/اسکریپت/مدیریتی در CommandPolicy رد می‌شوند.
TERMINAL_ALLOWED_COMMANDS = [
    {"program": "Get-Location", "shell": "powershell", "args": []},
    {"program": "Get-Date", "shell": "powershell", "args": []},
    {"program": "Get-ChildItem", "shell": "powershell", "args": ["*"]},
    {"program": "Get-ComputerInfo", "shell": "powershell", "args": ["*"]},
    {"program": "python", "shell": "any", "args": ["--version"]},
    {"program": "pip", "shell": "any", "args": ["--version"]},
    {"program": "git", "shell": "any", "args": ["--version", "status"]},
]
# الگوی کاراکترهای ممنوع در ورودی ترمینال (ضد Command Injection / زنجیره)
TERMINAL_FORBIDDEN_CHARS = (";", "|", "&", "`", "\n", "\r", ">", "<", "$(")
# پسوندهای مسیر برنامه قابل اجرا (applications) — فقط این‌ها اجرا می‌شوند
APP_EXECUTABLE_EXTENSIONS = (".exe",)
# نام‌های فرآیند سیستمی ممنوع برای applications.close/launch (گارد مضاعف)
APP_FORBIDDEN_TARGETS = (
    "winlogon", "csrss", "services", "lsass", "smss", "wininit",
    "explorer", "svchost", "system",
)

# --- مقادیر مجاز ---
VALID_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
VALID_LANGUAGES = ("fa", "en")

# --- متغیرهای محیطی (.env) ---
ENV_PREFIX = "PERSONAL_AGENT_"
ENV_KEY_OLLAMA_URL = ENV_PREFIX + "OLLAMA_URL"
ENV_KEY_MODEL = ENV_PREFIX + "MODEL"
ENV_KEY_LANGUAGE = ENV_PREFIX + "LANGUAGE"
ENV_KEY_LOG_LEVEL = ENV_PREFIX + "LOG_LEVEL"
ENV_KEY_ALLOWED_PATHS = ENV_PREFIX + "ALLOWED_PATHS"  # جداکننده «|» مثال: ~/Documents|~/projects

# --- متغیرهای محیطی تست Integration واقعی ---
# اگر ست شده باشد، تست‌های integration واقعی Ollama اجرا می‌شوند:
# PERSONAL_AGENT_INTEGRATION_MODEL=qwen3:8b pytest tests/test_integration_ollama.py
ENV_KEY_INTEGRATION_MODEL = ENV_PREFIX + "INTEGRATION_MODEL"
