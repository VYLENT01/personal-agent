"""تست‌های پیکربندی: Settings، .env و bootstrap."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from app.config import defaults as d
from app.config.settings import GenerationSettings, Settings
from app.bootstrap import load_env_file


class TestSettingsDefaults:
    def test_default_values(self) -> None:
        settings = Settings.create_default()
        assert settings.ollama_base_url == d.DEFAULT_OLLAMA_BASE_URL
        assert settings.active_model == d.DEFAULT_MODEL
        assert settings.language == "fa"
        assert settings.generation.num_ctx == d.DEFAULT_NUM_CTX
        assert settings.log_level == "INFO"

    def test_from_dict_ignores_unknown_keys(self) -> None:
        data = Settings.create_default().to_dict()
        data["unknown_future_key"] = "value"  # سازگاری رو به جلو
        data["active_model"] = "gemma3:4b"
        settings = Settings.from_dict(data)
        assert settings.active_model == "gemma3:4b"

    def test_load_missing_file_returns_defaults(self, tmp_path: Path) -> None:
        settings = Settings.load(tmp_path / "settings.json")
        assert settings.active_model == d.DEFAULT_MODEL
        assert settings.path == tmp_path / "settings.json"

    def test_load_corrupt_file_returns_defaults(self, tmp_path: Path) -> None:
        target = tmp_path / "settings.json"
        target.write_text("{این JSON نیست", encoding="utf-8")
        settings = Settings.load(target)
        assert settings.active_model == d.DEFAULT_MODEL


class TestSettingsSanitize:
    def test_bad_url_falls_back(self) -> None:
        settings = Settings.create_default()
        settings.ollama_base_url = "ftp://نه"
        settings.sanitize()
        assert settings.ollama_base_url == d.DEFAULT_OLLAMA_BASE_URL

    def test_timeout_clamped(self) -> None:
        settings = Settings.create_default()
        settings.connect_timeout = -5
        settings.read_timeout = 99_999
        settings.sanitize()
        assert settings.connect_timeout == d.DEFAULT_CONNECT_TIMEOUT
        # بالای سقف → به سقف (3600) بریده می‌شود؛ زیر کف (مثل 3) → به پیش‌فرض
        settings2 = Settings.create_default()
        settings2.read_timeout = 3.0
        settings2.sanitize()
        assert settings2.read_timeout == d.DEFAULT_READ_TIMEOUT

    def test_invalid_language_and_level(self) -> None:
        settings = Settings.create_default()
        settings.language = "de"
        settings.log_level = "چیز"
        settings.sanitize()
        assert settings.language == d.DEFAULT_LANGUAGE
        assert settings.log_level == d.DEFAULT_LOG_LEVEL

    def test_generation_clamped(self) -> None:
        gen = GenerationSettings(temperature=9.0, top_p=-1, num_ctx=10, num_predict=1)
        gen.sanitize()
        assert gen.temperature == d.DEFAULT_TEMPERATURE
        assert gen.top_p == d.DEFAULT_TOP_P
        assert gen.num_ctx == d.DEFAULT_NUM_CTX
        assert gen.num_predict == d.DEFAULT_NUM_PREDICT


class TestSettingsEnvOverride:
    def test_env_beats_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(d.ENV_KEY_OLLAMA_URL, "http://192.168.1.5:11434")
        monkeypatch.setenv(d.ENV_KEY_MODEL, "gemma3:4b")
        target = tmp_path / "settings.json"
        Settings(active_model="qwen3:8b").save(target)
        settings = Settings.load(target)
        assert settings.ollama_base_url == "http://192.168.1.5:11434"
        assert settings.active_model == "gemma3:4b"

    def test_env_invalid_url_sanitized_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(d.ENV_KEY_OLLAMA_URL, "not-a-url")
        settings = Settings.from_dict({})
        assert settings.ollama_base_url == d.DEFAULT_OLLAMA_BASE_URL


class TestSettingsPersistence:
    def test_save_and_reload_roundtrip(self, tmp_path: Path) -> None:
        target = tmp_path / "settings.json"
        settings = Settings(
            active_model="qwen2.5:7b-instruct",
            language="en",
            read_timeout=240.0,
            max_history_messages=8,
        )
        settings.save(target)
        loaded = Settings.load(target)
        assert loaded.active_model == "qwen2.5:7b-instruct"
        assert loaded.language == "en"
        assert loaded.read_timeout == 240.0
        assert loaded.max_history_messages == 8

    def test_save_is_atomic_no_tmp_left(self, tmp_path: Path) -> None:
        target = tmp_path / "settings.json"
        Settings.create_default().save(target)
        leftovers = list(tmp_path.glob("*.tmp"))
        assert leftovers == []

    def test_private_path_not_serialized(self) -> None:
        settings = Settings.create_default()
        assert "_path" not in settings.to_dict()


class TestEnvLoader:
    def test_load_env_file_sets_and_respects_existing(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text(
            "# نظر\nPERSONAL_AGENT_TEST_A=1\n"
            'PERSONAL_AGENT_TEST_B="quoted"\n'
            "PERSONAL_AGENT_TEST_C='single'\n"
            "BROKEN LINE WITHOUT EQUALS\n",
            encoding="utf-8",
        )
        os.environ.pop("PERSONAL_AGENT_TEST_A", None)
        os.environ["PERSONAL_AGENT_TEST_B"] = "already"
        try:
            applied = load_env_file(env_file)
            assert os.environ["PERSONAL_AGENT_TEST_A"] == "1"
            assert os.environ["PERSONAL_AGENT_TEST_B"] == "already"  # محیط اولویت دارد
            assert os.environ["PERSONAL_AGENT_TEST_C"] == "single"
            assert set(applied) == {"PERSONAL_AGENT_TEST_A", "PERSONAL_AGENT_TEST_C"}
        finally:
            os.environ.pop("PERSONAL_AGENT_TEST_A", None)
            os.environ.pop("PERSONAL_AGENT_TEST_B", None)
            os.environ.pop("PERSONAL_AGENT_TEST_C", None)

    def test_load_env_missing_file_is_noop(self, tmp_path: Path) -> None:
        assert load_env_file(tmp_path / "nope.env") == {}
