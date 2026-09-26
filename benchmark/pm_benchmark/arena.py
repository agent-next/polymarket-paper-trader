"""Forecast Arena — daily AI-vs-crowd forecasting on soon-resolving markets.

The arena answers "can AI forecast real-world events better than the crowd?"
Every day a CI job records one YES probability per (entrant, market) on
soon-resolving Polymarket binary markets — AI entrants via a single-shot LLM
call, naive baselines deterministically — with no market price in the prompt
(the crowd is the opponent, not an input). Once markets resolve, ``build``
scores each entrant against the outcome and the market price at forecast time.

Data layout (append-only, e.g. a checked-out ``arena-data`` branch)::

    <data>/forecasts/YYYY-MM-DD.jsonl   one row per (entrant, market)
    <data>/resolutions.json             {slug: {outcome, resolved_at, ...}}
"""
from __future__ import annotations

import json
import math
import os
import random
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import yaml

from pm_benchmark.config import LLMConfig
from pm_benchmark.jev import (
    JevError,
    is_jev_model,
    noul_probability,
    query_jev,
)
from pm_benchmark.market_info import (
    MarketInfo,
    MarketInfoError,
    fetch_prices,
    fetch_resolution_detail,
    list_markets,
)
from pm_benchmark.providers import LLMError, parse_decision, query_model
from pm_benchmark.scoring import (
    compute_alpha_score,
    compute_brier_score,
    compute_calibration_error,
)


class ArenaError(Exception):
    """Raised on arena configuration, selection, or forecast failures."""


VALID_KINDS = ("ai", "baseline")
BASELINE_RULES = ("crowd", "coin", "favorite")
CROWD_RULE = "crowd"

# Market selection filters (spec v1)
TOP_N = 20
FETCH_LIMIT = 100      # Gamma /markets page cap
MAX_PAGES = 5          # bounded paging past the 100-per-page cap (v1.2)
MAX_PER_EVENT = 2      # one event cannot fill the whole board (v1.2)
MIN_LIQUIDITY = 10_000.0
MIN_PRICE = 0.03
MAX_PRICE = 0.97
MIN_DAYS = 1
MAX_DAYS = 14

# Board constants
BOOTSTRAP_RESAMPLES = 1000
BOOTSTRAP_SEED = 0
SIG_MIN_MARKETS = 30      # `significant` is null below this many resolved events
ECE_MIN_N = 100           # ECE is null below this many resolved forecasts
DUELS_LIMIT = 10
HALL_OF_WRONG_LIMIT = 20
HALL_CONFIDENCE = 0.8
RATIONALE_LIMIT = 280

# Run-safety constants
ERROR_LIMIT = 200         # truncated error text stored in skip rows
LLM_NUM_RETRIES = 3       # litellm backoff retries inside each model call
MAX_ENTRANT_FAILURES = 3  # per-run cap: skip an entrant's remaining markets
JEV_TIMEOUT = 30.0

MARKET_URL = "https://polymarket.com/event/{slug}"
CNAME_DOMAIN = "polymarket-leaderboard.com"

_FORECAST_SYSTEM_PROMPT = """\
You are a forecaster estimating the probability that a prediction market \
resolves YES.

Respond in JSON format:
{
    "probability": 0.65,
    "reasoning": "1-3 sentences explaining your estimate"
}

Rules:
- probability must be between 0.0 and 1.0
- base your estimate on the question, description, resolution date, and your \
own knowledge
- keep reasoning under 280 characters\
"""

# The arena asks Jev a single typed noul question (not the trading envelope).
_JEV_ARENA_QUESTIONS: dict[str, dict] = {
    "probability": {
        "type": "noul",
        "instructions": "Will this market resolve YES?",
    }
}

_BASELINE_RATIONALES = {
    "crowd": "Market YES price at forecast time.",
    "coin": "Uninformative 0.5 prior.",
    "favorite": "0.9 toward the market favorite.",
}


