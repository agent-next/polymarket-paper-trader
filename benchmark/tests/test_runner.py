"""Tests for pm_benchmark.runner."""
from __future__ import annotations

import json
import time
from unittest.mock import MagicMock

import pytest

from pm_benchmark.config import LLMConfig, LeaderboardConfig, RunConfig
from pm_benchmark.market_info import MarketInfo, MarketInfoError
from pm_benchmark.providers import LLMError
from pm_benchmark.runner import EvalRun, MarketResult, RoundResult, Runner


def _make_market(
    slug: str = "test-market",
    question: str = "Will X happen?",
    active: bool = True,
    closed: bool = False,
) -> MarketInfo:
    return MarketInfo(
        slug=slug,
        question=question,
        description="Test",
        outcomes=["Yes", "No"],
        outcome_prices=[0.65, 0.35],
        volume=1_000_000.0,
        liquidity=200_000.0,
        end_date="2025-12-31T23:59:59Z",
        active=active,
        closed=closed,
    )


def _make_config(market_set: str = "mini") -> RunConfig:
    return RunConfig(
        llm=LLMConfig(model="test-model"),
        leaderboard=LeaderboardConfig(),
        market_set=market_set,
    )


def _make_decision_json(
    probability: float = 0.7,
    action: str = "buy_yes",
    amount: float = 250.0,
) -> str:
    return json.dumps({
        "probability": probability,
        "action": action,
        "confidence": "high",
        "amount_usd": amount,
        "reasoning": "Test reasoning",
    })


class TestMarketResult:
    def test_defaults(self):
        r = MarketResult(slug="test", question="Q?")
        assert r.action == "skip"
        assert r.error is None
        assert r.trade_result is None
        assert r.market_price_yes is None


class TestEvalRun:
    def test_counts(self):
        run = EvalRun(model="m", market_set="s", budget=10000)
        run.market_results = [
            MarketResult(slug="a", question="A"),  # completed
            MarketResult(slug="b", question="B", error="fail"),  # error
            MarketResult(slug="c", question="C", skipped_reason="closed"),  # skipped
        ]
        assert run.completed_count == 1
        assert run.error_count == 1
        assert run.skipped_count == 1


