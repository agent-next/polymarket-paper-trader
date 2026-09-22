"""Click CLI for polymarket-benchmark."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from pm_benchmark.config import LLMConfig, LeaderboardConfig, RunConfig
from pm_benchmark.history import append_to_history, load_history
from pm_benchmark.market_info import MarketInfoError, fetch_resolution
from pm_benchmark.market_set import list_market_sets, load_market_set
from pm_benchmark.report import generate_json_report, generate_markdown_report, save_reports
from pm_benchmark.runner import EvalRun, MarketResult, Runner
from pm_benchmark.scoring import score_run


def _ok(data: object) -> str:
    return json.dumps({"ok": True, "data": data}, indent=2)


def _err(error: str, code: str = "ERROR") -> str:
    return json.dumps({"ok": False, "error": error, "code": code}, indent=2)


def _load_eval_run(data: dict) -> EvalRun:
    """Deserialize an EvalRun from a JSON report dict."""
    eval_run = EvalRun(
        model=data["model"],
        market_set=data["market_set"],
        budget=data["budget"],
        timestamp=data.get("timestamp", ""),
    )
    for r in data.get("market_results", []):
        eval_run.market_results.append(MarketResult(**r))
    return eval_run


@click.group()
@click.version_option(package_name="polymarket-benchmark")
def main() -> None:
    """polymarket-benchmark — LLM evaluation on prediction markets."""


@main.command("run")
@click.option("--model", required=True, help="litellm model string")
@click.option("--market-set", default="mini", help="Market set name or YAML path")
@click.option("--budget", type=float, default=10_000.0, help="Starting budget")
@click.option("--output-dir", type=click.Path(path_type=Path), default=Path("./results"))
@click.option("--temperature", type=float, default=0.3)
@click.option("--leaderboard-url", default=None, help="Leaderboard server URL (enables trading)")
@click.option("--agent-name", default=None, help="Agent name (auto from model if omitted)")
@click.option("--account-name", default="default", help="Leaderboard account name")
@click.option("--api-key", default=None, help="Reconnect with existing agent API key")
@click.option("--verbose", is_flag=True, default=False)
@click.option("--max-trades-per-market", type=int, default=1, show_default=True)
@click.option("--timeout", type=float, default=60.0, show_default=True, help="Per-market timeout in seconds")
@click.option("--seed", type=int, default=None, help="Random seed for reproducibility")
@click.option("--n-rounds", type=int, default=1, help="Number of evaluation rounds")
@click.option("--blind", is_flag=True, default=False, help="Hide market prices from LLM")
def run_cmd(
    model: str,
    market_set: str,
    budget: float,
    output_dir: Path,
    temperature: float,
    leaderboard_url: str | None,
    agent_name: str | None,
    account_name: str,
    api_key: str | None,
    verbose: bool,
    max_trades_per_market: int,
    timeout: float,
    seed: int | None,
    n_rounds: int,
    blind: bool,
) -> None:
    """Run a benchmark evaluation.

    Without --leaderboard-url: LLM-only mode (no trades, scoring only).
    With --leaderboard-url: full mode (trades execute on leaderboard).
    """
    config = RunConfig(
        llm=LLMConfig(model=model, temperature=temperature),
        leaderboard=LeaderboardConfig(
            base_url=leaderboard_url or "http://localhost:8000",
            agent_name=agent_name,
            account_name=account_name,
        ),
        market_set=market_set,
        budget=budget,
        max_trades_per_market=max_trades_per_market,
        timeout=timeout,
        seed=seed,
        output_dir=output_dir,
        n_rounds=n_rounds,
        blind_mode=blind,
    )

    agent = None
    if leaderboard_url:
        from pm_benchmark.agent_bridge import AgentBridgeError, create_agent
        try:
            agent = create_agent(
                leaderboard_url,
                model,
                agent_name=agent_name,
                account_name=account_name,
                api_key=api_key,
            )
        except AgentBridgeError as e:
            click.echo(_err(str(e), "AGENT_ERROR"))
            sys.exit(1)

    if verbose:
        click.echo(f"Model: {model}")
        click.echo(f"Market set: {market_set}")
        click.echo(f"Budget: ${budget:,.2f}")
        mode = "full (trading on leaderboard)" if agent else "LLM-only (no trades)"
        click.echo(f"Mode: {mode}")
        click.echo("")

    try:
        runner = Runner(config, agent=agent)
        eval_run = runner.run()
        scores = score_run(eval_run)
        paths = save_reports(eval_run, scores, output_dir)

        # Auto-append to history
        history_path = output_dir / "history.jsonl"
        append_to_history(eval_run, scores, history_path)

        result: dict = {
            "composite_score": scores.composite_score,
            "brier_score": scores.brier_score,
            "alpha_score": scores.alpha_score,
            "completed": eval_run.completed_count,
            "errors": eval_run.error_count,
            "skipped": eval_run.skipped_count,
            "reports": {k: str(v) for k, v in paths.items()},
        }
        if agent and hasattr(agent, "api_key"):
            result["agent_api_key"] = agent.api_key
        click.echo(_ok(result))
    except Exception as e:
        click.echo(_err(str(e)))
        sys.exit(1)
    finally:
        if agent and hasattr(agent, "close"):
            agent.close()


@main.command("list-sets")
def list_sets_cmd() -> None:
    """List available market sets."""
    sets = list_market_sets()
    result = []
    for name in sets:
        try:
            ms = load_market_set(name)
            result.append({
                "name": ms.name,
                "description": ms.description,
                "markets": len(ms.markets),
            })
        except Exception:
            result.append({"name": name, "description": "?", "markets": 0})
    click.echo(_ok(result))


@main.command("score")
@click.option("--results", required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--resolved", type=click.Path(exists=True, path_type=Path), default=None)
def score_cmd(results: Path, resolved: Path | None) -> None:
    """Re-score a previous run with optional resolved outcomes."""
    try:
        data = json.loads(results.read_text())
        eval_run = _load_eval_run(data)
        eval_run.agent_stats = data.get("scores", {}).get("details", {})

        resolved_outcomes = None
        if resolved:
            resolved_outcomes = json.loads(resolved.read_text())

        scores = score_run(eval_run, resolved_outcomes=resolved_outcomes)
        click.echo(_ok({
            "composite_score": scores.composite_score,
            "brier_score": scores.brier_score,
            "alpha_score": scores.alpha_score,
            "calibration_error": scores.calibration_error,
            "roi_pct": scores.roi_pct,
            "skip_rate": scores.skip_rate,
        }))
    except Exception as e:
        click.echo(_err(str(e)))
        sys.exit(1)


@main.command("compare")
@click.option("--results", required=True, multiple=True, type=click.Path(exists=True, path_type=Path))
@click.option("--output", type=click.Path(path_type=Path), default=None)
def compare_cmd(results: tuple[Path, ...], output: Path | None) -> None:
    """Compare multiple benchmark runs."""
    try:
        runs = []
        for path in results:
            data = json.loads(path.read_text())
            runs.append({
                "model": data["model"],
                "market_set": data["market_set"],
                "composite_score": data["scores"]["composite_score"],
                "brier_score": data["scores"]["brier_score"],
                "roi_pct": data["scores"]["roi_pct"],
            })

        runs.sort(key=lambda r: r["composite_score"], reverse=True)

        if output:
            lines = ["# Benchmark Comparison", ""]
            lines.append("| Rank | Model | Score | Brier | ROI |")
            lines.append("|------|-------|-------|-------|-----|")
            for i, r in enumerate(runs, 1):
                lines.append(
                    f"| {i} | {r['model']} | {r['composite_score']} "
                    f"| {r['brier_score']} | {r['roi_pct']}% |"
                )
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text("\n".join(lines) + "\n")

        click.echo(_ok(runs))
    except Exception as e:
        click.echo(_err(str(e)))
        sys.exit(1)


@main.command("export")
@click.option("--results", required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--format", "fmt", type=click.Choice(["huggingface"]), default="huggingface")
@click.option("--output", type=click.Path(path_type=Path), default=None)
def export_cmd(results: Path, fmt: str, output: Path | None) -> None:
    """Export results to external format."""
    from pm_benchmark.export import save_huggingface_jsonl
    from pm_benchmark.scoring import BenchmarkScores

    try:
        data = json.loads(results.read_text())
        eval_run = _load_eval_run(data)

        scores_data = data.get("scores", {})
        scores = BenchmarkScores(**scores_data)

        out_path = output or Path(results.stem + ".jsonl")
        save_huggingface_jsonl(eval_run, scores, out_path)
        click.echo(_ok({"path": str(out_path), "format": fmt}))
    except Exception as e:
        click.echo(_err(str(e)))
        sys.exit(1)


@main.command("history")
@click.option("--path", type=click.Path(exists=True, path_type=Path), required=True,
              help="Path to history.jsonl file")
def history_cmd(path: Path) -> None:
    """Show run history summary."""
    try:
        entries = load_history(path)
        summary = []
        for e in entries:
            scores = e.get("scores", {})
            summary.append({
                "model": e.get("model", "?"),
                "market_set": e.get("market_set", "?"),
                "timestamp": e.get("timestamp", "?"),
                "composite_score": scores.get("composite_score", 0),
                "brier_score": scores.get("brier_score", 0),
                "alpha_score": scores.get("alpha_score", 0),
                "completed": e.get("completed", 0),
            })
        click.echo(_ok(summary))
    except Exception as e:
        click.echo(_err(str(e)))
        sys.exit(1)


@main.command("backfill")
@click.option("--results", required=True, type=click.Path(exists=True, path_type=Path),
              help="Path to report.json from a previous run")
@click.option("--output", type=click.Path(path_type=Path), default=None,
              help="Output path for backfilled report (default: overwrite input)")
def backfill_cmd(results: Path, output: Path | None) -> None:
    """Backfill resolved outcomes into a previous run and re-score.

    Fetches market resolutions from Polymarket API and re-scores
    the run with true ground truth instead of proxy prices.
    """
    try:
        data = json.loads(results.read_text())
        slugs = [r["slug"] for r in data.get("market_results", [])]

        resolved: dict[str, float] = {}
        for slug in slugs:
            try:
                outcome = fetch_resolution(slug)
                if outcome is not None:
                    resolved[slug] = outcome
            except MarketInfoError:
                pass  # skip markets that can't be fetched

        if not resolved:
            click.echo(_ok({"resolved": 0, "message": "No markets resolved yet"}))
            return

        eval_run = _load_eval_run(data)

        scores = score_run(eval_run, resolved_outcomes=resolved)
        from dataclasses import asdict
        data["scores"] = asdict(scores)
        data["resolved_outcomes"] = resolved

        out_path = output or results
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(data, indent=2) + "\n")

        click.echo(_ok({
            "resolved": len(resolved),
            "composite_score": scores.composite_score,
            "brier_score": scores.brier_score,
            "alpha_score": scores.alpha_score,
            "path": str(out_path),
        }))
    except Exception as e:
        click.echo(_err(str(e)))
        sys.exit(1)
