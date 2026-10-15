"""MCP server exposing pm-trader as tools for AI agents.

Run with:
    pm-trader mcp                  # stdio transport (default)
    pm-trader-mcp --transport streamable-http --host 127.0.0.1 --port 8000
    python -m pm_trader.mcp_server # direct execution
"""

from __future__ import annotations

import argparse
import asyncio
import contextvars
import functools
import importlib.metadata
import importlib.resources
import json
import os
import re
import sys
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import IO, Annotated, Any

from mcp.server import MCPServer
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.types import ToolAnnotations
from pydantic import Field

from pm_trader.engine import Engine
from pm_trader.kalshi_api import KalshiClient
from pm_trader.models import NotInitializedError, is_valid_account_name
from pm_trader.remote_auth import (
    DEFAULT_IDLE_DAYS,
    DEFAULT_MAX_ACCOUNTS,
    DEFAULT_RATE_LIMIT_PER_MINUTE,
    RemoteAuthConfig,
    RemoteAuthConfigError,
    acquire_server_lock,
    check_account_cap,
    configure_server,
    prune_remote,
    user_root,
)

DEFAULT_DATA_DIR = Path.home() / ".pm-trader" / "default"

_SERVER_INSTRUCTIONS = """\
pm-trader is a paper-trading simulator for Polymarket: real markets, real order
books and real prices, but every dollar is fake — no wallet, no API keys, no
real money at risk.

Suggested workflow: init_account (creates the paper account with starting
balance) -> search_markets / list_markets (find something to trade) ->
get_order_book (check live liquidity before sizing) -> buy / sell (or
place_limit_order for resting orders) -> portfolio / stats (track P&L and
performance over time).

Call the `trading_playbook` prompt (or read the `skill://trading-playbook`
resource) for the full trading methodology this server was built around."""


def _server_version() -> str:
    """Return the installed package version for MCP serverInfo."""
    try:
        return importlib.metadata.version("polymarket-paper-trader")
    except importlib.metadata.PackageNotFoundError:
        return "0.0.0+unknown"


def _skill_body() -> str:
    """Return SKILL.md's body (YAML frontmatter stripped), packaged via the
    symlink at ``pm_trader/_skill/SKILL.md``."""
    text = importlib.resources.files("pm_trader").joinpath(
        "_skill/SKILL.md"
    ).read_text(encoding="utf-8")
    return re.sub(r"\A---\n.*?\n---\n", "", text, count=1, flags=re.DOTALL)


# MCP Apps (the `io.modelcontextprotocol/ui` extension) renders a tool result by
# fetching the HTML resource its tool metadata points at, then handing the result
# to that document over the JSON-RPC postMessage bridge. Hosts without UI support
# ignore the metadata and read the same text result as before.
UI_RESOURCE_URI = "ui://pm-trader/portfolio"
MARKET_UI_RESOURCE_URI = "ui://pm-trader/market"
UI_MIME_TYPE = "text/html;profile=mcp-app"


def _ui_tool_meta(uri: str) -> dict[str, Any]:
    """Return the tool `_meta` pointing a tool's result at a packaged view.

    `_meta.ui.resourceUri` is the standardized field; `openai/outputTemplate` is
    ChatGPT's documented compatibility alias and `ui/resourceUri` is the spec's
    deprecated (still served) flat form.
    """
    return {
        "ui": {"resourceUri": uri},
        "openai/outputTemplate": uri,
        "ui/resourceUri": uri,
    }


_UI_TOOL_META = _ui_tool_meta(UI_RESOURCE_URI)
_MARKET_UI_TOOL_META = _ui_tool_meta(MARKET_UI_RESOURCE_URI)


def _ui_body(relpath: str = "ui/portfolio.html") -> str:
    """Return the packaged MCP Apps view HTML."""
    return importlib.resources.files("pm_trader").joinpath(relpath).read_text(
        encoding="utf-8"
    )


mcp = MCPServer(
    "pm-trader", version=_server_version(), instructions=_SERVER_INSTRUCTIONS
)


@mcp.prompt(
    name="trading_playbook",
    description="The full pm-trader trading methodology and workflow guide.",
)
def trading_playbook() -> str:
    """Return the pm-trader SKILL.md playbook body."""
    return _skill_body()


@mcp.resource(
    "skill://trading-playbook",
    name="trading_playbook",
    title="pm-trader trading playbook",
    description="The full pm-trader trading methodology and workflow guide.",
    mime_type="text/markdown",
)
def trading_playbook_resource() -> str:
    """Return the pm-trader SKILL.md playbook body."""
    return _skill_body()


@mcp.resource(
    UI_RESOURCE_URI,
    name="portfolio",
    title="pm-trader portfolio dashboard",
    description="Interactive portfolio dashboard for the portfolio and stats tools.",
    mime_type=UI_MIME_TYPE,
    meta={"ui": {"prefersBorder": True}},
)
def portfolio_view() -> str:
    """Return the portfolio dashboard HTML for MCP Apps hosts."""
    return _ui_body()


@mcp.resource(
    MARKET_UI_RESOURCE_URI,
    name="market",
    title="pm-trader market card and order ticket",
    description="Market card with a paper order ticket for the market lookup tool.",
    mime_type=UI_MIME_TYPE,
    meta={"ui": {"prefersBorder": True}},
)
def market_view() -> str:
    """Return the market card and order ticket HTML for MCP Apps hosts."""
    return _ui_body("ui/market.html")


# MCP SDK 2.x dispatches tools/call concurrently and runs sync tool functions
# on arbitrary anyio worker threads; the Engine's SQLite connection is bound
# to the thread that opened it. Serialize every tool call on one dedicated
# worker thread — the serialized execution semantics SDK 1.x provided.
_tool_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pm-trader-mcp")

# ToolAnnotations describe each tool's behavior to directory-listed hosts.
# ``destructive_hint`` is only meaningful when ``read_only_hint`` is false, so
# it is set explicitly false for read-only tools. ``idempotent_hint`` marks
# calls that are a no-op when repeated with the same arguments. ``open_world_hint``
# marks tools that reach the live Polymarket APIs.
_READ_ONLY_LIVE = ToolAnnotations(
    read_only_hint=True, destructive_hint=False,
    idempotent_hint=True, open_world_hint=True,
)
_READ_ONLY_LOCAL = ToolAnnotations(
    read_only_hint=True, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False,
)
_WRITE_LIVE = ToolAnnotations(
    read_only_hint=False, destructive_hint=False,
    idempotent_hint=False, open_world_hint=True,
)
_WRITE_LIVE_IDEMPOTENT = ToolAnnotations(
    read_only_hint=False, destructive_hint=False,
    idempotent_hint=True, open_world_hint=True,
)
_DESTRUCTIVE_LOCAL_IDEMPOTENT = ToolAnnotations(
    read_only_hint=False, destructive_hint=True,
    idempotent_hint=True, open_world_hint=False,
)


