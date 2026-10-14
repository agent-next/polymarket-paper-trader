"""Tests for the packaged MCP Apps market view and its server wiring.

Covers: the `ui://pm-trader/market` resource (URI, MIME type, body, meta), the
UI metadata on the market lookup tool, packaging of the HTML file, CSP hygiene
of the document, the bridge contract between the view and the JSON envelope the
tools actually return, and the order ticket's payload builder — the pure
JavaScript in `market.html` that turns a ticket into one `tools/call`.

The ticket logic is JavaScript, so it is pinned the way this repo's CI can
check it: the function sources are extracted from the shipped document and
asserted verbatim (whitespace-normalized), the argument keys are parsed out of
the builder and fed to the real `buy`/`sell` tools, and the book walk is
recomputed in Python to hold the expected numbers. The same values were checked
against node while writing the view.
"""

from __future__ import annotations

import fnmatch
import importlib.resources
import json
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import anyio
import pytest

from pm_trader import mcp_server
from pm_trader.models import MarketNotFoundError

ROOT = Path(__file__).resolve().parent.parent
UI_RELPATH = "ui/market.html"
UI_URI = "ui://pm-trader/market"
UI_MIME = "text/html;profile=mcp-app"
PORTFOLIO_URI = "ui://pm-trader/portfolio"

# Message names the MCP Apps spec defines for the view side of the bridge.
BRIDGE_METHODS = (
    "ui/initialize",
    "ui/notifications/initialized",
    "ui/notifications/tool-input",
    "ui/notifications/tool-input-partial",
    "ui/notifications/tool-result",
    "ui/notifications/tool-cancelled",
    "ui/notifications/size-changed",
    "ui/notifications/host-context-changed",
    "ui/resource-teardown",
    "tools/call",
)

# The only tools the view is allowed to reach through the bridge: three
# read-only lookups that already exist, and the two trade tools the ticket
# confirms. No limit orders, no live-trading path.
VIEW_TOOLS = {
    "MARKET_TOOL": "get_market",
    "BOOK_TOOL": "get_order_book",
    "PORTFOLIO_TOOL": "portfolio",
    "BUY_TOOL": "buy",
    "SELL_TOOL": "sell",
}

# Strings and comments carry no braces in the pinned functions, but the scanner
# skips both so an extractor cannot be fooled by one inside a literal.
_JS_STRING = re.compile(r"\"[^\"]*\"|'[^']*'", re.DOTALL)
_JS_COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.DOTALL)
_JS_NOISE = re.compile(
    r"\"[^\"]*\"|'[^']*'|//[^\n]*|/\*.*?\*/", re.DOTALL
)


@pytest.fixture(autouse=True)
def fresh_engine(tmp_path: Path):
    """Reset the module-global engine so tool calls hit a throwaway account."""
    mcp_server._engine = None
    with patch.object(Path, "home", return_value=tmp_path):
        yield
    _close_engine()


def _close_engine() -> None:
    """Close the global engine on the thread that owns its SQLite connection."""
    if mcp_server._engine is not None:
        mcp_server._engine.close()
        mcp_server._engine = None


def _view_html() -> str:
    """The packaged view, read the way the server reads it."""
    return importlib.resources.files("pm_trader").joinpath(UI_RELPATH).read_text(
        encoding="utf-8"
    )


def _repo_html() -> str:
    """The view as committed in the checkout."""
    return (ROOT / "pm_trader" / UI_RELPATH).read_text(encoding="utf-8")


def _script() -> str:
    """The single inline script of the view."""
    html = _view_html()
    assert html.count("<script>") == 1, "the view must carry exactly one inline script"
    return html.split("<script>")[1].split("</script>")[0]


