"""Tests for pm_benchmark.prompts."""
from __future__ import annotations

from pm_benchmark.market_info import MarketInfo
from pm_benchmark.prompts import build_analysis_prompt, build_round_context, build_system_prompt


class TestBuildSystemPrompt:
    def test_contains_json_format(self):
        prompt = build_system_prompt()
        assert "probability" in prompt
        assert "action" in prompt
        assert "buy_yes" in prompt
        assert "buy_no" in prompt
        assert "skip" in prompt
        assert "JSON" in prompt

    def test_is_string(self):
        assert isinstance(build_system_prompt(), str)

    def test_not_empty(self):
        assert len(build_system_prompt()) > 100


class TestBuildAnalysisPrompt:
    def _make_market(self) -> MarketInfo:
        return MarketInfo(
            slug="test-market",
            question="Will X happen?",
            description="Resolves Yes if X happens.",
            outcomes=["Yes", "No"],
            outcome_prices=[0.65, 0.35],
            volume=1_000_000.0,
            liquidity=200_000.0,
            end_date="2025-12-31T23:59:59Z",
            active=True,
            closed=False,
        )

    def test_contains_market_info(self):
        prompt = build_analysis_prompt(
            market=self._make_market(),
            balance=10_000.0,
            portfolio_summary="No open positions.",
            position_size_suggestion=500.0,
        )
        assert "Will X happen?" in prompt
        assert "Resolves Yes if X happens." in prompt
        assert "$1,000,000" in prompt
        assert "$200,000" in prompt

    def test_contains_portfolio(self):
        prompt = build_analysis_prompt(
            market=self._make_market(),
            balance=10_000.0,
            portfolio_summary="Holding 100 shares of BTC-Yes",
            position_size_suggestion=500.0,
        )
        assert "$10,000.00" in prompt
        assert "Holding 100 shares of BTC-Yes" in prompt
        assert "$500.00" in prompt

    def test_contains_prices(self):
        prompt = build_analysis_prompt(
            market=self._make_market(),
            balance=10_000.0,
            portfolio_summary="",
            position_size_suggestion=500.0,
        )
        assert "Yes: $0.65" in prompt
        assert "No: $0.35" in prompt

    def test_round_context_included(self):
        rc = build_round_context(2, 3, [
            {"slug": "m1", "action": "buy_yes", "amount_usd": 200, "probability": 0.7},
        ])
        prompt = build_analysis_prompt(
            market=self._make_market(),
            balance=10_000.0,
            portfolio_summary="No open positions.",
            position_size_suggestion=500.0,
            round_context=rc,
        )
        assert "Round 2 of 3" in prompt
        assert "m1: buy_yes $200" in prompt

    def test_no_round_context_when_empty(self):
        prompt = build_analysis_prompt(
            market=self._make_market(),
            balance=10_000.0,
            portfolio_summary="No open positions.",
            position_size_suggestion=500.0,
        )
        assert "Round" not in prompt


class TestBuildRoundContext:
    def test_with_actions(self):
        ctx = build_round_context(2, 3, [
            {"slug": "m1", "action": "buy_yes", "amount_usd": 200, "probability": 0.7},
            {"slug": "m2", "action": "skip"},
        ])
        assert "Round 2 of 3" in ctx
        assert "m1: buy_yes $200 (p=0.7)" in ctx
        assert "m2: skipped" in ctx

    def test_no_previous_actions(self):
        ctx = build_round_context(1, 3, [])
        assert "Round 1 of 3" in ctx
        assert "Previous" not in ctx

    def test_buy_no_action(self):
        ctx = build_round_context(2, 2, [
            {"slug": "x", "action": "buy_no", "amount_usd": 100, "probability": 0.3},
        ])
        assert "buy_no $100" in ctx


class TestBlindMode:
    def _make_market(self) -> MarketInfo:
        return MarketInfo(
            slug="test-market",
            question="Will X happen?",
            description="Resolves Yes if X happens.",
            outcomes=["Yes", "No"],
            outcome_prices=[0.65, 0.35],
            volume=1_000_000.0,
            liquidity=200_000.0,
            end_date="2025-12-31T23:59:59Z",
            active=True,
            closed=False,
        )

    def test_blind_hides_prices(self):
        prompt = build_analysis_prompt(
            market=self._make_market(),
            balance=10_000.0,
            portfolio_summary="No open positions.",
            position_size_suggestion=500.0,
            blind_mode=True,
        )
        assert "[hidden" in prompt
        assert "0.65" not in prompt
        assert "0.35" not in prompt

    def test_normal_shows_prices(self):
        prompt = build_analysis_prompt(
            market=self._make_market(),
            balance=10_000.0,
            portfolio_summary="No open positions.",
            position_size_suggestion=500.0,
            blind_mode=False,
        )
        assert "[hidden" not in prompt
        assert "0.65" in prompt