def _tool(
    *,
    title: str,
    annotations: ToolAnnotations,
    meta: dict[str, Any] | None = None,
) -> Callable[[Callable[..., str]], Callable[..., str]]:
    """Register the decorated function as an MCP tool.

    ``title`` is the human-readable label and ``annotations`` the
    read-only/destructive/idempotent/open-world hints; ``meta`` carries MCP Apps
    UI metadata for the tools that render a view. The registered callable
    is an async wrapper that submits the function to the single-worker
    executor; ``functools.wraps`` keeps the name, docstring and signature
    identical so the advertised tool schemas are unchanged. The module
    attribute stays the plain sync function for direct calls.
    """

    def register(fn: Callable[..., str]) -> Callable[..., str]:
        @functools.wraps(fn)
        async def _wrapper(**kwargs: object) -> str:
            limited = _rate_limit_error()
            if limited is not None:
                return limited
            loop = asyncio.get_running_loop()
            # The tool body runs on the worker thread, so carry the request's
            # contextvars across: handlers that read request-scoped state (the
            # verified MCP identity in authenticated remote mode) must see the
            # values of the request that is calling them.
            ctx = contextvars.copy_context()
            return await loop.run_in_executor(
                _tool_executor, lambda: ctx.run(functools.partial(fn, **kwargs))
            )

        mcp.add_tool(_wrapper, title=title, annotations=annotations, meta=meta)
        return fn

    return register


# ---------------------------------------------------------------------------
# Engine lifecycle — one Engine per server session
# ---------------------------------------------------------------------------

_engine: Engine | None = None

# Lock file handle held for the lifetime of a remote server (see acquire_server_lock).
_server_lock: IO[str] | None = None

# Set only when the opt-in remote mode is enabled (--auth-issuer and friends);
# see docs/remote-mcp.md. None means the local ~/.pm-trader tree.
_remote: RemoteAuthConfig | None = None


def _validate_account_name(account: str) -> str:
    """Validate account name to prevent path traversal."""
    if not is_valid_account_name(account):
        raise ValueError(f"Invalid account name: {account!r}")
    return account


def _data_root() -> Path:
    """Return the directory holding the accounts this session may touch.

    Stdio and unauthenticated HTTP see the local ``~/.pm-trader`` tree exactly as
    before. In authenticated remote mode every verified ``(issuer, subject)``
    pair gets its own root directory, named by a hash, and the ``account``
    argument is only a sub-account inside it — so one user can never read or
    trade another user's accounts, whatever they pass as ``account``.
    """
    if _remote is None:
        return Path.home() / ".pm-trader"
    token = get_access_token()
    if token is None or not token.subject or not (token.claims or {}).get("iss"):
        raise ValueError("Remote MCP tools require a verified bearer token")
    return user_root(str(token.claims["iss"]), token.subject)


def _rate_limit_error() -> str | None:
    """Return a RATE_LIMITED envelope when the remote caller is over its limit.

    Always None outside authenticated remote mode, and for a call without a
    verified principal (the data-root check refuses that one itself).
    """
    if _remote is None:
        return None
    try:
        key = _data_root().name
    except ValueError:
        return None
    if _remote.limiter.allow(key):
        return None
    return _err(
        f"Rate limit exceeded: at most {_remote.rate_limit_per_minute} "
        "tool calls per minute",
        "RATE_LIMITED",
    )


def _get_engine(account: str | None = None, *, create: bool = False) -> Engine:
    """Return the current Engine, creating if needed.

    ``account=None`` is the account-independent path (market tools): the
    ``default`` directory is used as a data cache. In remote mode an *explicit*
    account that does not exist yet raises ``NotInitializedError`` instead of
    creating a directory, unless ``create`` is set (``init_account`` only), and
    creating one is subject to the per-subject account cap.
    """
    global _engine
    name = _validate_account_name("default" if account is None else account)
    root = _data_root()
    data_dir = root / name
    if _remote is not None and account is not None and not data_dir.exists():
        if not create:
            raise NotInitializedError()
        check_account_cap(root, name, _remote.max_accounts)
    if _engine is None or _engine.db.data_dir != data_dir:
        if _engine is not None:
            _engine.close()
        _engine = Engine(data_dir)
    return _engine


def _ok(data: object) -> str:
    """Wrap data in the standard pm-trader JSON envelope."""
    return json.dumps({"ok": True, "data": data})


def _err(msg: str, code: str = "error") -> str:
    """Return an error envelope."""
    return json.dumps({"ok": False, "error": msg, "code": code})


_LIMIT_CODES = frozenset({"ACCOUNT_LIMIT", "RATE_LIMITED"})


def _err_from(e: Exception, code: str | None = None) -> str:
    """Return an error envelope from an exception, sanitizing internals.

    ``code`` overrides the code of exposed (SimError/ValueError/TypeError) errors.
    """
    from pm_trader.models import SimError

    if isinstance(e, (SimError, ValueError, TypeError)):
        own = getattr(e, "code", type(e).__name__)
        # Limit errors keep their own code whatever the tool would label them.
        return _err(str(e), own if own in _LIMIT_CODES else code or own)
    return _err("Internal error", "internal_error")


def _market_to_dict(m: object) -> dict:
    """Serialize a Market to the MCP wire format."""
    return {
        "slug": m.slug,
        "question": m.question,
        "condition_id": m.condition_id,
        "outcomes": m.outcomes,
        "outcome_prices": m.outcome_prices,
        "active": m.active,
        "accepting_orders": m.accepting_orders,
        "neg_risk": m.neg_risk,
        "volume": m.volume,
        "liquidity": m.liquidity,
        "closed": m.closed,
        "end_date": m.end_date,
    }


_Account = Annotated[str, Field(description=(
    "Paper account name; any name without '/', '\\', '..' or surrounding "
    "spaces. Each name is a separate local account. Default 'default'."
))]
_MarketRef = Annotated[str, Field(description=(
    "Market slug or condition ID. For a Kalshi market use the slug "
    "'kalshi:<TICKER>', e.g. 'kalshi:KXNBAGAME-26OCT16DENLAL-LAL'."
))]
_Outcome = Annotated[str, Field(description=(
    "Outcome to trade, e.g. 'yes' or 'no' (case-insensitive); must match one "
    "of the market's outcomes."
))]
_OrderType = Annotated[str, Field(description=(
    "'fok' (fill-or-kill: all or nothing) or 'fak' (fill-and-kill: partial "
    "fills allowed). Default 'fok'."
))]


# ---------------------------------------------------------------------------
# Account tools
# ---------------------------------------------------------------------------


@_tool(title="Initialize paper account", annotations=_DESTRUCTIVE_LOCAL_IDEMPOTENT)
def init_account(
    balance: Annotated[float, Field(description=(
        "Starting cash in USD, e.g. 10000. Default 10000."
    ))] = 10_000.0,
    account: _Account = "default",
) -> str:
    """Create a paper trading account with a starting USD balance.

    Writes the local account database; calling it again on an existing account
    replaces its balance. Run it before any trade; use reset_account to wipe
    trades and positions instead.
    """
    try:
        engine = _get_engine(account, create=True)
        acct = engine.init_account(balance)
        return _ok({
            "cash": acct.cash,
            "starting_balance": acct.starting_balance,
        })
    except Exception as e:
        return _err_from(e)