def _norm(source: str) -> str:
    """Normalize JavaScript for pinning: no comments, no formatting noise.

    Comments are dropped on purpose — a source pin guards behavior, and the view
    should be free to reword its comments without breaking the suite.
    """
    strings: list[str] = []

    def _keep(match: re.Match[str]) -> str:
        strings.append(match.group(0))
        return f"\x00{len(strings) - 1}\x00"

    scrubbed = _JS_STRING.sub(_keep, source)
    scrubbed = _JS_COMMENT.sub(" ", scrubbed)
    scrubbed = re.sub(r"\s+", " ", scrubbed).strip()
    for index, literal in enumerate(strings):
        scrubbed = scrubbed.replace(f"\x00{index}\x00", literal)
    return scrubbed


def _js(name: str) -> str:
    """Return one top-level function from the view, braces matched."""
    script = _script()
    start = script.index(f"  function {name}(")
    pos = script.index("{", start)
    depth = 0
    while pos < len(script):
        noise = _JS_NOISE.match(script, pos)
        if noise:
            pos = noise.end()
            continue
        if script[pos] == "{":
            depth += 1
        elif script[pos] == "}":
            depth -= 1
            if depth == 0:
                return script[start:pos + 1]
        pos += 1
    raise AssertionError(f"unbalanced braces in {name}()")


def _referenced_names(html: str) -> set[str]:
    """Names the document reads: dotted property access or quoted literals."""
    return {
        dotted or quoted
        for dotted, quoted in re.findall(
            r"""\.([A-Za-z_][A-Za-z0-9_]*)|["']([A-Za-z_][A-Za-z0-9_]*)["']""", html
        )
    }


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


def _read_ui(uri: str = UI_URI) -> tuple[str, str | None, dict | None]:
    async def _read() -> tuple[str, str | None, dict | None]:
        contents = list(await mcp_server.mcp.read_resource(uri))
        assert len(contents) == 1
        item = contents[0]
        return item.content, item.mime_type, item.meta

    return anyio.run(_read)


def _seed_account(sample_market, sample_order_book) -> None:
    """Create an account and mock the price lookups behind a real buy."""
    mcp_server.init_account(balance=10_000.0)
    engine = mcp_server._get_engine()
    engine.api.get_market = MagicMock(return_value=sample_market)
    engine.api.get_order_book = MagicMock(return_value=sample_order_book)
    engine.api.get_fee_rate = MagicMock(return_value=0)
    engine.api.get_midpoint = MagicMock(return_value=0.64)


def _book_payload(sample_order_book, market_slug: str, outcome: str) -> dict:
    """The get_order_book envelope, exactly as the view receives it."""
    return {
        "ok": True,
        "data": {
            "market_slug": market_slug,
            "outcome": outcome,
            "asks": [{"price": a.price, "size": a.size} for a in sample_order_book.asks],
            "bids": [{"price": b.price, "size": b.size} for b in sample_order_book.bids],
        },
    }


def _walk_book(levels: list[dict], amount: float, side: str) -> dict:
    """Python mirror of the view's `walkBook`, the expected preview numbers.

    Asks ascending for a buy (the amount is USD), bids descending for a sell
    (the amount is shares). Values checked against node while writing the view.
    """
    ordered = sorted(levels, key=lambda level: level["price"],
                     reverse=side != "buy")
    remaining = amount
    got = 0.0
    spent = 0.0
    for level in ordered:
        if remaining <= 1e-9:
            break
        take = min(level["size"], remaining / level["price"]) if side == "buy" \
            else min(level["size"], remaining)
        remaining -= take * level["price"] if side == "buy" else take
        got += take
        spent += take * level["price"]
    return {
        "shares": got,
        "cost": spent,
        "avg_price": spent / got if got > 0 else None,
        "filled": remaining <= 1e-9,
    }


# ---------------------------------------------------------------------------
# The ui:// resource
# ---------------------------------------------------------------------------


