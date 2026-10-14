"""Tests for the packaged MCP Apps portfolio view and its server wiring.

Covers: the `ui://pm-trader/portfolio` resource (URI, MIME type, body, meta),
the UI metadata scope on `portfolio`/`stats`, packaging of the HTML file, CSP
hygiene of the document, and the bridge contract between the view and the
JSON envelope the tools actually return.
"""

from __future__ import annotations

import asyncio
import fnmatch
import importlib.resources
import json
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import anyio
import pytest

from pm_trader import mcp_server

ROOT = Path(__file__).resolve().parent.parent
UI_RELPATH = "ui/portfolio.html"
UI_URI = "ui://pm-trader/portfolio"
MARKET_UI_URI = "ui://pm-trader/market"
UI_MIME = "text/html;profile=mcp-app"

# Message names the MCP Apps spec defines for the view side of the bridge.
BRIDGE_METHODS = (
    "ui/initialize",
    "ui/notifications/initialized",
    "ui/notifications/tool-input",
    "ui/notifications/tool-result",
    "ui/notifications/size-changed",
    "ui/resource-teardown",
    "tools/call",
)


@pytest.fixture(autouse=True)
def fresh_engine(tmp_path: Path):
    """Reset the module-global engine so tool calls hit a throwaway account."""
    mcp_server._engine = None
    with patch.object(Path, "home", return_value=tmp_path):
        yield
    _close_engine()


def _ui_html() -> str:
    """The packaged view, read the way the server reads it."""
    return importlib.resources.files("pm_trader").joinpath(UI_RELPATH).read_text(
        encoding="utf-8"
    )


def _repo_ui_html() -> str:
    """The view as committed in the checkout."""
    return (ROOT / "pm_trader" / UI_RELPATH).read_text(encoding="utf-8")


def _tools() -> dict[str, dict]:
    """tools/list as the wire sees it, keyed by tool name."""
    async def _list() -> dict[str, dict]:
        tools = await mcp_server.mcp.list_tools()
        return {
            t.name: t.model_dump(by_alias=True, exclude_none=True) for t in tools
        }

    return anyio.run(_list)


def _resources() -> list[dict]:
    async def _list() -> list[dict]:
        resources = await mcp_server.mcp.list_resources()
        return [r.model_dump(by_alias=True, exclude_none=True) for r in resources]

    return anyio.run(_list)


def _read_ui() -> tuple[str, str | None, dict | None]:
    async def _read() -> tuple[str, str | None, dict | None]:
        contents = list(await mcp_server.mcp.read_resource(UI_URI))
        assert len(contents) == 1
        item = contents[0]
        return item.content, item.mime_type, item.meta

    return anyio.run(_read)


def _referenced_names(html: str) -> set[str]:
    """Names the document reads: dotted property access or quoted literals.

    ``stats.cash`` and ``stats["cash"]`` both count as a reference to `cash`.
    """
    return {
        dotted or quoted
        for dotted, quoted in re.findall(
            r"""\.([A-Za-z_][A-Za-z0-9_]*)|["']([A-Za-z_][A-Za-z0-9_]*)["']""", html
        )
    }


def _seed_account(sample_market, sample_order_book) -> None:
    """Create an account and mock the price lookups behind a real buy."""
    mcp_server.init_account(balance=10_000.0)
    engine = mcp_server._get_engine()
    engine.api.get_market = MagicMock(return_value=sample_market)
    engine.api.get_order_book = MagicMock(return_value=sample_order_book)
    engine.api.get_fee_rate = MagicMock(return_value=0)
    engine.api.get_midpoint = MagicMock(return_value=0.64)


def _close_engine() -> None:
    """Close the global engine on the thread that owns its SQLite connection."""
    if mcp_server._engine is not None:
        mcp_server._engine.close()
        mcp_server._engine = None


# ---------------------------------------------------------------------------
# The ui:// resource
# ---------------------------------------------------------------------------


