"""Render the Forecast Arena board as a self-contained static HTML page.

`render_site(board)` consumes the `board` dict produced by the arena pipeline
(see CONTRACT-board.md) and returns one HTML document: inline CSS only, no
JavaScript, no external assets, light and dark color schemes.
"""
from __future__ import annotations

import math
import re
from collections.abc import Hashable
from datetime import datetime, timezone
from html import escape
from typing import Any

SITE_TITLE = "Forecast Arena"
PAGE_TITLE = "Forecast Arena (Unofficial)"
EYEBROW = "Same questions. Different minds. Real outcomes."
HERO_SUB = (
    "A public, daily benchmark where AI models and simple baselines "
    "forecast the same real-world events before they resolve."
)
TAGLINE = "A more open future for forecasting."
DAILY_RUN = "06:17 UTC"
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
_MONTHS = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)
# Fixed domain for the alpha-vs-crowd confidence whisker, so rows compare.
_CI_LO, _CI_HI = -0.15, 0.15
# One fixed accent per entrant, in board order: (light theme, dark theme).
# Pairs are chosen for WCAG-AA text contrast on each theme's surfaces; the
# crowd is neutral gray. Indexes wrap when there are more entrants.
_PALETTE = (
    ("#1d4ed8", "#6ea8fe"),  # blue
    ("#7c3aed", "#b794f6"),  # violet
    ("#0f766e", "#2dd4bf"),  # teal
    ("#c2410c", "#fb923c"),  # orange
    ("#be185d", "#f472b6"),  # pink
    ("#0e7490", "#22d3ee"),  # cyan
    ("#92400e", "#fbbf24"),  # amber
    ("#15803d", "#4ade80"),  # green
)
_CROWD_COLORS = ("#64748b", "#9aa7b4")
_TOKENS = re.compile(r"[a-z0-9]+")

