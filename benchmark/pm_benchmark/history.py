"""Run history persistence via JSONL."""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from pm_benchmark.runner import EvalRun
from pm_benchmark.scoring import BenchmarkScores


def append_to_history(
    eval_run: EvalRun,
    scores: BenchmarkScores,
    path: Path,
) -> None:
    """Append a run summary to JSONL history file.

    Each line is a JSON object with model, scores, and metadata.
    Creates the file and parent directories if needed.
    """
    record = {
        "model": eval_run.model,
        "market_set": eval_run.market_set,
        "budget": eval_run.budget,
        "timestamp": eval_run.timestamp,
        "n_markets": len(eval_run.market_results),
        "completed": eval_run.completed_count,
        "errors": eval_run.error_count,
        "skipped": eval_run.skipped_count,
        "scores": asdict(scores),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(record) + "\n")


def load_history(path: Path) -> list[dict]:
    """Load run history from JSONL file.

    Returns:
        List of run summary dicts. Empty list if file doesn't exist.
    """
    try:
        with open(path) as f:
            entries: list[dict] = []
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
            return entries
    except FileNotFoundError:
        return []
