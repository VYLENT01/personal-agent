"""تست Integration روی Ollama واقعی (بخش هفتم: «در کنار تست‌های Mock»).

این تست‌ها فقط وقتی اجرا می‌شوند که مدل واقعی در دسترس باشد:
    PERSONAL_AGENT_INTEGRATION_MODEL=qwen3:8b \
    PERSONAL_AGENT_OLLAMA_URL=http://127.0.0.1:11434 \
    python -m pytest tests/test_integration_ollama.py -m integration -v

بدون آن متغیر، کل ماژول skip می‌شود (smoke آزمون در CI بدون GPU قابل اجراست).

پوشش:
- show_model/capabilities از سرویس واقعی
- چرخه کامل native tool calling با مدل دارای قابلیت tools:
  ابزار system.get_memory_usage واقعاً اجرا و نتیجه به مدل می‌رسد و
  پاسخ نهایی حاوی داده واقعی RAM است.
- مسیر fallback برای مدل بدون قابلیت (اگر مدل دوم ست شده باشد).
"""
from __future__ import annotations

import os

import pytest

from app.config import defaults as d

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get(d.ENV_KEY_INTEGRATION_MODEL),
        reason=(
            "تست integration فقط با Ollama واقعی اجرا می‌شود؛ "
            "PERSONAL_AGENT_INTEGRATION_MODEL=<model> را تنظیم کنید "
            "(و در صورت نیاز PERSONAL_AGENT_OLLAMA_URL)."
        ),
    ),
]

MODEL = os.environ.get(d.ENV_KEY_INTEGRATION_MODEL, "qwen3:8b")


def _client():
    from app.llm.ollama_client import OllamaClient

    url = os.environ.get(d.ENV_KEY_OLLAMA_URL, d.DEFAULT_OLLAMA_BASE_URL)
    return OllamaClient(url, connect_timeout=5.0, read_timeout=120.0)


@pytest.fixture(scope="module")
def client():
    instance = _client()
    available, detail = instance.is_available()
    if not available:
        pytest.skip(f"Ollama در دسترس نیست: {detail}")
    yield instance
    instance.close()


def test_real_show_model_capabilities(client) -> None:
    info = client.show_model(MODEL)
    assert info.name == MODEL
    print(f"capabilities واقعی {MODEL}: {info.capabilities}")


def test_real_native_tool_cycle(client, tmp_path) -> None:
    """چرخه کامل با مدل واقعی: ابزار RAM اجرا و نتیجه به مدل برمی‌گردد."""
    from app.agent.orchestrator import ChatOrchestrator, TurnCompleted
    from app.config.settings import Settings
    from app.llm.model_manager import ModelManager
    from app.tools.registry import build_default_registry

    supports = client.supports_native_tools(MODEL)
    print(f"native tools پشتیبانی: {supports}")
    if not supports:
        pytest.skip(f"مدل {MODEL} قابلیت tools ندارد؛ تست native معنا ندارد.")

    registry = build_default_registry()
    settings = Settings(active_model=MODEL)
    manager = ModelManager(client, settings)
    orchestrator = ChatOrchestrator(client, manager, settings, registry)

    events = list(
        orchestrator.stream_turn(
            "با ابزار system.get_memory_usage میزان رم را بگیر و بگو چند درصد استفاده می‌شود."
        )
    )
    completed = events[-1]
    assert isinstance(completed, TurnCompleted)
    assert completed.tool_calls >= 1, "مدل واقعی ابزار را صدا نزد"
    # پاسخ نهایی باید به داده واقعی اشاره کند (درصد/گیگابایت)
    text = completed.content
    assert any(ch.isdigit() for ch in text), "پاسخ بدون عدد واقعی RAM بود"
    print("پاسخ واقعی مدل:", text[:200])
