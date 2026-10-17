"""Runtime settings for the harness.

Every value can be overridden by an environment variable or a `.env` file at
the repo root. Names and defaults mirror `.claude/specs/02.architecture.md` §7.9.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    LLM_BASE_URL: str = "http://127.0.0.1:8000/v1"
    LLM_MODEL: str = "qwen3-coder-30b"
    LLM_TEMPERATURE: float = 0.1
    LLM_MAX_OUTPUT_TOKENS: int = 400
    LLM_FORCE_JSON_MODE: bool = False

    MODE: Literal["build", "test", "live"] = "build"
    HEADLESS: bool = False

    MAX_TRIES_PER_STEP: int = 8
    MAX_ROUNDS_PER_COMPANY: int = 60
    SAME_ERROR_SWITCH: int = 3
    SAME_ERROR_ASK: int = 5

    OBSERVE_TOKEN_CAP: int = 1500
    PROMPT_TOKEN_CAP: int = 3500
    DEBUG_PROMPTS: bool = False

    HARNESS_VERSION: str = "0.1.0"

    @property
    def repo_root(self) -> Path:
        return REPO_ROOT
