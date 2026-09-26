"""Pydantic configuration models for benchmark runs."""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field, field_validator


class LLMConfig(BaseModel):
    """LLM provider configuration (litellm format)."""

    model: str = Field(description="litellm model string, e.g. 'claude-opus-4'")
    temperature: float = Field(default=0.3, ge=0.0, le=2.0)
    max_tokens: int = Field(default=2048, ge=1)
    api_key_env: str | None = Field(
        default=None,
        description="Env var name holding API key (e.g. ANTHROPIC_API_KEY)",
    )
    api_base: str | None = Field(
        default=None,
        description="Custom API base URL passed to litellm (e.g. GitHub Models)",
    )
    num_retries: int | None = Field(
        default=None,
        description="litellm retry count with backoff on transient failures",
    )


class LeaderboardConfig(BaseModel):
    """Leaderboard SDK connection config."""

    base_url: str = Field(default="http://localhost:8000")
    agent_name: str | None = Field(
        default=None,
        description="Agent name on leaderboard; auto-generated from model if None",
    )
    account_name: str = Field(default="default")


class RunConfig(BaseModel):
    """Full configuration for a benchmark run."""

    llm: LLMConfig
    leaderboard: LeaderboardConfig = Field(default_factory=LeaderboardConfig)
    market_set: str = Field(
        default="mini",
        description="Name of bundled market set or path to YAML file",
    )
    budget: float = Field(default=10_000.0, gt=0)
    max_trades_per_market: int = Field(default=1, ge=1)
    position_size_pct: float = Field(
        default=5.0, gt=0, le=100,
        description="Max % of budget per trade",
    )
    timeout: float = Field(default=60.0, gt=0, description="Per-market timeout in seconds")
    seed: int | None = Field(default=None, description="Random seed for reproducibility")
    output_dir: Path = Field(default=Path("./results"))
    n_rounds: int = Field(default=1, ge=1, description="Number of evaluation rounds")
    round_delay_seconds: float = Field(default=0.0, ge=0, description="Delay between rounds")
    blind_mode: bool = Field(default=False, description="Hide market prices from LLM prompt")

    @field_validator("market_set")
    @classmethod
    def _validate_market_set(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("market_set cannot be empty")
        return v.strip()
