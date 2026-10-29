"""配置加载、启动校验与密钥脱敏的单元测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from wechat_agent.config import ConfigError, Settings, load_settings, main
from wechat_agent.security import mask_mapping, mask_secret

REQUIRED_ENV = {
    "LLM_BASE_URL": "https://llm.example.test/v1",
    "LLM_API_KEY": "sk-test-0123456789abcdefghijklmnopqrstuvwxyz",
    "LLM_MODEL": "test-model",
}


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """清空全部配置环境变量，再提供最小的必填配置。"""
    for field_name in Settings.model_fields:
        monkeypatch.delenv(field_name.upper(), raising=False)
    for key, value in REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    return monkeypatch


def test_missing_llm_api_key_reports_key_name_only(env: pytest.MonkeyPatch) -> None:
    env.delenv("LLM_API_KEY")

    with pytest.raises(ConfigError) as excinfo:
        load_settings(env_file=None)

    message = str(excinfo.value)
    assert "LLM_API_KEY" in message
    assert REQUIRED_ENV["LLM_BASE_URL"] not in message
    assert REQUIRED_ENV["LLM_MODEL"] not in message
    assert "LLM_BASE_URL" not in message


def test_main_returns_non_zero_when_required_key_missing(
    env: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    env.delenv("LLM_API_KEY")

    assert main(env_file=None) == 1

    stderr = capsys.readouterr().err
    assert "LLM_API_KEY" in stderr
    assert REQUIRED_ENV["LLM_MODEL"] not in stderr


@pytest.mark.parametrize("value", ["", None])
def test_allow_from_allows_empty_value(env: pytest.MonkeyPatch, value: str | None) -> None:
    if value is None:
        env.delenv("ALLOW_FROM", raising=False)
    else:
        env.setenv("ALLOW_FROM", value)

    settings = load_settings(env_file=None)

    assert settings.allow_from == ""


def test_invalid_numeric_type_reports_key_name_only(env: pytest.MonkeyPatch) -> None:
    env.setenv("LLM_TIMEOUT_SECONDS", "not-a-number")

    with pytest.raises(ConfigError) as excinfo:
        load_settings(env_file=None)

    message = str(excinfo.value)
    assert "LLM_TIMEOUT_SECONDS" in message
    assert "not-a-number" not in message


def test_defaults_match_design_table(env: pytest.MonkeyPatch) -> None:
    settings = load_settings(env_file=None)

    assert settings.llm_timeout_seconds == 30
    assert settings.llm_max_retries == 1
    assert settings.short_memory_size == 20
    assert settings.summary_trigger_count == 10
    assert settings.summary_idle_minutes == 10
    assert settings.max_input_chars == 2000
    assert settings.max_output_chars == 2000
    assert settings.repeat_window_seconds == 30
    assert settings.dry_run is False
    assert settings.data_dir == Path("data")
    assert settings.log_dir == Path("data/logs")
    assert settings.log_level == "info"
    assert settings.ilink_base_url == "https://ilinkai.weixin.qq.com"
    assert settings.ilink_bot_type == 3
    assert settings.fallback_reply_enabled is True
    assert settings.system_prompt
    assert settings.fallback_reply_text
    assert settings.unsupported_media_reply


def test_settings_repr_does_not_leak_api_key(env: pytest.MonkeyPatch) -> None:
    settings = load_settings(env_file=None)

    assert REQUIRED_ENV["LLM_API_KEY"] not in repr(settings)


def test_mask_secret_keeps_edges_only() -> None:
    masked = mask_secret("sk-1234567890abcdef")

    assert masked.startswith("sk-1")
    assert masked.endswith("cdef")
    assert "4567890" not in masked


def test_mask_secret_fully_masks_short_values() -> None:
    assert mask_secret("") == ""
    assert mask_secret("abc") == "***"


def test_mask_mapping_hides_full_values() -> None:
    api_key = "sk-proj-0123456789abcdefghijklmnopqrstuvwxyz"
    bot_token = "bot_token_0123456789abcdefghijklmnopqrstuvwxyz"
    original = {
        "api_key": api_key,
        "Authorization": f"Bearer {bot_token}",
        "max_tokens": 256,
        "model": "test-model",
    }

    masked = mask_mapping(original)
    rendered = repr(masked)

    assert api_key not in rendered
    assert bot_token not in rendered
    assert masked["max_tokens"] == 256
    assert masked["model"] == "test-model"
    assert original["api_key"] == api_key

    masked_key = masked["api_key"]
    masked_auth = masked["Authorization"]
    assert isinstance(masked_key, str)
    assert isinstance(masked_auth, str)
    assert masked_key.startswith("sk-p")
    assert masked_key.endswith("wxyz")
    assert masked_auth.startswith("Bearer ")
    assert bot_token not in masked_auth