class TestUiResource:
    def test_listed_with_spec_uri_and_mime_type(self):
        entry = [r for r in _resources() if r["uri"] == UI_URI]
        assert len(entry) == 1
        assert entry[0]["mimeType"] == UI_MIME
        assert entry[0]["name"] == "portfolio"
        assert entry[0]["description"]

    def test_only_the_two_views_use_the_ui_scheme(self):
        ui_resources = [r["uri"] for r in _resources() if r["uri"].startswith("ui://")]
        assert ui_resources == [UI_URI, MARKET_UI_URI]

    def test_resource_body_is_the_packaged_html(self):
        content, mime_type, _ = _read_ui()
        assert content == _repo_ui_html()
        assert mime_type == UI_MIME

    def test_resource_meta_asks_for_a_border_and_no_external_origins(self):
        entry = next(r for r in _resources() if r["uri"] == UI_URI)
        assert entry["_meta"] == {"ui": {"prefersBorder": True}}

    def test_ui_module_constants_match_the_spec(self):
        assert mcp_server.UI_RESOURCE_URI == UI_URI
        assert mcp_server.UI_MIME_TYPE == UI_MIME


# ---------------------------------------------------------------------------
# Tool metadata
# ---------------------------------------------------------------------------


class TestToolMeta:
    def test_portfolio_and_stats_declare_the_view(self):
        tools = _tools()
        assert tools["portfolio"]["_meta"]["ui"]["resourceUri"] == UI_URI
        assert tools["stats"]["_meta"]["ui"]["resourceUri"] == UI_URI

    def test_chatgpt_compatibility_alias_is_present(self):
        tools = _tools()
        for name in ("portfolio", "stats"):
            assert tools[name]["_meta"]["openai/outputTemplate"] == UI_URI

    def test_declared_flat_alias_matches(self):
        tools = _tools()
        for name in ("portfolio", "stats"):
            assert tools[name]["_meta"]["ui/resourceUri"] == UI_URI

    @pytest.mark.parametrize("name", ["portfolio", "stats"])
    def test_meta_does_not_touch_the_input_schema(self, name):
        """Adding `_meta` must leave the advertised schema byte-identical."""
        tools = _tools()
        entry = tools[name]
        assert entry["inputSchema"] == {
            "properties": {
                "account": {"default": "default", "title": "Account", "type": "string"}
            },
            "title": f"{name}Arguments",
            "type": "object",
        }
        assert entry["outputSchema"] == {
            "properties": {"result": {"title": "Result", "type": "string"}},
            "required": ["result"],
            "title": f"{name}Output",
            "type": "object",
        }

    def test_no_other_tool_carries_ui_meta(self):
        tools = _tools()
        with_ui = {
            name
            for name, entry in tools.items()
            if "resourceUri" in json.dumps(entry.get("_meta", {}))
        }
        assert with_ui == {"get_market", "portfolio", "stats"}

    def test_referenced_resource_exists(self):
        uris = {r["uri"] for r in _resources()}
        for name in ("portfolio", "stats"):
            assert _tools()[name]["_meta"]["ui"]["resourceUri"] in uris

    def test_tool_results_are_unchanged(self, sample_market, sample_order_book):
        """`_meta` rides alongside the tool: the text result is still the envelope."""

        async def _roundtrip() -> tuple[str, str]:
            await mcp_server.mcp.call_tool("init_account", {"balance": 10_000.0})
            engine = mcp_server._get_engine()
            engine.api.get_market = MagicMock(return_value=sample_market)
            engine.api.get_order_book = MagicMock(return_value=sample_order_book)
            engine.api.get_fee_rate = MagicMock(return_value=0)
            engine.api.get_midpoint = MagicMock(return_value=0.64)
            await mcp_server.mcp.call_tool(
                "buy",
                {
                    "slug_or_id": sample_market.slug,
                    "outcome": "yes",
                    "amount_usd": 250.0,
                },
            )
            portfolio_result = await mcp_server.mcp.call_tool("portfolio", {})
            stats_result = await mcp_server.mcp.call_tool("stats", {})
            # The engine was created on the tool worker thread; close it there.
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(mcp_server._tool_executor, _close_engine)
            return portfolio_result.content[0].text, stats_result.content[0].text

        portfolio_text, stats_text = anyio.run(_roundtrip)

        positions = json.loads(portfolio_text)
        assert sorted(positions) == ["data", "ok"]
        assert positions["ok"] is True
        assert len(positions["data"]) == 1
        assert positions["data"][0]["market_slug"] == sample_market.slug
        # Bought across the spread, marked at the 0.64 midpoint: still losing.
        assert positions["data"][0]["unrealized_pnl"] < 0

        stats = json.loads(stats_text)
        assert sorted(stats) == ["data", "ok"]
        assert stats["data"]["total_trades"] == 1
        assert stats["data"]["cash"] == pytest.approx(10_000.0 - 250.0)