_CSS = """
:root {
  color-scheme: light dark;
  --bg: #f6f8fb;
  --bg2: #eef2f7;
  --card: #ffffff;
  --fg: #0f172a;
  --muted: #475569;
  --border: #e2e8f0;
  --track: #e5eaf1;
  --accent: #1d4ed8;
  --accent-soft: #dfe8ff;
  --good: #15803d;
  --bad: #b91c1c;
  --open: #b45309;
  --gap-bg: #fbe4e4;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0a0e14;
    --bg2: #0d131b;
    --card: #10161f;
    --fg: #e8edf4;
    --muted: #93a0b0;
    --border: #222c38;
    --track: #1c2530;
    --accent: #6ea8fe;
    --accent-soft: #152238;
    --good: #4ade80;
    --bad: #f87171;
    --open: #fbbf24;
    --gap-bg: #381624;
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
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
code {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.9em;
}
.wrap { margin: 0 auto; max-width: 1120px; padding: 0 18px; }

/* top bar */
.topbar {
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  background: color-mix(in srgb, var(--bg) 86%, transparent);
  border-bottom: 1px solid var(--border);
  position: sticky;
  top: 0;
  z-index: 20;
}
.topbar-in {
  align-items: center;
  display: flex;
  flex-wrap: wrap;
  gap: 4px 14px;
  padding-bottom: 6px;
  padding-top: 6px;
}
.wordmark { color: var(--fg); font-size: 1.02rem; font-weight: 800; }
a.wordmark:hover { text-decoration: none; }
.nav { display: flex; margin-left: 6px; }
.nav a {
  color: var(--muted);
  display: inline-block;
  font-size: 0.85rem;
  font-weight: 600;
  min-height: 40px;
  padding: 10px;
}
.nav a:hover { color: var(--fg); text-decoration: none; }
.pill {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 999px;
  color: var(--muted);
  font-size: 0.7rem;
  font-weight: 600;
  margin-left: auto;
  padding: 5px 12px;
  white-space: nowrap;
}

/* hero */
.hero {
  align-items: center;
  display: grid;
  gap: 28px;
  grid-template-columns: minmax(0, 1fr) auto;
  padding: 46px 0 6px;
}
.eyebrow {
  color: var(--accent);
  font-size: 0.72rem;
  font-weight: 800;
  letter-spacing: 0.18em;
  margin: 0 0 10px;
  text-transform: uppercase;
}
h1 {
  font-size: clamp(2.1rem, 5.4vw, 3.3rem);
  font-weight: 800;
  letter-spacing: -0.03em;
  line-height: 1.05;
  margin: 0 0 12px;
}
h1 .ai { color: var(--accent); }
.hero-sub {
  color: var(--muted);
  font-size: 1.02rem;
  margin: 0 0 22px;
  max-width: 46ch;
}
.hero-art { align-items: center; display: flex; gap: 12px; }
.hero-art svg { display: block; }
.vt {
  font-size: 0.66rem;
  font-weight: 700;
  letter-spacing: 0.28em;
  opacity: 0.7;
  text-transform: uppercase;
  writing-mode: vertical-rl;
}
.stats {
  display: grid;
  gap: 10px;
  grid-template-columns: repeat(auto-fit, minmax(112px, 1fr));
}
.stat {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 11px 14px;
}
.stat-num {
  display: block;
  font-size: 1.3rem;
  font-variant-numeric: tabular-nums;
  font-weight: 800;
  letter-spacing: -0.01em;
}
.stat-label {
  color: var(--muted);
  font-size: 0.72rem;
  font-weight: 600;
  letter-spacing: 0.05em;
  text-transform: uppercase;
}
.hero-note { color: var(--muted); font-size: 0.8rem; margin: 12px 2px 0; }

/* sections */
main { padding-bottom: 24px; }
section { margin-top: 46px; }
.sec-head {
  align-items: baseline;
  display: flex;
  flex-wrap: wrap;
  gap: 4px 16px;
  justify-content: space-between;
  margin-bottom: 14px;
}
h2 { font-size: 1.4rem; font-weight: 800; letter-spacing: -0.02em; margin: 0; }
.sec-link { font-size: 0.82rem; font-weight: 600; white-space: nowrap; }
.sec-note { color: var(--muted); font-size: 0.82rem; margin: 0; }
.section-sub { color: var(--muted); font-size: 0.85rem; margin: -6px 0 12px; }

/* tables inside panels */
.panel {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 14px;
  overflow: hidden;
}
.table-wrap { overflow-x: auto; }
table { border-collapse: collapse; min-width: 720px; width: 100%; }
.mkt { min-width: 880px; }
th, td {
  font-size: 0.86rem;
  padding: 10px 14px;
  text-align: left;
  vertical-align: middle;
}
th {
  border-bottom: 1px solid var(--border);
  color: var(--muted);
  font-size: 0.66rem;
  font-weight: 700;
  letter-spacing: 0.07em;
  text-transform: uppercase;
  white-space: nowrap;
}
td { border-bottom: 1px solid var(--border); }
tbody tr:last-child td { border-bottom: 0; }
tr.baseline td { color: var(--muted); }
tr.ghost td { color: var(--muted); }
.num { font-variant-numeric: tabular-nums; text-align: right; }
.pct { font-variant-numeric: tabular-nums; font-weight: 600; text-align: center; }
td.q, .who { min-width: 180px; }
td.q a { color: var(--fg); font-weight: 600; }
td.q a:hover { color: var(--accent); }
.kdot {
  background: var(--ec, var(--muted));
  border-radius: 50%;
  display: inline-block;
  height: 9px;
  margin-right: 7px;
  width: 9px;
}
.who-meta {
  color: var(--muted);
  display: block;
  font-size: 0.72rem;
  font-weight: 400;
  margin-top: 2px;
}
.chip {
  background: color-mix(in srgb, var(--ec, var(--muted)) 14%, transparent);
  border-radius: 6px;
  color: var(--ec, var(--muted));
  display: inline-block;
  font-size: 0.66rem;
  font-weight: 800;
  letter-spacing: 0.06em;
  padding: 2px 8px;
  text-transform: uppercase;
  white-space: nowrap;
}

/* alpha value + CI whisker */
.acell { min-width: 190px; }
.aline { align-items: center; display: flex; gap: 9px; }
.aval { font-variant-numeric: tabular-nums; font-weight: 700; }
.ci-track {
  display: inline-block;
  height: 14px;
  position: relative;
  width: 96px;
}
.ci-track::before {
  background: var(--track);
  border-radius: 1px;
  content: "";
  height: 2px;
  left: 0;
  position: absolute;
  right: 0;
  top: 6px;
}
.ci-zero {
  background: var(--muted);
  border-radius: 1px;
  height: 10px;
  opacity: 0.45;
  position: absolute;
  top: 2px;
  transform: translateX(-50%);
  width: 2px;
}
.ci-bar {
  background: var(--ec, var(--accent));
  border-left: 2px solid var(--ec, var(--accent));
  border-right: 2px solid var(--ec, var(--accent));
  border-radius: 2px;
  height: 4px;
  opacity: 0.45;
  position: absolute;
  top: 5px;
}
.ci-dot {
  background: var(--ec, var(--accent));
  border: 1.5px solid var(--card);
  border-radius: 50%;
  height: 8px;
  position: absolute;
  top: 2px;
  transform: translateX(-50%);
  width: 8px;
}
.ns { color: var(--muted); display: block; font-size: 0.7rem; }

/* leaderboard empty state */
.lb-empty { border-top: 1px dashed var(--border); padding: 24px 16px 26px; text-align: center; }
.lb-empty-t { font-weight: 700; margin: 0 0 4px; }
.lb-empty-s { color: var(--muted); font-size: 0.83rem; margin: 0; }

/* duel cards */
.duels { display: grid; gap: 14px; grid-template-columns: repeat(3, 1fr); }
.duel {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 14px;
  display: flex;
  flex-direction: column;
  padding: 14px 16px;
}
.duel-q { font-size: 0.95rem; font-weight: 700; line-height: 1.35; margin: 0 0 6px; }
.duel-q a { color: var(--fg); }
.duel-q a:hover { color: var(--accent); text-decoration: none; }
.duel-meta {
  align-items: center;
  color: var(--muted);
  display: flex;
  flex-wrap: wrap;
  font-size: 0.72rem;
  gap: 7px;
  margin: 0 0 4px;
}
.status {
  border: 1px solid currentColor;
  border-radius: 999px;
  font-size: 0.64rem;
  font-weight: 800;
  letter-spacing: 0.06em;
  padding: 1px 8px;
  text-transform: uppercase;
}
.status-open { color: var(--open); }
.status-won { color: var(--good); }
.status-lost { color: var(--bad); }
.status-tie { color: var(--muted); }
.track {
  background: var(--track);
  border-radius: 999px;
  height: 10px;
  margin: 12px 0 2px;
  position: relative;
}
.pdot {
  background: var(--ec, var(--muted));
  border: 2px solid var(--card);
  border-radius: 50%;
  height: 11px;
  position: absolute;
  top: 50%;
  transform: translate(-50%, -50%);
  width: 11px;
}
.ticks {
  color: var(--muted);
  display: flex;
  font-size: 0.64rem;
  justify-content: space-between;
  margin: 3px 1px 9px;
}
.dlegend {
  display: flex;
  flex-wrap: wrap;
  font-size: 0.8rem;
  gap: 4px 14px;
  margin: 0 0 10px;
}
.leg { white-space: nowrap; }
.leg .kdot { margin-right: 5px; }
.duel-gap { margin: auto 0 0; }
.gap {
  background: var(--gap-bg);
  border-radius: 999px;
  color: var(--bad);
  display: inline-block;
  font-size: 0.78rem;
  font-weight: 800;
  padding: 3px 10px;
}
.quote {
  background: var(--bg2);
  border-left: 3px solid var(--ec, var(--border));
  border-radius: 0 8px 8px 0;
  color: var(--muted);
  font-size: 0.82rem;
  margin: 10px 0 0;
  padding: 9px 12px;
}
.quote p { margin: 0; }
.quote cite {
  color: var(--fg);
  display: block;
  font-size: 0.74rem;
  font-style: normal;
  font-weight: 700;
  margin-top: 6px;
}

/* open markets: market view mini track */
.mv { min-width: 84px; width: 84px; }
.mini { display: block; height: 10px; margin: 2px 0; position: relative; }
.mini::before {
  background: var(--track);
  border-radius: 1px;
  content: "";
  height: 2px;
  left: 0;
  position: absolute;
  right: 0;
  top: 4px;
}
.mini .pdot { border: 0; height: 7px; width: 7px; }
.mvt {
  color: var(--muted);
  display: flex;
  font-size: 0.58rem;
  font-weight: 400;
  justify-content: space-between;
  letter-spacing: 0;
  text-transform: none;
}

/* hall of wrong */
.wrong {
  background: var(--card);
  border: 1px solid var(--border);
  border-left: 3px solid var(--bad);
  border-radius: 10px;
  margin: 0 0 10px;
  padding: 12px 16px;
}
.wrong-q { font-weight: 700; margin: 0 0 4px; }
.wrong-q a { color: var(--fg); }
.wrong-q a:hover { color: var(--accent); }
.wrong-line { color: var(--muted); font-size: 0.83rem; margin: 0; }

/* empty panels */
.empty {
  background: var(--card);
  border: 1px dashed var(--border);
  border-radius: 10px;
  color: var(--muted);
  font-size: 0.9rem;
  padding: 14px 16px;
}

/* footer */
.site-footer {
  background: var(--bg2);
  border-top: 1px solid var(--border);
  margin-top: 56px;
}
.cols3 {
  display: grid;
  gap: 20px;
  grid-template-columns: repeat(3, 1fr);
  padding-bottom: 30px;
  padding-top: 30px;
}
.fcol h3 {
  align-items: center;
  display: flex;
  font-size: 0.92rem;
  gap: 8px;
  margin: 0 0 8px;
}
.fcol h3 svg { color: var(--accent); flex: none; }
.fcol p { color: var(--muted); font-size: 0.83rem; margin: 0 0 10px; }
.fcol details { font-size: 0.83rem; }
.fcol summary {
  color: var(--accent);
  cursor: pointer;
  font-weight: 600;
  padding: 10px 0;
}
.method { color: var(--muted); padding-left: 18px; }
.method li { margin: 5px 0; }
.btn {
  background: var(--accent);
  border-radius: 8px;
  color: #fff;
  display: inline-block;
  font-size: 0.82rem;
  font-weight: 700;
  min-height: 40px;
  padding: 10px 16px;
}
.btn:hover { opacity: 0.9; text-decoration: none; }
.botbar { border-top: 1px solid var(--border); }
.botbar-in {
  align-items: center;
  display: flex;
  flex-wrap: wrap;
  gap: 6px 12px;
  padding-bottom: 6px;
  padding-top: 14px;
}
.botbar .pill { margin-left: auto; }
.tagline { color: var(--muted); font-size: 0.8rem; }
.stamps { color: var(--muted); font-size: 0.72rem; margin: 0; padding-bottom: 18px; }

@media (max-width: 960px) {
  .duels { grid-template-columns: repeat(2, 1fr); }
}
@media (max-width: 840px) {
  .hero { grid-template-columns: 1fr; }
  .hero-art { display: none; }
}
@media (max-width: 760px) {
  .cols3 { grid-template-columns: 1fr; }
}
@media (max-width: 720px) {
  .nav { flex-basis: 100%; margin-left: 0; order: 3; }
  .pill { margin-left: 0; white-space: normal; }
  .botbar .pill { margin-left: 0; }
}
@media (max-width: 640px) {
  .duels { grid-template-columns: 1fr; }
}
@media (max-width: 600px) {
  .hide-sm { display: none; }
}
"""