class TestMarketResource:
    def test_listed_with_spec_uri_and_mime_type(self):
        entry = [r for r in _resources() if r["uri"] == UI_URI]
        assert len(entry) == 1
        assert entry[0]["mimeType"] == UI_MIME
        assert entry[0]["name"] == "market"
        assert entry[0]["title"] == "pm-trader market card and order ticket"
        assert entry[0]["description"]

    def test_the_two_views_are_listed_side_by_side(self):
        ui_resources = [r["uri"] for r in _resources() if r["uri"].startswith("ui://")]
        assert ui_resources == [PORTFOLIO_URI, UI_URI]

    def test_resource_body_is_the_packaged_html(self):
        content, mime_type, _ = _read_ui()
        assert content == _repo_html()
        assert mime_type == UI_MIME

    def test_resource_meta_asks_for_a_border_and_no_external_origins(self):
        entry = next(r for r in _resources() if r["uri"] == UI_URI)
        assert entry["_meta"] == {"ui": {"prefersBorder": True}}

    def test_ui_module_constants_match_the_spec(self):
        assert mcp_server.MARKET_UI_RESOURCE_URI == UI_URI
        assert mcp_server.UI_MIME_TYPE == UI_MIME

    def test_ui_body_defaults_to_the_portfolio_view(self):
        assert mcp_server._ui_body() == _read_ui(PORTFOLIO_URI)[0]
        assert mcp_server._ui_body(UI_RELPATH) == _read_ui()[0]


# ---------------------------------------------------------------------------
# Tool metadata
# ---------------------------------------------------------------------------


class TestMarketToolMeta:
    def test_the_market_lookup_tool_declares_this_view(self):
        tools = _tools()
        assert tools["get_market"]["_meta"]["ui"]["resourceUri"] == UI_URI
        assert tools["get_market"]["_meta"]["openai/outputTemplate"] == UI_URI
        assert tools["get_market"]["_meta"]["ui/resourceUri"] == UI_URI

    def test_the_portfolio_view_is_not_repointed(self):
        tools = _tools()
        for name in ("portfolio", "stats"):
            assert tools[name]["_meta"]["ui"]["resourceUri"] == PORTFOLIO_URI

    def test_meta_does_not_touch_the_input_schema(self):
        entry = _tools()["get_market"]
        assert entry["inputSchema"] == {
            "properties": {
                "slug_or_id": {"title": "Slug Or Id", "type": "string"}
            },
            "required": ["slug_or_id"],
            "title": "get_marketArguments",
            "type": "object",
        }
        assert entry["outputSchema"] == {
            "properties": {"result": {"title": "Result", "type": "string"}},
            "required": ["result"],
            "title": "get_marketOutput",
            "type": "object",
        }
        assert entry["annotations"] == {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        }

    def test_referenced_resource_exists(self):
        uris = {r["uri"] for r in _resources()}
        assert _tools()["get_market"]["_meta"]["ui"]["resourceUri"] in uris

    def test_tool_result_is_unchanged(self, sample_market):
        """`_meta` rides alongside the tool: the text result is still the envelope."""
        engine = mcp_server._get_engine()
        engine.api.get_market = MagicMock(return_value=sample_market)

        envelope = json.loads(mcp_server.get_market(sample_market.slug))
        assert sorted(envelope) == ["data", "ok"]
        assert envelope["ok"] is True
        assert envelope["data"]["question"] == sample_market.question
        assert envelope["data"]["slug"] == sample_market.slug
        assert envelope["data"]["outcomes"] == ["Yes", "No"]
        assert envelope["data"]["outcome_prices"] == [0.65, 0.35]


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
        assert _view_html() == _repo_html()
        assert _view_html().startswith("<!DOCTYPE html>")


# ---------------------------------------------------------------------------
# Document hygiene — the view must run under the spec's restrictive default CSP
# ---------------------------------------------------------------------------


