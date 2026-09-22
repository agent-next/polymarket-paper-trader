"""Tests for pm_benchmark.config."""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from pm_benchmark.config import LLMConfig, LeaderboardConfig, RunConfig


class TestLLMConfig:
    def test_defaults(self):
        cfg = LLMConfig(model="claude-opus-4")
        assert cfg.model == "claude-opus-4"
        assert cfg.temperature == 0.3
        assert cfg.max_tokens == 2048
        assert cfg.api_key_env is None

    def test_custom_values(self):
        cfg = LLMConfig(
            model="gpt-4o",
            temperature=0.7,
            max_tokens=4096,
            api_key_env="OPENAI_API_KEY",
        )
        assert cfg.model == "gpt-4o"
        assert cfg.temperature == 0.7
        assert cfg.max_tokens == 4096
        assert cfg.api_key_env == "OPENAI_API_KEY"

    def test_temperature_bounds(self):
        with pytest.raises(ValidationError):
            LLMConfig(model="x", temperature=-0.1)
        with pytest.raises(ValidationError):
            LLMConfig(model="x", temperature=2.1)

    def test_max_tokens_positive(self):
        with pytest.raises(ValidationError):
            LLMConfig(model="x", max_tokens=0)

    def test_model_required(self):
        with pytest.raises(ValidationError):
            LLMConfig()


class TestLeaderboardConfig:
    def test_defaults(self):
        cfg = LeaderboardConfig()
        assert cfg.base_url == "http://localhost:8000"
        assert cfg.agent_name is None
        assert cfg.account_name == "default"

    def test_custom(self):
        cfg = LeaderboardConfig(
            base_url="https://lb.example.com",
            agent_name="bench-bot",
            account_name="eval",
        )
        assert cfg.base_url == "https://lb.example.com"
        assert cfg.agent_name == "bench-bot"
        assert cfg.account_name == "eval"


class TestRunConfig:
    def test_defaults(self):
        cfg = RunConfig(llm=LLMConfig(model="claude-opus-4"))
        assert cfg.market_set == "mini"
        assert cfg.budget == 10_000.0
        assert cfg.max_trades_per_market == 1
        assert cfg.position_size_pct == 5.0
        assert cfg.timeout == 60.0
        assert cfg.seed is None
        assert cfg.output_dir == Path("./results")

    def test_full_config(self):
        cfg = RunConfig(
            llm=LLMConfig(model="gpt-4o"),
            leaderboard=LeaderboardConfig(base_url="http://lb:9000"),
            market_set="mixed",
            budget=50_000.0,
            max_trades_per_market=3,
            position_size_pct=10.0,
            timeout=120.0,
            seed=42,
            output_dir=Path("/tmp/results"),
        )
        assert cfg.market_set == "mixed"
        assert cfg.budget == 50_000.0
        assert cfg.seed == 42

    def test_market_set_empty(self):
        with pytest.raises(ValidationError):
            RunConfig(llm=LLMConfig(model="x"), market_set="")

    def test_market_set_whitespace_stripped(self):
        cfg = RunConfig(llm=LLMConfig(model="x"), market_set="  mixed  ")
        assert cfg.market_set == "mixed"

    def test_budget_positive(self):
        with pytest.raises(ValidationError):
            RunConfig(llm=LLMConfig(model="x"), budget=0)

    def test_position_size_bounds(self):
        with pytest.raises(ValidationError):
            RunConfig(llm=LLMConfig(model="x"), position_size_pct=0)
        with pytest.raises(ValidationError):
            RunConfig(llm=LLMConfig(model="x"), position_size_pct=101)

    def test_n_rounds_default(self):
        cfg = RunConfig(llm=LLMConfig(model="x"))
        assert cfg.n_rounds == 1
        assert cfg.round_delay_seconds == 0.0

    def test_n_rounds_validation(self):
        with pytest.raises(ValidationError):
            RunConfig(llm=LLMConfig(model="x"), n_rounds=0)

    def test_round_delay_validation(self):
        with pytest.raises(ValidationError):
            RunConfig(llm=LLMConfig(model="x"), round_delay_seconds=-1.0)

    def test_blind_mode_default(self):
        cfg = RunConfig(llm=LLMConfig(model="x"))
        assert cfg.blind_mode is False

    def test_blind_mode_enabled(self):
        cfg = RunConfig(llm=LLMConfig(model="x"), blind_mode=True)
        assert cfg.blind_mode is True
