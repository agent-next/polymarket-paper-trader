"""Evaluation loop orchestrator."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from pm_benchmark.config import RunConfig
from pm_benchmark.market_info import MarketInfo, MarketInfoError, fetch_market_info
from pm_benchmark.market_set import load_market_set
from pm_benchmark.prompts import build_analysis_prompt, build_round_context, build_system_prompt
from pm_benchmark.providers import LLMError, MarketDecision, parse_decision, query_model


class AgentProtocol(Protocol):
    """Minimal interface we need from the leaderboard Agent SDK."""

    def buy(self, market_slug: str, outcome: str, amount: float) -> dict: ...
    def sell(self, market_slug: str, outcome: str, shares: float) -> dict: ...
    def portfolio(self) -> list[dict]: ...
    def balance(self) -> dict: ...
    def stats(self) -> dict: ...


@dataclass
class MarketResult:
    """Result for a single market evaluation."""

    slug: str
    question: str
    model_probability: float | None = None
    action: str = "skip"
    confidence: str = ""
    amount_usd: float = 0.0
    reasoning: str = ""
    trade_result: dict | None = None
    latency_seconds: float = 0.0
    error: str | None = None
    skipped_reason: str | None = None
    market_price_yes: float | None = None


@dataclass
class RoundResult:
    """Results for a single evaluation round."""

    round_number: int
    market_results: list[MarketResult] = field(default_factory=list)
    round_latency_seconds: float = 0.0


@dataclass
class EvalRun:
    """Full evaluation run results."""

    model: str
    market_set: str
    budget: float
    market_results: list[MarketResult] = field(default_factory=list)
    rounds: list[RoundResult] = field(default_factory=list)
    agent_stats: dict = field(default_factory=dict)
    total_latency_seconds: float = 0.0
    timestamp: str = ""

    @property
    def completed_count(self) -> int:
        return sum(1 for r in self.market_results if r.error is None and r.skipped_reason is None)

    @property
    def error_count(self) -> int:
        return sum(1 for r in self.market_results if r.error is not None)

    @property
    def skipped_count(self) -> int:
        return sum(1 for r in self.market_results if r.skipped_reason is not None)


class Runner:
    """Orchestrates a benchmark evaluation run."""

    def __init__(
        self,
        config: RunConfig,
        *,
        agent: AgentProtocol | None = None,
        market_fetcher: Any = None,
        model_caller: Any = None,
    ) -> None:
        self._config = config
        self._agent = agent
        self._fetch_market = market_fetcher or fetch_market_info
        self._query_model = model_caller or query_model
        self._previous_actions: list[dict] = []
        self._current_round = 1
        self._market_trade_counts: dict[str, int] = {}

    def run(self) -> EvalRun:
        """Execute the full evaluation loop."""
        market_set = load_market_set(self._config.market_set)
        system_prompt = build_system_prompt()
        run_start = time.time()
        self._market_trade_counts = {}

        eval_run = EvalRun(
            model=self._config.llm.model,
            market_set=self._config.market_set,
            budget=self._config.budget,
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )

        n_rounds = self._config.n_rounds
        for round_num in range(1, n_rounds + 1):
            self._current_round = round_num
            round_start = time.time()
            round_result = RoundResult(round_number=round_num)
            round_actions: list[dict] = []

            for entry in market_set.markets:
                result = self._evaluate_market(entry.slug, system_prompt)
                round_result.market_results.append(result)
                eval_run.market_results.append(result)
                round_actions.append({
                    "slug": result.slug,
                    "action": result.action,
                    "amount_usd": result.amount_usd,
                    "probability": result.model_probability,
                })

            self._previous_actions = round_actions
            round_result.round_latency_seconds = time.time() - round_start
            eval_run.rounds.append(round_result)

            # Delay between rounds (skip after last round)
            if round_num < n_rounds and self._config.round_delay_seconds > 0:
                time.sleep(self._config.round_delay_seconds)

        eval_run.total_latency_seconds = time.time() - run_start

        # Fetch final agent stats if agent is available
        if self._agent is not None:
            try:
                eval_run.agent_stats = self._agent.stats()
            except Exception:
                pass

        return eval_run

    def _check_timeout(self, start_time: float) -> None:
        elapsed = time.time() - start_time
        if elapsed > self._config.timeout:
            raise TimeoutError(
                f"Per-market timeout exceeded ({self._config.timeout:.2f}s)"
            )

    def _query_model_with_compat(self, prompt: str, system_prompt: str) -> str:
        """Call model with timeout/seed, fallback for legacy callables."""
        try:
            return self._query_model(
                self._config.llm,
                prompt,
                system_prompt,
                timeout=self._config.timeout,
                seed=self._config.seed,
            )
        except TypeError as e:
            msg = str(e)
            if "unexpected keyword argument" in msg:
                return self._query_model(self._config.llm, prompt, system_prompt)
            raise

    def _evaluate_market(self, slug: str, system_prompt: str) -> MarketResult:
        """Evaluate a single market."""
        start = time.time()
        result = MarketResult(slug=slug, question="")

        # 1. Fetch market info
        try:
            market = self._fetch_market(slug)
        except MarketInfoError as e:
            result.error = f"Market fetch failed: {e}"
            result.latency_seconds = time.time() - start
            return result
        except Exception as e:
            result.error = f"Market fetch failed: {e}"
            result.latency_seconds = time.time() - start
            return result

        try:
            self._check_timeout(start)
        except TimeoutError as e:
            result.error = str(e)
            result.latency_seconds = time.time() - start
            return result

        result.question = market.question
        if market.outcome_prices:
            result.market_price_yes = market.outcome_prices[0]

        # Skip closed/inactive markets
        if market.closed or not market.active:
            result.skipped_reason = "Market closed or inactive"
            result.latency_seconds = time.time() - start
            return result

        # 2. Get portfolio context
        balance = self._config.budget
        portfolio_summary = "No open positions."
        if self._agent is not None:
            try:
                bal = self._agent.balance()
                balance = bal.get("cash", self._config.budget)
                positions = self._agent.portfolio()
                if positions:
                    lines = [
                        f"  - {p.get('market_slug', '?')}: "
                        f"{p.get('shares', 0)} shares ({p.get('outcome', '?')})"
                        for p in positions
                    ]
                    portfolio_summary = "**Open Positions:**\n" + "\n".join(lines)
            except Exception:
                pass

        # 3. Build prompt and query LLM
        position_size = balance * (self._config.position_size_pct / 100.0)
        rc = ""
        if self._config.n_rounds > 1:
            rc = build_round_context(
                self._current_round,
                self._config.n_rounds,
                self._previous_actions,
            )
        prompt = build_analysis_prompt(
            market, balance, portfolio_summary, position_size,
            round_context=rc, blind_mode=self._config.blind_mode,
        )

        try:
            raw_response = self._query_model_with_compat(prompt, system_prompt)
            self._check_timeout(start)
        except TimeoutError as e:
            result.error = str(e)
            result.latency_seconds = time.time() - start
            return result
        except LLMError as e:
            result.error = f"LLM call failed: {e}"
            result.latency_seconds = time.time() - start
            return result

        # 4. Parse decision
        try:
            decision = parse_decision(raw_response)
        except LLMError as e:
            result.error = f"Parse failed: {e}"
            result.latency_seconds = time.time() - start
            return result

        result.model_probability = decision.probability
        result.action = decision.action
        result.confidence = decision.confidence
        result.amount_usd = decision.amount_usd
        result.reasoning = decision.reasoning

        # 5. Execute trade
        if decision.action != "skip" and self._agent is not None:
            trades_done = self._market_trade_counts.get(slug, 0)
            if trades_done >= self._config.max_trades_per_market:
                result.skipped_reason = "Max trades per market reached"
                result.latency_seconds = time.time() - start
                return result
            try:
                outcome = "yes" if decision.action == "buy_yes" else "no"
                trade = self._agent.buy(slug, outcome, decision.amount_usd)
                result.trade_result = trade
                self._market_trade_counts[slug] = trades_done + 1
                self._check_timeout(start)
            except TimeoutError as e:
                result.error = str(e)
            except Exception as e:
                result.error = f"Trade failed: {e}"

        result.latency_seconds = time.time() - start
        return result