class TestHtmlHygiene:
    def test_no_absolute_urls(self):
        html = _view_html()
        assert "http://" not in html
        assert "https://" not in html

    def test_no_external_references(self):
        html = _view_html()
        for pattern in (r"\bsrc\s*=", r"\bhref\s*=", r"url\(", r"@import"):
            assert re.search(pattern, html) is None, pattern

    def test_no_external_script_tags(self):
        assert re.search(r"<script[^>]+src", _view_html()) is None

    def test_no_dynamic_code_or_html_injection(self):
        html = _view_html()
        for pattern in (
            "innerHTML", "outerHTML", "insertAdjacentHTML", "document.write",
            "eval(", "new Function", "setAttribute(\"srcdoc",
        ):
            assert pattern not in html, pattern

    def test_all_dom_text_is_written_through_text_content(self):
        """Text reaches the page through `el()`/textContent and nothing else."""
        html = _view_html()
        assert _norm(_js("el")) == (
            "function el(tag, className, text) { var node = "
            "document.createElement(tag); if (className) { node.className = "
            "className; } if (text !== undefined && text !== null) { "
            "node.textContent = String(text); } return node; }"
        )
        assert _norm(_js("option")) == (
            "function option(value, label) { var node = "
            "document.createElement(\"option\"); node.value = value; "
            "node.textContent = label; return node; }"
        )
        # The only other text sinks: the header badges, the two button labels, the
        # host font sheet, and the values of the three ticket controls.
        assert sorted(set(re.findall(r"(\w+)\.textContent =", html))) == [
            "accountEl", "confirmButton", "node", "previewButton", "sheet", "sourceEl",
        ]
        assert sorted(set(re.findall(r"(\w+)\.value =", html))) == [
            "amountInput", "node", "outcomeSelect", "sideSelect",
        ]
        assert "contentEl.replaceChildren(" in html

    def test_styles_and_script_are_inline(self):
        html = _view_html()
        assert "<style>" in html
        assert html.count("<script>") == 1
        assert "</script>" in html

    def test_advertises_no_csp_domains(self):
        """No `ui.csp` metadata means the host applies its restrictive default."""
        assert "connectDomains" not in _view_html()
        assert mcp_server._MARKET_UI_TOOL_META["ui"].keys() == {"resourceUri"}


# ---------------------------------------------------------------------------
# Bridge contract
# ---------------------------------------------------------------------------


class TestBridgeContract:
    def test_handshake_and_notification_methods_are_present(self):
        html = _view_html()
        for method in BRIDGE_METHODS:
            assert f'"{method}"' in html, method

    def test_initialization_carries_app_info_and_capabilities(self):
        html = _view_html()
        for field in ("appInfo", "appCapabilities", "hostCapabilities", "hostContext"):
            assert field in html, field

    def test_tool_calls_are_gated_on_the_host_capability(self):
        """`serverTools` is the spec's "host can proxy tool calls" capability."""
        html = _view_html()
        assert _norm(_js("canCallServer")) == (
            "function canCallServer() { return hostReady "
            "&& !!(hostCapabilities && hostCapabilities.serverTools); }"
        )
        assert "if (!canCallServer()) {" in html

    def test_theme_follows_the_host_and_the_system(self):
        html = _view_html()
        assert "prefers-color-scheme: dark" in html
        assert "setAttribute(\"data-theme\"" in html

    def test_the_view_calls_only_existing_tools(self):
        """Three read-only lookups plus the two trade tools — nothing else."""
        constants = dict(re.findall(r"var ([A-Z_]+) = \"([^\"]+)\";", _script()))
        called = {name: constants[name] for name in VIEW_TOOLS if name in constants}
        assert called == VIEW_TOOLS

        tools = _tools()
        for name in called.values():
            assert name in tools, name
        for name in ("get_market", "get_order_book", "portfolio"):
            assert tools[name]["annotations"]["readOnlyHint"] is True
        for name in ("buy", "sell"):
            assert tools[name]["annotations"]["readOnlyHint"] is False

    def test_the_trade_tools_are_the_ones_they_were(self):
        """The ticket reuses `buy`/`sell` unchanged: no new trading path."""
        tools = _tools()
        for name, size_key in (("buy", "amount_usd"), ("sell", "shares")):
            assert tools[name]["inputSchema"]["required"] == [
                "slug_or_id", "outcome", size_key,
            ]
            assert tools[name]["inputSchema"]["properties"]["order_type"]["default"] == "fok"


# ---------------------------------------------------------------------------
# The order ticket: the payload Confirm sends
# ---------------------------------------------------------------------------


