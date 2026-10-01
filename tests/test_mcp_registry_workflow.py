"""MCP registry workflow wiring — pins the PyPI-presence guard before publishing."""
from __future__ import annotations

from pathlib import Path

WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "mcp-registry.yml"


def test_publish_requires_version_on_pypi() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    guard = text.index("https://pypi.org/pypi/polymarket-paper-trader/${version}/json")
    assert "curl -fsS" in text[guard - 120 : guard]
    assert guard < text.index("./mcp-publisher publish"), "guard must run before publishing"
    assert "jq -r .version server.json" in text[:guard]