_ICON_DOC = (
    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" '
    'stroke="currentColor" stroke-width="2" stroke-linecap="round" '
    'stroke-linejoin="round" aria-hidden="true">'
    '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>'
    '<path d="M14 2v6h6"/><path d="M9 13h6M9 17h6"/></svg>'
)
_ICON_AGENT = (
    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" '
    'stroke="currentColor" stroke-width="2" stroke-linecap="round" '
    'stroke-linejoin="round" aria-hidden="true">'
    '<rect x="5" y="9" width="14" height="11" rx="2"/>'
    '<path d="M12 9V5M9 5h6"/><path d="M9 14h.01M15 14h.01"/>'
    '<path d="M9 17h6"/></svg>'
)
_ICON_CODE = (
    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" '
    'stroke="currentColor" stroke-width="2" stroke-linecap="round" '
    'stroke-linejoin="round" aria-hidden="true">'
    '<path d="m8 6-6 6 6 6"/><path d="m16 6 6 6-6 6"/></svg>'
)

_METHODOLOGY = f"""
<ul class="method">
  <li><strong>Population.</strong> Each daily run selects open binary
    Polymarket markets ending in 1–14 days, with liquidity ≥ $10k and a
    market price in [0.03, 0.97], at most 2 markets per event,
    top 20 by volume — excluding markets already forecast, closed, or
    already resolved. Results describe this population of high-volume,
    near-resolution markets only —
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
    with ≥30 distinct resolved events — below that the column reads
    <em>too few events</em>. The crowd row's Brier covers the union
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


def _klass(*names: str) -> str:
    """Join truthy class names."""
    return " ".join(n for n in names if n)


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
    """Format a count like ``n``; missing or non-finite as an em dash."""
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return _EM_DASH
    return _esc(value)


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


def _fmt_day(ts: Any) -> str:
    """Format an ISO date/timestamp like ``Sep 30``; bad values an em dash."""
    if not isinstance(ts, str):
        return _EM_DASH
    try:
        day = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return _EM_DASH
    return f"{_MONTHS[day.month - 1]} {day.day}"


def _fmt_dt(day: datetime) -> str:
    """Format a datetime like ``Sep 30``."""
    return f"{_MONTHS[day.month - 1]} {day.day}"


def _fmt_outcome(outcome: Any) -> str:
    """Render a binary resolution (``0``/``1``) as No/Yes or an em dash."""
    if outcome == 1:
        return "Yes"
    if outcome == 0:
        return "No"
    return _EM_DASH


def _parse_ts(ts: Any) -> datetime | None:
    """Parse an ISO timestamp (naive values treated as UTC)."""
    if not isinstance(ts, str) or not ts:
        return None
    try:
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _first_end(board: dict) -> datetime | None:
    """Earliest open-market ``end_date``; ``None`` when none parses."""
    markets = board.get("open")
    if not isinstance(markets, list):
        return None
    best: datetime | None = None
    for market in markets:
        if not isinstance(market, dict):
            continue
        end = _parse_ts(market.get("end_date"))
        if end is not None and (best is None or end < best):
            best = end
    return best


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


def _short_label(entrants: dict[str, dict], entrant_id: Any) -> str:
    """Escaped compact label: the label minus any parenthetical suffix."""
    info = _entrant_info(entrants, entrant_id)
    label = info.get("label") or entrant_id or "?"
    text = str(label)
    short = text.split("(")[0].strip()
    return _esc(short or text.strip())


def _eid_cls(order: dict, entrant_id: Any) -> str:
    """The per-entrant color class (``e{index}``); ``""`` when unknown."""
    if isinstance(entrant_id, Hashable):
        index = order.get(entrant_id)
        if index is not None:
            return f"e{index}"
    return ""


def _palette_css(order: dict) -> str:
    """Emit ``--ec`` rules assigning each entrant one fixed color."""
    light, dark = [], []
    for eid, index in order.items():
        pair = _CROWD_COLORS if eid == _CROWD_ID else _PALETTE[
            index % len(_PALETTE)
        ]
        light.append(f".e{index}{{--ec:{pair[0]}}}")
        dark.append(f".e{index}{{--ec:{pair[1]}}}")
    return (
        "".join(light)
        + "@media (prefers-color-scheme: dark){"
        + "".join(dark)
        + "}"
    )


def _kdot(cls: str = "") -> str:
    """A small inline legend dot in the entrant's color."""
    return f'<i class="{_klass("kdot", cls)}"></i>'