def _trade_pairs() -> dict[str, str]:
    """The `ticket.field -> arguments key` pairs parsed out of the JS builder."""
    source = _norm(_js("buildTradeRequest"))
    arguments = source.split("var args = {")[1].split("};")[0]
    return dict(re.findall(r"(\w+): ticket\.(\w+)", arguments))


def _branch_key(side: str) -> str:
    """The `args.<key> = ticket.amount` the JS uses for one side."""
    source = _norm(_js("buildTradeRequest"))
    branches = source.split("else {")[0], source.split("} else {")[1]
    found = re.findall(r"args\.(\w+) = ticket\.amount", branches[0 if side == "buy" else 1])
    assert len(found) == 1, f"no single amount key for a {side}"
    return found[0]


class TestTicketPayload:
    def test_the_builder_is_the_pinned_one(self):
        assert _norm(_js("buildTradeRequest")) == (
            "function buildTradeRequest(ticket) { var args = { slug_or_id: "
            "ticket.slug, outcome: ticket.outcome, account: ticket.account }; if "
            '(ticket.side === "buy") { args.amount_usd = ticket.amount; } else '
            "{ args.shares = ticket.amount; } return { method: TRADE_METHOD, "
            "params: { name: ticket.side === \"buy\" ? BUY_TOOL : SELL_TOOL, "
            "arguments: args } }; }"
        )

    def test_it_sends_exactly_one_tools_call(self):
        source = _norm(_js("buildTradeRequest"))
        assert 'method: TRADE_METHOD' in source
        assert re.search(r"var TRADE_METHOD = \"tools/call\";", _script())

    def test_argument_keys_are_the_trade_tools_own(self):
        pairs = _trade_pairs()
        assert pairs == {
            "slug_or_id": "slug",
            "outcome": "outcome",
            "account": "account",
        }
        assert _branch_key("buy") == "amount_usd"
        assert _branch_key("sell") == "shares"

        tools = _tools()
        for name in ("buy", "sell"):
            properties = tools[name]["inputSchema"]["properties"]
            assert set(_trade_pairs()) <= set(properties), name
        assert set(_trade_pairs()) | {_branch_key("buy")} <= set(
            tools["buy"]["inputSchema"]["properties"]
        )
        assert set(_trade_pairs()) | {_branch_key("sell")} <= set(
            tools["sell"]["inputSchema"]["properties"]
        )

    def test_the_buy_payload_fills_the_buy_tool(self, sample_market, sample_order_book):
        """Feed the parsed keys to the real tool: the payload it accepts is ours."""
        _seed_account(sample_market, sample_order_book)
        ticket = {
            "slug": sample_market.slug,
            "outcome": "yes",
            "account": "default",
            "amount": 250.0,
        }
        args = {key: ticket[field] for key, field in _trade_pairs().items()}
        args[_branch_key("buy")] = ticket["amount"]
        assert set(args) == {"slug_or_id", "outcome", "account", "amount_usd"}

        envelope = json.loads(mcp_server.buy(**args))
        assert envelope["ok"] is True
        trade = envelope["data"]["trade"]
        assert trade["side"] == "buy"
        assert trade["outcome"] == "yes"
        assert trade["market_slug"] == sample_market.slug
        assert trade["amount_usd"] == pytest.approx(250.0)
        assert trade["shares"] == pytest.approx(
            _walk_book(
                [{"price": a.price, "size": a.size} for a in sample_order_book.asks],
                250.0,
                "buy",
            )["shares"],
        )
        assert envelope["data"]["account"]["cash"] == pytest.approx(10_000.0 - 250.0)

    def test_the_sell_payload_fills_the_sell_tool(self, sample_market, sample_order_book):
        """Same payload shape, the sell branch: shares, not USD."""
        _seed_account(sample_market, sample_order_book)
        mcp_server.buy(sample_market.slug, "yes", 250.0)

        ticket = {
            "slug": sample_market.slug,
            "outcome": "yes",
            "account": "default",
            "amount": 100.0,
        }
        args = {key: ticket[field] for key, field in _trade_pairs().items()}
        args[_branch_key("sell")] = ticket["amount"]
        assert set(args) == {"slug_or_id", "outcome", "account", "shares"}

        envelope = json.loads(mcp_server.sell(**args))
        assert envelope["ok"] is True
        trade = envelope["data"]["trade"]
        assert trade["side"] == "sell"
        assert trade["outcome"] == "yes"
        assert trade["shares"] == pytest.approx(100.0)
        assert trade["avg_price"] == pytest.approx(0.64)
        assert trade["amount_usd"] == pytest.approx(64.0)

    def test_the_tool_name_follows_the_side(self):
        source = _norm(_js("buildTradeRequest"))
        assert 'name: ticket.side === "buy" ? BUY_TOOL : SELL_TOOL' in source


