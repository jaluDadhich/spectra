"""Environment-based typed settings for Spectra.

Secrets (e.g. OPENAI_API_KEY) are read from the environment only and are
never logged, serialized into API responses, or written to reports.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- OpenAI / LLM ---
    openai_api_key: Optional[SecretStr] = Field(default=None)
    openai_model: str = Field(default="gpt-6-luna")
    # Low-cost Luna handles intake and specialist discovery; Terra handles
    # final cross-document comparison and issue-scope judgment at lower cost
    # than Sol while retaining a reasoning-oriented model for this workload.
    openai_fast_model: Optional[str] = Field(default="gpt-6-luna")
    openai_reasoning_model: Optional[str] = Field(default="gpt-6.1-terra")
    openai_fast_reasoning_effort: str = Field(default="low")
    openai_reasoning_effort: str = Field(default="high")
    openai_base_url: Optional[str] = Field(default=None)
    openai_timeout_seconds: float = Field(default=60.0, gt=0)
    # Token-packed final-judge batches get a longer timeout than specialist calls.
    openai_scope_timeout_seconds: float = Field(default=180.0, gt=0)
    openai_max_retries: int = Field(default=3, ge=0, le=10)
    openai_max_output_tokens: int = Field(default=2000, gt=0)
    openai_reasoning_max_output_tokens: int = Field(default=6000, gt=0)
    openai_scope_max_output_tokens: int = Field(default=16000, gt=0)
    # Hard ceiling for estimated prompt + completion tokens for one GPT call.
    openai_max_request_tokens: int = Field(default=25_000, ge=1_000, le=25_000)
    openai_temperature: Optional[float] = Field(default=None)

    spectra_llm_enabled: bool = Field(default=False)
    spectra_llm_budget_usd_per_case: float = Field(default=1.00, ge=0)
    spectra_max_llm_calls_per_case: int = Field(default=64, ge=0)
    spectra_max_concurrent_llm_calls: int = Field(default=2, ge=1)
    spectra_max_evidence_payload_chars: int = Field(default=24_000, gt=0)
    # Maximum cited-evidence bundle for one token-packed scope-judge batch.
    spectra_scope_evidence_payload_chars: int = Field(default=42_000, gt=0)

    # USD per 1K tokens at default Luna/6.1 Terra rates; override when models
    # or provider pricing change.
    spectra_price_input_per_1k: Optional[float] = Field(default=0.0001, ge=0)
    spectra_price_output_per_1k: Optional[float] = Field(default=0.0005, ge=0)
    spectra_reasoning_price_input_per_1k: Optional[float] = Field(default=0.001, ge=0)
    spectra_reasoning_price_output_per_1k: Optional[float] = Field(default=0.005, ge=0)

    # --- Runtime ---
    spectra_log_level: str = Field(default="INFO")
    spectra_log_file: Path = Field(default=Path("logs/spectra.log"))
    database_url: str = Field(default="sqlite:///./data/spectra.db")
    spectra_data_dir: Path = Field(default=Path("./data"))
    spectra_ruleset_dir: Path = Field(default=Path("./rulesets"))
    spectra_profile_dir: Path = Field(default=Path("./profiles"))
    # Root folder containing one subfolder per account, each holding that
    # account's submission JSON files. Defaults to <workspace>/documents.
    spectra_accounts_dir: Optional[Path] = Field(default=None)

    # --- API security ---
    # Optional bearer token for the API boundary hook. When unset the API
    # runs in local single-user mode; production deployments must set this
    # (or front the service with an auth gateway).
    spectra_api_token: Optional[SecretStr] = Field(default=None)
    spectra_max_upload_bytes: int = Field(default=5_000_000, gt=0)

    # --- External source adapters (disabled by default) ---
    spectra_ext_state_registry_enabled: bool = Field(default=False)
    spectra_ext_state_registry_api_key: Optional[SecretStr] = Field(default=None)
    spectra_ext_fmcsa_enabled: bool = Field(default=False)
    spectra_ext_fmcsa_api_key: Optional[SecretStr] = Field(default=None)

    # Optional public-record/news research. Local external_records are always
    # preferred; live search is opt-in and never runs when that file exists.
    spectra_web_search_enabled: bool = Field(default=False)
    spectra_web_search_api_key: Optional[SecretStr] = Field(default=None)
    spectra_web_search_url: str = Field(default="https://api.tavily.com/search")
    spectra_web_search_cache_ttl_hours: int = Field(default=168, ge=1)

    @field_validator("spectra_log_level")
    @classmethod
    def _log_level(cls, v: str) -> str:
        level = v.upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError(f"Invalid log level: {v}")
        return level

    @property
    def llm_available(self) -> bool:
        """True only when GPT-assisted review is enabled AND a key exists."""
        return self.spectra_llm_enabled and self.openai_api_key is not None

    @property
    def accounts_dir(self) -> Path:
        if self.spectra_accounts_dir is not None:
            return Path(self.spectra_accounts_dir)
        code_root = Path(__file__).resolve().parents[2]
        return code_root.parent / "documents"

    @property
    def sqlite_path(self) -> Path:
        prefix = "sqlite:///"
        if self.database_url.startswith(prefix):
            return Path(self.database_url[len(prefix):])
        raise ValueError(
            "Only sqlite:/// DATABASE_URL values are supported by the bundled "
            "repository; configure a migration-ready RDBMS for production."
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