def _pdot(prob: Any, cls: str = "") -> str:
    """A positioned dot on a 0-100% track; ``""`` when prob is missing."""
    number = _num(prob)
    if number is None:
        return ""
    pos = min(max(number, 0.0), 1.0) * 100
    return f'<i class="{_klass("pdot", cls)}" style="left:{pos:.2f}%"></i>'


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


def _clean_rationale(rationale: Any, info: dict) -> str | None:
    """A rationale worth showing, else ``None``.

    Hidden: empty values, mechanical baseline boilerplate, and texts that
    merely restate the entrant's id/label/model (e.g. ``"Jev
    opencode/jev-1.13-free"``).
    """
    if not isinstance(rationale, str) or not rationale.strip():
        return None
    if info.get("kind") == "baseline" or info.get("id") == _CROWD_ID:
        return None
    text = rationale.strip()
    source = " ".join(
        str(v)
        for v in (info.get("id"), info.get("label"), info.get("model"))
        if v
    )
    source_tokens = set(_TOKENS.findall(source.lower()))
    tokens = set(_TOKENS.findall(text.lower()))
    if tokens and tokens <= source_tokens:
        return None
    return text


def _gap_text(prob: Any, market_prob: Any) -> str:
    """Signed AI-minus-crowd gap in percentage points."""
    ai, crowd = _num(prob), _num(market_prob)
    if ai is None or crowd is None:
        return f"{_EM_DASH} pts vs crowd"
    return f"{(ai - crowd) * 100:+.0f} pts vs crowd"