@_tool(title="Get account balance", annotations=_READ_ONLY_LIVE)
def get_balance(account: _Account = "default") -> str:
    """Get an account's cash, reserved and available cash, positions value and P&L.

    Reads the local account and prices positions with live market data. Use
    portfolio for per-position detail and stats for performance metrics.
    """
    try:
        engine = _get_engine(account)
        bal = engine.get_balance()
        return _ok(bal)
    except Exception as e:
        return _err_from(e, "not_initialized")


@_tool(title="Reset account", annotations=_DESTRUCTIVE_LOCAL_IDEMPOTENT)
def reset_account(account: _Account = "default") -> str:
    """Delete an account's trades, positions and orders and reset its balance.

    Irreversible local change. Use init_account to set a new starting balance
    afterwards; use cancel_all_orders to remove only pending orders.
    """
    try:
        engine = _get_engine(account)
        engine.reset()
        return _ok({"reset": True})
    except Exception as e:
        return _err_from(e)


# ---------------------------------------------------------------------------
# Market data tools
# ---------------------------------------------------------------------------


MAX_RESULTS = 100


def _clamp_limit(limit: int, cap: int = MAX_RESULTS) -> int:
    """Clamp a caller-supplied limit to [1, cap] (a negative SQLite LIMIT means unlimited)."""
    return max(1, min(limit, cap))


@_tool(title="Search markets", annotations=_READ_ONLY_LIVE)
def search_markets(
    query: Annotated[str, Field(description=(
        "Free-text search, e.g. 'bitcoin' or 'fed rate cut'."
    ))],
    limit: Annotated[int, Field(description=(
        "Maximum markets to return, clamped to 1-100. Default 10."
    ))] = 10,
) -> str:
    """Search Polymarket markets by text and return slug, question, prices and volume.

    Queries the live Polymarket API and changes nothing. Searches Polymarket
    only: use kalshi_markets to find Kalshi markets, list_markets to browse by
    volume, or get_markets_by_tag to browse a category.
    """
    try:
        engine = _get_engine()
        markets = engine.api.search_markets(query, limit=_clamp_limit(limit))
        return _ok([_market_to_dict(m) for m in markets])
    except Exception as e:
        return _err_from(e)


@_tool(title="List markets", annotations=_READ_ONLY_LIVE)
def list_markets(
    limit: Annotated[int, Field(description=(
        "Maximum markets to return, clamped to 1-100. Default 20."
    ))] = 20,
    sort_by: Annotated[str, Field(description=(
        "'volume' or 'liquidity', highest first. Default 'volume'."
    ))] = "volume",
) -> str:
    """List active Polymarket markets, highest volume or liquidity first.

    Queries the live Polymarket API and changes nothing. Use search_markets
    for a keyword, get_markets_by_tag for a category, or kalshi_markets for
    Kalshi.
    """
    try:
        engine = _get_engine()
        markets = engine.api.list_markets(limit=_clamp_limit(limit), sort_by=sort_by)
        return _ok([_market_to_dict(m) for m in markets])
    except Exception as e:
        return _err_from(e)


@_tool(title="Get market", annotations=_READ_ONLY_LIVE, meta=_MARKET_UI_TOOL_META)
def get_market(
    slug_or_id: _MarketRef,
) -> str:
    """Get one market's question, outcome prices, volume, liquidity and status.

    Takes a Polymarket slug or condition ID, or a Kalshi market as
    'kalshi:<TICKER>'. Reads live data and changes nothing; use get_order_book
    for depth.
    """
    try:
        engine = _get_engine()
        m = engine.api.get_market(slug_or_id)
        return _ok(_market_to_dict(m))
    except Exception as e:
        return _err_from(e, "market_not_found")


@_tool(title="Get order book", annotations=_READ_ONLY_LIVE)
def get_order_book(
    slug_or_id: _MarketRef,
    outcome: Annotated[str, Field(description=(
        "Outcome whose book to read, 'yes' or 'no'. Default 'yes'."
    ))] = "yes",
) -> str:
    """Get the live asks and bids (price and size) for one market outcome.

    Works for Polymarket slugs and for Kalshi markets as 'kalshi:<TICKER>'.
    Reads live data and changes nothing; check it before sizing a buy or sell.
    """
    try:
        engine = _get_engine()
        market = engine.api.get_market(slug_or_id)
        token_id = market.get_token_id(outcome)
        book = engine.api.get_order_book(token_id)
        return _ok({
            "market_slug": market.slug,
            "outcome": outcome.lower(),
            "asks": [{"price": a.price, "size": a.size} for a in book.asks],
            "bids": [{"price": b.price, "size": b.size} for b in book.bids],
        })
    except Exception as e:
        return _err_from(e, "order_book_error")


_KALSHI_STATUSES = ("open", "closed", "settled")


@_tool(title="List Kalshi markets", annotations=_READ_ONLY_LIVE)
def kalshi_markets(
    limit: Annotated[int, Field(description=(
        "Maximum markets to return, clamped to 1-100. Default 20."
    ))] = 20,
    status: Annotated[str, Field(description=(
        "'open', 'closed' or 'settled'. Default 'open'."
    ))] = "open",
    series_ticker: Annotated[str | None, Field(description=(
        "Only markets in this Kalshi series ticker. Default none (all)."
    ))] = None,
    event_ticker: Annotated[str | None, Field(description=(
        "Only markets in this Kalshi event ticker. Default none (all)."
    ))] = None,
) -> str:
    """List Kalshi markets for discovery; this tool is read-only and places no trade.

    Reads the live Kalshi API. To trade a result, pass 'kalshi:<TICKER>' as
    slug_or_id to buy, sell or place_limit_order. Use search_markets or
    list_markets for Polymarket instead.
    """
    if status not in _KALSHI_STATUSES:
        return _err(f"status must be one of {', '.join(_KALSHI_STATUSES)}", "invalid_status")
    try:
        client = KalshiClient(_get_engine().db)
        try:
            markets = client.list_markets(
                limit=_clamp_limit(limit), status=status,
                series_ticker=series_ticker, event_ticker=event_ticker,
            )
        finally:
            client.close()
        return _ok([_market_to_dict(m) for m in markets])
    except Exception as e:
        return _err_from(e)


@_tool(title="Get Kalshi market", annotations=_READ_ONLY_LIVE)
def kalshi_market(
    ticker: Annotated[str, Field(description=(
        "Kalshi market ticker, e.g. 'KXNBAGAME-26OCT16DENLAL-LAL'."
    ))],
) -> str:
    """Get one Kalshi market by ticker; read-only discovery, no trade is placed.

    Reads the live Kalshi API. To trade it, pass 'kalshi:<TICKER>' as
    slug_or_id to buy or sell. The same slug also works in get_market.
    """
    try:
        client = KalshiClient(_get_engine().db)
        try:
            m = client.get_market(ticker)
        finally:
            client.close()
        return _ok(_market_to_dict(m))
    except Exception as e:
        return _err_from(e, "market_not_found")


