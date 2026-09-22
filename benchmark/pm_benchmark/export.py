"""HuggingFace JSONL export for benchmark results."""
from __future__ import annotations

import json
from pathlib import Path

from pm_benchmark.runner import EvalRun
from pm_benchmark.scoring import BenchmarkScores


def generate_huggingface_jsonl(eval_run: EvalRun, scores: BenchmarkScores) -> str:
    """Generate HuggingFace-compatible JSONL string.

    Each line is a JSON object representing one market evaluation.
    Final line is a summary record.
    """
    lines: list[str] = []

    for r in eval_run.market_results:
        record = {
            "type": "market_result",
            "model": eval_run.model,
            "market_set": eval_run.market_set,
            "slug": r.slug,
            "question": r.question,
            "model_probability": r.model_probability,
            "action": r.action,
            "confidence": r.confidence,
            "amount_usd": r.amount_usd,
            "reasoning": r.reasoning,
            "latency_seconds": round(r.latency_seconds, 3),
            "error": r.error,
            "skipped_reason": r.skipped_reason,
            "market_price_yes": r.market_price_yes,
        }
        lines.append(json.dumps(record))

    summary = {
        "type": "summary",
        "model": eval_run.model,
        "market_set": eval_run.market_set,
        "budget": eval_run.budget,
        "timestamp": eval_run.timestamp,
        "composite_score": scores.composite_score,
        "brier_score": scores.brier_score,
        "calibration_error": scores.calibration_error,
        "roi_pct": scores.roi_pct,
        "sharpe_ratio": scores.sharpe_ratio,
        "win_rate": scores.win_rate,
        "max_drawdown": scores.max_drawdown,
        "skip_rate": scores.skip_rate,
        "alpha_score": scores.alpha_score,
        "total_markets": len(eval_run.market_results),
        "completed": eval_run.completed_count,
        "errors": eval_run.error_count,
        "skipped": eval_run.skipped_count,
    }
    lines.append(json.dumps(summary))

    return "\n".join(lines) + "\n"


def save_huggingface_jsonl(
    eval_run: EvalRun,
    scores: BenchmarkScores,
    output_path: Path,
) -> Path:
    """Save HuggingFace JSONL to file.

    Returns:
        Path to the saved file.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(generate_huggingface_jsonl(eval_run, scores))
    return output_path