def _whisker(row: dict, cls: str) -> str:
    """A fixed-domain whisker bar for the alpha CI, with a dot at alpha."""
    ci = row.get("alpha_ci")
    if not isinstance(ci, (list, tuple)) or len(ci) != 2:
        return ""
    lo, hi = (_num(v) for v in ci)
    if lo is None or hi is None:
        return ""
    span = _CI_HI - _CI_LO

    def pos(value: float) -> float:
        return min(max((value - _CI_LO) / span, 0.0), 1.0) * 100

    alpha = _num(row.get("alpha"))
    dot = ""
    if alpha is not None:
        dot = (
            f'<i class="{_klass("ci-dot", cls)}" '
            f'style="left:{pos(alpha):.2f}%"></i>'
        )
    left, right = pos(lo), pos(hi)
    return (
        f'<span class="ci-track" title="95% CI {_fmt_ci(ci)}">'
        f'<i class="ci-zero" style="left:{pos(0):.2f}%"></i>'
        f'<i class="{_klass("ci-bar", cls)}" '
        f'style="left:{left:.2f}%;width:{max(right - left, 0.0):.2f}%"></i>'
        f"{dot}</span>"
    )


def _alpha_cell(row: dict, cls: str) -> str:
    """Headline alpha-vs-crowd cell: value, CI whisker, significance."""
    inner = (
        f'<span class="aline"><span class="aval">'
        f'{_fmt_signed(row.get("alpha"))}</span>'
    )
    significant = row.get("significant")
    # A CI over too few events reads as false certainty; show the whisker
    # only once significance is assessed.
    if significant is not None:
        inner += _whisker(row, cls)
    inner += "</span>"
    if significant is None:
        # Significance is withheld below the unique-market floor (M1); the
        # crowd row is the reference and gets no marker.
        if row.get("entrant") != _CROWD_ID:
            inner += '<span class="ns">too few events</span>'
    elif not significant:
        inner += '<span class="ns">not significant</span>'
    return inner


def _hero_art() -> str:
    """Decorative dotted-globe SVG beside the hero copy."""
    radius, step = 92, 9
    dots = []
    for y in range(-radius, radius + 1, step):
        chord = int((radius * radius - y * y) ** 0.5)
        for x in range(-chord, chord + 1, step):
            dots.append(f'<circle cx="{x}" cy="{y}" r="1.6"/>')
    return (
        '<div class="hero-art" aria-hidden="true">'
        '<svg width="210" height="210" viewBox="-104 -104 208 208" '
        f'fill="var(--accent)" opacity="0.5">{"".join(dots)}</svg>'
        '<span class="vt">Same questions. Brighter answers.</span></div>'
    )


def _stats_strip(board: dict) -> str:
    """Hero stat strip: forecasts, markets, entrants, resolution, cadence."""
    stats = board.get("stats")
    if not isinstance(stats, dict):
        stats = {}
    cells = [
        ("forecasts", _fmt_count(stats.get("forecasts"))),
        ("markets", _fmt_count(stats.get("markets"))),
        ("entrants", _fmt_count(stats.get("entrants"))),
    ]
    resolved = _num(stats.get("resolved"))
    if resolved is not None and resolved > 0:
        cells.append(("resolved", _fmt_count(stats.get("resolved"))))
    else:
        first = _first_end(board)
        cells.append(
            ("first resolution", _fmt_dt(first) if first else _EM_DASH)
        )
    cells.append(("updated daily", DAILY_RUN))
    body = "".join(
        f'<div class="stat"><span class="stat-num">{value}</span>'
        f'<span class="stat-label">{label}</span></div>'
        for label, value in cells
    )
    note = []
    if stats.get("since"):
        note.append(f"tracking since {_fmt_date(stats['since'])}")
    if board.get("last_run"):
        note.append(f"last pipeline run {_esc(board['last_run'])}")
    note_html = (
        f'<p class="hero-note">{" · ".join(note)}</p>' if note else ""
    )
    return f'<div class="stats">{body}</div>{note_html}'


def _hero(board: dict) -> str:
    return (
        '<section class="hero"><div>'
        f'<p class="eyebrow">{EYEBROW}</p>'
        '<h1>Can <span class="ai">AI</span> beat the crowd?</h1>'
        f'<p class="hero-sub">{HERO_SUB}</p>'
        + _stats_strip(board)
        + "</div>"
        + _hero_art()
        + "</section>"
    )