@_tool(title="Get Kalshi order book", annotations=_READ_ONLY_LIVE)
def kalshi_book(
    ticker: Annotated[str, Field(description=(
        "Kalshi market ticker, e.g. 'KXNBAGAME-26OCT16DENLAL-LAL'."
    ))],
    depth: Annotated[int, Field(description=(
        "Maximum price levels per side, clamped to 1-100. Default 10."
    ))] = 10,
) -> str:
    """Get the live YES order book for a Kalshi market, best levels first; read-only.

    Kalshi publishes bids only, so asks are derived from NO bids (a NO bid at
    p is a YES ask at 1 - p). get_order_book returns the same book for the
    slug 'kalshi:<TICKER>' and also covers the NO side.
    """
    try:
        client = KalshiClient(_get_engine().db)
        try:
            book = client.get_order_book(ticker)
        finally:
            client.close()
        depth = _clamp_limit(depth)
        asks = sorted(book.asks, key=lambda l: l.price)[:depth]
        bids = sorted(book.bids, key=lambda l: l.price, reverse=True)[:depth]
        return _ok({
            "ticker": ticker,
            "asks": [{"price": a.price, "size": a.size} for a in asks],
            "bids": [{"price": b.price, "size": b.size} for b in bids],
        })
    except Exception as e:
        return _err_from(e, "order_book_error")


@_tool(title="List market tags", annotations=_READ_ONLY_LIVE)
def get_tags() -> str:
    """List all Polymarket market categories (tags) with their slugs.

    Reads the live Polymarket API and changes nothing. Pass a returned slug to
    get_markets_by_tag.
    """
    try:
        engine = _get_engine()
        tags = engine.api.get_tags()
        return _ok(tags)
    except Exception as e:
        return _err_from(e)


@_tool(title="List markets by tag", annotations=_READ_ONLY_LIVE)
def get_markets_by_tag(
    tag_slug: Annotated[str, Field(description=(
        "Tag slug from get_tags, e.g. 'politics'. An unknown slug returns an "
        "empty list."
    ))],
    limit: Annotated[int, Field(description=(
        "Maximum markets to return, clamped to 1-100. Default 20."
    ))] = 20,
) -> str:
    """List open Polymarket markets in one category tag.

    Reads the live Polymarket API and changes nothing. Get slugs from get_tags;
    use search_markets for a keyword or list_markets for the top markets
    overall.
    """
    try:
        engine = _get_engine()
        markets = engine.api.get_markets_by_tag(
            tag_slug, limit=_clamp_limit(limit),
        )
        return _ok([_market_to_dict(m) for m in markets])
    except Exception as e:
        return _err_from(e)


@_tool(title="Get event", annotations=_READ_ONLY_LIVE)
def get_event(
    slug: Annotated[str, Field(description=(
        "Polymarket event slug, e.g. the event part of a polymarket.com/event/ URL."
    ))],
) -> str:
    """Get a Polymarket event: the group of related markets sharing one topic.

    Reads the live Polymarket API and changes nothing. Use get_market for a
    single market.
    """
    try:
        engine = _get_engine()
        event = engine.api.get_event(slug)
        return _ok(event)
    except Exception as e:
        return _err_from(e)


@_tool(title="Watch prices", annotations=_READ_ONLY_LIVE)
def watch_prices(
    slugs: Annotated[str, Field(description=(
        "Comma-separated market slugs or condition IDs, e.g. 'slug-a,slug-b'. "
        "Markets that cannot be fetched are skipped."
    ))],
    outcomes: Annotated[str, Field(description=(
        "Comma-separated outcomes to price, e.g. 'yes,no'. Default 'yes'."
    ))] = "yes",
) -> str:
    """Get live midpoint prices for several markets and outcomes in one call.

    Reads live data and changes nothing. Use get_order_book for depth on one
    market, or portfolio for prices of the positions you hold.
    """
    try:
        engine = _get_engine()
        slug_list = [s.strip() for s in slugs.split(",")]
        outcome_list = [o.strip() for o in outcomes.split(",")]
        prices = engine.watch_prices(slug_list, outcome_list)
        return _ok(prices)
    except Exception as e:
        return _err_from(e)


# ---------------------------------------------------------------------------
# Trading tools
# ---------------------------------------------------------------------------


@_tool(title="Buy shares", annotations=_WRITE_LIVE)
def buy(
    slug_or_id: _MarketRef,
    outcome: _Outcome,
    amount_usd: Annotated[float, Field(description=(
        "USD to spend, e.g. 100. Minimum 1.0 on Polymarket; no minimum on "
        "Kalshi."
    ))],
    order_type: _OrderType = "fok",
    account: _Account = "default",
) -> str:
    """Buy shares of a Polymarket or Kalshi ('kalshi:<TICKER>') outcome with paper money.

    Fills immediately against the live order book and records the trade and
    fee (simulated; no real order). Kalshi fees follow the series fee schedule.
    Use place_limit_order for a resting order at your price, or sell to exit.
    """
    try:
        engine = _get_engine(account)
        result = engine.buy(slug_or_id, outcome, amount_usd, order_type)
        return _ok({
            "trade": {
                "id": result.trade.id,
                "market_slug": result.trade.market_slug,
                "outcome": result.trade.outcome,
                "side": result.trade.side,
                "avg_price": result.trade.avg_price,
                "amount_usd": result.trade.amount_usd,
                "shares": result.trade.shares,
                "fee": result.trade.fee,
                "slippage_bps": result.trade.slippage,
            },
            "account": {
                "cash": result.account.cash,
            },
        })
    except Exception as e:
        return _err_from(e)


@_tool(title="Sell shares", annotations=_WRITE_LIVE)
def sell(
    slug_or_id: _MarketRef,
    outcome: _Outcome,
    shares: Annotated[float, Field(description=(
        "Number of shares to sell, at most the shares held in that outcome."
    ))],
    order_type: _OrderType = "fok",
    account: _Account = "default",
) -> str:
    """Sell held shares of a Polymarket or Kalshi ('kalshi:<TICKER>') outcome for paper cash.

    Fills immediately against the live order book and records the trade and
    fee (simulated; no real order). Use place_limit_order with side 'sell' to
    rest an exit at your price, or resolve to collect payout on a closed market.
    """
    try:
        engine = _get_engine(account)
        result = engine.sell(slug_or_id, outcome, shares, order_type)
        return _ok({
            "trade": {
                "id": result.trade.id,
                "market_slug": result.trade.market_slug,
                "outcome": result.trade.outcome,
                "side": result.trade.side,
                "avg_price": result.trade.avg_price,
                "amount_usd": result.trade.amount_usd,
                "shares": result.trade.shares,
                "fee": result.trade.fee,
                "slippage_bps": result.trade.slippage,
            },
            "account": {
                "cash": result.account.cash,
            },
        })
    except Exception as e:
        return _err_from(e)


# ---------------------------------------------------------------------------
# Portfolio tools
# ---------------------------------------------------------------------------


