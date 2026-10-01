"""Arena workflow wiring — token isolation and the honest-red guard."""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "arena.yml"

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "arena_rows.py"
_spec = importlib.util.spec_from_file_location("arena_rows", SCRIPT)
arena_rows = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(arena_rows)


def _text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


class TestTokenIsolation:
    def test_remote_url_is_token_free(self) -> None:
        assert "x-access-token:${GH_TOKEN}@" not in _text()

    def test_token_passed_per_command_not_stored(self) -> None:
        text = _text()
        assert "http.extraheader=AUTHORIZATION: basic" in text
        assert re.search(r"git -c \"http\.extraheader[^\n]*\" push ", text)
        assert re.search(r"git -c \"http\.extraheader[^\n]*\" clone ", text)
        assert "git config http.extraheader" not in text
        assert "clone -c" not in text

    def test_push_step_gets_the_token(self) -> None:
        commit = _text().split("- name: Commit data")[1].split("- name: Build site")[0]
        assert "GH_TOKEN: ${{ github.token }}" in commit


class TestHonestRed:
    def test_failures_are_recorded_not_swallowed(self) -> None:
        text = _text()
        assert "|| echo ::warning" not in text
        assert "predict_rc=$?" in text and "resolve_rc=$?" in text
        assert "ARENA_PREDICT_RC=$predict_rc" in text

    def test_guard_fails_when_no_new_rows(self) -> None:
        text = _text()
        guard = text.split("- name: Fail the run when predict produced nothing")[1]
        assert "scripts/arena_rows.py guard" in guard
        assert "ARENA_ROWS_BEFORE" in guard and "ARENA_OK_BEFORE" in guard
        assert "ARENA_PREDICT_RC" in guard
        assert "scripts/arena_rows.py count" in text
        assert text.index("Deploy to Cloudflare Pages") < text.index("Fail the run when")

    def test_pages_deploy_survives_the_guard(self) -> None:
        text = _text()
        assert "pages_ready: ${{ steps.pages.outcome == 'success' }}" in text
        assert "!cancelled() && needs.run.outputs.pages_ready == 'true'" in text


def _write(data: Path, name: str, statuses: list[str]) -> None:
    d = data / "forecasts"
    d.mkdir(parents=True, exist_ok=True)
    with (d / name).open("a") as f:
        for st in statuses:
            f.write(json.dumps({"status": st}) + "\n")
        f.write("\n")


class TestArenaRows:
    def test_count_splits_ok_from_skip(self, tmp_path: Path) -> None:
        assert arena_rows.count_rows(tmp_path) == (0, 0)
        _write(tmp_path, "a.jsonl", ["ok", "skip"])
        _write(tmp_path, "b.jsonl", ["ok"])
        assert arena_rows.count_rows(tmp_path) == (3, 2)

    def test_all_skip_run_is_red(self) -> None:
        assert arena_rows.verdict(5, 3, 9, 3, 0) is not None

    def test_ok_rows_keep_it_green(self) -> None:
        assert arena_rows.verdict(5, 3, 9, 4, 0) is None

    def test_no_new_rows_with_rc0_is_green(self) -> None:
        assert arena_rows.verdict(5, 3, 5, 3, 0) is None

    def test_nonzero_predict_rc_is_red(self) -> None:
        assert "rc=2" in arena_rows.verdict(5, 3, 9, 8, 2)

    def test_cli(self, tmp_path: Path, capsys) -> None:
        _write(tmp_path, "a.jsonl", ["skip", "skip"])
        assert arena_rows.main(["x", "count", str(tmp_path)]) == 0
        assert capsys.readouterr().out.strip() == "2 0"
        assert arena_rows.main(["x", "guard", str(tmp_path), "0", "0", "0"]) == 1
        assert "::error::" in capsys.readouterr().out
        assert arena_rows.main(["x", "guard", str(tmp_path), "2", "0", "0"]) == 0
        assert arena_rows.main(["x"]) == 2