class TestTicketGuards:
    def test_a_second_click_cannot_resend_the_ticket(self):
        """One click, one `tools/call`: the click handler is the guard."""
        assert _norm(_js("confirm")).startswith(
            "function confirm() { if (!state.preview || state.inFlight "
            "|| state.submitted) { return; }"
        )

    def test_confirm_stays_disabled_until_a_preview_and_after_a_send(self):
        html = _view_html()
        assert "confirmButton.disabled = !state.preview || state.inFlight || state.submitted;" in html
        assert 'confirmButton.textContent = state.submitted ? "Sent" : "Confirm";' in html

    def test_an_edit_invalidates_the_preview(self):
        assert _norm(_js("editTicket")) == (
            "function editTicket(changes) { Object.keys(changes).forEach(function "
            "(key) { state.draft[key] = changes[key]; }); state.preview = null; "
            "state.status = null; if (!state.inFlight) { state.submitted = false; } "
            "if (changes.side) { state.receipt = null; } render(); }"
        )

    def test_a_new_market_drops_the_old_preview(self):
        """A host push of another market must not leave the last ticket armed."""
        body = _norm(_js("applyToolResult"))
        assert (
            "state.preview = null; state.status = null; state.draft.amount = \"\"; "
            "if (!state.inFlight) { state.submitted = false; }"
        ) in body

    def test_only_one_call_is_in_flight_at_a_time(self):
        assert html_calls_sendrequest("confirm") == 1


def html_calls_sendrequest(name: str) -> int:
    """How many times a function sends a message to the host."""
    return _norm(_js(name)).count("sendRequest(")