@_tool(title="Get portfolio", annotations=_READ_ONLY_LIVE, meta=_UI_TOOL_META)
def portfolio(account: _Account = "default") -> str:
    """List an account's open positions with live prices and unrealized P&L.

    Reads the local account and prices positions with live market data. Use
    get_balance for cash totals, history for past trades, or stats for
    performance metrics.
    """
    try:
        engine = _get_engine(account)
        positions = engine.get_portfolio()
        return _ok(positions)
    except Exception as e:
        return _err_from(e)


@_tool(title="Get trade history", annotations=_READ_ONLY_LOCAL)
def history(
    limit: Annotated[int, Field(description=(
        "Maximum trades to return, newest first, clamped to 1-1000. Default 50."
    ))] = 50,
    account: _Account = "default",
) -> str:
    """List an account's recent trades with market, side, price, shares, USD and fee.

    Reads the local database only (no network) and changes nothing. Use
    portfolio for open positions or stats for aggregate performance.
    """
    try:
        limit = _clamp_limit(limit, MAX_RESULTS * 10)
        engine = _get_engine(account)
        trades = engine.get_history(limit)
        return _ok([
            {
                "id": t.id,
                "market_slug": t.market_slug,
                "outcome": t.outcome,
                "side": t.side,
                "avg_price": t.avg_price,
                "amount_usd": t.amount_usd,
                "shares": t.shares,
                "fee": t.fee,
                "created_at": t.created_at,
            }
            for t in trades
        ])
    except Exception as e:
        return _err_from(e)


# ---------------------------------------------------------------------------
# Limit order tools
# ---------------------------------------------------------------------------


@_tool(title="Place limit order", annotations=_WRITE_LIVE)
def place_limit_order(
    slug_or_id: _MarketRef,
    outcome: _Outcome,
    side: Annotated[str, Field(description="'buy' or 'sell'.")],
    amount: Annotated[float, Field(description=(
        "For a buy, USD to spend (minimum 1.0 on Polymarket, none on Kalshi); "
        "for a sell, number of shares to sell."
    ))],
    limit_price: Annotated[float, Field(description=(
        "Limit price strictly between 0 and 1, on the market's tick grid "
        "(e.g. 0.42)."
    ))],
    order_type: Annotated[str, Field(description=(
        "'gtc' (open until filled or cancelled) or 'gtd' (expires at "
        "expires_at). Default 'gtc'."
    ))] = "gtc",
    expires_at: Annotated[str | None, Field(description=(
        "ISO 8601 expiry timestamp, e.g. '2026-12-31T23:59:00Z'; required "
        "when order_type is 'gtd'. Default none."
    ))] = None,
    account: _Account = "default",
) -> str:
    """Place a resting paper limit order on a Polymarket or Kalshi ('kalshi:<TICKER>') market.

    Stores the order locally and reserves cash for a buy; an order that
    crosses the book fills at once as a taker. Resting orders fill only when
    check_orders runs. Use buy or sell for an immediate market-price fill.
    """
    try:
        engine = _get_engine(account)
        order = engine.place_limit_order(
            slug_or_id, outcome, side, amount, limit_price,
            order_type, expires_at,
        )
        return _ok(order)
    except Exception as e:
        return _err_from(e)


@_tool(title="List limit orders", annotations=_READ_ONLY_LOCAL)
def list_orders(account: _Account = "default") -> str:
    """List an account's pending limit orders.

    Reads the local database only and changes nothing. Use check_orders to
    evaluate them against live prices, or cancel_order to remove one.
    """
    try:
        engine = _get_engine(account)
        orders = engine.get_pending_orders()
        return _ok(orders)
    except Exception as e:
        return _err_from(e)


@_tool(title="Cancel limit order", annotations=_DESTRUCTIVE_LOCAL_IDEMPOTENT)
def cancel_order(
    order_id: Annotated[int, Field(description=(
        "ID of the pending order, from place_limit_order or list_orders."
    ))],
    account: _Account = "default",
) -> str:
    """Cancel one pending limit order by ID and release its reserved cash.

    Local change only. Returns a not_found error if the order does not exist
    or is no longer pending. Use cancel_all_orders to cancel every pending order.
    """
    try:
        engine = _get_engine(account)
        order = engine.cancel_limit_order(order_id)
        if order is None:
            return _err(f"Order {order_id} not found or not pending", "not_found")
        return _ok(order)
    except Exception as e:
        return _err_from(e)


@_tool(title="Cancel all limit orders", annotations=_DESTRUCTIVE_LOCAL_IDEMPOTENT)
def cancel_all_orders(account: _Account = "default") -> str:
    """Cancel every pending limit order of an account and release the reserved cash.

    Local change only; filled orders and open positions are untouched. Returns
    the count and the cancelled orders. Use cancel_order to cancel just one.
    """
    try:
        engine = _get_engine(account)
        cancelled = engine.cancel_all_orders()
        return _ok({"cancelled": len(cancelled), "orders": cancelled})
    except Exception as e:
        return _err_from(e)


@_tool(title="Check limit orders", annotations=_WRITE_LIVE)
def check_orders(account: _Account = "default") -> str:
    """Evaluate pending limit orders against live prices and execute any that fill.

    Writes fills and cash changes to the local account. Resting orders never
    fill on their own, so call this periodically. Use list_orders to only view
    pending orders.
    """
    try:
        engine = _get_engine(account)
        results = engine.check_orders()
        return _ok(results)
    except Exception as e:
        return _err_from(e)


# ---------------------------------------------------------------------------
# Analytics tools
# ---------------------------------------------------------------------------


@_tool(title="Get performance stats", annotations=_READ_ONLY_LIVE, meta=_UI_TOOL_META)
def stats(account: _Account = "default") -> str:
    """Get one account's raw performance numbers: win rate, ROI, profit, Sharpe, max drawdown.

    Reads the local account and prices positions live; changes nothing. Use
    this for analysis, stats_card for a postable text card, or
    leaderboard_entry for the ranking record.
    """
    try:
        engine = _get_engine(account)
        from pm_trader.analytics import compute_stats

        acct = engine.get_account()
        trades = engine.db.get_all_trades()
        portfolio_items = engine.get_portfolio()
        positions_value = sum(p["current_value"] for p in portfolio_items)

        result = compute_stats(
            trades, acct, positions_value, resolutions=engine.get_resolutions(),
        )
        return _ok(result)
    except Exception as e:
        return _err_from(e)


