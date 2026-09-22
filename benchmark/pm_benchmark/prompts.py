"""Prompt templates for LLM market analysis."""
from __future__ import annotations

from pm_benchmark.market_info import MarketInfo

_SYSTEM_PROMPT = """\
You are an expert prediction market analyst. Your task is to analyze \
prediction markets and make trading decisions.

For each market, you must provide:
1. Your estimated probability (0.0 to 1.0) that the market resolves YES
2. A trading action: buy_yes, buy_no, or skip
3. Your confidence level (low, medium, high)
4. Dollar amount to trade (if not skipping)
5. Brief reasoning for your decision

Respond in JSON format:
{
    "probability": 0.65,
    "action": "buy_yes",
    "confidence": "medium",
    "amount_usd": 250.00,
    "reasoning": "Your reasoning here"
}

Rules:
- probability must be between 0.0 and 1.0
- action must be one of: buy_yes, buy_no, skip
- confidence must be one of: low, medium, high
- amount_usd must be positive (ignored if action is skip)
- Be concise in reasoning (1-3 sentences)
- Consider market liquidity and current prices when sizing positions
- If uncertain, prefer skip over random bets\
"""


def build_system_prompt() -> str:
    """Return the system prompt for market analysis."""
    return _SYSTEM_PROMPT


def build_round_context(
    round_number: int,
    total_rounds: int,
    previous_actions: list[dict],
) -> str:
    """Build round context for multi-round evaluation.

    Args:
        round_number: Current round (1-indexed).
        total_rounds: Total number of rounds.
        previous_actions: List of dicts with slug, action, amount_usd, probability.
    """
    lines = [f"## Round {round_number} of {total_rounds}", ""]
    if previous_actions:
        lines.append("**Previous Actions:**")
        for a in previous_actions:
            action = a.get("action", "skip")
            slug = a.get("slug", "?")
            if action == "skip":
                lines.append(f"- {slug}: skipped")
            else:
                amount = a.get("amount_usd", 0)
                prob = a.get("probability", "?")
                lines.append(f"- {slug}: {action} ${amount:.0f} (p={prob})")
        lines.append("")
    return "\n".join(lines)


def build_analysis_prompt(
    market: MarketInfo,
    balance: float,
    portfolio_summary: str,
    position_size_suggestion: float,
    *,
    round_context: str = "",
    blind_mode: bool = False,
) -> str:
    """Build a market analysis prompt with context.

    Args:
        market: Market info from Gamma API.
        balance: Current cash balance.
        portfolio_summary: Human-readable portfolio summary.
        position_size_suggestion: Suggested max trade size in USD.
        round_context: Optional round context for multi-round evaluation.
        blind_mode: If True, hide market prices from prompt.
    """
    if blind_mode:
        prices_str = "[hidden — blind mode]"
    else:
        prices_str = ", ".join(
            f"{o}: ${p:.2f}" for o, p in zip(market.outcomes, market.outcome_prices)
        )

    parts = []
    if round_context:
        parts.append(round_context)

    parts.append(
        f"## Market Analysis Request\n\n"
        f"**Question:** {market.question}\n\n"
        f"**Description:** {market.description}\n\n"
        f"**Current Prices:** {prices_str}\n"
        f"**Volume:** ${market.volume:,.0f}\n"
        f"**Liquidity:** ${market.liquidity:,.0f}\n"
        f"**End Date:** {market.end_date}\n\n"
        f"## Your Portfolio\n\n"
        f"**Cash Balance:** ${balance:,.2f}\n"
        f"{portfolio_summary}\n\n"
        f"**Suggested Max Position:** ${position_size_suggestion:,.2f}\n\n"
        f"Analyze this market and provide your trading decision as JSON."
    )

    return "\n\n".join(parts)