class TestPreviewMaths:
    def test_the_walker_is_the_pinned_one(self):
        source = _norm(_js("walkBook"))
        assert "var take = side === \"buy\" ? Math.min(size, remaining / px) : " \
            "Math.min(size, remaining);" in source
        assert "return side === \"buy\" ? pa - pb : pb - pa;" in source
        assert source.endswith(
            "return { shares: filledShares, cost: spent, avg_price: filledShares "
            "> 0 ? spent / filledShares : null, filled: remaining <= 1e-9 }; }"
        )

    def test_a_buy_walks_the_asks(self, sample_order_book):
        levels = [{"price": a.price, "size": a.size} for a in sample_order_book.asks]
        # $50 never reaches the second level: 50 / 0.66 shares at the best ask.
        estimate = _walk_book(levels, 50.0, "buy")
        assert estimate["shares"] == pytest.approx(50.0 / 0.66)
        assert estimate["avg_price"] == pytest.approx(0.66)
        assert estimate["cost"] == pytest.approx(50.0)
        assert estimate["filled"] is True

        # $250 spans three levels and averages above the best ask.
        estimate = _walk_book(levels, 250.0, "buy")
        assert estimate["shares"] == pytest.approx(80 + 120 + 116.8 / 0.68)
        assert estimate["cost"] == pytest.approx(250.0)
        assert estimate["avg_price"] == pytest.approx(0.672468, abs=1e-6)
        assert estimate["filled"] is True

    def test_a_sell_walks_the_bids(self, sample_order_book):
        levels = [{"price": b.price, "size": b.size} for b in sample_order_book.bids]
        estimate = _walk_book(levels, 100.0, "sell")
        assert estimate["shares"] == pytest.approx(100.0)
        assert estimate["avg_price"] == pytest.approx(0.64)
        assert estimate["cost"] == pytest.approx(64.0)

        # Every bid level together: 150 @ 0.64, 200 @ 0.63, 300 @ 0.62, 350 @ 0.60.
        estimate = _walk_book(levels, 1000.0, "sell")
        assert estimate["shares"] == pytest.approx(1000.0)
        assert estimate["cost"] == pytest.approx(150 * 0.64 + 200 * 0.63 + 300 * 0.62 + 350 * 0.60)
        assert estimate["avg_price"] == pytest.approx(0.618)

    def test_a_thin_book_reports_a_partial_fill(self, sample_order_book):
        levels = [{"price": sample_order_book.asks[0].price, "size": 80.0}]
        estimate = _walk_book(levels, 500.0, "buy")
        assert estimate["shares"] == pytest.approx(80.0)
        assert estimate["cost"] == pytest.approx(52.8)
        assert estimate["filled"] is False

    def test_an_empty_book_has_no_price(self):
        estimate = _walk_book([], 50.0, "buy")
        assert estimate["shares"] == 0
        assert estimate["avg_price"] is None
        assert estimate["filled"] is False

    def test_the_preview_reads_the_side_it_trades(self):
        source = _norm(_js("preview"))
        assert "var levels = ticket.side === \"buy\" ? (book ? book.asks : null) : " \
            "(book ? book.bids : null);" in source
        assert "var estimate = walkBook(levels, ticket.amount, ticket.side);" in source

    def test_the_fee_is_never_guessed(self):
        """The payload carries no fee rate, so the preview says it is charged
        at fill instead of showing a number the server did not send."""
        assert 'kpi("Est. fee", "at fill", "the market\'s fee schedule")' in _view_html()

    def test_the_preview_refuses_orders_the_engine_would_reject(self):
        source = _norm(_js("preview"))
        assert "if (ticket.side === \"sell\" && estimate.cost < MIN_ORDER_USD) {" in source
        assert _norm(_js("ticketProblem")).startswith(
            "function ticketProblem(ticket) { if (ticket.amount === null"
        )


class TestTextFallback:
    def test_the_instruction_is_the_pinned_one(self):
        assert _norm(_js("ticketInstructionText")) == (
            "function ticketInstructionText(ticket) { var amount = ticket.amount "
            '!== null && isFinite(ticket.amount) ? ticket.amount : null; var what '
            "= upper(ticket.outcome); if (amount !== null) { what = "
            'ticket.side === "buy" ? "$" + amount.toFixed(2) + " of " + what : '
            'amount.toFixed(2) + " " + what + " shares"; } return "This host '
            'will not run tools from a view. Ask the assistant to " + ticket.side '
            '+ " " + what + " on " + ticket.slug + " (paper trading)."; }'
        )

    def test_the_fallback_names_the_trade_in_words(self):
        """The sentence is assembled from these pieces — pin each one."""
        source = _js("ticketInstructionText")
        for piece in (
            'return "This host will not run tools from a view. Ask the assistant to "\n'
            '      + ticket.side + " " + what + " on " + ticket.slug + " (paper trading).";',
            '? "$" + amount.toFixed(2) + " of " + what',
            ': amount.toFixed(2) + " " + what + " shares";',
        ):
            assert piece in source, piece

    def test_an_untouched_ticket_still_produces_a_sentence(self):
        """The fallback renders before the user types anything: no amount yet."""
        source = _norm(_js("ticketInstructionText"))
        assert "var amount = ticket.amount !== null && isFinite(ticket.amount) " \
            "? ticket.amount : null;" in source
        assert "if (amount !== null) {" in source
        # Only the sized form calls toFixed, so a null amount cannot throw.
        assert source.count("toFixed(2)") == 2

    def test_the_ticket_is_replaced_by_the_instruction_without_server_tools(self):
        html = _view_html()
        assert "box.appendChild(el(\"p\", \"fallback\", ticketInstructionText(ticket)));" in html
        assert "if (!canCallServer()) {" in html

    def test_a_dropped_call_falls_back_to_the_same_instruction(self):
        source = _norm(_js("confirm"))
        assert ".catch(function (err) {" in source
        assert "ticketInstructionText(state.preview ? state.preview.ticket : readDraft())" \
            in source


