"""Arena workflow wiring — token isolation and the honest-red guard."""
from __future__ import annotations

import re
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "arena.yml"


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
        assert "rows_after" in guard and "ARENA_ROWS_BEFORE" in guard
        assert "exit 1" in guard
        assert text.index("Deploy to Cloudflare Pages") < text.index("Fail the run when")

    def test_pages_deploy_survives_the_guard(self) -> None:
        text = _text()
        assert "pages_ready: ${{ steps.pages.outcome == 'success' }}" in text
        assert "!cancelled() && needs.run.outputs.pages_ready == 'true'" in text