def _ghost_row(index: int, entrants: dict[str, dict], order: dict, eid: str) -> str:
    """A leaderboard placeholder row for an unscored entrant."""
    info = _entrant_info(entrants, eid)
    label = _entrant_label(entrants, eid)
    cls = _eid_cls(order, eid)
    kind = info.get("kind") or ""
    chip = (
        f'<span class="{_klass("chip", cls)}">{_esc(str(kind).upper())}</span>'
        if kind
        else _EM_DASH
    )
    return (
        f'<tr class="ghost"><td class="num">{index}</td>'
        f'<td class="who">{_kdot(cls)}{label}{_entrant_meta(info)}</td>'
        f"<td>{chip}</td>"
        '<td class="acell"><span class="aline"><span class="aval">—</span>'
        '<span class="ci-track"><i class="ci-zero" '
        'style="left:50.00%"></i></span></span></td>'
        '<td class="num">—</td><td class="num hide-sm">—</td>'
        '<td class="num">—</td><td class="num">—</td>'
        '<td class="hide-sm">—</td></tr>'
    )


def _lb_empty_panel(board: dict) -> str:
    """Centered note under the ghost leaderboard before resolution."""
    first = _first_end(board)
    title = "Scores appear when the first markets resolve"
    if first is not None:
        title += f" — {_fmt_dt(first)}"
    stats = board.get("stats")
    if not isinstance(stats, dict):
        stats = {}
    forecasts, markets = _num(stats.get("forecasts")), _num(stats.get("markets"))
    sub = "Results and scores publish after the first resolution."
    if forecasts is not None and markets is not None:
        sub = (
            f"{forecasts:.0f} forecasts across {markets:.0f} markets are "
            f"live. {sub}"
        )
    return (
        f'<div class="lb-empty"><p class="lb-empty-t">{title}</p>'
        f'<p class="lb-empty-s">{sub}</p></div>'
    )


def _leaderboard_section(board: dict, entrants: dict[str, dict], order: dict) -> str:
    head = (
        '<section id="leaderboard"><div class="sec-head">'
        "<h2>Leaderboard</h2>"
        '<p class="sec-note">Ranked by Brier score — lower is better · '
        "alpha vs crowd is the headline, negative beats the crowd</p>"
        "</div>"
    )
    rows = board.get("leaderboard")
    rows = rows if isinstance(rows, list) else []
    valid = [r for r in rows if isinstance(r, dict)]
    if not valid:
        if not order:
            return head + (
                '<p class="empty">First results after markets resolve.</p>'
                "</section>"
            )
        ghosts = "".join(
            _ghost_row(i, entrants, order, eid)
            for i, eid in enumerate(order, start=1)
        )
        return head + (
            '<div class="panel">' + _lb_table_inner(ghosts)
            + _lb_empty_panel(board) + "</div></section>"
        )
    body = []
    for i, row in enumerate(valid, start=1):
        eid = row.get("entrant")
        info = _entrant_info(entrants, eid)
        label = _entrant_label(entrants, eid)
        cls = _eid_cls(order, eid)
        kind = info.get("kind") or ""
        chip = (
            f'<span class="{_klass("chip", cls)}">'
            f"{_esc(str(kind).upper())}</span>"
            if kind
            else _EM_DASH
        )
        tr = ' class="baseline"' if kind == "baseline" else ""
        body.append(
            f"<tr{tr}><td class=\"num\">{i}</td>"
            f'<td class="who">{_kdot(cls)}{label}{_entrant_meta(info)}</td>'
            f"<td>{chip}</td>"
            f'<td class="acell">{_alpha_cell(row, cls)}</td>'
            f'<td class="num">{_fmt_score(row.get("brier"))}</td>'
            f'<td class="num hide-sm">{_fmt_score(row.get("ece"))}</td>'
            f'<td class="num">{_fmt_count(row.get("n_markets"))}</td>'
            f'<td class="num">{_fmt_prob(row.get("coverage"))}</td>'
            f'<td class="hide-sm">{_fmt_date(row.get("since"))}</td></tr>'
        )
    return head + (
        '<div class="panel">' + _lb_table_inner("".join(body))
        + "</div></section>"
    )


def _lb_table_inner(body: str) -> str:
    return (
        '<div class="table-wrap"><table><thead><tr>'
        '<th class="num">#</th><th>Entrant</th><th>Type</th>'
        "<th>Alpha vs crowd (95% CI)</th>"
        '<th class="num">Brier</th><th class="num hide-sm">ECE</th>'
        '<th class="num">Markets</th><th class="num">Coverage</th>'
        '<th class="hide-sm">Since</th>'
        "</tr></thead><tbody>" + body + "</tbody></table></div>"
    )


def _prob_of(forecasts: dict, eid: Any) -> Any:
    """The forecast probability for *eid*; ``None`` for bad shapes."""
    fc = forecasts.get(eid)
    return fc.get("prob") if isinstance(fc, dict) else None


