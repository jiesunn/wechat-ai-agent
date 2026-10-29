"""应用配置：从环境变量与 .env 加载，带类型校验与启动期校验。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Final

from pydantic import Field, SecretStr, ValidationError, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_ENV_FILE: Final = ".env"
DEFAULT_SYSTEM_PROMPT: Final = (
    "你是一个简洁、友好的中文微信助手。回答要口语化、直接，不使用 Markdown 语法，"
    "单条回复尽量不超过 200 字。"
)
DEFAULT_FALLBACK_REPLY_TEXT: Final = "抱歉，我这边暂时出了问题，稍后再聊。"
DEFAULT_UNSUPPORTED_MEDIA_REPLY: Final = "我暂时看不了图片、语音和文件，发文字给我吧。"
DEFAULT_ILINK_BASE_URL: Final = "https://ilinkai.weixin.qq.com"
LOG_LEVELS: Final = frozenset({"debug", "info", "warning", "error", "critical"})


class ConfigError(RuntimeError):
    """配置缺失或非法；消息只包含键名，不包含键值。"""


class Settings(BaseSettings):
    """D8 配置表中的全部配置项，可由环境变量或 .env 覆盖。"""

    model_config = SettingsConfigDict(
        env_file=DEFAULT_ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- LLM（必填）----
    llm_base_url: str = Field(min_length=1)
    llm_api_key: SecretStr
    llm_model: str = Field(min_length=1)
    llm_timeout_seconds: float = Field(default=30.0, gt=0)
    llm_max_retries: int = Field(default=1, ge=0)

    # ---- 白名单：空值表示全部拒绝 ----
    allow_from: str = ""

    # ---- 人设与降级 ----
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    fallback_reply_enabled: bool = True
    fallback_reply_text: str = DEFAULT_FALLBACK_REPLY_TEXT
    unsupported_media_reply: str = DEFAULT_UNSUPPORTED_MEDIA_REPLY

    # ---- 记忆 ----
    short_memory_size: int = Field(default=20, gt=0)
    summary_trigger_count: int = Field(default=10, gt=0)
    summary_idle_minutes: int = Field(default=10, ge=0)

    # ---- 输入输出 ----
    max_input_chars: int = Field(default=2000, gt=0)
    max_output_chars: int = Field(default=2000, gt=0)
    repeat_window_seconds: int = Field(default=30, ge=0)

    dry_run: bool = False

    # ---- 运行目录与日志 ----
    data_dir: Path = Path("data")
    log_dir: Path = Path("data/logs")
    log_level: str = "info"

    # ---- iLink ----
    ilink_base_url: str = DEFAULT_ILINK_BASE_URL
    ilink_bot_type: int = Field(default=3, ge=1)

    @field_validator("llm_base_url", "ilink_base_url")
    @classmethod
    def _normalize_base_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        if not normalized:
            raise ValueError("不能为空")
        return normalized

    @field_validator("llm_model")
    @classmethod
    def _reject_empty_model(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("不能为空")
        return normalized

    @field_validator("llm_api_key")
    @classmethod
    def _reject_empty_api_key(cls, value: SecretStr) -> SecretStr:
        secret = value.get_secret_value().strip()
        if not secret:
            raise ValueError("不能为空")
        return SecretStr(secret)

    @field_validator("system_prompt", "fallback_reply_text", "unsupported_media_reply")
    @classmethod
    def _fallback_to_builtin_text(cls, value: str, info: ValidationInfo) -> str:
        builtin = {
            "system_prompt": DEFAULT_SYSTEM_PROMPT,
            "fallback_reply_text": DEFAULT_FALLBACK_REPLY_TEXT,
            "unsupported_media_reply": DEFAULT_UNSUPPORTED_MEDIA_REPLY,
        }
        return value.strip() or builtin[str(info.field_name)]

    @field_validator("log_level")
    @classmethod
    def _normalize_log_level(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in LOG_LEVELS:
            raise ValueError("必须是 debug/info/warning/error/critical 之一")
        return normalized


def _invalid_env_keys(error: ValidationError) -> list[str]:
    """从校验错误中提取环境变量键名，不触碰错误里的输入值。"""
    keys: set[str] = set()
    for item in error.errors():
        for part in item.get("loc", ()):
            if isinstance(part, str):
                keys.add(part.upper())
                break
    return sorted(keys)


def load_settings(*, env_file: str | Path | None = DEFAULT_ENV_FILE) -> Settings:
    """加载并校验配置；失败时抛出 ConfigError，消息只列出键名。"""
    try:
        return Settings(_env_file=env_file)
    except ValidationError as error:
        keys = _invalid_env_keys(error)
        detail = "、".join(keys) if keys else "未知配置项"
        raise ConfigError(f"缺少或非法的配置项：{detail}") from None


def main(*, env_file: str | Path | None = DEFAULT_ENV_FILE) -> int:
    """启动前校验配置；失败时只输出键名并返回非零退出码。"""
    try:
        load_settings(env_file=env_file)
    except ConfigError as error:
        print(f"配置校验失败：{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