# ---------------------------------------------------------------------------
# The envelopes the view parses
# ---------------------------------------------------------------------------


class TestEnvelopeContract:
    def test_reads_every_market_field(self, sample_market):
        engine = mcp_server._get_engine()
        engine.api.get_market = MagicMock(return_value=sample_market)
        envelope = json.loads(mcp_server.get_market(sample_market.slug))
        assert envelope["ok"] is True

        names = _referenced_names(_view_html())
        missing = sorted(key for key in envelope["data"] if key not in names)
        assert missing == []

    def test_reads_the_envelope_keys(self):
        names = _referenced_names(_view_html())
        assert {"ok", "data", "error", "code", "content", "arguments", "type", "text"} \
            <= names

    def test_reads_the_order_book_fields(self, sample_market, sample_order_book):
        envelope = _book_payload(sample_order_book, sample_market.slug, "yes")
        names = _referenced_names(_view_html())
        assert sorted(key for key in envelope["data"] if key not in names) == []
        # The levels the walk reads.
        assert "price" in names and "size" in names

    def test_reads_every_trade_receipt_field(self, sample_market, sample_order_book):
        _seed_account(sample_market, sample_order_book)
        receipt = json.loads(mcp_server.buy(sample_market.slug, "yes", 250.0))["data"]
        assert receipt["trade"]["id"] == 1

        names = _referenced_names(_view_html())
        missing = sorted(
            key for key in receipt["trade"] if key not in names
        ) + sorted(key for key in receipt["account"] if key not in names)
        assert missing == []

    def test_a_trade_result_is_not_mistaken_for_a_market(self):
        """The host pushes tool-result for this view's own buy/sell too."""
        assert _norm(_js("isMarket")) == (
            'function isMarket(data) { return !!data && typeof data === "object" '
            '&& !Array.isArray(data) && typeof data.question === "string"; }'
        )
        source = _norm(_js("applyToolResult"))
        assert "if (!isMarket(envelope.data)) { return; }" in source

    def test_handles_an_error_envelope(self, sample_market):
        engine = mcp_server._get_engine()
        engine.api.get_market = MagicMock(
            side_effect=MarketNotFoundError("no-such-market-xyz")
        )
        envelope = json.loads(mcp_server.get_market("no-such-market-xyz"))
        assert envelope["ok"] is False
        assert envelope["code"] == "market_not_found"
        html = _view_html()
        assert "renderError" in html
        assert "envelope.error" in html

    def test_handles_a_tool_result_with_no_text(self):
        assert _norm(_js("extractText")).startswith(
            "function extractText(result) { if (!result || !Array.isArray"
        )
        assert "The host sent a tool result with no text content." in _view_html()

    def test_handles_a_market_without_a_price_list(self, sample_market):
        """Gamma omits outcomePrices on some markets: the row shows a dash."""
        engine = mcp_server._get_engine()
        bare = MagicMock(**{
            "slug": sample_market.slug,
            "question": sample_market.question,
            "condition_id": sample_market.condition_id,
            "outcomes": ["Yes", "No"],
            "outcome_prices": [0.0, 0.0],
            "active": True,
            "accepting_orders": True,
            "neg_risk": False,
            "volume": 0.0,
            "liquidity": 0.0,
            "closed": False,
            "end_date": "",
        })
        engine.api.get_market = MagicMock(return_value=bare)
        envelope = json.loads(mcp_server.get_market(sample_market.slug))
        assert envelope["data"]["outcome_prices"] == [0.0, 0.0]
        assert _norm(_js("price")) == (
            'function price(value) { return num(value) ? value.toFixed(3) : "—"; }'
        )
        assert _norm(_js("day")) == (
            'function day(value) { if (typeof value !== "string" || value.length '
            '< 10) { return "—"; } return value.slice(0, 10); }'
        )