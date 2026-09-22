"""JSON and Markdown report generation."""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from pm_benchmark.runner import EvalRun
from pm_benchmark.scoring import SCORING_METHODOLOGY, BenchmarkScores


def generate_json_report(eval_run: EvalRun, scores: BenchmarkScores) -> str:
    """Generate a JSON report string."""
    report = {
        "model": eval_run.model,
        "market_set": eval_run.market_set,
        "budget": eval_run.budget,
        "timestamp": eval_run.timestamp,
        "scores": asdict(scores),
        "summary": {
            "total_markets": len(eval_run.market_results),
            "completed": eval_run.completed_count,
            "errors": eval_run.error_count,
            "skipped": eval_run.skipped_count,
            "total_latency_seconds": round(eval_run.total_latency_seconds, 2),
        },
        "market_results": [asdict(r) for r in eval_run.market_results],
    }
    return json.dumps(report, indent=2)


def generate_markdown_report(eval_run: EvalRun, scores: BenchmarkScores) -> str:
    """Generate a Markdown report string."""
    lines = [
        f"# Benchmark Report: {eval_run.model}",
        "",
        f"**Market Set:** {eval_run.market_set}  ",
        f"**Budget:** ${eval_run.budget:,.2f}  ",
        f"**Timestamp:** {eval_run.timestamp}  ",
        "",
        "## Scores",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Composite Score | **{scores.composite_score}** / 100 |",
        f"| Brier Score | {scores.brier_score} |",
        f"| Calibration Error | {scores.calibration_error} |",
        f"| ROI | {scores.roi_pct}% |",
        f"| Sharpe Ratio | {scores.sharpe_ratio} |",
        f"| Win Rate | {scores.win_rate:.1%} |",
        f"| Max Drawdown | {scores.max_drawdown:.1%} |",
        f"| Skip Rate | {scores.skip_rate:.1%} |",
        f"| Alpha Score | {scores.alpha_score} |",
        "",
        "## Summary",
        "",
        f"- **Total Markets:** {len(eval_run.market_results)}",
        f"- **Completed:** {eval_run.completed_count}",
        f"- **Errors:** {eval_run.error_count}",
        f"- **Skipped:** {eval_run.skipped_count}",
        f"- **Total Latency:** {eval_run.total_latency_seconds:.1f}s",
        "",
        "## Market Details",
        "",
        "| Market | Probability | Market Price | Action | Amount | Status |",
        "|--------|------------|-------------|--------|--------|--------|",
    ]

    for r in eval_run.market_results:
        prob = f"{r.model_probability:.2f}" if r.model_probability is not None else "-"
        mprice = f"{r.market_price_yes:.2f}" if r.market_price_yes is not None else "-"
        amount = f"${r.amount_usd:,.0f}" if r.amount_usd > 0 else "-"
        if r.error:
            status = f"Error: {r.error[:50]}"
        elif r.skipped_reason:
            status = f"Skipped: {r.skipped_reason}"
        else:
            status = "OK"
        lines.append(f"| {r.slug} | {prob} | {mprice} | {r.action} | {amount} | {status} |")

    lines.append("")
    lines.append(SCORING_METHODOLOGY)
    return "\n".join(lines)


def save_reports(
    eval_run: EvalRun,
    scores: BenchmarkScores,
    output_dir: Path,
) -> dict[str, Path]:
    """Save JSON and Markdown reports to output directory.

    Returns:
        Dict mapping format name to file path.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    json_path = output_dir / "report.json"
    json_path.write_text(generate_json_report(eval_run, scores))

    md_path = output_dir / "report.md"
    md_path.write_text(generate_markdown_report(eval_run, scores))

    return {"json": json_path, "markdown": md_path}
