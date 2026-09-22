"""Multi-dimensional scoring for benchmark evaluations."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from pm_benchmark.runner import EvalRun, MarketResult

SCORING_METHODOLOGY = """\
## Scoring Methodology

### Brier Score (lower is better, 0–1)
Mean squared error between model probability and actual outcome.
Uses market price as proxy truth when markets are unresolved.

### Alpha Score (negative is better)
alpha = model_brier - market_brier. Measures whether the model
outperforms market consensus. Negative = model beats market.

### Calibration Error (lower is better, 0–1)
Expected Calibration Error (ECE). Bins predictions and compares
average predicted probability vs average actual outcome.

### Composite Score (higher is better, 0–100)

**LLM-only mode** (no trades):
  Brier: 35%, Alpha: 30%, Calibration: 20%, Skip Rate: 15%

**Agent mode** (with trades):
  ROI: 20%, Brier: 20%, Alpha: 15%, Sharpe: 10%, Win Rate: 10%,
  Calibration: 10%, Max Drawdown: 10%, Skip Rate: 5%

### Normalization
- Brier/Calibration: inverted (1 - score)
- ROI/Sharpe: sigmoid normalization
- Alpha: mapped from [-1, 1] to [1, 0] then halved to [0, 1]
- Win Rate/Skip Rate: used directly (already 0–1)
- Max Drawdown: inverted (1 - drawdown)
"""

_WEIGHTS_FULL = {
    "roi": 0.20, "brier": 0.20, "alpha": 0.15, "sharpe": 0.10,
    "win": 0.10, "calibration": 0.10, "drawdown": 0.10, "skip": 0.05,
}
_WEIGHTS_LLM_ONLY = {
    "brier": 0.35, "alpha": 0.30, "calibration": 0.20, "skip": 0.15,
}


def _validate_weights() -> None:
    """Assert weight profiles sum to 1.0."""
    assert abs(sum(_WEIGHTS_FULL.values()) - 1.0) < 1e-9, "Full weights must sum to 1.0"
    assert abs(sum(_WEIGHTS_LLM_ONLY.values()) - 1.0) < 1e-9, "LLM-only weights must sum to 1.0"


_validate_weights()


@dataclass(frozen=True)
class BenchmarkScores:
    """Computed scores for a benchmark run."""

    brier_score: float
    calibration_error: float
    composite_score: float
    roi_pct: float
    sharpe_ratio: float
    win_rate: float
    max_drawdown: float
    skip_rate: float
    alpha_score: float = 0.0
    details: dict = field(default_factory=dict)


def compute_brier_score(
    predictions: list[tuple[float, float]],
) -> float:
    """Compute mean Brier score.

    Args:
        predictions: List of (predicted_probability, actual_outcome).
            actual_outcome is 1.0 for Yes, 0.0 for No.
            When markets are unresolved, use market price as proxy.

    Returns:
        Mean Brier score (lower is better, 0 = perfect).
    """
    if not predictions:
        return 1.0  # worst possible score
    total = sum((pred - actual) ** 2 for pred, actual in predictions)
    return total / len(predictions)


def compute_calibration_error(
    predictions: list[tuple[float, float]],
    n_bins: int = 10,
) -> float:
    """Compute Expected Calibration Error (ECE).

    Bins predictions by predicted probability and compares
    average prediction vs average outcome in each bin.

    Returns:
        ECE value (lower is better, 0 = perfectly calibrated).
    """
    if not predictions:
        return 1.0

    bins: list[list[tuple[float, float]]] = [[] for _ in range(n_bins)]
    for pred, actual in predictions:
        idx = min(int(pred * n_bins), n_bins - 1)
        bins[idx].append((pred, actual))

    ece = 0.0
    total_count = len(predictions)
    for bin_items in bins:
        if not bin_items:
            continue
        avg_pred = sum(p for p, _ in bin_items) / len(bin_items)
        avg_actual = sum(a for _, a in bin_items) / len(bin_items)
        ece += (len(bin_items) / total_count) * abs(avg_pred - avg_actual)

    return ece


def compute_alpha_score(
    triples: list[tuple[float, float, float]],
) -> float:
    """Compute alpha: model_brier - market_brier.

    Args:
        triples: List of (model_prob, market_price, actual_outcome).
            Negative alpha = model beats market (good).

    Returns:
        Alpha score. 0.0 if empty.
    """
    if not triples:
        return 0.0
    model_brier = sum((m - a) ** 2 for m, _, a in triples) / len(triples)
    market_brier = sum((p - a) ** 2 for _, p, a in triples) / len(triples)
    return model_brier - market_brier


def compute_composite_score(
    *,
    roi_pct: float,
    brier: float,
    sharpe: float,
    win_rate: float,
    calibration: float,
    max_drawdown: float,
    skip_rate: float,
    alpha: float = 0.0,
    agent_mode: bool = False,
) -> float:
    """Compute weighted composite score (0-100, higher is better).

    Two weight profiles:
        agent_mode=True (full):  roi=0.20, brier=0.20, alpha=0.15, sharpe=0.10,
                                 win=0.10, cal=0.10, dd=0.10, skip=0.05
        agent_mode=False (LLM-only): brier=0.35, alpha=0.30, cal=0.20, skip=0.15
    """
    # Normalize each metric to 0-1 (higher = better)
    roi_score = _sigmoid(roi_pct, center=0, scale=20)
    brier_score = 1.0 - min(brier, 1.0)  # brier 0=perfect, 1=worst
    sharpe_score = _sigmoid(sharpe, center=0, scale=1)
    win_score = win_rate  # already 0-1
    cal_score = 1.0 - min(calibration, 1.0)  # ECE 0=perfect
    dd_score = 1.0 - min(max_drawdown, 1.0)  # drawdown 0=none, 1=total loss
    skip_score = 1.0 - min(skip_rate, 1.0)  # 0 skip rate = best
    # Alpha: clamp to [-1, 1], then invert (negative alpha = good → high score)
    alpha_score = 1.0 - min(max(alpha, -1.0), 1.0)  # maps -1→2, 0→1, 1→0
    alpha_score = alpha_score / 2.0  # normalize to 0-1 range

    if agent_mode:
        w = _WEIGHTS_FULL
        composite = (
            w["roi"] * roi_score
            + w["brier"] * brier_score
            + w["alpha"] * alpha_score
            + w["sharpe"] * sharpe_score
            + w["win"] * win_score
            + w["calibration"] * cal_score
            + w["drawdown"] * dd_score
            + w["skip"] * skip_score
        )
    else:
        w = _WEIGHTS_LLM_ONLY
        composite = (
            w["brier"] * brier_score
            + w["alpha"] * alpha_score
            + w["calibration"] * cal_score
            + w["skip"] * skip_score
        )

    return round(composite * 100, 1)


def _resolve_actual(
    r: MarketResult,
    resolved_outcomes: dict[str, float] | None,
) -> float:
    """Determine actual outcome for a market result."""
    if resolved_outcomes and r.slug in resolved_outcomes:
        return resolved_outcomes[r.slug]
    if r.market_price_yes is not None:
        return r.market_price_yes
    return 0.5


def score_run(
    eval_run: EvalRun,
    *,
    resolved_outcomes: dict[str, float] | None = None,
) -> BenchmarkScores:
    """Score a completed evaluation run.

    Args:
        eval_run: Completed evaluation run with market results.
        resolved_outcomes: Optional mapping of slug -> actual outcome (1.0=Yes, 0.0=No).
            If None, uses market prices as proxy truth.

    Returns:
        BenchmarkScores with all computed metrics.
    """
    results = [r for r in eval_run.market_results if r.model_probability is not None]

    # Build prediction pairs for Brier/calibration and triples for alpha
    predictions: list[tuple[float, float]] = []
    alpha_triples: list[tuple[float, float, float]] = []
    for r in results:
        assert r.model_probability is not None
        market_price = r.market_price_yes if r.market_price_yes is not None else 0.5
        actual = _resolve_actual(r, resolved_outcomes)
        predictions.append((r.model_probability, actual))
        alpha_triples.append((r.model_probability, market_price, actual))

    brier = compute_brier_score(predictions)
    calibration = compute_calibration_error(predictions)
    alpha = compute_alpha_score(alpha_triples)

    # Extract agent stats
    stats = eval_run.agent_stats
    roi_pct = float(stats.get("roi_pct", 0.0))
    sharpe = float(stats.get("sharpe_ratio", 0.0))
    win_rate_val = float(stats.get("win_rate", 0.0))
    max_dd = float(stats.get("max_drawdown", 0.0))

    total = len(eval_run.market_results)
    skip_rate = eval_run.skipped_count / total if total > 0 else 0.0

    has_trades = any(r.trade_result is not None for r in eval_run.market_results)
    composite = compute_composite_score(
        roi_pct=roi_pct,
        brier=brier,
        sharpe=sharpe,
        win_rate=win_rate_val,
        calibration=calibration,
        max_drawdown=max_dd,
        skip_rate=skip_rate,
        alpha=alpha,
        agent_mode=has_trades,
    )

    details: dict = {
        "n_predictions": len(predictions),
        "n_total": total,
        "n_completed": eval_run.completed_count,
        "n_errors": eval_run.error_count,
        "n_skipped": eval_run.skipped_count,
    }

    # Per-round Brier if multiple rounds
    if len(eval_run.rounds) > 1:
        per_round: list[dict] = []
        for rnd in eval_run.rounds:
            rnd_preds: list[tuple[float, float]] = []
            for r in rnd.market_results:
                if r.model_probability is None:
                    continue
                a = _resolve_actual(r, resolved_outcomes)
                rnd_preds.append((r.model_probability, a))
            rnd_brier = compute_brier_score(rnd_preds)
            per_round.append({
                "round": rnd.round_number,
                "brier_score": round(rnd_brier, 4),
                "n_predictions": len(rnd_preds),
            })
        details["per_round"] = per_round

    return BenchmarkScores(
        brier_score=round(brier, 4),
        calibration_error=round(calibration, 4),
        composite_score=composite,
        roi_pct=round(roi_pct, 2),
        sharpe_ratio=round(sharpe, 2),
        win_rate=round(win_rate_val, 4),
        max_drawdown=round(max_dd, 4),
        skip_rate=round(skip_rate, 4),
        alpha_score=round(alpha, 4),
        details=details,
    )


def _sigmoid(x: float, center: float = 0, scale: float = 1) -> float:
    """Sigmoid normalization to 0-1 range."""
    z = (x - center) / scale if scale != 0 else 0.0
    # Clamp to prevent overflow
    z = max(-500, min(500, z))
    return 1.0 / (1.0 + math.exp(-z))
