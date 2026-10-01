"""Count arena forecast rows by status for the honest-red guard in arena.yml.

    python scripts/arena_rows.py count DATA_DIR
    python scripts/arena_rows.py guard DATA_DIR TOTAL_BEFORE OK_BEFORE PREDICT_RC
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def count_rows(data_dir: Path) -> tuple[int, int]:
    """Return (all rows, rows with status "ok") across data_dir/forecasts/*.jsonl."""
    total = ok = 0
    for path in sorted((data_dir / "forecasts").glob("*.jsonl")):
        for line in path.read_text().splitlines():
            # Skip blank, torn and non-object lines, as arena.load_forecasts does.
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            total += 1
            if row.get("status") == "ok":
                ok += 1
    return total, ok


def verdict(
    total_before: int, ok_before: int, total_after: int, ok_after: int, predict_rc: int
) -> str | None:
    """Return an error message when the run must be red, else None."""
    if predict_rc != 0:
        return f"predict failed (rc={predict_rc})"
    if total_after > total_before and ok_after <= ok_before:
        return "predict attempted forecasts but produced no ok rows"
    return None


def main(argv: list[str]) -> int:
    if len(argv) == 3 and argv[1] == "count":
        print(*count_rows(Path(argv[2])))
        return 0
    if len(argv) == 6 and argv[1] == "guard":
        total_after, ok_after = count_rows(Path(argv[2]))
        error = verdict(int(argv[3]), int(argv[4]), total_after, ok_after, int(argv[5]))
        print(f"forecast rows: total {argv[3]}->{total_after} ok {argv[4]}->{ok_after}")
        if error:
            print(f"::error::{error}")
            return 1
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
