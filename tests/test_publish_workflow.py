"""Publish workflow wiring — pins the concurrency guard on tag publishes.

Reads the real file on disk (same pattern as test_opencode_bot.py). The guard
exists because a duplicated tag-push event (observed on v0.3.0, 2026-09-23)
raced two concurrent publishes that each lost one channel to the other.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "publish.yml"


def _workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


class TestPublishConcurrency:
    def test_workflow_exists(self) -> None:
        assert WORKFLOW.is_file(), WORKFLOW

    def test_one_publish_per_tag(self) -> None:
        """Duplicate tag events must cancel, not race PyPI/ClawHub."""
        text = _workflow_text()
        assert re.search(
            r"^concurrency:\s*$", text, re.M
        ), "publish.yml is missing a concurrency block"
        assert re.search(
            r"group:\s*publish-\$\{\{\s*github\.ref\s*\}\}", text
        ), "concurrency group must key on the tag ref"
        assert re.search(r"cancel-in-progress:\s*true", text), (
            "cancel-in-progress must be true so the duplicate run cancels"
        )

    def test_trigger_still_tag_only(self) -> None:
        text = _workflow_text()
        assert re.search(r"tags:\s*$", text, re.M)
        assert re.search(r'-\s*"v\*"', text), "tag glob must stay v*"