def _open_section(board: dict, entrants: dict[str, dict], order: dict) -> str:
    markets = board.get("open")
    if not isinstance(markets, list) or not markets:
        return (
            '<section id="markets"><div class="sec-head">'
            "<h2>Open markets</h2></div>"
            '<p class="empty">No open forecasts yet — the next daily run '
            "adds fresh markets.</p></section>"
        )
    # Columns: board entrants minus the crowd, then any unknown ids.
    cols = [eid for eid in order if eid != _CROWD_ID]
    extra: list = []
    for market in markets:
        if not isinstance(market, dict):
            continue
        forecasts = market.get("forecasts")
        if not isinstance(forecasts, dict):
            continue
        for eid in forecasts:
            if (
                eid != _CROWD_ID
                and isinstance(eid, Hashable)
                and eid not in order
                and eid not in extra
            ):
                extra.append(eid)
    cols += extra
    ccls = _eid_cls(order, _CROWD_ID)
    head_cells = (
        '<th class="num">#</th><th>Question</th><th>Closes</th>'
        f'<th class="{_klass("pct", ccls)}">{_kdot()}Crowd</th>'
    )
    for eid in cols:
        cls = _eid_cls(order, eid)
        head_cells += (
            f'<th class="{_klass("pct", cls)}">{_kdot()}'
            f"{_short_label(entrants, eid)}</th>"
        )
    head_cells += (
        '<th class="mv">Market view'
        '<span class="mvt"><span>0%</span><span>50%</span>'
        "<span>100%</span></span></th>"
    )
    body = []
    index = 0
    for market in markets:
        if not isinstance(market, dict):
            continue
        index += 1
        forecasts = market.get("forecasts")
        if not isinstance(forecasts, dict):
            forecasts = {}
        question = _esc(
            market.get("question") or market.get("slug") or "Untitled market"
        )
        cells = (
            f'<td class="num">{index}</td>'
            f'<td class="q">'
            f'{_link_or_text(question, market.get("url"))}</td>'
            f"<td>{_fmt_day(market.get('end_date'))}</td>"
            f'<td class="{_klass("pct", ccls)}">'
            f'{_fmt_prob(market.get("market_prob"))}</td>'
        )
        dots = []
        for eid in cols:
            cls = _eid_cls(order, eid)
            prob = _prob_of(forecasts, eid)
            cells += f'<td class="{_klass("pct", cls)}">{_fmt_prob(prob)}</td>'
            dots.append(_pdot(prob, cls))
        dots.append(_pdot(market.get("market_prob"), ccls))
        cells += f'<td class="mv"><span class="mini">{"".join(dots)}</span></td>'
        body.append(f"<tr>{cells}</tr>")
    return (
        '<section id="markets"><div class="sec-head"><h2>Open markets</h2>'
        '<p class="sec-note">YES probabilities at forecast time · '
        "Crowd is the market price</p></div>"
        '<div class="panel"><div class="table-wrap"><table class="mkt">'
        f"<thead><tr>{head_cells}</tr></thead><tbody>"
        + "".join(body)
        + "</tbody></table></div></div></section>"
    )


def _duel_card(d: dict, entrants: dict[str, dict], order: dict) -> str:
    question = _esc(d.get("question") or d.get("slug") or "?")
    title = _link_or_text(question, d.get("url"))
    eid = d.get("entrant")
    info = _entrant_info(entrants, eid)
    cls = _eid_cls(order, eid)
    ccls = _eid_cls(order, _CROWD_ID)
    short = _short_label(entrants, eid)
    status = str(d.get("status") or "open")
    chip = (
        f'<span class="status status-{escape(status)}">'
        f"{escape(status)}</span>"
    )
    resolved = ""
    if d.get("outcome") in (0, 1):
        resolved = f"<span>· resolved {_fmt_outcome(d.get('outcome'))}</span>"
    meta = (
        f'<p class="duel-meta">{chip}<span>{short} vs crowd</span>'
        f"{resolved}</p>"
    )
    track = (
        f'<div class="track" aria-hidden="true">'
        f'{_pdot(d.get("prob"), cls)}'
        f'{_pdot(d.get("market_prob"), ccls)}</div>'
        '<div class="ticks" aria-hidden="true"><span>0%</span>'
        "<span>25%</span><span>50%</span><span>75%</span>"
        "<span>100%</span></div>"
    )
    legend = (
        f'<div class="dlegend"><span class="{_klass("leg", cls)}">'
        f'{_kdot()}{short} {_fmt_prob(d.get("prob"))}</span>'
        f'<span class="{_klass("leg", ccls)}">{_kdot()}Crowd '
        f'{_fmt_prob(d.get("market_prob"))}</span></div>'
    )
    gap = (
        f'<p class="duel-gap"><span class="gap">'
        f'{_gap_text(d.get("prob"), d.get("market_prob"))}</span></p>'
    )
    rationale = _clean_rationale(d.get("rationale"), info)
    quote = ""
    if rationale is not None:
        quote = (
            f'<blockquote class="{_klass("quote", cls)}">'
            f"<p>“{_esc(rationale)}”</p><cite>— {short}</cite></blockquote>"
        )
    return (
        f'<article class="duel"><h3 class="duel-q">{title}</h3>{meta}'
        f"{track}{legend}{gap}{quote}</article>"
    )


def _duels_section(board: dict, entrants: dict[str, dict], order: dict) -> str:
    duels = board.get("duels")
    if not isinstance(duels, list) or not duels:
        return (
            '<section id="duels"><div class="sec-head">'
            "<h2>Biggest disagreements</h2></div>"
            '<p class="empty">No large AI-vs-crowd disagreements yet.</p>'
            "</section>"
        )
    cards = "".join(
        _duel_card(d, entrants, order)
        for d in duels
        if isinstance(d, dict)
    )
    return (
        '<section id="duels"><div class="sec-head">'
        "<h2>Biggest disagreements</h2>"
        '<a class="sec-link" href="#markets">View all markets →</a>'
        "</div>"
        '<p class="section-sub">The largest gaps between an AI forecast '
        "and the crowd — won/lost once the market resolves.</p>"
        f'<div class="duels">{cards}</div></section>'
    )