@_tool(title="Get stats card", annotations=_READ_ONLY_LIVE)
def stats_card(
    account: _Account = "default",
    format: Annotated[str, Field(description=(
        "'tweet' (X/Twitter), 'markdown' (chat apps) or 'plain' (no "
        "formatting). Default 'markdown'."
    ))] = "markdown",
) -> str:
    """Render one account's ROI, Sharpe, win rate and P&L as a postable text card.

    Reads the local account and posts nothing. Open positions are valued at live midpoints (0 if a lookup fails).
    Use stats for raw numbers,
    share_content for platform and template choices, or pk_card to compare two
    accounts.
    """
    try:
        engine = _get_engine(account)
        from pm_trader.analytics import compute_stats
        from pm_trader.card import generate_card, generate_card_plain, generate_tweet

        acct = engine.get_account()
        trades = engine.db.get_all_trades()
        portfolio_items = engine.get_portfolio()
        positions_value = sum(p["current_value"] for p in portfolio_items)

        result = compute_stats(
            trades, acct, positions_value, resolutions=engine.get_resolutions(),
        )
        if format == "tweet":
            card = generate_tweet(result, account, portfolio_items)
        elif format == "plain":
            card = generate_card_plain(result, account, portfolio_items)
        else:
            card = generate_card(result, account, portfolio_items)
        return _ok({"card": card, "stats": result})
    except Exception as e:
        return _err_from(e)


@_tool(title="Get leaderboard entry", annotations=_READ_ONLY_LIVE)
def leaderboard_entry(account: _Account = "default") -> str:
    """Return one account's standardized leaderboard record as JSON.

    Includes ROI%, Sharpe, win rate, trade count, max drawdown, fees, first and
    last trade times, and 'qualified' (10+ trades). Reads the local account; Open positions are valued at live midpoints (0 if a lookup fails).
    Use leaderboard_card to rank many accounts, or stats for analysis.
    """
    try:
        engine = _get_engine(account)
        from pm_trader.analytics import compute_stats

        acct = engine.get_account()
        trades = engine.db.get_all_trades()
        portfolio_items = engine.get_portfolio()
        positions_value = sum(p["current_value"] for p in portfolio_items)
        result = compute_stats(
            trades, acct, positions_value, resolutions=engine.get_resolutions(),
        )

        first_trade = trades[-1].created_at if trades else None
        last_trade = trades[0].created_at if trades else None

        return _ok({
            "account": account,
            "starting_balance": result.get("starting_balance", 0.0),
            "total_value": result.get("total_value", 0.0),
            "roi_pct": result.get("roi_pct", 0.0),
            "pnl": result.get("pnl", 0.0),
            "sharpe_ratio": result.get("sharpe_ratio", 0.0),
            "win_rate": result.get("win_rate", 0.0),
            "total_trades": result.get("total_trades", 0),
            "max_drawdown": result.get("max_drawdown", 0.0),
            "total_fees": result.get("total_fees", 0.0),
            "first_trade_at": first_trade,
            "last_trade_at": last_trade,
            "open_positions": len(portfolio_items),
            "qualified": result.get("total_trades", 0) >= 10,
        })
    except Exception as e:
        return _err_from(e)


@_tool(title="Generate share content", annotations=_READ_ONLY_LIVE)
def share_content(
    account: _Account = "default",
    platform: Annotated[str, Field(description=(
        "'twitter', 'telegram', 'discord' or 'plain'. Default 'twitter'."
    ))] = "twitter",
    template: Annotated[str, Field(description=(
        "'performance' (stats overview), 'milestone' (achievement) or 'daily' "
        "(daily report with positions). Default 'performance'."
    ))] = "performance",
) -> str:
    """Generate post-ready text for a platform and template from one account's stats.

    Reads the local account and posts nothing. Open positions are valued at live midpoints (0 if a lookup fails).
    Use this when you need a
    template (milestone, daily); use stats_card for a plain stats card or
    stats for raw numbers.
    """
    try:
        from pm_trader.analytics import compute_stats
        from pm_trader.card import (
            generate_card,
            generate_card_plain,
            generate_daily_report,
            generate_milestone_tweet,
            generate_tweet,
        )

        engine = _get_engine(account)
        acct = engine.get_account()
        trades = engine.db.get_all_trades()
        portfolio_items = engine.get_portfolio()
        positions_value = sum(p["current_value"] for p in portfolio_items)
        result = compute_stats(
            trades, acct, positions_value, resolutions=engine.get_resolutions(),
        )

        if template == "milestone":
            card = generate_milestone_tweet(result)
        elif template == "daily":
            card = generate_daily_report(result, portfolio_items, account)
        elif platform == "twitter":
            card = generate_tweet(result, account, portfolio_items)
        elif platform == "plain":
            card = generate_card_plain(result, account, portfolio_items)
        else:
            card = generate_card(result, account, portfolio_items)

        return _ok({"card": card, "platform": platform, "template": template})
    except Exception as e:
        return _err_from(e)


@_tool(title="Generate PK card", annotations=_READ_ONLY_LIVE)
def pk_card(
    account_a: Annotated[str, Field(description=(
        "First paper account name. Default 'default'."
    ))] = "default",
    account_b: Annotated[str, Field(description=(
        "Second paper account name. Default 'aggressive'."
    ))] = "aggressive",
) -> str:
    """Compare two existing accounts head-to-head and return a card naming the winner.

    Compares ROI, Sharpe, win rate, trades and tier from local accounts. Open positions are valued at live midpoints (0 if a lookup fails).
    Use pk_battle to run two strategies from scratch instead, or stats_card for
    a single account.
    """
    try:
        from pm_trader.analytics import compute_stats
        from pm_trader.card import generate_pk_card

        results = {}
        for name in (account_a, account_b):
            engine = _get_engine(name)
            acct = engine.get_account()
            trades = engine.db.get_all_trades()
            portfolio_items = engine.get_portfolio()
            positions_value = sum(p["current_value"] for p in portfolio_items)
            results[name] = compute_stats(
                trades, acct, positions_value, resolutions=engine.get_resolutions(),
            )

        card = generate_pk_card(
            results[account_a], account_a,
            results[account_b], account_b,
        )
        return _ok({
            "card": card,
            account_a: results[account_a],
            account_b: results[account_b],
        })
    except Exception as e:
        return _err_from(e)


# ---------------------------------------------------------------------------
# Leaderboard card tool
# ---------------------------------------------------------------------------


@_tool(title="Generate leaderboard card", annotations=_READ_ONLY_LIVE)
def leaderboard_card(
    accounts: Annotated[str, Field(description=(
        "Comma-separated account names to rank, e.g. 'default,aggressive'. "
        "Default empty: every account in this session's data root."
    ))] = "",
) -> str:
    """Rank accounts with 10+ trades by ROI% and return a Top 10 leaderboard card.

    Scans local accounts (~/.pm-trader locally, the caller's own directory in
    remote mode) and changes nothing. Open positions are valued at live midpoints (0 if a lookup fails).
    Use leaderboard_entry for one account's
    record or pk_card for two.
    """
    try:
        from pm_trader.analytics import compute_stats
        from pm_trader.card import generate_leaderboard_card

        base = _data_root()
        if accounts:
            names = [n.strip() for n in accounts.split(",")]
        else:
            if not base.exists():
                return _ok({"card": "No accounts found.", "entries": []})
            names = [
                d.name for d in sorted(base.iterdir())
                if d.is_dir() and (d / "paper.db").exists()
            ]

        entries = []
        for name in names:
            try:
                engine = _get_engine(name)
                acct = engine.get_account()
                trades = engine.db.get_all_trades()
                portfolio_items = engine.get_portfolio()
                positions_value = sum(p["current_value"] for p in portfolio_items)
                result = compute_stats(
                    trades, acct, positions_value, resolutions=engine.get_resolutions(),
                )
                result["account"] = name
                entries.append(result)
            except Exception:
                continue

        # Sort by ROI descending, qualified first
        qualified = [e for e in entries if e.get("total_trades", 0) >= 10]
        qualified.sort(key=lambda e: e.get("roi_pct", 0.0), reverse=True)

        card = generate_leaderboard_card(qualified)
        return _ok({"card": card, "entries": qualified})
    except Exception as e:
        return _err_from(e)