class TestRunner:
    def test_full_run_no_agent(self):
        """Run without agent — LLM is queried, no trades executed."""
        config = _make_config("mini")
        fetcher = MagicMock(side_effect=lambda slug, **kw: _make_market(slug=slug))
        caller = MagicMock(return_value=_make_decision_json())

        runner = Runner(config, market_fetcher=fetcher, model_caller=caller)
        result = runner.run()

        assert result.model == "test-model"
        assert result.market_set == "mini"
        assert len(result.market_results) == 3
        assert result.completed_count == 3
        assert result.error_count == 0

    def test_run_with_agent(self):
        """Run with agent — trades are executed."""
        config = _make_config("mini")
        fetcher = MagicMock(side_effect=lambda slug, **kw: _make_market(slug=slug))
        caller = MagicMock(return_value=_make_decision_json())

        agent = MagicMock()
        agent.balance.return_value = {"cash": 10_000.0}
        agent.portfolio.return_value = []
        agent.buy.return_value = {"shares": 100, "avg_price": 0.65}
        agent.stats.return_value = {"roi_pct": 5.0}

        runner = Runner(config, agent=agent, market_fetcher=fetcher, model_caller=caller)
        result = runner.run()

        assert result.completed_count == 3
        assert agent.buy.call_count == 3
        assert result.agent_stats == {"roi_pct": 5.0}

    def test_passes_seed_and_timeout_to_model_caller(self):
        config = RunConfig(
            llm=LLMConfig(model="test-model"),
            market_set="mini",
            seed=42,
            timeout=9.5,
        )
        fetcher = MagicMock(side_effect=lambda slug, **kw: _make_market(slug=slug))
        caller = MagicMock(return_value=_make_decision_json(action="skip", amount=0))

        runner = Runner(config, market_fetcher=fetcher, model_caller=caller)
        runner.run()

        _, kwargs = caller.call_args
        assert kwargs["seed"] == 42
        assert kwargs["timeout"] == 9.5

    def test_max_trades_per_market_enforced(self):
        config = RunConfig(
            llm=LLMConfig(model="test-model"),
            market_set="mini",
            n_rounds=2,
            max_trades_per_market=1,
        )
        fetcher = MagicMock(side_effect=lambda slug, **kw: _make_market(slug=slug))
        caller = MagicMock(return_value=_make_decision_json(action="buy_yes", amount=100))

        agent = MagicMock()
        agent.balance.return_value = {"cash": 10_000.0}
        agent.portfolio.return_value = []
        agent.buy.return_value = {"shares": 100, "avg_price": 0.65}
        agent.stats.return_value = {}

        runner = Runner(config, agent=agent, market_fetcher=fetcher, model_caller=caller)
        result = runner.run()

        assert agent.buy.call_count == 3
        skipped = [r for r in result.market_results if r.skipped_reason == "Max trades per market reached"]
        assert len(skipped) == 3

    def test_timeout_marks_market_error(self):
        config = RunConfig(
            llm=LLMConfig(model="test-model"),
            market_set="mini",
            timeout=0.001,
        )
        fetcher = MagicMock(side_effect=lambda slug, **kw: _make_market(slug=slug))

        def slow_caller(*_args, **_kwargs):
            time.sleep(0.01)
            return _make_decision_json(action="skip", amount=0)

        runner = Runner(config, market_fetcher=fetcher, model_caller=slow_caller)
        result = runner.run()

        assert result.error_count == 3
        assert all("Per-market timeout exceeded" in (r.error or "") for r in result.market_results)

    def test_legacy_model_caller_without_kwargs_compat(self):
        config = RunConfig(
            llm=LLMConfig(model="test-model"),
            market_set="mini",
            seed=1,
            timeout=10.0,
        )
        fetcher = MagicMock(side_effect=lambda slug, **kw: _make_market(slug=slug))

        def legacy_caller(llm_config, prompt, system_prompt):
            return _make_decision_json(action="skip", amount=0)

        runner = Runner(config, market_fetcher=fetcher, model_caller=legacy_caller)
        result = runner.run()
        assert result.completed_count == 3

    def test_model_caller_typeerror_reraised_when_not_kwargs_signature_issue(self):
        config = _make_config("mini")

        def caller_bad(llm_config, prompt, system_prompt, **kwargs):
            raise TypeError("bad signature state")

        runner = Runner(config, model_caller=caller_bad)
        with pytest.raises(TypeError, match="bad signature state"):
            runner._query_model_with_compat("prompt", "system")

    def test_market_fetch_error(self):
        """Market fetch failure is recorded as error."""
        config = _make_config("mini")
        fetcher = MagicMock(side_effect=MarketInfoError("not found"))
        caller = MagicMock()

        runner = Runner(config, market_fetcher=fetcher, model_caller=caller)
        result = runner.run()

        assert result.error_count == 3
        assert "Market fetch failed" in result.market_results[0].error

    def test_market_fetch_unexpected_exception(self):
        config = _make_config("mini")
        fetcher = MagicMock(side_effect=RuntimeError("boom"))
        caller = MagicMock()

        runner = Runner(config, market_fetcher=fetcher, model_caller=caller)
        result = runner.run()

        assert result.error_count == 3
        assert "Market fetch failed: boom" in (result.market_results[0].error or "")
        assert caller.call_count == 0

    def test_timeout_after_market_fetch(self):
        config = _make_config("mini")
        fetcher = MagicMock(side_effect=lambda slug, **kw: _make_market(slug=slug))
        caller = MagicMock()

        runner = Runner(config, market_fetcher=fetcher, model_caller=caller)
        runner._check_timeout = MagicMock(side_effect=TimeoutError("fetch timeout"))  # type: ignore[method-assign]
        result = runner.run()

        assert result.error_count == 3
        assert all(r.error == "fetch timeout" for r in result.market_results)
        assert caller.call_count == 0

    def test_closed_market_skipped(self):
        """Closed markets are skipped."""
        config = _make_config("mini")
        fetcher = MagicMock(return_value=_make_market(closed=True))
        caller = MagicMock()

        runner = Runner(config, market_fetcher=fetcher, model_caller=caller)
        result = runner.run()

        assert result.skipped_count == 3
        assert caller.call_count == 0

    def test_inactive_market_skipped(self):
        """Inactive markets are skipped."""
        config = _make_config("mini")
        fetcher = MagicMock(return_value=_make_market(active=False))
        caller = MagicMock()

        runner = Runner(config, market_fetcher=fetcher, model_caller=caller)
        result = runner.run()

        assert result.skipped_count == 3

    def test_llm_error(self):
        """LLM call failure is recorded as error."""
        config = _make_config("mini")
        fetcher = MagicMock(return_value=_make_market())
        caller = MagicMock(side_effect=LLMError("API down"))

        runner = Runner(config, market_fetcher=fetcher, model_caller=caller)
        result = runner.run()

        assert result.error_count == 3
        assert "LLM call failed" in result.market_results[0].error

    def test_parse_error(self):
        """Invalid LLM response is recorded as error."""
        config = _make_config("mini")
        fetcher = MagicMock(return_value=_make_market())
        caller = MagicMock(return_value="not json at all")

        runner = Runner(config, market_fetcher=fetcher, model_caller=caller)
        result = runner.run()

        assert result.error_count == 3
        assert "Parse failed" in result.market_results[0].error

    def test_trade_error(self):
        """Trade execution failure is recorded."""
        config = _make_config("mini")
        fetcher = MagicMock(return_value=_make_market())
        caller = MagicMock(return_value=_make_decision_json())

        agent = MagicMock()
        agent.balance.return_value = {"cash": 10_000.0}
        agent.portfolio.return_value = []
        agent.buy.side_effect = RuntimeError("Insufficient balance")
        agent.stats.return_value = {}

        runner = Runner(config, agent=agent, market_fetcher=fetcher, model_caller=caller)
        result = runner.run()

        assert result.error_count == 3
        assert "Trade failed" in result.market_results[0].error

    def test_timeout_during_trade_execution(self):
        config = _make_config("mini")
        fetcher = MagicMock(return_value=_make_market())
        caller = MagicMock(return_value=_make_decision_json(action="buy_yes", amount=100))

        agent = MagicMock()
        agent.balance.return_value = {"cash": 10_000.0}
        agent.portfolio.return_value = []
        agent.buy.return_value = {"shares": 10}
        agent.stats.return_value = {}

        runner = Runner(config, agent=agent, market_fetcher=fetcher, model_caller=caller)

        calls = {"n": 0}

        def _timeout_on_trade(_start):
            calls["n"] += 1
            if calls["n"] == 3:
                raise TimeoutError("trade timeout")

        runner._check_timeout = _timeout_on_trade  # type: ignore[method-assign]
        result = runner._evaluate_market("test-market", "system")
        assert result.error == "trade timeout"

    def test_skip_action_no_trade(self):
        """Skip action doesn't call agent.buy."""
        config = _make_config("mini")
        fetcher = MagicMock(return_value=_make_market())
        caller = MagicMock(return_value=_make_decision_json(action="skip", amount=0))

        agent = MagicMock()
        agent.balance.return_value = {"cash": 10_000.0}
        agent.portfolio.return_value = []
        agent.stats.return_value = {}

        runner = Runner(config, agent=agent, market_fetcher=fetcher, model_caller=caller)
        result = runner.run()

        agent.buy.assert_not_called()

    def test_buy_no_action(self):
        """buy_no action buys 'no' outcome."""
        config = _make_config("mini")
        fetcher = MagicMock(return_value=_make_market())
        caller = MagicMock(return_value=_make_decision_json(action="buy_no"))

        agent = MagicMock()
        agent.balance.return_value = {"cash": 10_000.0}
        agent.portfolio.return_value = []
        agent.buy.return_value = {"shares": 50}
        agent.stats.return_value = {}

        runner = Runner(config, agent=agent, market_fetcher=fetcher, model_caller=caller)
        result = runner.run()

        # Check the first buy call used "no" outcome
        call_args = agent.buy.call_args_list[0]
        assert call_args[0][1] == "no"

    def test_agent_stats_failure_graceful(self):
        """Agent stats failure doesn't crash the run."""
        config = _make_config("mini")
        fetcher = MagicMock(return_value=_make_market())
        caller = MagicMock(return_value=_make_decision_json(action="skip", amount=0))

        agent = MagicMock()
        agent.balance.return_value = {"cash": 10_000.0}
        agent.portfolio.return_value = []
        agent.stats.side_effect = RuntimeError("stats failed")

        runner = Runner(config, agent=agent, market_fetcher=fetcher, model_caller=caller)
        result = runner.run()

        assert result.agent_stats == {}

    def test_agent_portfolio_with_positions(self):
        """Portfolio with positions is formatted in prompt."""
        config = _make_config("mini")
        fetcher = MagicMock(return_value=_make_market())
        caller = MagicMock(return_value=_make_decision_json(action="skip", amount=0))

        agent = MagicMock()
        agent.balance.return_value = {"cash": 8_000.0}
        agent.portfolio.return_value = [
            {"market_slug": "btc-100k", "shares": 100, "outcome": "yes"},
        ]
        agent.stats.return_value = {}

        runner = Runner(config, agent=agent, market_fetcher=fetcher, model_caller=caller)
        result = runner.run()

        # Verify the prompt was built with portfolio data
        call_args = caller.call_args_list[0]
        prompt = call_args[0][1]
        assert "$8,000.00" in prompt
        assert "btc-100k" in prompt

    def test_market_price_yes_populated(self):
        """market_price_yes is set from first outcome price on success."""
        config = _make_config("mini")
        fetcher = MagicMock(side_effect=lambda slug, **kw: _make_market(slug=slug))
        caller = MagicMock(return_value=_make_decision_json())

        runner = Runner(config, market_fetcher=fetcher, model_caller=caller)
        result = runner.run()

        for r in result.market_results:
            assert r.market_price_yes == 0.65

    def test_market_price_yes_none_on_fetch_error(self):
        """market_price_yes is None when market fetch fails."""
        config = _make_config("mini")
        fetcher = MagicMock(side_effect=MarketInfoError("not found"))
        caller = MagicMock()

        runner = Runner(config, market_fetcher=fetcher, model_caller=caller)
        result = runner.run()

        for r in result.market_results:
            assert r.market_price_yes is None

    def test_market_price_yes_set_on_skip(self):
        """market_price_yes is set even when market is skipped (closed)."""
        config = _make_config("mini")
        fetcher = MagicMock(return_value=_make_market(closed=True))
        caller = MagicMock()

        runner = Runner(config, market_fetcher=fetcher, model_caller=caller)
        result = runner.run()

        for r in result.market_results:
            assert r.market_price_yes == 0.65

    def test_agent_balance_failure_uses_budget(self):
        """If agent.balance() fails, use config budget."""
        config = _make_config("mini")
        fetcher = MagicMock(return_value=_make_market())
        caller = MagicMock(return_value=_make_decision_json(action="skip", amount=0))

        agent = MagicMock()
        agent.balance.side_effect = RuntimeError("API down")
        agent.portfolio.side_effect = RuntimeError("API down")
        agent.stats.return_value = {}

        runner = Runner(config, agent=agent, market_fetcher=fetcher, model_caller=caller)
        result = runner.run()

        # Should still complete without errors (balance fallback)
        assert result.completed_count == 3

    def test_multi_round(self):
        """n_rounds=2 produces 6 results for 3 markets."""
        config = RunConfig(
            llm=LLMConfig(model="test-model"),
            market_set="mini",
            n_rounds=2,
        )
        fetcher = MagicMock(side_effect=lambda slug, **kw: _make_market(slug=slug))
        caller = MagicMock(return_value=_make_decision_json())

        runner = Runner(config, market_fetcher=fetcher, model_caller=caller)
        result = runner.run()

        assert len(result.market_results) == 6  # 3 markets * 2 rounds
        assert len(result.rounds) == 2
        assert result.rounds[0].round_number == 1
        assert result.rounds[1].round_number == 2
        assert len(result.rounds[0].market_results) == 3
        assert len(result.rounds[1].market_results) == 3

    def test_multi_round_with_delay(self, monkeypatch):
        """Round delay is applied between rounds."""
        config = RunConfig(
            llm=LLMConfig(model="test-model"),
            market_set="mini",
            n_rounds=2,
            round_delay_seconds=0.01,
        )
        fetcher = MagicMock(side_effect=lambda slug, **kw: _make_market(slug=slug))
        caller = MagicMock(return_value=_make_decision_json())

        sleep_calls: list[float] = []
        monkeypatch.setattr("pm_benchmark.runner.time.sleep", lambda s: sleep_calls.append(s))

        runner = Runner(config, market_fetcher=fetcher, model_caller=caller)
        result = runner.run()

        assert len(sleep_calls) == 1  # 1 delay between 2 rounds
        assert sleep_calls[0] == 0.01
        assert len(result.market_results) == 6

    def test_single_round_backward_compat(self):
        """n_rounds=1 (default) works like before."""
        config = _make_config("mini")
        fetcher = MagicMock(side_effect=lambda slug, **kw: _make_market(slug=slug))
        caller = MagicMock(return_value=_make_decision_json())

        runner = Runner(config, market_fetcher=fetcher, model_caller=caller)
        result = runner.run()

        assert len(result.market_results) == 3
        assert len(result.rounds) == 1
        assert result.rounds[0].round_number == 1


    def test_round_2_prompt_includes_context(self):
        """In round 2, the prompt includes round context with previous actions."""
        config = RunConfig(
            llm=LLMConfig(model="test-model"),
            market_set="mini",
            n_rounds=2,
        )
        fetcher = MagicMock(side_effect=lambda slug, **kw: _make_market(slug=slug))
        prompts_seen: list[str] = []

        def mock_caller(llm_config, prompt, system_prompt):
            prompts_seen.append(prompt)
            return _make_decision_json()

        runner = Runner(config, market_fetcher=fetcher, model_caller=mock_caller)
        runner.run()

        # Round 1 prompts (first 3) should have "Round 1 of 2" but no previous actions
        assert "Round 1 of 2" in prompts_seen[0]
        assert "Previous" not in prompts_seen[0]

        # Round 2 prompts (last 3) should have "Round 2 of 2" and previous actions
        assert "Round 2 of 2" in prompts_seen[3]
        assert "Previous Actions" in prompts_seen[3]
        assert "buy_yes" in prompts_seen[3]


    def test_blind_mode_hides_prices(self):
        """In blind mode, prices are hidden in the prompt."""
        config = RunConfig(
            llm=LLMConfig(model="test-model"),
            market_set="mini",
            blind_mode=True,
        )
        fetcher = MagicMock(side_effect=lambda slug, **kw: _make_market(slug=slug))
        prompts_seen: list[str] = []

        def mock_caller(llm_config, prompt, system_prompt):
            prompts_seen.append(prompt)
            return _make_decision_json()

        runner = Runner(config, market_fetcher=fetcher, model_caller=mock_caller)
        runner.run()

        assert "[hidden" in prompts_seen[0]
        assert "0.65" not in prompts_seen[0]


class TestRoundResult:
    def test_defaults(self):
        r = RoundResult(round_number=1)
        assert r.round_number == 1
        assert r.market_results == []
        assert r.round_latency_seconds == 0.0
