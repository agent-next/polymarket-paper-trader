"""Render the Forecast Arena board as a self-contained static HTML page.

`render_site(board)` consumes the `board` dict produced by the arena pipeline
(see CONTRACT-board.md) and returns one HTML document: inline CSS only, no
JavaScript, no external assets, light and dark color schemes.
"""
from __future__ import annotations

import math
from collections.abc import Hashable
from html import escape
from typing import Any

SITE_TITLE = "Forecast Arena"
PAGE_TITLE = "Forecast Arena (Unofficial)"
PITCH = "Can AI forecast real-world events better than the crowd?"
DISCLAIMER = (
    "Unofficial · not affiliated with Polymarket · "
    "paper forecasts only, no real money"
)
META_DESCRIPTION = (
    "Forecast Arena — can AI forecast real-world events better than the "
    "crowd? AI entrants and naive baselines publish probabilities on "
    "Polymarket markets before they resolve, scored with Brier and alpha "
    "vs the crowd. Unofficial, paper forecasts only."
)
REPO_URL = "https://github.com/agent-next/polymarket-paper-trader"
DATA_BRANCH = "arena-data"
# No JavaScript on this page at all: style/img are the only allowed sources.
CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; "
    "img-src data:; script-src 'none'"
)

_EM_DASH = "—"
_CROWD_ID = "crowd"
_FAVICON = (
    "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' "
    "viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='7' "
    "fill='%232563eb'/%3E%3Cpath d='M8 22v-6M14 22V9M20 22v-9M26 22V12' "
    "stroke='white' stroke-width='3' stroke-linecap='round'/%3E%3C/svg%3E"
)