def _hall_section(board: dict, entrants: dict[str, dict], order: dict) -> str:
    items = board.get("hall_of_wrong")
    if not isinstance(items, list) or not items:
        return (
            '<section id="hall-of-wrong"><div class="sec-head">'
            "<h2>Hall of Wrong</h2></div>"
            '<p class="empty">No confident misses yet.</p></section>'
        )
    cards = []
    for it in items:
        if not isinstance(it, dict):
            continue
        question = _esc(it.get("question") or it.get("slug") or "?")
        title = _link_or_text(question, it.get("url"))
        eid = it.get("entrant")
        cls = _eid_cls(order, eid)
        label = _entrant_label(entrants, eid)
        info = _entrant_info(entrants, eid)
        line = (
            f"{_kdot(cls)}<strong>{label}</strong> forecast "
            f"{_fmt_prob(it.get('prob'))} — resolved "
            f"{_fmt_outcome(it.get('outcome'))} "
            f"(crowd was at {_fmt_prob(it.get('market_prob'))}) · "
            f"{_fmt_date(it.get('resolved_at'))}"
        )
        rationale = _clean_rationale(it.get("rationale"), info)
        quote = (
            f'<blockquote class="{_klass("quote", cls)}">'
            f"<p>“{_esc(rationale)}”</p><cite>— {label}</cite></blockquote>"
            if rationale is not None
            else ""
        )
        cards.append(
            f'<div class="wrong"><p class="wrong-q">{title}</p>'
            f'<p class="wrong-line">{line}</p>{quote}</div>'
        )
    return (
        '<section id="hall-of-wrong"><div class="sec-head">'
        "<h2>Hall of Wrong</h2></div>"
        '<p class="section-sub">Resolved forecasts that were ≥80% '
        "confident and wrong — most confident first.</p>"
        + "".join(cards) + "</section>"
    )


def _footer(board: dict) -> str:
    bits = []
    generated = board.get("generated_at")
    if generated:
        bits.append(f"Board generated {_esc(generated)}")
    last_run = board.get("last_run")
    if last_run:
        bits.append(f"last pipeline run {_esc(last_run)}")
    stats = board.get("stats")
    if not isinstance(stats, dict):
        stats = {}
    if stats.get("since"):
        bits.append(f"tracking since {_fmt_date(stats['since'])}")
    unresolvable = _num(stats.get("unresolvable"))
    if unresolvable:
        bits.append(f"{unresolvable:.0f} unresolvable")
    bits.append(f"data branch <code>{DATA_BRANCH}</code>")
    stamps = f'<p class="stamps">{" · ".join(bits)}</p>'
    return (
        '<footer class="site-footer"><div class="wrap cols3">'
        '<div class="fcol" id="method">'
        f"<h3>{_ICON_DOC}Methodology</h3>"
        "<p>Same questions, scored before resolution. We report alpha "
        "vs the crowd with 95% confidence intervals and Brier score.</p>"
        '<details><summary>Full details</summary>'
        f"{_METHODOLOGY}</details></div>"
        '<div class="fcol">'
        f"<h3>{_ICON_AGENT}Add your agent</h3>"
        "<p>Build a forecasting agent? Join Forecast Arena — open, "
        "reproducible, and community-driven.</p>"
        f'<p><a class="btn" href="{REPO_URL}">Get started →</a></p></div>'
        '<div class="fcol">'
        f"<h3>{_ICON_CODE}Open source</h3>"
        "<p>Code, data, and analysis are on GitHub. Suggestions and "
        "corrections welcome.</p>"
        f'<p><a href="{REPO_URL}">View on GitHub →</a></p></div>'
        "</div>"
        '<div class="botbar"><div class="wrap">'
        '<div class="botbar-in">'
        f'<span class="wordmark">{SITE_TITLE}</span>'
        f'<span class="tagline">{TAGLINE}</span>'
        f'<span class="pill">{DISCLAIMER}</span>'
        f"</div>{stamps}</div></div></footer>"
    )


def render_site(board: dict) -> str:
    """Render the arena *board* dict as one self-contained HTML document."""
    entrants = _entrant_index(board)
    order = {eid: i for i, eid in enumerate(entrants)}
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
            f"<style>{_CSS}{_palette_css(order)}</style>",
            "</head>",
            "<body>",
            (
                '<header class="topbar" id="top"><div class="wrap topbar-in">'
                f'<a class="wordmark" href="#top">{SITE_TITLE}</a>'
                '<nav class="nav" aria-label="Sections">'
                '<a href="#leaderboard">Leaderboard</a>'
                '<a href="#markets">Markets</a>'
                '<a href="#method">Method</a>'
                f'<a href="{REPO_URL}">GitHub</a>'
                "</nav>"
                f'<span class="pill">{DISCLAIMER}</span>'
                "</div></header>"
            ),
            '<main class="wrap">',
            _hero(board),
            _leaderboard_section(board, entrants, order),
            _duels_section(board, entrants, order),
            _open_section(board, entrants, order),
            _hall_section(board, entrants, order),
            "</main>",
            _footer(board),
            "</body>",
            "</html>",
        ]
    )