# ---------------------------------------------------------------------------
# PK battle tool
# ---------------------------------------------------------------------------


@_tool(title="Run strategy PK battle", annotations=_WRITE_LIVE)
def pk_battle(
    strategy_a: Annotated[str, Field(description=(
        "Dotted Python path of strategy A, e.g. 'examples.momentum.run'; only "
        "'examples.' modules are allowed."
    ))],
    strategy_b: Annotated[str, Field(description=(
        "Dotted Python path of strategy B, e.g. 'examples.contrarian.run'; "
        "only 'examples.' modules are allowed."
    ))],
    name_a: Annotated[str, Field(description=(
        "Display name of strategy A on the PK card. Default 'player_a'."
    ))] = "player_a",
    name_b: Annotated[str, Field(description=(
        "Display name of strategy B on the PK card. Default 'player_b'."
    ))] = "player_b",
    balance: Annotated[float, Field(description=(
        "Starting USD for each strategy. Default 10000."
    ))] = 10_000.0,
) -> str:
    """Run two strategies from scratch on equal balances and return stats, a PK card and the winner.

    Executes the strategy functions against live market data in temporary
    accounts. Use pk_card to compare accounts that already exist, or backtest
    to test one strategy on historical data.
    """
    try:
        from pm_trader.benchmark import pk_battle as _pk_battle

        result = _pk_battle(strategy_a, strategy_b, name_a, name_b, balance)
        return _ok(result)
    except Exception as e:
        return _err_from(e)


# ---------------------------------------------------------------------------
# Resolution tools
# ---------------------------------------------------------------------------


@_tool(title="Resolve market positions", annotations=_WRITE_LIVE_IDEMPOTENT)
def resolve(
    slug_or_id: _MarketRef,
    account: _Account = "default",
) -> str:
    """Settle an account's positions in one closed market, paying $1 per winning share.

    Writes payouts to the local account and cancels resting orders on that
    market. Fails if the market is not closed, the winner is ambiguous, or no
    position is held. Use resolve_all to settle every closed market at once.
    """
    try:
        engine = _get_engine(account)
        results = engine.resolve_market(slug_or_id)
        return _ok([
            {
                "outcome": r.position.outcome,
                "shares": r.position.shares,
                "payout": r.payout,
                "cash_after": r.account.cash,
            }
            for r in results
        ])
    except Exception as e:
        return _err_from(e)


@_tool(title="Resolve all closed positions", annotations=_WRITE_LIVE_IDEMPOTENT)
def resolve_all(account: _Account = "default") -> str:
    """Settle every open position whose market has closed, paying $1 per winning share.

    Writes payouts to the local account; open markets are skipped, as are
    markets that hit a transient network error (retry later). Use resolve to
    settle a single market.
    """
    try:
        engine = _get_engine(account)
        results = engine.resolve_all()
        return _ok([
            {
                "market_slug": r.position.market_slug,
                "outcome": r.position.outcome,
                "shares": r.position.shares,
                "payout": r.payout,
            }
            for r in results
        ])
    except Exception as e:
        return _err_from(e)


# ---------------------------------------------------------------------------
# Backtesting tools
# ---------------------------------------------------------------------------


@_tool(title="Run backtest", annotations=_WRITE_LIVE)
def backtest(
    data_path: Annotated[str, Field(description=(
        "Path to a CSV or JSON file of price snapshots (timestamp, "
        "market_slug, outcome, midpoint); must be under the home directory or "
        "/tmp."
    ))],
    strategy_path: Annotated[str, Field(description=(
        "Dotted Python path of the strategy function, e.g. "
        "'examples.momentum.run'; only 'examples.' modules are allowed."
    ))],
    balance: Annotated[float, Field(description=(
        "Starting USD balance. Default 10000."
    ))] = 10_000.0,
    spread: Annotated[float, Field(description=(
        "Synthetic order-book spread as a price fraction (0.02 = 2 cents). "
        "Default 0.02."
    ))] = 0.02,
    depth: Annotated[float, Field(description=(
        "Synthetic order-book size in shares at each price level. Default 500."
    ))] = 500.0,
) -> str:
    """Replay a strategy over historical price snapshots on synthetic order books.

    Reads the data file and returns performance metrics; it does not touch your
    paper accounts. Use pk_battle to compare two strategies live, or stats for
    an account's real results.
    """
    try:
        from csv import Error as CsvError
        from pathlib import Path as P

        from pm_trader.backtest import (
            load_snapshots_csv,
            load_snapshots_json,
            run_backtest,
        )
        from pm_trader.benchmark import _load_strategy

        # Validate data_path is under allowed directories (no traversal)
        data = P(data_path).resolve()
        allowed_parents = (P.home(), P("/tmp"))
        if not any(data.is_relative_to(p) for p in allowed_parents):
            return _err(
                "data_path must be under home directory or /tmp",
                "invalid_path",
            )

        strategy_fn = _load_strategy(strategy_path)

        if not data.is_file():
            return _err(f"data file not found: {data}", "not_found")

        try:
            if data.suffix == ".json":
                snapshots = load_snapshots_json(data)
            else:
                snapshots = load_snapshots_csv(data)
        except (
            OSError, ValueError, KeyError, TypeError, AttributeError, CsvError,
        ) as e:
            return _err(f"could not parse data file: {e}", "invalid_data")

        from dataclasses import asdict
        result = run_backtest(
            snapshots, strategy_fn, strategy_path, balance, spread, depth,
        )
        return _ok(asdict(result))
    except Exception as e:
        return _err_from(e)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pm-trader-mcp", description="Run the pm-trader MCP server."
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default="stdio",
        help="Transport to serve on (default: stdio)",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind for streamable-http (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to bind for streamable-http (default: 8000)",
    )
    parser.add_argument(
        "--auth-issuer",
        default=os.environ.get("PM_TRADER_AUTH_ISSUER"),
        help="OAuth/OIDC issuer that mints access tokens; enables the opt-in "
        "authenticated remote mode (env: PM_TRADER_AUTH_ISSUER). Requires "
        "--auth-audience and --resource-url. See docs/remote-mcp.md",
    )
    parser.add_argument(
        "--auth-audience",
        default=os.environ.get("PM_TRADER_AUTH_AUDIENCE"),
        help="Value access tokens must carry in 'aud' (env: "
        "PM_TRADER_AUTH_AUDIENCE)",
    )
    parser.add_argument(
        "--resource-url",
        default=os.environ.get("PM_TRADER_RESOURCE_URL"),
        help="Public URL of this server, advertised in the protected-resource "
        "metadata (env: PM_TRADER_RESOURCE_URL)",
    )
    parser.add_argument(
        "--rate-limit-per-minute",
        type=int,
        default=os.environ.get(
            "PM_TRADER_RATE_LIMIT_PER_MINUTE", DEFAULT_RATE_LIMIT_PER_MINUTE
        ),
        help="Remote mode: tool calls allowed per user per minute (default: "
        f"{DEFAULT_RATE_LIMIT_PER_MINUTE}; env: PM_TRADER_RATE_LIMIT_PER_MINUTE)",
    )
    parser.add_argument(
        "--max-accounts",
        type=int,
        default=os.environ.get("PM_TRADER_MAX_ACCOUNTS", DEFAULT_MAX_ACCOUNTS),
        help="Remote mode: accounts allowed per user (default: "
        f"{DEFAULT_MAX_ACCOUNTS}; env: PM_TRADER_MAX_ACCOUNTS)",
    )
    parser.add_argument(
        "--prune-idle-days",
        type=int,
        default=os.environ.get("PM_TRADER_PRUNE_IDLE_DAYS", DEFAULT_IDLE_DAYS),
        help="Remote mode: delete user directories idle this many days, at "
        f"startup and every 24 h (default: {DEFAULT_IDLE_DAYS}, 0 disables; "
        "env: PM_TRADER_PRUNE_IDLE_DAYS)",
    )
    return parser