_CSS = """
:root {
  --bg: #f5f6f8;
  --fg: #16181d;
  --muted: #4d5560;
  --card: #ffffff;
  --border: #d9dde3;
  --track: #e3e7ec;
  --accent: #1d4ed8;
  --accent-soft: #dfe8ff;
  --ai: #6d28d9;
  --baseline: #475569;
  --good: #166534;
  --bad: #b91c1c;
  --open: #8a5808;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0d1013;
    --fg: #e7eaef;
    --muted: #9ba5b1;
    --card: #161b21;
    --border: #2b323b;
    --track: #272f39;
    --accent: #6ea8fe;
    --accent-soft: #1c2a45;
    --ai: #b79bf7;
    --baseline: #94a3b8;
    --good: #5fd08a;
    --bad: #f28585;
    --open: #e5b65c;
  }
}
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body {
  background: var(--bg);
  color: var(--fg);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
    "Helvetica Neue", Arial, sans-serif;
  font-size: 16px;
  line-height: 1.5;
  margin: 0;
}
a { color: var(--accent); }
.site-header, main, .site-footer {
  margin: 0 auto;
  max-width: 960px;
  padding-left: 16px;
  padding-right: 16px;
}
.site-header { padding-top: 20px; }
.disclaimer {
  background: var(--accent-soft);
  border-radius: 999px;
  color: var(--accent);
  display: inline-block;
  font-size: 0.8rem;
  font-weight: 600;
  padding: 4px 12px;
}
.site-header h1 { font-size: 1.9rem; margin: 10px 0 2px; }
.pitch { color: var(--muted); margin: 0; }
main { padding-bottom: 32px; }
section { margin-top: 28px; }
h2 {
  border-bottom: 1px solid var(--border);
  font-size: 1.15rem;
  margin: 0 0 10px;
  padding-bottom: 5px;
}
.section-sub {
  color: var(--muted);
  font-size: 0.85rem;
  margin: -4px 0 10px;
}
.stats {
  display: grid;
  gap: 8px;
  grid-template-columns: repeat(auto-fit, minmax(104px, 1fr));
}
.stat {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 10px 12px;
}
.stat-num {
  display: block;
  font-size: 1.25rem;
  font-variant-numeric: tabular-nums;
  font-weight: 700;
}
.stat-label { color: var(--muted); font-size: 0.78rem; }
.table-wrap { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; }
th, td {
  border-bottom: 1px solid var(--border);
  font-size: 0.88rem;
  padding: 8px 10px;
  text-align: left;
  vertical-align: top;
}
th {
  color: var(--muted);
  font-size: 0.72rem;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}
.num { font-variant-numeric: tabular-nums; text-align: right; }
tr.baseline td { background: var(--bg); color: var(--muted); }
.badge {
  border: 1px solid currentColor;
  border-radius: 999px;
  display: inline-block;
  font-size: 0.7rem;
  font-weight: 600;
  padding: 0 8px;
  text-transform: uppercase;
}
.badge-ai { color: var(--ai); }
.badge-baseline { color: var(--baseline); }
.who-meta {
  color: var(--muted);
  display: block;
  font-size: 0.72rem;
  font-weight: 400;
}
.ci, .ns { color: var(--muted); font-size: 0.8rem; white-space: nowrap; }
.market {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 10px;
  margin: 0 0 12px;
  padding: 12px 14px;
}
.market h3 { font-size: 1rem; margin: 0 0 2px; }
.meta { color: var(--muted); font-size: 0.8rem; margin: 0 0 8px; }
.prob-row {
  align-items: center;
  display: grid;
  font-size: 0.85rem;
  gap: 8px;
  grid-template-columns: minmax(80px, 150px) 1fr 46px;
  padding: 3px 0;
}
.who { overflow: hidden; text-overflow: ellipsis; }
.prob-row.crowd .who { color: var(--baseline); font-weight: 600; }
.bar {
  background: var(--track);
  border-radius: 4px;
  display: block;
  height: 10px;
  overflow: hidden;
}
.bar > span { background: var(--accent); display: block; height: 100%; }
.prob-row.crowd .bar > span { background: var(--baseline); }
.prob-row .num { padding: 0; }
details { font-size: 0.85rem; margin-top: 6px; }
summary { color: var(--accent); cursor: pointer; }
details p { color: var(--muted); margin: 6px 0 0; }
.fc-ts { font-size: 0.75rem; }
.status {
  border: 1px solid currentColor;
  border-radius: 999px;
  font-size: 0.72rem;
  font-weight: 700;
  padding: 0 8px;
  text-transform: uppercase;
}
.status-open { color: var(--open); }
.status-won { color: var(--good); }
.status-lost { color: var(--bad); }
.status-tie { color: var(--muted); }
.wrong {
  background: var(--card);
  border: 1px solid var(--border);
  border-left: 3px solid var(--bad);
  border-radius: 10px;
  margin: 0 0 10px;
  padding: 10px 14px;
}
.wrong-q { font-weight: 600; margin: 0 0 4px; }
.wrong-line { color: var(--muted); font-size: 0.85rem; margin: 0; }
.empty {
  background: var(--card);
  border: 1px dashed var(--border);
  border-radius: 10px;
  color: var(--muted);
  font-size: 0.9rem;
  padding: 14px 16px;
}
.method { font-size: 0.9rem; padding-left: 18px; }
.method li { margin: 5px 0; }
.site-footer {
  border-top: 1px solid var(--border);
  color: var(--muted);
  font-size: 0.85rem;
  margin-top: 36px;
  padding-bottom: 28px;
  padding-top: 14px;
}
@media (min-width: 640px) {
  .site-header h1 { font-size: 2.3rem; }
}
"""

_METHODOLOGY = f"""
<ul class="method">
  <li><strong>Population.</strong> Each daily run selects open binary
    Polymarket markets ending in 1–14 days, with liquidity ≥ $10k and a
    market price in [0.03, 0.97], top 20 by volume — excluding markets
    already forecast, closed, or already resolved. Results describe
    this population of high-volume, near-resolution markets only —
    not forecasting ability in general.</li>
  <li><strong>No market price in prompts.</strong> Entrants see only the
    market question, description and closing date in a single-shot call
    with no tools — the crowd is the opponent, not an input. Each
    entrant's web access and training cutoff are listed on the
    leaderboard. A model may still have memorized information about the
    events being forecast; contamination from training data is a known
    limitation we cannot exclude in v1.</li>
  <li><strong>Committed before resolution.</strong> Every forecast is
    appended to the <code>{DATA_BRANCH}</code> git branch before the
    market resolves. The branch is append-only by convention; its git
    history is the audit log, not a tamper-proof guarantee. Forecast
    timestamps are self-reported by the runner — the commit timestamp is
    the independent witness.</li>
  <li><strong>Brier score.</strong> Mean of <code>(p − o)²</code> over
    resolved forecasts, where <code>o</code> is 0 or 1. Lower is better.
    Shown descriptively — entrants cover different market subsets, so
    raw Brier is not a like-for-like comparison.</li>
  <li><strong>Alpha vs crowd (headline).</strong> Entrant Brier minus
    crowd Brier on the same resolved markets — negative beats the crowd.
    The 95% confidence interval is a fixed-seed bootstrap
    (1,000 resamples, clustered by market event); one interval per
    entrant, no multiplicity correction. Significance is only reported
    with ≥30 unique resolved markets — below that the column reads
    <em>too few markets</em>. The crowd row's Brier covers the union
    of rows scored against AI entrants.</li>
  <li><strong>Calibration error (ECE).</strong> Mean gap between forecast
    probability and observed frequency across 10 equal-width bins.
    Reported only at ≥100 resolved forecasts — an em dash
    otherwise.</li>
  <li><strong>Baselines.</strong> <code>crowd</code> is the market YES
    price at forecast time, <code>coin</code> always forecasts 50%, and
    <code>favorite</code> forecasts 0.9 toward the side the crowd favors
    at forecast time. The Hall of Wrong lists AI entrants only.</li>
</ul>
"""


