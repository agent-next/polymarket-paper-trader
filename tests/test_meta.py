"""Meta-consistency guards: version strings and doc counts must not drift.

The 0.1.8 release existed because the same version string lived in five
hand-edited fields and drifted (server.json sat two releases behind). These
tests pin every copy to the installed package version so the release gate
fails before a mismatched artifact ships.
"""
from __future__ import annotations

import filecmp
import json
import re
from pathlib import Path

from importlib.metadata import version as pkg_version

ROOT = Path(__file__).resolve().parent.parent


def _installed_version() -> str:
    return pkg_version("polymarket-paper-trader")


def _frontmatter_version(path: Path) -> str:
    match = re.search(r"^version:\s*(\S+)$", path.read_text(encoding="utf-8"), re.MULTILINE)
    assert match, f"no version field in {path}"
    return match.group(1)


def _tool_names() -> list[str]:
    import ast

    src = (ROOT / "pm_trader" / "mcp_server.py").read_text(encoding="utf-8")
    names: list[str] = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.FunctionDef):
            continue
        for dec in node.decorator_list:
            target = dec.func if isinstance(dec, ast.Call) else dec
            if isinstance(target, ast.Name) and target.id == "_tool":
                names.append(node.name)
    return sorted(names)


def _cli_leaf_commands() -> list[str]:
    """Return every leaf command path in the click tree (e.g. 'markets list')."""
    import click

    from pm_trader.cli import main

    leaves: list[str] = []

    def walk(group: click.Group, prefix: str) -> None:
        for name, cmd in group.commands.items():
            path = f"{prefix} {name}".strip()
            if isinstance(cmd, click.Group):
                walk(cmd, path)
            else:
                leaves.append(path)

    walk(main, "")
    return sorted(leaves)


def _readme_cli_commands() -> list[str]:
    """Extract command paths from the README 'CLI commands' table.

    Each row's first cell holds the invocation in backticks; the command path
    is the leading run of lowercase word tokens (args like SLUG or [--opt x]
    stop the match).
    """
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    section = re.search(r"## CLI commands\n\n(.*?)\n\n", readme, re.DOTALL)
    assert section, "CLI commands table not found in README.md"
    commands = []
    for line in section.group(1).splitlines():
        match = re.match(r"^\| `([^`]+)` ", line)
        if not match:
            continue
        path = []
        for word in match.group(1).split():
            if not re.fullmatch(r"[a-z][a-z0-9-]*", word):
                break
            path.append(word)
        if path:
            commands.append(" ".join(path))
    return sorted(commands)


def _readme_works_with_commands() -> list[str]:
    """Extract install commands from the README 'Works with' table.

    A command is a backticked span that invokes uvx, `codex mcp add` or
    `gemini extensions install`; docs/integrations.md documents each of them.
    """
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    section = re.search(r"## Works with\n\n(.*?)\n\n", readme, re.DOTALL)
    assert section, "Works with table not found in README.md"
    commands = []
    for span in re.findall(r"`([^`]+)`", section.group(1)):
        if re.search(r"\buvx\b|codex mcp add|gemini extensions install", span):
            commands.append(span)
    return sorted(commands)


class TestVersionConsistency:
    def test_server_json_matches_installed(self) -> None:
        data = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
        assert data["version"] == _installed_version()
        assert data["packages"][0]["version"] == _installed_version()

    def test_server_json_description_fits_registry(self) -> None:
        # registry.modelcontextprotocol.io rejects descriptions over 100 chars (422).
        data = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
        assert len(data["description"]) <= 100

    def test_agent_manifests_match_installed(self) -> None:
        for rel in (".claude-plugin/plugin.json", "gemini-extension.json"):
            data = json.loads((ROOT / rel).read_text(encoding="utf-8"))
            assert data["version"] == _installed_version(), rel

    def test_mcpb_manifest_matches_installed(self) -> None:
        data = json.loads((ROOT / "mcpb" / "manifest.json").read_text(encoding="utf-8"))
        assert data["version"] == _installed_version()
        assert data["server"]["type"] == "uv"
        pyproject = (ROOT / "mcpb" / "pyproject.toml").read_text(encoding="utf-8")
        assert f'version = "{_installed_version()}"' in pyproject
        assert f"polymarket-paper-trader=={_installed_version()}" in pyproject

    def test_skill_frontmatter_matches_installed(self) -> None:
        for rel in ("skill/polymarket-paper-trader/SKILL.md", ".claude/skills/polymarket-paper-trader/SKILL.md"):
            assert _frontmatter_version(ROOT / rel) == _installed_version(), rel

    def test_changelog_latest_heading_matches_installed(self) -> None:
        text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        match = re.search(r"^## \[(\S+)\] - ", text, re.MULTILINE)
        assert match, "no release heading in CHANGELOG.md"
        assert match.group(1) == _installed_version()

    def test_skill_copies_are_identical(self) -> None:
        assert filecmp.cmp(
            ROOT / "skill/polymarket-paper-trader/SKILL.md",
            ROOT / ".claude/skills/polymarket-paper-trader/SKILL.md",
            shallow=False,
        )


class TestDocCounts:
    def test_readme_tool_table_matches_registered_tools(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        table = re.search(r"### MCP tools\n\n.*?\n\n", readme, re.DOTALL)
        assert table, "MCP tools table not found in README.md"
        rows = re.findall(r"^\| `([a-z_]+)` ", table.group(0), re.MULTILINE)
        assert sorted(rows) == _tool_names()

    def test_readme_cli_table_matches_click_tree(self) -> None:
        """Every leaf CLI command appears in the README table, and vice versa."""
        assert _readme_cli_commands() == _cli_leaf_commands()

    def test_readme_works_with_commands_in_integrations(self) -> None:
        """Every install command in the README 'Works with' table appears verbatim
        in docs/integrations.md — editing one without the other fails here."""
        integrations = (ROOT / "docs" / "integrations.md").read_text(encoding="utf-8")
        commands = _readme_works_with_commands()
        assert commands, "no install commands found in the README 'Works with' table"
        missing = [command for command in commands if command not in integrations]
        assert not missing, f"in README but not docs/integrations.md: {missing}"

    def test_mcpb_manifest_tools_match_registered_tools(self) -> None:
        data = json.loads((ROOT / "mcpb" / "manifest.json").read_text(encoding="utf-8"))
        assert sorted(tool["name"] for tool in data["tools"]) == _tool_names()

    def test_skill_tools_table_matches_registered_tools(self) -> None:
        """Both SKILL.md copies list exactly the registered MCP tools."""
        for rel in (
            "skill/polymarket-paper-trader/SKILL.md",
            ".claude/skills/polymarket-paper-trader/SKILL.md",
        ):
            text = (ROOT / rel).read_text(encoding="utf-8")
            section = re.search(r"## Tools\n\n(.*?)\n\n", text, re.DOTALL)
            assert section, f"Tools table not found in {rel}"
            rows = re.findall(r"^\| `([a-z0-9_]+)` ", section.group(1), re.MULTILINE)
            assert sorted(rows) == _tool_names(), rel