# ---------------------------------------------------------------------------
# Packaging
# ---------------------------------------------------------------------------


class TestPackaging:
    def test_pyproject_ships_the_view_as_package_data(self):
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        table = re.search(r"\[tool\.setuptools\.package-data\](.*?)(\n\[|\Z)", text, re.DOTALL)
        assert table, "package-data table not found in pyproject.toml"
        patterns = re.findall(r'"([^"]+)"', table.group(1))
        assert any(fnmatch.fnmatch(UI_RELPATH, p) for p in patterns), (
            f"{UI_RELPATH} not matched by package-data {patterns}"
        )

    def test_view_is_readable_through_importlib_resources(self):
        assert _ui_html() == _repo_ui_html()
        assert _ui_html().startswith("<!DOCTYPE html>")


# ---------------------------------------------------------------------------
# Document hygiene — the view must run under the spec's restrictive default CSP
# ---------------------------------------------------------------------------


class TestHtmlHygiene:
    def test_no_absolute_urls(self):
        html = _ui_html()
        assert "http://" not in html
        assert "https://" not in html

    def test_no_external_references(self):
        html = _ui_html()
        for pattern in (r"\bsrc\s*=", r"\bhref\s*=", r"url\(", r"@import"):
            assert re.search(pattern, html) is None, pattern

    def test_no_external_script_tags(self):
        assert re.search(r"<script[^>]+src", _ui_html()) is None

    def test_styles_and_script_are_inline(self):
        html = _ui_html()
        assert "<style>" in html
        assert html.count("<script>") == 1
        assert "</script>" in html

    def test_advertises_no_csp_domains(self):
        """No `ui.csp` metadata means the host applies its restrictive default."""
        assert "connectDomains" not in _ui_html()
        assert mcp_server._UI_TOOL_META["ui"].keys() == {"resourceUri"}


class TestBridgeContract:
    def test_handshake_and_notification_methods_are_present(self):
        html = _ui_html()
        for method in BRIDGE_METHODS:
            assert f'"{method}"' in html, method

    def test_initialization_carries_app_info_and_capabilities(self):
        html = _ui_html()
        for field in ("appInfo", "appCapabilities", "hostCapabilities", "hostContext"):
            assert field in html, field

    def test_theme_follows_the_host_and_the_system(self):
        html = _ui_html()
        assert "prefers-color-scheme: dark" in html
        assert 'data-theme="' in html or "setAttribute(\"data-theme\"" in html


# ---------------------------------------------------------------------------
# The envelope the view parses
# ---------------------------------------------------------------------------


class TestEnvelopeContract:
    def test_reads_every_portfolio_field(self, sample_market, sample_order_book):
        _seed_account(sample_market, sample_order_book)
        mcp_server.buy("will-bitcoin-hit-100k", "yes", 250.0)
        envelope = json.loads(mcp_server.portfolio())
        assert envelope["ok"] is True
        position = envelope["data"][0]
        assert position["outcome"] == "yes"

        names = _referenced_names(_ui_html())
        assert not sorted(key for key in position if key not in names)

    def test_reads_every_stats_field(self, sample_market, sample_order_book):
        _seed_account(sample_market, sample_order_book)
        mcp_server.buy("will-bitcoin-hit-100k", "yes", 250.0)
        envelope = json.loads(mcp_server.stats())
        assert envelope["ok"] is True

        names = _referenced_names(_ui_html())
        assert not sorted(key for key in envelope["data"] if key not in names)

    def test_reads_the_envelope_keys(self):
        names = _referenced_names(_ui_html())
        assert {"ok", "data", "error", "code", "content", "arguments"} <= names

    def test_handles_an_empty_portfolio(self, sample_market, sample_order_book):
        _seed_account(sample_market, sample_order_book)
        envelope = json.loads(mcp_server.portfolio())
        assert envelope["data"] == []
        assert "No open positions" in _ui_html()

    def test_handles_an_error_envelope(self):
        envelope = json.loads(mcp_server.portfolio())  # account not initialized
        assert envelope["ok"] is False
        assert envelope["code"]
        html = _ui_html()
        assert "renderError" in html
        assert "envelope.error" in html