@dataclass(frozen=True)
class Entrant:
    """A Forecast Arena entrant: an AI model or a deterministic baseline.

    ``web_access``/``cutoff`` are disclosure metadata rendered on the board;
    ``None`` means the config did not declare them.
    """

    id: str
    label: str
    kind: str
    model: str | None = None
    api_base: str | None = None
    api_key_env: str | None = None
    web_access: bool | None = None
    cutoff: str | None = None

    def __post_init__(self) -> None:
        if not self.id:
            raise ArenaError("Entrant id cannot be empty")
        if not self.label:
            raise ArenaError(f"Entrant '{self.id}' label cannot be empty")
        if self.kind not in VALID_KINDS:
            raise ArenaError(
                f"Entrant '{self.id}' kind must be one of {VALID_KINDS}"
            )
        if self.kind == "ai" and not self.model:
            raise ArenaError(f"AI entrant '{self.id}' requires a model")
        if self.kind == "baseline" and self.id not in BASELINE_RULES:
            raise ArenaError(
                f"Unknown baseline '{self.id}', must be one of {BASELINE_RULES}"
            )


def load_entrants(config_path: Path) -> list[Entrant]:
    """Load entrants from a YAML file with an ``entrants`` list.

    Baseline ids select their rule: ``crowd`` records the market price,
    ``coin`` records 0.5, ``favorite`` records 0.9 toward the market favorite.
    """
    if not config_path.exists():
        raise ArenaError(f"Entrants config not found: {config_path}")
    raw = yaml.safe_load(config_path.read_text())
    if not isinstance(raw, dict) or not isinstance(raw.get("entrants"), list):
        raise ArenaError(f"Invalid entrants config in {config_path}")

    entrants: list[Entrant] = []
    seen: set[str] = set()
    for item in raw["entrants"]:
        if not isinstance(item, dict):
            raise ArenaError(f"Invalid entrant entry in {config_path}: {item!r}")
        web_access = item.get("web_access")
        entrant = Entrant(
            id=str(item.get("id") or ""),
            label=str(item.get("label") or item.get("id") or ""),
            kind=str(item.get("kind") or ""),
            model=item.get("model"),
            api_base=item.get("api_base"),
            api_key_env=item.get("api_key_env"),
            web_access=None if web_access is None else bool(web_access),
            cutoff=item.get("cutoff"),
        )
        if entrant.id in seen:
            raise ArenaError(f"Duplicate entrant id '{entrant.id}'")
        seen.add(entrant.id)
        entrants.append(entrant)
    if not entrants:
        raise ArenaError(f"No entrants defined in {config_path}")
    return entrants


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    """Format as UTC ISO-8601 with a trailing Z."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_ts(value: str) -> datetime | None:
    """Parse an ISO-8601 timestamp; naive values are assumed UTC."""
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _yes_index(outcomes: list[str]) -> int:
    """Index of the Yes outcome by label; defaults to 0."""
    for i, outcome in enumerate(outcomes):
        if outcome.strip().lower() == "yes":
            return i
    return 0


def _yes_prob(market: MarketInfo) -> float | None:
    """YES price from the listing payload; None when prices are missing."""
    idx = _yes_index(market.outcomes)
    if idx < len(market.outcome_prices):
        return market.outcome_prices[idx]
    return None


def _list_candidate_markets(
    *,
    page_size: int = FETCH_LIMIT,
    max_pages: int = MAX_PAGES,
    http_client: httpx.Client | None = None,
) -> list[MarketInfo]:
    """Fetch candidate markets with bounded offset paging (v1.2).

    Gamma returns at most ~100 markets per page, and the 1-14 day window can
    be dominated by a single huge event, so one page is not enough. Fetches
    until a short page (< ``page_size`` rows) or the ``max_pages`` bound,
    deduping by slug — offset paging can overlap when ordering shifts
    mid-scan.
    """
    seen: set[str] = set()
    markets: list[MarketInfo] = []
    for page in range(max_pages):
        batch = list_markets(
            limit=page_size,
            offset=page * page_size,
            http_client=http_client,
        )
        for m in batch:
            if m.slug and m.slug not in seen:
                seen.add(m.slug)
                markets.append(m)
        if len(batch) < page_size:
            break
    return markets


def select_markets(
    markets: list[MarketInfo],
    taken_slugs: set[str],
    *,
    now: datetime,
    top_n: int = TOP_N,
    max_per_event: int = MAX_PER_EVENT,
    min_liquidity: float = MIN_LIQUIDITY,
    min_price: float = MIN_PRICE,
    max_price: float = MAX_PRICE,
    min_days: int = MIN_DAYS,
    max_days: int = MAX_DAYS,
) -> list[MarketInfo]:
    """Pick soon-resolving binary markets for forecasting.

    Keeps open Yes/No markets ending in ``min_days``–``max_days`` days with
    liquidity >= ``min_liquidity`` and a YES price inside
    [``min_price``, ``max_price``], drops ``taken_slugs`` (already-forecast
    and already-resolved markets), keeps at most ``max_per_event`` markets
    per Gamma event (highest volume wins, ``event_id`` falling back to
    ``slug``), then returns the top ``top_n`` by volume.
    """
    eligible: list[MarketInfo] = []
    for m in markets:
        if not m.slug or m.slug in taken_slugs:
            continue
        if m.closed or not m.active:
            continue
        if sorted(o.strip().lower() for o in m.outcomes) != ["no", "yes"]:
            continue
        if not m.outcome_prices:
            continue
        price = _yes_prob(m)
        if price is None or not min_price <= price <= max_price:
            continue
        if m.liquidity < min_liquidity:
            continue
        end = _parse_ts(m.end_date)
        if end is None:
            continue
        delta = end - now
        if not timedelta(days=min_days) <= delta <= timedelta(days=max_days):
            continue
        eligible.append(m)
    eligible.sort(key=lambda m: m.volume, reverse=True)
    per_event: dict[str, int] = {}
    capped: list[MarketInfo] = []
    for m in eligible:
        key = m.event_id or m.slug
        if per_event.get(key, 0) >= max_per_event:
            continue
        per_event[key] = per_event.get(key, 0) + 1
        capped.append(m)
    return capped[:top_n]


def build_forecast_prompt(market: MarketInfo) -> str:
    """Build the forecast prompt — question, description, end date; no price."""
    return (
        f"## Forecast Request\n\n"
        f"**Question:** {market.question}\n\n"
        f"**Description:** {market.description}\n\n"
        f"**Resolution Date:** {market.end_date}\n\n"
        f"Estimate the probability this market resolves YES. Respond as JSON."
    )


def _baseline_prob(rule: str, market_prob: float) -> float:
    """Deterministic baseline probability for a market."""
    if rule == "coin":
        return 0.5
    if rule == "favorite":
        return 0.9 if market_prob >= 0.5 else 0.1
    return market_prob  # crowd


def _fetch_market_prob(
    market: MarketInfo,
    *,
    http_client: httpx.Client | None = None,
) -> float:
    """Fetch the current YES price for a market, mapped by outcome label."""
    prices = fetch_prices(market.slug, http_client=http_client)
    for label, price in prices.items():
        if label.strip().lower() == "yes":
            return float(price)
    raise MarketInfoError(f"Market '{market.slug}' has no YES outcome price")


def _ai_forecast(
    entrant: Entrant,
    market: MarketInfo,
    *,
    timeout: float | None,
) -> tuple[float, str]:
    """Single-shot YES probability + rationale for an AI entrant.

    One model call; on a malformed response exactly one retry is allowed
    (L1 — no re-asking until the output parses). Call-level failures
    propagate so the caller can record a skip row.
    """
    prompt = build_forecast_prompt(market)
    if is_jev_model(entrant.model or ""):
        state = f"{_FORECAST_SYSTEM_PROMPT}\n\n{prompt}"
        answers = query_jev(
            entrant.model or "",
            state,
            _JEV_ARENA_QUESTIONS,
            timeout=timeout if timeout is not None else JEV_TIMEOUT,
        )
        try:
            prob = noul_probability(answers, "probability")
        except JevError:
            answers = query_jev(
                entrant.model or "",
                state,
                _JEV_ARENA_QUESTIONS,
                timeout=timeout if timeout is not None else JEV_TIMEOUT,
            )
            prob = noul_probability(answers, "probability")
        return prob, f"Jev {entrant.model}"

    cfg = LLMConfig(
        model=entrant.model or "",
        api_base=entrant.api_base,
        api_key_env=entrant.api_key_env,
        num_retries=LLM_NUM_RETRIES,
    )
    raw = query_model(cfg, prompt, _FORECAST_SYSTEM_PROMPT, timeout=timeout)
    try:
        decision = parse_decision(raw)
    except LLMError:
        raw = query_model(cfg, prompt, _FORECAST_SYSTEM_PROMPT, timeout=timeout)
        decision = parse_decision(raw)
    reasoning = decision.reasoning if isinstance(decision.reasoning, str) else ""
    return decision.probability, reasoning[:RATIONALE_LIMIT]


def forecast_market(
    entrant: Entrant,
    market: MarketInfo,
    *,
    now: datetime,
    timeout: float | None = None,
    price_fetch: Callable[[MarketInfo], float] | None = None,
    http_client: httpx.Client | None = None,
) -> dict:
    """Record one entrant's YES probability for a market as a forecast row.

    The market YES price is fetched immediately *before* the model call so
    the recorded crowd reference predates the answer (``price_ts`` marks the
    snapshot). Raises ArenaError when the market's end date has already
    passed; other failures propagate for the caller to record as skip rows.
    """
    end = _parse_ts(market.end_date)
    if end is not None and end <= now:
        raise ArenaError(
            f"Market '{market.slug}' already ended at {market.end_date}"
        )

    fetch = price_fetch or (
        lambda m: _fetch_market_prob(m, http_client=http_client)
    )
    market_prob = fetch(market)

    if entrant.kind == "baseline":
        prob = _baseline_prob(entrant.id, market_prob)
        rationale = _BASELINE_RATIONALES[entrant.id]
    else:
        prob, rationale = _ai_forecast(entrant, market, timeout=timeout)

    return {
        "ts": _iso(now),
        "slug": market.slug,
        "event_id": market.event_id or market.slug,
        "question": market.question,
        "end_date": market.end_date,
        "closed": market.closed,
        "market_prob": market_prob,
        "price_ts": _iso(now),
        "url": MARKET_URL.format(slug=market.slug),
        "entrant": entrant.id,
        "model": entrant.model,
        "prob": prob,
        "rationale": rationale,
        "status": "ok",
    }


def _clean_error(error: Exception, secrets: list[str]) -> str:
    """Redact env-provided secrets and truncate an error for a skip row."""
    msg = str(error) or type(error).__name__
    for secret in secrets:
        if secret:
            msg = msg.replace(secret, "<redacted>")
    return msg[:ERROR_LIMIT]


def _skip_row(
    entrant: Entrant,
    market: MarketInfo,
    *,
    now: datetime,
    error: str,
) -> dict:
    """A forecast row recording that an entrant failed on a market."""
    return {
        "ts": _iso(now),
        "slug": market.slug,
        "event_id": market.event_id or market.slug,
        "question": market.question,
        "end_date": market.end_date,
        "closed": market.closed,
        "market_prob": _yes_prob(market),
        "price_ts": None,
        "url": MARKET_URL.format(slug=market.slug),
        "entrant": entrant.id,
        "model": entrant.model,
        "prob": None,
        "rationale": "",
        "status": "skip",
        "error": error,
    }


def load_forecasts(data_dir: Path) -> list[dict]:
    """Read all forecast rows from ``forecasts/*.jsonl``, oldest file first.

    A line that is not valid JSON (e.g. torn by a killed run) is skipped with
    a warning on stderr, so one bad write cannot stall every later run.
    """
    forecasts_dir = data_dir / "forecasts"
    if not forecasts_dir.is_dir():
        return []
    rows: list[dict] = []
    for path in sorted(forecasts_dir.glob("*.jsonl")):
        for n, line in enumerate(path.read_text().splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                print(f"arena: skipping corrupt line {path.name}:{n}", file=sys.stderr)
    return rows


def append_forecasts(data_dir: Path, rows: list[dict], *, now: datetime) -> Path:
    """Append rows to today's ``forecasts/YYYY-MM-DD.jsonl`` file."""
    forecasts_dir = data_dir / "forecasts"
    forecasts_dir.mkdir(parents=True, exist_ok=True)
    path = forecasts_dir / f"{_iso(now)[:10]}.jsonl"
    with path.open("a") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    return path


def load_resolutions(data_dir: Path) -> dict[str, dict]:
    """Read ``resolutions.json`` ({slug: {outcome, resolved_at, ...}})."""
    path = data_dir / "resolutions.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    return data if isinstance(data, dict) else {}


def save_resolutions(data_dir: Path, resolutions: dict[str, dict]) -> Path:
    """Write ``resolutions.json`` atomically with stable key ordering."""
    path = data_dir / "resolutions.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(resolutions, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, path)
    return path


def _default_config() -> Path:
    """Repo-relative default entrants config (``benchmark/arena.yaml``)."""
    return Path(__file__).resolve().parent.parent / "arena.yaml"


def run_predict(
    data_dir: Path,
    config_path: Path | None = None,
    *,
    top_n: int = TOP_N,
    fetch_limit: int = FETCH_LIMIT,
    max_pages: int = MAX_PAGES,
    timeout: float | None = None,
    http_client: httpx.Client | None = None,
    now: datetime | None = None,
    price_fetch: Callable[[MarketInfo], float] | None = None,
    max_entrant_failures: int = MAX_ENTRANT_FAILURES,
) -> dict:
    """Select markets and record one forecast per (entrant, market).

    Idempotent: slugs forecast on earlier days or already resolved are
    excluded from selection, an entrant already present in today's file is
    skipped entirely (a same-day re-run never re-queries it), and existing
    (entrant, slug) pairs are never rewritten — so a re-run appends nothing.

    Failures are recorded as skip rows (prob null), never as a 0.5 forecast;
    after ``max_entrant_failures`` an entrant's remaining markets are skipped
    without further calls. Zero selected markets exits cleanly with no file.
    """
    now = now or _utc_now()
    today = _iso(now)[:10]
    entrants = load_entrants(config_path or _default_config())
    existing = load_forecasts(data_dir)
    resolutions = load_resolutions(data_dir)
    resolved_slugs = {
        s for s, r in resolutions.items() if r.get("outcome") is not None
    }
    taken_slugs = {
        r["slug"]
        for r in existing
        if isinstance(r.get("ts"), str)
        and r["ts"][:10] < today
        and r.get("prob") is not None
    } | resolved_slugs
    done_today = {
        r.get("entrant")
        for r in existing
        if isinstance(r.get("ts"), str) and r["ts"][:10] == today
    }
    taken_pairs = {(r.get("entrant"), r.get("slug")) for r in existing}

    markets = _list_candidate_markets(
        page_size=fetch_limit, max_pages=max_pages, http_client=http_client
    )
    selected = select_markets(markets, taken_slugs, now=now, top_n=top_n)

    key_envs = {e.api_key_env for e in entrants if e.api_key_env}
    key_envs |= {"JEV_API_KEY", "OPENCODE_API_KEY"}
    secrets = [key for env in sorted(key_envs) if (key := os.environ.get(env))]
    fetch = price_fetch or (
        lambda m: _fetch_market_prob(m, http_client=http_client)
    )

    rows: list[dict] = []
    for entrant in entrants:
        if entrant.id in done_today:
            continue
        failures = 0
        for market in selected:
            if (entrant.id, market.slug) in taken_pairs:
                continue
            if failures >= max_entrant_failures:
                row = _skip_row(
                    entrant, market, now=now,
                    error=f"entrant failure cap reached ({failures} failures)",
                )
            else:
                try:
                    row = forecast_market(
                        entrant, market, now=now, timeout=timeout,
                        price_fetch=fetch,
                    )
                except Exception as e:
                    failures += 1
                    row = _skip_row(
                        entrant, market, now=now,
                        error=_clean_error(e, secrets),
                    )
            rows.append(row)
            taken_pairs.add((entrant.id, market.slug))

    path = append_forecasts(data_dir, rows, now=now) if rows else None
    return {
        "date": today,
        "markets": len(selected),
        "forecasts": sum(1 for r in rows if r["status"] == "ok"),
        "skips": sum(1 for r in rows if r["status"] == "skip"),
        "file": str(path) if path else None,
    }


def run_resolve(
    data_dir: Path,
    *,
    http_client: httpx.Client | None = None,
    now: datetime | None = None,
) -> dict:
    """Fetch outcomes for forecast markets that have resolved.

    Checks every forecast slug without a known outcome; markets still open
    stay pending for the next run. Closed markets whose outcome cannot be
    determined are recorded as ``outcome: null`` (counted as unresolvable in
    the board stats) but keep being retried — a late oracle update can still
    land a real outcome.
    """
    now = now or _utc_now()
    forecasts = load_forecasts(data_dir)
    resolutions = load_resolutions(data_dir)
    pending = sorted(
        {r.get("slug") for r in forecasts if r.get("slug")}
        - {s for s, r in resolutions.items() if r.get("outcome") is not None}
    )

    resolved_n = 0
    unresolvable_n = 0
    dirty = False
    for slug in pending:
        try:
            detail = fetch_resolution_detail(slug, http_client=http_client)
        except MarketInfoError:
            continue
        if detail.outcome is not None:
            resolutions[slug] = {
                "outcome": int(round(detail.outcome)),
                "resolved_at": _iso(now),
                "resolution_source": detail.source or "price",
            }
            resolved_n += 1
            dirty = True
        elif detail.closed:
            unresolvable_n += 1
            if slug not in resolutions:
                resolutions[slug] = {
                    "outcome": None,
                    "resolved_at": _iso(now),
                    "resolution_source": "closed_unresolvable",
                }
                dirty = True

    if dirty or not (data_dir / "resolutions.json").exists():
        save_resolutions(data_dir, resolutions)
    return {
        "checked": len(pending),
        "resolved": resolved_n,
        "unresolvable": unresolvable_n,
        "total": len(resolutions),
    }


def _cluster_key(row: dict) -> str:
    """Event cluster key for a forecast row (Gamma event id, slug fallback)."""
    return str(row.get("event_id") or row.get("slug") or "")


def _bootstrap_alpha_ci(
    rows: list[tuple[str, float, float, float]],
    *,
    resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> tuple[float, float]:
    """95% bootstrap CI of alpha, resampling whole event clusters.

    ``rows`` are ``(cluster_key, prob, market_prob, outcome)``. Markets under
    one Gamma event resolve together, so rows are resampled by cluster, not
    i.i.d., which keeps the interval honest about correlated outcomes.
    """
    clusters: dict[str, list[tuple[float, float, float]]] = {}
    for key, prob, market_prob, outcome in rows:
        clusters.setdefault(key, []).append((prob, market_prob, outcome))
    keys = sorted(clusters)
    rng = random.Random(seed)
    alphas = sorted(
        compute_alpha_score(
            [t for _ in range(len(keys)) for t in clusters[rng.choice(keys)]]
        )
        for _ in range(resamples)
    )
    return alphas[int(0.025 * resamples)], alphas[int(0.975 * resamples) - 1]


def _resolved_slugs(resolutions: dict[str, dict]) -> set[str]:
    return {s for s, r in resolutions.items() if r.get("outcome") is not None}


def _leaderboard(
    forecasts: list[dict],
    ok_rows: list[dict],
    resolutions: dict[str, dict],
    entrants: list[Entrant],
    ai_ids: set[str],
) -> list[dict]:
    """Per-entrant resolved scoring, sorted by Brier ascending.

    Only rows on markets with a known outcome are scored. ``n_markets`` is
    the count of distinct resolved events (cluster bootstrap units); the
    crowd row is computed over the union of AI-scored markets so the board
    compares like with like. ``significant`` is null when fewer than
    SIG_MIN_MARKETS distinct events resolved ("too few events").
    """
    resolved = _resolved_slugs(resolutions)
    resolved_ok = [r for r in ok_rows if r["slug"] in resolved]
    ai_resolved = {
        r["slug"] for r in resolved_ok if r["entrant"] in ai_ids
    }
    eligible: dict[str, set[str]] = {}
    for r in forecasts:
        slug = r.get("slug")
        entrant = r.get("entrant")
        if slug in resolved and entrant:
            eligible.setdefault(entrant, set()).add(slug)

    rows: list[dict] = []
    for entrant in entrants:
        e_rows = [r for r in resolved_ok if r["entrant"] == entrant.id]
        ent_eligible = eligible.get(entrant.id, set())
        if entrant.id == CROWD_RULE:
            # Score the crowd only on markets some AI entrant is scored on.
            e_rows = [r for r in e_rows if r["slug"] in ai_resolved]
            ent_eligible &= ai_resolved
        if not e_rows:
            continue
        predictions = [
            (r["prob"], resolutions[r["slug"]]["outcome"]) for r in e_rows
        ]
        brier = compute_brier_score(predictions)
        ece = (
            compute_calibration_error(predictions)
            if len(e_rows) >= ECE_MIN_N
            else None
        )
        n_markets = len({_cluster_key(r) for r in e_rows})
        covered = len({r["slug"] for r in e_rows})
        coverage = covered / len(ent_eligible) if ent_eligible else None
        since = min(
            (
                r["ts"][:10]
                for r in forecasts
                if r.get("entrant") == entrant.id
                and isinstance(r.get("ts"), str)
            ),
            default=None,
        )
        if entrant.id == CROWD_RULE:
            alpha, alpha_ci, significant = 0.0, None, None
        else:
            triples = [
                (
                    _cluster_key(r),
                    r["prob"],
                    r["market_prob"],
                    resolutions[r["slug"]]["outcome"],
                )
                for r in e_rows
            ]
            alpha = compute_alpha_score([t[1:] for t in triples])
            lo, hi = _bootstrap_alpha_ci(triples)
            alpha_ci = [lo, hi]
            significant = (
                (lo > 0 or hi < 0) if n_markets >= SIG_MIN_MARKETS else None
            )
        rows.append(
            {
                "entrant": entrant.id,
                "n": len(e_rows),
                "n_markets": n_markets,
                "coverage": coverage,
                "since": since,
                "brier": brier,
                "ece": ece,
                "alpha": alpha,
                "alpha_ci": alpha_ci,
                "significant": significant,
            }
        )
    rows.sort(key=lambda r: (r["brier"], r["entrant"]))
    return rows


def _open_markets(
    ok_rows: list[dict],
    resolutions: dict[str, dict],
) -> list[dict]:
    """Unresolved markets with the latest forecast per entrant."""
    latest: dict[str, dict] = {}
    forecasts_by_slug: dict[str, dict[str, dict]] = {}
    for r in sorted(ok_rows, key=lambda r: r.get("ts") or ""):
        if r["slug"] in resolutions:
            continue
        latest[r["slug"]] = r
        forecasts_by_slug.setdefault(r["slug"], {})[r["entrant"]] = {
            "prob": r["prob"],
            "rationale": r.get("rationale", ""),
            "ts": r["ts"],
        }
    open_markets = [
        {
            "slug": slug,
            "question": latest[slug]["question"],
            "end_date": latest[slug]["end_date"],
            "market_prob": latest[slug]["market_prob"],
            "url": latest[slug]["url"],
            "forecasts": forecasts_by_slug[slug],
        }
        for slug in forecasts_by_slug
    ]
    open_markets.sort(key=lambda m: (m["end_date"], m["slug"]))
    return open_markets


def _duels(ok_rows: list[dict], resolutions: dict[str, dict], ai_ids: set[str]) -> list[dict]:
    """Largest AI-vs-crowd disagreements, open or resolved (top 10 by gap)."""
    resolved = _resolved_slugs(resolutions)
    pool = [r for r in ok_rows if r["entrant"] in ai_ids]
    pool.sort(
        key=lambda r: (-abs(r["prob"] - r["market_prob"]), r["slug"], r["entrant"])
    )
    duels = []
    for r in pool[:DUELS_LIMIT]:
        res = resolutions.get(r["slug"])
        outcome = res["outcome"] if res and r["slug"] in resolved else None
        if outcome is None:
            status = "open"
        else:
            d_entrant = abs(r["prob"] - outcome)
            d_crowd = abs(r["market_prob"] - outcome)
            if math.isclose(d_entrant, d_crowd, abs_tol=1e-12):
                status = "tie"
            else:
                status = "won" if d_entrant < d_crowd else "lost"
        duels.append(
            {
                "slug": r["slug"],
                "question": r["question"],
                "url": r["url"],
                "market_prob": r["market_prob"],
                "entrant": r["entrant"],
                "prob": r["prob"],
                "gap": abs(r["prob"] - r["market_prob"]),
                "rationale": r.get("rationale", ""),
                "status": status,
                "outcome": outcome,
            }
        )
    return duels


def _hall_of_wrong(
    ok_rows: list[dict],
    resolutions: dict[str, dict],
    ai_ids: set[str],
) -> list[dict]:
    """Resolved AI forecasts that were confident (>=0.8) and wrong (top 20)."""
    resolved = _resolved_slugs(resolutions)
    wrong: list[tuple[float, dict]] = []
    for r in ok_rows:
        if r["entrant"] not in ai_ids or r["slug"] not in resolved:
            continue
        outcome = resolutions[r["slug"]]["outcome"]
        confidence = max(r["prob"], 1 - r["prob"])
        wrong_side = (r["prob"] >= 0.5) != (outcome >= 0.5)
        if confidence >= HALL_CONFIDENCE and wrong_side:
            wrong.append((confidence, r))
    wrong.sort(key=lambda t: (-t[0], t[1]["slug"], t[1]["entrant"]))
    return [
        {
            "slug": r["slug"],
            "question": r["question"],
            "url": r["url"],
            "entrant": r["entrant"],
            "prob": r["prob"],
            "outcome": resolutions[r["slug"]]["outcome"],
            "market_prob": r["market_prob"],
            "rationale": r.get("rationale", ""),
            "resolved_at": resolutions[r["slug"]]["resolved_at"],
        }
        for _, r in wrong[:HALL_OF_WRONG_LIMIT]
    ]


def _board_entrants(
    forecasts: list[dict],
    entrants: list[Entrant],
) -> list[Entrant]:
    """Config entrants plus ids that only exist in historical rows.

    An entrant removed from the config keeps its recorded model and shows up
    under its own id, so history is neither dropped nor silently relabelled.
    """
    seen = {e.id for e in entrants}
    merged = list(entrants)
    for row in forecasts:
        rid = row.get("entrant")
        if not rid or rid in seen:
            continue
        seen.add(rid)
        merged.append(
            Entrant(
                id=rid,
                label=rid,
                kind="baseline" if rid in BASELINE_RULES else "ai",
                model=row.get("model") or "unknown",
            )
        )
    return merged


def build_board(
    forecasts: list[dict],
    resolutions: dict[str, dict],
    entrants: list[Entrant],
    *,
    now: datetime,
) -> dict:
    """Build the ``board`` dict consumed by the site renderer (data.json)."""
    board_entrants = _board_entrants(forecasts, entrants)
    ok_rows = [
        r
        for r in forecasts
        if r.get("status") == "ok" and r.get("prob") is not None
    ]
    ai_ids = {e.id for e in board_entrants if e.kind == "ai"}
    resolved_ok = [r for r in ok_rows if r["slug"] in _resolved_slugs(resolutions)]

    return {
        "generated_at": _iso(now),
        "last_run": max(
            (r["ts"] for r in forecasts if isinstance(r.get("ts"), str)),
            default=None,
        ),
        "entrants": [
            {
                "id": e.id,
                "label": e.label,
                "kind": e.kind,
                # drop litellm's provider routing prefix for display
                "model": (
                    e.model.removeprefix("openai/")
                    if e.model and e.api_base
                    else e.model
                ),
                "web_access": e.web_access,
                "cutoff": e.cutoff,
            }
            for e in board_entrants
        ],
        "leaderboard": _leaderboard(forecasts, ok_rows, resolutions, board_entrants, ai_ids),
        "open": _open_markets(ok_rows, resolutions),
        "duels": _duels(ok_rows, resolutions, ai_ids),
        "hall_of_wrong": _hall_of_wrong(ok_rows, resolutions, ai_ids),
        "stats": {
            "forecasts": len(ok_rows),
            "resolved": len(resolved_ok),
            "markets": len({r["slug"] for r in ok_rows}),
            "n_markets": len({_cluster_key(r) for r in resolved_ok}),
            "entrants": len(board_entrants),
            "since": min(
                (r["ts"][:10] for r in forecasts if isinstance(r.get("ts"), str)),
                default=None,
            ),
            "unresolvable": sum(
                1 for r in resolutions.values() if r.get("outcome") is None
            ),
        },
    }


def run_build(
    data_dir: Path,
    config_path: Path | None = None,
    out_dir: Path = Path("site"),
    *,
    now: datetime | None = None,
) -> dict:
    """Build the board and write ``site/`` (data.json, index.html, CNAME)."""
    from pm_benchmark.arena_site import render_site

    now = now or _utc_now()
    board = build_board(
        load_forecasts(data_dir),
        load_resolutions(data_dir),
        load_entrants(config_path or _default_config()),
        now=now,
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    data_path = out_dir / "data.json"
    data_path.write_text(json.dumps(board, indent=2) + "\n")

    index_path = out_dir / "index.html"
    index_path.write_text(render_site(board))
    (out_dir / "CNAME").write_text(f"{CNAME_DOMAIN}\n")

    return {
        "data_json": str(data_path),
        "index_html": str(index_path),
        "markets_open": len(board["open"]),
        "leaderboard": len(board["leaderboard"]),
    }