# Tools that read arbitrary local files or import/execute local strategy
# modules. Safe on stdio (trusted local caller) but never on a network
# transport, where a remote caller could read files under $HOME/tmp or run
# arbitrary code.
_LOCAL_ONLY_TOOLS = ("backtest", "pk_battle")


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

# Each auth argument with the flag name used in its error message.
_AUTH_ARGS = (
    ("auth_issuer", "auth-issuer"),
    ("auth_audience", "auth-audience"),
    ("resource_url", "resource-url"),
)


def _auth_config(args: argparse.Namespace) -> RemoteAuthConfig | None:
    """Return the remote-mode config from the parsed args, or None when off.

    None of the three set means the mode stays off (today's behaviour). Setting
    only some of them is a startup error: a half-configured resource server
    would quietly accept the wrong audience.
    """
    if not any((args.auth_issuer, args.auth_audience, args.resource_url)):
        return None
    missing = [flag for attr, flag in _AUTH_ARGS if not getattr(args, attr)]
    if missing:
        raise RemoteAuthConfigError(
            "remote MCP auth needs --auth-issuer, --auth-audience and "
            f"--resource-url (missing: {', '.join('--' + m for m in missing)})"
        )
    return RemoteAuthConfig(
        issuer=args.auth_issuer, audience=args.auth_audience,
        resource_url=args.resource_url,
        rate_limit_per_minute=args.rate_limit_per_minute,
        max_accounts=args.max_accounts,
        prune_idle_days=args.prune_idle_days,
    )


PRUNE_INTERVAL_SECONDS = 86400.0


def _prune_idle_users(idle_days: int) -> list[Path]:
    """Prune idle remote users; runs on `_tool_executor`, so never during a tool call.

    The cached engine is closed first when it sits inside a directory about to
    be deleted, so no open SQLite handle outlives its files.
    """
    def close_cached_engine(stale: list[Path]) -> None:
        global _engine
        if _engine is not None and any(
            d == _engine.db.data_dir or d in _engine.db.data_dir.parents
            for d in stale
        ):
            _engine.close()
            _engine = None

    try:
        return prune_remote(idle_days, before_delete=close_cached_engine)
    except OSError as e:
        print(f"WARNING: remote prune failed: {e}", file=sys.stderr)
        return []


def _schedule_prune(idle_days: int, interval: float = PRUNE_INTERVAL_SECONDS):
    """Queue a prune on the tool executor now and again every `interval` seconds."""
    future = _tool_executor.submit(_prune_idle_users, idle_days)
    timer = threading.Timer(interval, _schedule_prune, (idle_days, interval))
    timer.daemon = True
    timer.start()
    return future


def _run(
    transport: str, host: str, port: int,
    auth: RemoteAuthConfig | None = None,
) -> None:
    global _remote, _server_lock
    if transport == "streamable-http":
        if auth is None and host not in _LOOPBACK_HOSTS:
            print(
                f"WARNING: streamable-http is UNAUTHENTICATED and is binding to "
                f"{host!r}: anyone who can reach it can trade, reset and resolve "
                f"every account. Bind 127.0.0.1 or put it behind an "
                f"authenticating proxy.",
                file=sys.stderr,
            )
        for name in _LOCAL_ONLY_TOOLS:
            mcp.remove_tool(name)
        if auth is not None:
            # Authenticated and multi-user: every request must carry a bearer
            # token, and each verified subject trades in its own data root.
            _server_lock = acquire_server_lock()
            _remote = auth
            configure_server(mcp, auth)
            if auth.prune_idle_days > 0:
                _schedule_prune(auth.prune_idle_days)
        mcp.run(transport="streamable-http", host=host, port=port)
    else:
        mcp.run()


def _prune_remote_main(argv: list[str]) -> None:
    """Run ``pm-trader-mcp prune-remote``: delete idle remote-user directories."""
    parser = argparse.ArgumentParser(
        prog="pm-trader-mcp prune-remote",
        description="Delete remote-mode user directories idle for N days.",
    )
    parser.add_argument(
        "--idle-days", type=int, default=DEFAULT_IDLE_DAYS,
        help=f"Delete users whose newest file is older than this (default: {DEFAULT_IDLE_DAYS})",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what would be removed without deleting anything",
    )
    args = parser.parse_args(argv)
    try:
        lock = acquire_server_lock()
    except RemoteAuthConfigError:
        print("server running; use the in-server prune", file=sys.stderr)
        raise SystemExit(1) from None
    try:
        pruned = prune_remote(args.idle_days, dry_run=args.dry_run)
    except RemoteAuthConfigError as e:
        parser.error(str(e))
    finally:
        lock.close()
    verb = "would remove" if args.dry_run else "removed"
    for path in pruned:
        print(f"{verb} {path}")
    print(f"{verb} {len(pruned)} idle user director{'y' if len(pruned) == 1 else 'ies'}")


def main(argv: list[str] | None = None) -> None:
    """Run MCP server on stdio (default) or streamable-http transport."""
    if argv is None:
        argv = sys.argv[1:]
    if argv[:1] == ["prune-remote"]:
        _prune_remote_main(argv[1:])
        return
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    try:
        auth = _auth_config(args)
    except RemoteAuthConfigError as e:
        parser.error(str(e))
    if auth is not None and args.transport != "streamable-http":
        parser.error("--auth-issuer needs --transport streamable-http")
    try:
        _run(args.transport, args.host, args.port, auth)
    except RemoteAuthConfigError as e:
        parser.error(str(e))


if __name__ == "__main__":  # pragma: no cover
    main()