def _esc(value: Any) -> str:
    """HTML-escape a board value; ``None`` becomes the empty string."""
    return escape(str(value)) if value is not None else ""


def _safe_url(url: Any) -> str | None:
    """Return *url* for ``href`` use only when it is an https URL."""
    if isinstance(url, str) and url.startswith("https://"):
        return escape(url, quote=True)
    return None


def _link_or_text(text: str, url: Any) -> str:
    """Wrap already-escaped *text* in an anchor for safe URLs only."""
    safe = _safe_url(url)
    return f'<a href="{safe}">{text}</a>' if safe is not None else text


def _num(value: Any) -> float | None:
    """Coerce *value* to a finite float; ``None`` when not numeric."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _fmt_prob(prob: Any) -> str:
    """Format a [0, 1] probability as a percentage; bad values an em dash."""
    number = _num(prob)
    return _EM_DASH if number is None else f"{number * 100:.0f}%"


def _fmt_score(value: Any) -> str:
    """Format a Brier/ECE-style score to 3 decimals; bad values an em dash."""
    number = _num(value)
    return _EM_DASH if number is None else f"{number:.3f}"


def _fmt_signed(value: Any) -> str:
    """Format a signed metric to 3 decimals; bad values an em dash."""
    number = _num(value)
    return _EM_DASH if number is None else f"{number:+.3f}"


def _fmt_count(value: Any) -> str:
    """Format a count like ``n``; ``None`` as an em dash."""
    return _EM_DASH if value is None else _esc(value)


def _fmt_ci(ci: Any) -> str | None:
    """Format a ``[lo, hi]`` confidence interval; ``None`` when absent."""
    if not isinstance(ci, (list, tuple)) or len(ci) != 2:
        return None
    lo, hi = (_num(v) for v in ci)
    if lo is None or hi is None:
        return None
    return f"[{lo:+.3f}, {hi:+.3f}]"


def _fmt_date(ts: Any) -> str:
    """Trim an ISO timestamp to a readable date; ``None`` as an em dash."""
    if not isinstance(ts, str) or not ts:
        return _EM_DASH
    return escape(ts[:10])


def _fmt_outcome(outcome: Any) -> str:
    """Render a binary resolution (``0``/``1``) as No/Yes or an em dash."""
    if outcome == 1:
        return "Yes"
    if outcome == 0:
        return "No"
    return _EM_DASH


def _entrant_index(board: dict) -> dict[str, dict]:
    """Map entrant id to its record, preserving board order."""
    entrants = board.get("entrants")
    if not isinstance(entrants, list):
        return {}
    return {
        e["id"]: e
        for e in entrants
        if isinstance(e, dict) and e.get("id")
    }


def _entrant_info(entrants: dict[str, dict], entrant_id: Any) -> dict:
    """The entrant record for *entrant_id*; ``{}`` when unknown."""
    if isinstance(entrant_id, Hashable):
        return entrants.get(entrant_id) or {}
    return {}


def _entrant_label(entrants: dict[str, dict], entrant_id: Any) -> str:
    """Escaped display label for an entrant id, falling back to the id."""
    info = _entrant_info(entrants, entrant_id)
    return _esc(info.get("label") or entrant_id or "?")


def _prob_row(who: str, prob: Any, kind: str = "") -> str:
    """One labeled probability bar; *who* must be escaped already."""
    number = _num(prob)
    width = 0.0 if number is None else min(max(number, 0.0), 1.0) * 100
    cls = f"prob-row {kind}" if kind else "prob-row"
    return (
        f'<div class="{cls}"><span class="who">{who}</span>'
        f'<span class="bar"><span style="width:{width:.2f}%"></span></span>'
        f'<span class="num">{_fmt_prob(prob)}</span></div>'
    )


def _forecast_ids(forecasts: dict, entrants: dict[str, dict]) -> list:
    """Forecast entrant ids in board order, then any unknown ids."""
    ordered = [eid for eid in entrants if eid in forecasts]
    return ordered + [eid for eid in forecasts if eid not in entrants]


def _stats_section(board: dict) -> str:
    stats = board.get("stats")
    if not isinstance(stats, dict):
        stats = {}
    cells = [
        ("forecasts", _fmt_count(stats.get("forecasts"))),
        ("resolved", _fmt_count(stats.get("resolved"))),
        ("markets", _fmt_count(stats.get("markets"))),
        ("unresolvable", _fmt_count(stats.get("unresolvable"))),
        ("entrants", _fmt_count(stats.get("entrants"))),
        ("tracking since", _fmt_date(stats.get("since"))),
    ]
    body = "".join(
        f'<div class="stat"><span class="stat-num">{value}</span>'
        f'<span class="stat-label">{label}</span></div>'
        for label, value in cells
    )
    return f'<section class="stats" aria-label="Arena statistics">{body}</section>'


def _entrant_meta(info: dict) -> str:
    """Small metadata line under an entrant label: model, access, cutoff."""
    bits = []
    model = info.get("model")
    if model:
        bits.append(_esc(model))
    web = info.get("web_access")
    if web is True:
        bits.append("web access")
    elif web is False:
        bits.append("no web access")
    cutoff = info.get("cutoff")
    if cutoff:
        bits.append(f"cutoff {_esc(cutoff)}")
    if not bits:
        return ""
    return f'<span class="who-meta">{" · ".join(bits)}</span>'


def _alpha_cell(row: dict) -> str:
    """Headline alpha-vs-crowd cell: value, CI, and significance marker."""
    cell = _fmt_signed(row.get("alpha"))
    ci = _fmt_ci(row.get("alpha_ci"))
    if ci:
        cell += f' <span class="ci">{ci}</span>'
    significant = row.get("significant")
    if significant is None:
        # Significance is withheld below the unique-market floor (M1); the
        # crowd row is the reference and gets no marker.
        if row.get("entrant") != _CROWD_ID:
            cell += ' <span class="ns">too few markets</span>'
    elif not significant:
        cell += ' <span class="ns">not significant</span>'
    return cell


def _leaderboard_section(board: dict, entrants: dict[str, dict]) -> str:
    rows = board.get("leaderboard")
    if not isinstance(rows, list) or not rows:
        return (
            '<section id="leaderboard"><h2>Leaderboard</h2>'
            '<p class="empty">First results after markets resolve.</p>'
            "</section>"
        )
    body = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        info = _entrant_info(entrants, row.get("entrant"))
        label = _entrant_label(entrants, row.get("entrant"))
        kind = info.get("kind") or ""
        badge = (
            f'<span class="badge badge-{escape(str(kind))}">'
            f"{_esc(kind).upper()}</span>"
            if kind
            else ""
        )
        tr = ' class="baseline"' if kind == "baseline" else ""
        body.append(
            f"<tr{tr}><td>{label}{_entrant_meta(info)}</td><td>{badge}</td>"
            f"<td>{_alpha_cell(row)}</td>"
            f'<td class="num">{_fmt_score(row.get("brier"))}</td>'
            f'<td class="num">{_fmt_score(row.get("ece"))}</td>'
            f'<td class="num">{_fmt_count(row.get("n"))}</td>'
            f'<td class="num">{_fmt_count(row.get("n_markets"))}</td>'
            f'<td class="num">{_fmt_prob(row.get("coverage"))}</td>'
            f"<td>{_fmt_date(row.get('since'))}</td></tr>"
        )
    return (
        '<section id="leaderboard"><h2>Leaderboard</h2>'
        '<p class="section-sub">Resolved forecasts only, sorted by Brier '
        "score — lower is better. Alpha vs the crowd is the headline "
        "metric: negative beats the crowd; Brier is descriptive.</p>"
        '<div class="table-wrap"><table><thead><tr><th>Entrant</th>'
        '<th>Kind</th><th>Alpha vs crowd (95% CI)</th>'
        '<th class="num">Brier</th><th class="num">ECE</th>'
        '<th class="num">n</th><th class="num">n_markets</th>'
        '<th class="num">Coverage</th><th>Since</th>'
        "</tr></thead><tbody>" + "".join(body) + "</tbody></table></div>"
        "</section>"
    )


def _open_section(board: dict, entrants: dict[str, dict]) -> str:
    markets = board.get("open")
    if not isinstance(markets, list) or not markets:
        return (
            '<section id="open"><h2>Open forecasts</h2>'
            '<p class="empty">No open forecasts yet — the next daily run '
            "adds fresh markets.</p></section>"
        )
    cards = []
    for market in markets:
        if not isinstance(market, dict):
            continue
        question = _esc(
            market.get("question") or market.get("slug") or "Untitled market"
        )
        title = _link_or_text(question, market.get("url"))
        rows = [_prob_row("Crowd", market.get("market_prob"), "crowd")]
        details = []
        forecasts = market.get("forecasts")
        if not isinstance(forecasts, dict):
            forecasts = {}
        for eid in _forecast_ids(forecasts, entrants):
            if eid == _CROWD_ID:
                continue  # the Crowd row already shows market_prob
            fc = forecasts.get(eid)
            if not isinstance(fc, dict):
                fc = {}
            label = _entrant_label(entrants, eid)
            rows.append(_prob_row(label, fc.get("prob")))
            bits = []
            ts = fc.get("ts")
            if ts:
                bits.append(
                    f'<p class="fc-ts">forecast at {_esc(ts)} UTC</p>'
                )
            rationale = fc.get("rationale")
            if rationale:
                bits.append(f"<p>{_esc(rationale)}</p>")
            if bits:
                summary = "rationale" if rationale else "forecast"
                details.append(
                    f"<details><summary>{label} — {summary}</summary>"
                    + "".join(bits) + "</details>"
                )
        cards.append(
            f'<div class="market"><h3>{title}</h3>'
            f'<p class="meta">closes {_fmt_date(market.get("end_date"))}</p>'
            + "".join(rows) + "".join(details) + "</div>"
        )
    return (
        '<section id="open"><h2>Open forecasts</h2>'
        '<p class="section-sub">Latest probabilities on markets still '
        "awaiting resolution — the crowd bar is the market price at "
        "forecast time.</p>" + "".join(cards) + "</section>"
    )


def _duels_section(board: dict, entrants: dict[str, dict]) -> str:
    duels = board.get("duels")
    if not isinstance(duels, list) or not duels:
        return (
            '<section id="duels"><h2>Duels: AI vs the crowd</h2>'
            '<p class="empty">No large AI-vs-crowd disagreements yet.</p>'
            "</section>"
        )
    rows = []
    for d in duels:
        if not isinstance(d, dict):
            continue
        question = _esc(d.get("question") or d.get("slug") or "?")
        market = _link_or_text(question, d.get("url"))
        rationale = d.get("rationale")
        if rationale:
            market += (
                f"<details><summary>rationale</summary>"
                f"<p>{_esc(rationale)}</p></details>"
            )
        status = str(d.get("status") or "open")
        badge = (
            f'<span class="status status-{escape(status)}">'
            f"{escape(status)}</span>"
        )
        rows.append(
            f"<tr><td>{market}</td>"
            f"<td>{_entrant_label(entrants, d.get('entrant'))}</td>"
            f'<td class="num">{_fmt_prob(d.get("prob"))}</td>'
            f'<td class="num">{_fmt_prob(d.get("market_prob"))}</td>'
            f'<td class="num">{_fmt_prob(d.get("gap"))}</td>'
            f"<td>{badge}</td>"
            f'<td class="num">{_fmt_outcome(d.get("outcome"))}</td></tr>'
        )
    return (
        '<section id="duels"><h2>Duels: AI vs the crowd</h2>'
        '<p class="section-sub">The largest gaps between an AI forecast '
        "and the crowd — won/lost once the market resolves, tie when "
        "equally close.</p>"
        '<div class="table-wrap"><table><thead><tr><th>Market</th>'
        '<th>Entrant</th><th class="num">AI</th><th class="num">Crowd</th>'
        '<th class="num">Gap</th><th>Status</th><th class="num">Outcome</th>'
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
        "</section>"
    )


def _hall_section(board: dict, entrants: dict[str, dict]) -> str:
    items = board.get("hall_of_wrong")
    if not isinstance(items, list) or not items:
        return (
            '<section id="hall-of-wrong"><h2>Hall of Wrong</h2>'
            '<p class="empty">No confident misses yet.</p></section>'
        )
    cards = []
    for it in items:
        if not isinstance(it, dict):
            continue
        question = _esc(it.get("question") or it.get("slug") or "?")
        title = _link_or_text(question, it.get("url"))
        label = _entrant_label(entrants, it.get("entrant"))
        line = (
            f"<strong>{label}</strong> forecast "
            f"{_fmt_prob(it.get('prob'))} — resolved "
            f"{_fmt_outcome(it.get('outcome'))} "
            f"(crowd was at {_fmt_prob(it.get('market_prob'))}) · "
            f"{_fmt_date(it.get('resolved_at'))}"
        )
        rationale = it.get("rationale")
        detail = (
            f"<details><summary>rationale</summary>"
            f"<p>{_esc(rationale)}</p></details>"
            if rationale
            else ""
        )
        cards.append(
            f'<div class="wrong"><p class="wrong-q">{title}</p>'
            f'<p class="wrong-line">{line}</p>{detail}</div>'
        )
    return (
        '<section id="hall-of-wrong"><h2>Hall of Wrong</h2>'
        '<p class="section-sub">Resolved forecasts that were ≥80% '
        "confident and wrong — most confident first.</p>"
        + "".join(cards) + "</section>"
    )


def _methodology_section() -> str:
    return (
        '<section id="methodology"><h2>Methodology</h2>'
        + _METHODOLOGY + "</section>"
    )


def _footer(board: dict) -> str:
    bits = []
    generated = board.get("generated_at")
    if generated:
        bits.append(f"Board generated {_esc(generated)}")
    last_run = board.get("last_run")
    if last_run:
        bits.append(f"last pipeline run {_esc(last_run)}")
    bits.append(f"data branch <code>{DATA_BRANCH}</code>")
    stamp = f'<p class="updated">{" · ".join(bits)}</p>'
    return (
        '<footer class="site-footer"><p>'
        f'<a href="{REPO_URL}">polymarket-paper-trader on GitHub</a> · '
        '<span class="soon">Add your agent — coming soon</span></p>'
        f"{stamp}</footer>"
    )


def render_site(board: dict) -> str:
    """Render the arena *board* dict as one self-contained HTML document."""
    entrants = _entrant_index(board)
    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" '
            'content="width=device-width, initial-scale=1">',
            '<meta http-equiv="Content-Security-Policy" '
            f'content="{CSP}">',
            f"<title>{PAGE_TITLE}</title>",
            f'<meta name="description" '
            f'content="{escape(META_DESCRIPTION)}">',
            f'<meta property="og:title" content="{PAGE_TITLE}">',
            f'<meta property="og:description" '
            f'content="{escape(META_DESCRIPTION)}">',
            '<meta property="og:type" content="website">',
            '<meta name="color-scheme" content="light dark">',
            f'<link rel="icon" href="{_FAVICON}">',
            f"<style>{_CSS}</style>",
            "</head>",
            "<body>",
            '<header class="site-header">',
            f'<span class="disclaimer">{DISCLAIMER}</span>',
            f"<h1>{SITE_TITLE}</h1>",
            f'<p class="pitch">{PITCH}</p>',
            "</header>",
            "<main>",
            _stats_section(board),
            _leaderboard_section(board, entrants),
            _open_section(board, entrants),
            _duels_section(board, entrants),
            _hall_section(board, entrants),
            _methodology_section(),
            "</main>",
            _footer(board),
            "</body>",
            "</html>",
        ]
    )
