"""Tests for pm_benchmark.arena_site."""
from __future__ import annotations

import re

from pm_benchmark.arena_site import _fmt_ci, render_site

DISCLAIMER = (
    "Unofficial · not affiliated with Polymarket · "
    "paper forecasts only, no real money"
)


def sample_board() -> dict:
    """Full board fixture per team/CONTRACT-board.md + the v1.1 addendum.

    Exercises every entrant kind, every significance state, every duel
    status, and the board keys (``last_run``, ``n_markets``, ``coverage``,
    ``since``, ``unresolvable``, ``web_access``/``cutoff``).
    """
    return {
        "generated_at": "2026-09-26T06:00:00Z",
        "last_run": "2026-09-26T05:58:11Z",
        "entrants": [
            {
                "id": "jev",
                "label": "Jev 1.13 (free)",
                "kind": "ai",
                "model": "opencode/jev-1.13-free",
                "web_access": False,
                "cutoff": "2025-09",
            },
            {
                "id": "gpt-oss",
                "label": "GPT-OSS 120B",
                "kind": "ai",
                "model": "openai/gpt-oss-120b",
                "web_access": True,
                "cutoff": "2024-06",
            },
            {
                "id": "crowd",
                "label": "Crowd (market price)",
                "kind": "baseline",
                "model": None,
                "web_access": None,
                "cutoff": None,
            },
            {
                "id": "coin",
                "label": "Coin flip (0.5)",
                "kind": "baseline",
                "model": None,
                "web_access": None,
                "cutoff": None,
            },
        ],
        "leaderboard": [
            {
                "entrant": "jev",
                "n": 42,
                "brier": 0.182,
                "ece": 0.071,
                "alpha": -0.012,
                "alpha_ci": [-0.031, 0.006],
                "significant": False,
                "n_markets": 30,
                "coverage": 0.9,
                "since": "2026-09-26",
            },
            {
                "entrant": "gpt-oss",
                "n": 40,
                "brier": 0.19,
                "ece": None,
                "alpha": -0.05,
                "alpha_ci": [-0.09, -0.01],
                "significant": True,
                "n_markets": 32,
                "coverage": 0.86,
                "since": "2026-09-26",
            },
            {
                "entrant": "crowd",
                "n": 82,
                "brier": 0.195,
                "ece": None,
                "alpha": 0.0,
                "alpha_ci": None,
                "significant": None,
                "n_markets": 30,
                "coverage": 1.0,
                "since": "2026-09-26",
            },
            {
                "entrant": "coin",
                "n": 42,
                "brier": 0.25,
                "ece": 0.25,
                "alpha": 0.055,
                "alpha_ci": [0.02, 0.09],
                "significant": False,
                "n_markets": 30,
                "coverage": 1.0,
                "since": "2026-09-26",
            },
        ],
        "open": [
            {
                "slug": "fed-cut-october",
                "question": "Will the Fed cut rates in October?",
                "end_date": "2026-10-01T00:00:00Z",
                "market_prob": 0.62,
                "url": "https://polymarket.com/event/fed-cut-october",
                "forecasts": {
                    "jev": {
                        "prob": 0.71,
                        "rationale": "Recent FOMC minutes lean dovish.",
                        "ts": "2026-09-26T05:58:11Z",
                    },
                    "gpt-oss": {"prob": 0.55, "rationale": None},
                    "coin": {"prob": 0.5, "rationale": ""},
                    "crowd": {"prob": 0.62, "rationale": "n/a"},
                },
            },
            {
                "slug": "shutdown-over",
                "question": None,
                "end_date": None,
                "market_prob": None,
                "url": "http://insecure.example/market",
                "forecasts": {
                    "mystery-model": {"prob": 0.2, "rationale": "Edge."},
                },
            },
        ],
        "duels": [
            {
                "slug": "fed-cut-october",
                "question": "Will the Fed cut rates in October?",
                "url": "https://polymarket.com/event/fed-cut-october",
                "market_prob": 0.30,
                "entrant": "jev",
                "prob": 0.72,
                "gap": 0.42,
                "rationale": "Doves are underpriced.",
                "status": "open",
                "outcome": None,
            },
            {
                "slug": "ai-bench",
                "question": "Will a model beat the bench?",
                "url": None,
                "market_prob": 0.6,
                "entrant": "gpt-oss",
                "prob": 0.2,
                "gap": 0.4,
                "rationale": None,
                "status": "won",
                "outcome": 0,
            },
            {
                "slug": "rain-nyc",
                "question": None,
                "url": "https://polymarket.com/event/rain-nyc",
                "market_prob": 0.1,
                "entrant": "jev",
                "prob": 0.45,
                "gap": 0.35,
                "rationale": "Jev opencode/jev-1.13-free",
                "status": "lost",
                "outcome": 0,
            },
            {
                "slug": "launch-window",
                "question": "Launch this window?",
                "url": "https://polymarket.com/event/launch-window",
                "market_prob": 0.5,
                "entrant": "coin",
                "prob": 0.8,
                "gap": 0.3,
                "rationale": "Uninformative 0.5 prior.",
                "status": "tie",
                "outcome": 1,
            },
        ],
        "hall_of_wrong": [
            {
                "slug": "rain-nyc",
                "question": "Will it rain in NYC on Friday?",
                "url": "https://polymarket.com/event/rain-nyc",
                "entrant": "jev",
                "prob": 0.91,
                "outcome": 0,
                "market_prob": 0.55,
                "rationale": "Models showed a dry front.",
                "resolved_at": "2026-09-24T00:00:00Z",
            },
            {
                "slug": "ai-bench",
                "question": "Will a model beat the bench?",
                "url": None,
                "entrant": "gpt-oss",
                "prob": 0.12,
                "outcome": 1,
                "market_prob": 0.6,
                "rationale": None,
                "resolved_at": None,
            },
        ],
        "stats": {
            "forecasts": 180,
            "resolved": 42,
            "markets": 30,
            "unresolvable": 2,
            "entrants": 6,
            "since": "2026-09-26",
        },
    }


class TestDocument:
    def test_single_document_shell(self) -> None:
        html = render_site(sample_board())
        assert html.startswith("<!doctype html>")
        assert html.rstrip().endswith("</html>")
        assert html.count("<html") == 1
        assert html.count("<body") == 1
        assert html.count("</html>") == 1
        assert '<html lang="en">' in html
        assert '<meta charset="utf-8">' in html

    def test_head_metadata(self) -> None:
        html = render_site(sample_board())
        assert "<title>Forecast Arena (Unofficial)</title>" in html
        assert 'property="og:title" content="Forecast Arena (Unofficial)"' in html
        assert 'name="description" content="' in html
        assert 'property="og:description"' in html
        assert 'content="width=device-width, initial-scale=1"' in html
        assert 'name="color-scheme" content="light dark"' in html
        assert 'rel="icon" href="data:image/svg+xml,' in html

    def test_csp_and_no_javascript(self) -> None:
        html = render_site(sample_board())
        assert 'http-equiv="Content-Security-Policy"' in html
        assert "default-src 'none'" in html
        assert "style-src 'unsafe-inline'" in html
        assert "img-src data:" in html
        assert "script-src 'none'" in html
        assert "<script" not in html
        assert "onclick" not in html

    def test_no_external_assets(self) -> None:
        html = render_site(sample_board())
        for tag in ("<img", "@import", "font-face", "<iframe"):
            assert tag not in html
        # the only link tag is the inline data: favicon
        assert html.count("<link") == 1
        assert '<link rel="icon" href="data:' in html

    def test_wide_content_column(self) -> None:
        html = render_site(sample_board())
        assert "max-width: 1220px" in html

    def test_compact_section_spacing(self) -> None:
        html = render_site(sample_board())
        assert "padding: 26px" in _css_rule(html, ".hero")
        assert "margin-top: 34px" in _css_rule(html, "section")

    def test_muted_text_holds_aa_contrast_in_light_theme(self) -> None:
        html = render_site(sample_board())
        root = _css_rule(html, ":root")
        tokens = dict(re.findall(r"(--[\w-]+): (#[0-9a-f]{6})", root))
        for surface in ("--bg", "--bg2", "--card", "--gap-bg"):
            assert _contrast(tokens["--muted"], tokens[surface]) >= 4.5


class TestTopBar:
    def test_wordmark_nav_and_pill(self) -> None:
        html = render_site(sample_board())
        assert '<header class="topbar"' in html
        assert '<a class="wordmark" href="#top">Forecast Arena</a>' in html
        for anchor in ("#leaderboard", "#markets", "#method"):
            assert f'href="{anchor}"' in html
        assert (
            'href="https://github.com/agent-next/polymarket-paper-trader"'
            in html
        )
        # disclaimer pill in the top bar and again in the bottom bar
        assert html.count(DISCLAIMER) == 2
        assert html.index(DISCLAIMER) < html.index("<h1")

    def test_sticky_bar_css(self) -> None:
        html = render_site(sample_board())
        assert "position: sticky" in html


class TestHero:
    def test_eyebrow_headline_sub(self) -> None:
        html = render_site(sample_board())
        assert "Same questions. Different minds. Real outcomes." in html
        assert '<h1>Can <span class="ai">AI</span> beat the crowd?</h1>' in html
        assert "forecast the same real-world events before they resolve" in html

    def test_stat_strip(self) -> None:
        html = render_site(sample_board())
        for label in (
            "forecasts",
            "markets",
            "entrants",
            "updated daily",
        ):
            assert f'<span class="stat-label">{label}</span>' in html
        assert '<span class="stat-num">180</span>' in html
        assert '<span class="stat-num">30</span>' in html
        assert '<span class="stat-num">6</span>' in html
        assert '<span class="stat-num">06:17 UTC</span>' in html

    def test_resolved_count_replaces_first_resolution(self) -> None:
        html = render_site(sample_board())
        assert '<span class="stat-label">resolved</span>' in html
        assert '<span class="stat-num">42</span>' in html
        assert "first resolution" not in html

    def test_first_resolution_is_earliest_open_end(self) -> None:
        board = sample_board()
        board["stats"]["resolved"] = 0
        html = render_site(board)
        assert '<span class="stat-label">first resolution</span>' in html
        assert '<span class="stat-num">Oct 1</span>' in html

    def test_first_resolution_none_when_no_dates(self) -> None:
        html = render_site({"stats": {"resolved": 0}, "open": []})
        assert '<span class="stat-num">—</span>' in html

    def test_missing_stats_em_dash(self) -> None:
        html = render_site({"stats": {}})
        # forecasts, markets, entrants, first resolution
        assert html.count('<span class="stat-num">—</span>') == 4

    def test_hero_note(self) -> None:
        html = render_site(sample_board())
        assert "tracking since 2026-09-26" in html
        assert (
            'Updated <time datetime="2026-09-26T05:58:11Z">'
            "Sep 26, 05:58 UTC</time>" in html
        )

    def test_hero_note_absent_without_stamps(self) -> None:
        html = render_site({})
        assert 'class="hero-note"' not in html

    def test_stats_form_one_bordered_strip(self) -> None:
        html = render_site(sample_board())
        stats = _css_rule(html, ".stats")
        assert "border: 1px solid var(--border)" in stats
        assert "gap: 1px" in stats  # the border color shows as dividers
        # the cells themselves carry no card borders of their own
        assert "border" not in _css_rule(html, ".stat")
        # five cells; on narrow screens the strip wraps to two columns
        # inside the same container and the last cell fills its row
        block = html.split("@media (max-width: 700px)")[1].split("@media")[0]
        assert "repeat(2, 1fr)" in block
        assert "grid-column: 1 / -1" in block

    def test_hero_art_hidden_below_900px(self) -> None:
        html = render_site(sample_board())
        assert '<div class="hero-art" aria-hidden="true">' in html
        block = html.split("@media (max-width: 900px)")[1]
        assert ".hero-art { display: none; }" in block.split("@media")[0]

    def test_stamp_falls_back_to_escaped_raw_text(self) -> None:
        html = render_site({"last_run": "not-a-date<"})
        assert "Updated not-a-date&lt;" in html
        assert "<time" not in html

    def test_stamp_falls_back_when_utc_conversion_overflows(self) -> None:
        # parses fine, but astimezone(UTC) lands before year 1
        html = render_site({"generated_at": "0001-01-01T00:00:00+14:00"})
        assert "Generated 0001-01-01T00:00:00+14:00" in html
        assert "<time" not in html

    def test_unconvertible_end_date_skipped_in_first_resolution(self) -> None:
        board = {
            "stats": {"resolved": 0},
            "open": [
                {"slug": "a", "end_date": "0001-01-01T00:00:00+14:00"},
                {"slug": "b", "end_date": "2026-10-05T00:00:00Z"},
            ],
        }
        html = render_site(board)
        assert '<span class="stat-num">Oct 5</span>' in html

    def test_no_vertical_side_text(self) -> None:
        html = render_site(sample_board())
        assert "Brighter answers" not in html
        assert "writing-mode" not in html


def _luminance(hex_color: str) -> float:
    """WCAG relative luminance of a ``#rrggbb`` color."""
    channels = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [
        c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
        for c in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(a: str, b: str) -> float:
    """WCAG contrast ratio between two ``#rrggbb`` colors."""
    la, lb = _luminance(a), _luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def _css_rule(html: str, selector: str) -> str:
    """The declaration block of a top-level CSS rule."""
    match = re.search(rf"{re.escape(selector)} \{{([^}}]*)\}}", html)
    assert match, f"no rule for {selector}"
    return match.group(1)


class TestEntrantColors:
    def test_palette_rules_emitted_in_board_order(self) -> None:
        html = render_site(sample_board())
        # jev is entrant 0 -> first palette pair; crowd -> neutral gray
        assert ".e0{--ec:#1d4ed8}" in html
        assert ".e1{--ec:#7c3aed}" in html
        assert ".e2{--ec:#64748b}" in html  # crowd: neutral gray
        assert ".e3{--ec:#15803d}" in html  # coin flip: green
        dark = html.split("prefers-color-scheme: dark){", 1)[1]
        assert ".e0{--ec:#6ea8fe}" in dark
        assert ".e2{--ec:#9aa7b4}" in dark
        assert ".e3{--ec:#4ade80}" in dark

    def test_baseline_ids_pin_their_palette_colors(self) -> None:
        board = {
            "entrants": [
                {"id": "m", "label": "M", "kind": "ai"},
                {"id": "crowd", "kind": "baseline"},
                {"id": "coin", "kind": "baseline"},
                {"id": "favorite", "kind": "baseline"},
            ]
        }
        html = render_site(board)
        assert ".e0{--ec:#1d4ed8}" in html  # ai: palette by board order
        assert ".e1{--ec:#64748b}" in html  # crowd gray
        assert ".e2{--ec:#15803d}" in html  # coin flip green
        assert ".e3{--ec:#c2410c}" in html  # favorite orange
        dark = html.split("prefers-color-scheme: dark){", 1)[1]
        assert ".e2{--ec:#4ade80}" in dark
        assert ".e3{--ec:#fb923c}" in dark

    def test_entrant_colors_hold_aa_contrast_on_chips(self) -> None:
        # chip text pulls the accent toward the theme foreground so the
        # washed-out 14% tint background keeps >= 4.5:1 contrast
        html = render_site(sample_board())
        chip = _css_rule(html, ".chip")
        assert "color-mix(in srgb, var(--ec, var(--muted)) 82%, var(--fg))" in chip

    def test_palette_wraps_after_eight_entrants(self) -> None:
        board = {
            "entrants": [{"id": f"e{i}", "label": f"E{i}"} for i in range(9)],
            "leaderboard": [{"entrant": "e8", "alpha": 0.0,
                             "alpha_ci": None, "significant": True}],
        }
        html = render_site(board)
        assert ".e8{--ec:#1d4ed8}" in html  # wraps to palette[0]

    def test_color_class_consistent_across_sections(self) -> None:
        html = render_site(sample_board())
        # jev (e0): leaderboard dot, duel dot/legend, market column header
        assert '<i class="kdot e0"></i>Jev 1.13 (free)' in html
        assert 'class="pdot e0" style="left:72.00%"' in html
        assert '<th class="pct e0"><i class="kdot"></i>Jev 1.13</th>' in html


class TestLeaderboard:
    def test_columns_and_rows(self) -> None:
        html = render_site(sample_board())
        for header in (
            "#",
            "Entrant",
            "Type",
            "Alpha vs crowd (95% CI)",
            "Brier",
            "ECE",
            "Markets",
            "Coverage",
            "Since",
        ):
            assert f">{header}</th>" in html
        assert "Jev 1.13 (free)" in html
        assert '<span class="chip e0">AI</span>' in html
        assert '<span class="chip e2">BASELINE</span>' in html
        assert '<tr class="baseline">' in html
        assert ">0.182</td>" in html
        assert ">0.071</td>" in html
        assert ">90%</td>" in html
        assert ">32</td>" in html
        assert ">2026-09-26</td>" in html
        # rank column numbers the rows
        assert '<td class="num">1</td>' in html
        assert '<td class="num">4</td>' in html

    def test_alpha_whisker_positions(self) -> None:
        html = render_site(sample_board())
        # fixed domain [-0.15, +0.15]: pos(v) = (v + 0.15) / 0.30 * 100
        # jev: ci [-0.031, +0.006] -> 39.67%..52.00%, alpha -0.012 -> 46.00%
        assert 'title="95% CI [-0.031, +0.006]"' in html
        assert 'style="left:50.00%"' in html  # zero line
        assert 'style="left:39.67%;width:12.33%"' in html
        assert 'class="ci-dot e0" style="left:46.00%"' in html
        assert "-0.012" in html

    def test_significance_states(self) -> None:
        html = render_site(sample_board())
        assert "not significant" in html
        # significant: true renders the CI whisker with no marker
        assert 'title="95% CI [-0.090, -0.010]"' in html
        # crowd reference row: alpha shown, no CI, no marker
        assert "+0.000" in html

    def test_ci_withheld_below_event_floor(self) -> None:
        board = {
            "entrants": [{"id": "x", "label": "X", "kind": "ai"}],
            "leaderboard": [
                {"entrant": "x", "alpha": -0.2, "alpha_ci": [-0.25, -0.15],
                 "significant": None}
            ],
        }
        html = render_site(board)
        assert "too few events" in html
        assert 'class="ci-track"' not in html

    def test_ci_shown_once_significance_assessed(self) -> None:
        board = {
            "entrants": [{"id": "x", "label": "X", "kind": "ai"}],
            "leaderboard": [
                {"entrant": "x", "alpha": -0.2, "alpha_ci": [-0.25, -0.15],
                 "significant": False}
            ],
        }
        html = render_site(board)
        assert 'class="ci-track"' in html
        assert "not significant" in html

    def test_entrant_web_access_and_cutoff(self) -> None:
        html = render_site(sample_board())
        assert "no web access · cutoff 2025-09" in html
        assert "web access · cutoff 2024-06" in html

    def test_entrant_model_in_meta(self) -> None:
        html = render_site(sample_board())
        assert "opencode/jev-1.13-free · no web access" in html
        assert "openai/gpt-oss-120b · web access · cutoff 2024-06" in html

    def test_auxiliary_columns_hidden_on_narrow(self) -> None:
        html = render_site(sample_board())
        assert "@media (max-width: 600px)" in html
        assert ".hide-sm" in html
        assert '<th class="num hide-sm">ECE</th>' in html
        assert '<th class="hide-sm">Since</th>' in html
        assert '<td class="num hide-sm">' in html
        assert '<td class="hide-sm">' in html
        assert '<th class="num">Coverage</th>' in html
        assert '<th>Since</th>' not in html

    def test_mobile_stacked_cards(self) -> None:
        html = render_site(sample_board())
        # the media query swaps tables for stacked cards
        assert "@media (max-width: 640px)" in html
        assert ".table-wrap { display: none; }" in html
        assert ".lcards { display: block; }" in html
        section = html.split('id="leaderboard"')[1].split("</section>")[0]
        cards = section.split('<div class="lcards">')[1]
        assert cards.count('<article class="lcard">') == 4
        assert "Alpha vs crowd" in cards
        assert '<span class="chip e0">AI</span>' in cards
        assert "Brier <b>0.182</b>" in cards
        assert "Coverage <b>90%</b>" in cards
        assert "Since <b>2026-09-26</b>" in cards
        # the alpha whisker markup carries over to the card
        assert 'class="ci-dot e0"' in cards

    def test_mobile_ghost_cards_when_no_resolved(self) -> None:
        board = sample_board()
        board["leaderboard"] = []
        html = render_site(board)
        section = html.split('id="leaderboard"')[1].split("</section>")[0]
        cards = section.split('<div class="lcards">')[1]
        assert cards.count('<article class="lcard">') == 4
        assert "Alpha vs crowd" in cards
        assert "Brier <b>—</b>" in cards
        assert "Scores appear when the first markets resolve" in cards

    def test_ghost_rows_when_no_resolved(self) -> None:
        board = sample_board()
        board["leaderboard"] = []
        html = render_site(board)
        section = html.split('id="leaderboard"')[1].split("</section>")[0]
        # every entrant listed once, with em dashes for the metrics
        assert section.count('<tr class="ghost">') == 4
        for label in (
            "Jev 1.13 (free)",
            "GPT-OSS 120B",
            "Crowd (market price)",
            "Coin flip (0.5)",
        ):
            assert label in section
        assert section.count("<td") > 4 * 8
        assert "Scores appear when the first markets resolve — Oct 1" in html
        assert (
            "180 forecasts across 30 markets are live."
            in html
        )

    def test_ghost_row_without_kind_gets_dash_chip(self) -> None:
        board = {
            "entrants": [{"id": "x", "label": "X"}],
            "leaderboard": [],
        }
        html = render_site(board)
        row = html.split('<tr class="ghost">')[1].split("</tr>")[0]
        assert ">—</td>" in row

    def test_empty_panel_without_date_or_counts(self) -> None:
        board = {
            "entrants": [{"id": "x", "label": "X"}],
            "leaderboard": [],
        }
        html = render_site(board)
        assert "Scores appear when the first markets resolve" in html
        assert "Results and scores publish after the first resolution." in html
        assert "forecasts across" not in html

    def test_empty_state_no_entrants(self) -> None:
        html = render_site({"leaderboard": []})
        assert "First results after markets resolve." in html


class TestOpenMarkets:
    def test_table_layout(self) -> None:
        html = render_site(sample_board())
        assert '<section id="markets">' in html
        for header in ("Question", "Closes", "Crowd", "Market view"):
            assert f">{header}" in html
        # per-entrant columns in board order, minus the crowd, plus unknowns
        assert '<th class="pct e0"><i class="kdot"></i>Jev 1.13</th>' in html
        assert (
            '<th class="pct e1"><i class="kdot"></i>GPT-OSS 120B</th>' in html
        )
        assert '<th class="pct e3"><i class="kdot"></i>Coin flip</th>' in html
        assert "<i class=\"kdot\"></i>mystery-model" in html
        # the crowd column header carries the crowd's neutral class
        assert '<th class="pct e2"><i class="kdot"></i>Crowd</th>' in html

    def test_values_and_dot_strip(self) -> None:
        html = render_site(sample_board())
        assert (
            '<a href="https://polymarket.com/event/fed-cut-october">'
            "Will the Fed cut rates in October?</a>" in html
        )
        assert "<td>Oct 1</td>" in html
        assert 'class="pct e0">71%</td>' in html
        assert 'class="pct e2">62%</td>' in html
        assert 'class="pdot e0" style="left:71.00%"' in html
        assert 'class="pdot e2" style="left:62.00%"' in html
        # unknown entrant gets a dot with the neutral fallback color
        assert 'class="pdot" style="left:20.00%"' in html
        # missing forecasts render an em dash cell and no dot
        market2 = html.split("shutdown-over")[1].split("</tr>")[0]
        assert "—" in market2

    def test_fallbacks(self) -> None:
        html = render_site(sample_board())
        # question None -> slug link text; url http -> no anchor
        assert "shutdown-over" in html
        assert 'href="http://insecure.example' not in html
        assert "<td>—</td>" in html  # missing end_date
        assert 'class="pct e2">—</td>' in html  # missing market_prob

    def test_untitled_market(self) -> None:
        html = render_site({"open": [{"forecasts": {}}]})
        assert "Untitled market" in html

    def test_bad_end_date_string(self) -> None:
        board = {"open": [{"slug": "s", "end_date": "not a date"}]}
        html = render_site(board)
        assert "<td>—</td>" in html
        # and it is skipped by the first-resolution scan
        board2 = {"stats": {"resolved": 0}, "open": board["open"]}
        assert '<span class="stat-num">—</span>' in render_site(board2)

    def test_naive_end_date_counts_for_first_resolution(self) -> None:
        board = {
            "stats": {"resolved": 0},
            "open": [{"slug": "s", "end_date": "2026-09-30"}],
        }
        html = render_site(board)
        assert '<span class="stat-num">Sep 30</span>' in html

    def test_parenthetical_only_label_falls_back(self) -> None:
        board = {
            "open": [
                {
                    "slug": "s",
                    "forecasts": {"w": {"prob": 0.5}},
                }
            ],
            "entrants": [{"id": "w", "label": "(paren) X"}],
        }
        html = render_site(board)
        assert "(paren) X" in html  # label kept when stripping leaves ""

    def test_prob_clamped_on_dots_not_text(self) -> None:
        board = {
            "open": [
                {
                    "slug": "s",
                    "question": "Q",
                    "market_prob": 1.5,
                    "forecasts": {"e": {"prob": -0.2}},
                }
            ],
            "entrants": [{"id": "e", "label": "E"}],
        }
        html = render_site(board)
        assert "left:100.00%" in html
        assert "left:0.00%" in html
        assert ">150%</td>" in html
        assert ">-20%</td>" in html

    def test_market_view_dots_at_least_10px(self) -> None:
        html = render_site(sample_board())
        mini = _css_rule(html, ".mini .pdot")
        for dim in ("height", "width"):
            px = float(re.search(rf"{dim}: ([\d.]+)px", mini).group(1))
            assert px >= 10

    def test_empty_state(self) -> None:
        html = render_site({"open": []})
        assert "No open forecasts yet" in html

    def test_mobile_stacked_cards(self) -> None:
        html = render_site(sample_board())
        section = html.split('id="markets"')[1].split("</section>")[0]
        cards = section.split('<div class="lcards">')[1]
        assert cards.count('<article class="lcard">') == 2
        card = cards.split('<article class="lcard">')[1]
        # question, closes, crowd %, entrant chips, dot strip
        assert "Will the Fed cut rates in October?" in card
        assert "Closes Oct 1" in card
        assert '<span class="chip e2">Crowd 62%</span>' in card
        assert '<span class="chip e0">Jev 1.13 71%</span>' in card
        assert '<span class="chip e1">GPT-OSS 120B 55%</span>' in card
        assert '<span class="chip e3">Coin flip 50%</span>' in card
        track = card.split('class="track mkc-track"')[1]
        assert 'class="pdot e0" style="left:71.00%"' in track
        assert 'class="pdot e2" style="left:62.00%"' in track
        # the second card falls back to the slug and an unknown entrant chip
        card2 = cards.split('<article class="lcard">')[2]
        assert "shutdown-over" in card2
        assert "Closes —" in card2
        assert '<span class="chip">mystery-model 20%</span>' in card2
        assert '<span class="chip e2">Crowd —</span>' in card2


class TestDuels:
    def test_cards(self) -> None:
        html = render_site(sample_board())
        assert "<h2>Biggest disagreements</h2>" in html
        # the sample board's four duel groups are capped at three cards
        # (the tie card has the smallest gap and drops off)
        assert html.count('<article class="duel">') == 3
        assert (
            '<a href="https://polymarket.com/event/fed-cut-october">'
            "Will the Fed cut rates in October?</a>" in html
        )
        # question falls back to slug, url None -> plain text
        assert "rain-nyc" in html
        for status in ("open", "won", "lost"):
            assert f"status-{status}" in html
            assert f">{status}</span>" in html

    def test_track_dots_and_legend(self) -> None:
        html = render_site(sample_board())
        assert 'class="pdot e0" style="left:72.00%"' in html
        assert 'class="pdot e2" style="left:30.00%"' in html
        assert "Jev 1.13 72%" in html
        assert "Crowd 30%" in html
        for tick in ("0%", "25%", "50%", "75%", "100%"):
            assert f"<span>{tick}</span>" in html

    def test_gap_is_signed_ai_minus_crowd(self) -> None:
        html = render_site(sample_board())
        # badge is labelled with the headline entrant
        assert ">Jev 1.13 +42 pts vs crowd</span>" in html  # 0.72 - 0.30
        assert ">GPT-OSS 120B -40 pts vs crowd</span>" in html  # 0.20 - 0.60
        assert ">Jev 1.13 +35 pts vs crowd</span>" in html  # 0.45 - 0.10

    def test_one_card_per_market_groups_entrants(self) -> None:
        board = sample_board()
        board["duels"][3] = {  # same market as row 0, second entrant
            "slug": "fed-cut-october",
            "question": "Will the Fed cut rates in October?",
            "url": "https://polymarket.com/event/fed-cut-october",
            "market_prob": 0.30,
            "entrant": "gpt-oss",
            "prob": 0.55,
            "gap": 0.25,
            "rationale": "Rates stay put.",
            "status": "open",
            "outcome": None,
        }
        html = render_site(board)
        assert html.count('<article class="duel">') == 3
        card = html.split("Will the Fed cut rates in October?")[1].split(
            "</article>"
        )[0]
        # one track carrying both AI dots plus the crowd dot
        track = card.split('class="track"')[1].split("</div>")[0]
        assert track.count("pdot") == 3
        assert 'class="pdot e0" style="left:72.00%"' in track
        assert 'class="pdot e1" style="left:55.00%"' in track
        assert 'class="pdot e2" style="left:30.00%"' in track
        # the legend lists every AI on the card plus the crowd
        assert "Jev 1.13 72%" in card
        assert "GPT-OSS 120B 55%" in card
        assert "Crowd 30%" in card
        assert "2 entrants vs crowd" in card
        # the headline gap is the largest |AI - crowd| on the market
        assert ">Jev 1.13 +42 pts vs crowd</span>" in card
        assert "GPT-OSS 120B +25 pts" not in card
        # headline entrant's non-empty rationale wins
        assert "“Doves are underpriced.”" in card
        assert "Rates stay put." not in card

    def test_rationale_falls_back_to_next_ai(self) -> None:
        board = {
            "duels": [
                {
                    "slug": "s",
                    "question": "Q?",
                    "market_prob": 0.5,
                    "entrant": "a",
                    "prob": 0.9,
                    "gap": 0.4,
                    "rationale": None,
                    "status": "open",
                },
                {
                    "slug": "s",
                    "entrant": "b",
                    "prob": 0.2,
                    "gap": 0.3,
                    "rationale": "The next best reason.",
                },
            ],
            "entrants": [
                {"id": "a", "label": "A", "kind": "ai"},
                {"id": "b", "label": "B", "kind": "ai"},
            ],
        }
        html = render_site(board)
        assert "“The next best reason.”" in html
        assert "<cite>— B</cite>" in html

    def test_three_cards_max_ordered_by_headline_gap(self) -> None:
        board = {
            "duels": [
                {
                    "slug": f"m{i}",
                    "question": f"Q{i}?",
                    "market_prob": 0.5,
                    "entrant": "a",
                    "prob": 0.5 + i * 0.05,
                    "gap": i * 0.05,
                }
                for i in range(8)
            ],
            "entrants": [{"id": "a", "label": "A", "kind": "ai"}],
        }
        html = render_site(board)
        assert html.count('<article class="duel">') == 3
        # largest gaps first: m7 (0.35), m6 (0.30), m5 (0.25); m0-m4 cut
        section = html.split('id="duels"')[1]
        assert section.index("Q7?") < section.index("Q6?")
        assert section.index("Q6?") < section.index("Q5?")
        assert "Q4?" not in section and "Q0?" not in section

    def test_duplicate_entrant_rows_deduped_per_card(self) -> None:
        board = {
            "duels": [
                {
                    "slug": "s",
                    "question": "Q?",
                    "market_prob": 0.5,
                    "entrant": "a",
                    "prob": 0.9,
                    "gap": 0.4,
                },
                {
                    "slug": "s",
                    "entrant": "a",
                    "prob": 0.1,
                    "gap": 0.4,
                },
            ],
            "entrants": [{"id": "a", "label": "A", "kind": "ai"}],
        }
        html = render_site(board)
        assert html.count('<article class="duel">') == 1
        card = html.split('<article class="duel">')[1]
        # one dot per entrant, not per duel row
        assert card.count('class="pdot e0"') == 1

    def test_gap_badge_neutral_open_colored_resolved(self) -> None:
        # one status per board so all four fit under the 3-card cap
        for status, cls in (
            ("open", "gap-open"),   # neutral while open
            ("won", "gap-won"),
            ("lost", "gap-lost"),
            ("tie", "gap-open"),    # tie resolves to the neutral badge
        ):
            board = {
                "duels": [
                    {"slug": "s", "question": "Q?", "entrant": "a",
                     "prob": 0.9, "market_prob": 0.5, "status": status},
                ],
                "entrants": [{"id": "a", "label": "A", "kind": "ai"}],
            }
            html = render_site(board)
            assert f'class="gap {cls}"' in html
            assert f"status-{status}" in html
        html = render_site(sample_board())
        assert ".gap-won {" in html and "var(--good)" in html
        assert ".gap-lost {" in html and "var(--bad)" in html

    def test_gap_falls_back_to_board_gap_field(self) -> None:
        board = {
            "duels": [
                {"slug": "x", "question": "Big?", "entrant": "a",
                 "gap": 0.9},
                {"slug": "y", "question": "Small?", "entrant": "a",
                 "gap": 0.1},
            ],
            "entrants": [{"id": "a", "label": "A", "kind": "ai"}],
        }
        html = render_site(board)
        section = html.split('id="duels"')[1]
        assert section.index("Big?") < section.index("Small?")

    def test_grouping_falls_back_to_question_then_row(self) -> None:
        board = {
            "duels": [
                {"slug": None, "question": "Same Q?", "entrant": "a",
                 "prob": 0.9, "market_prob": 0.5},
                {"slug": "", "question": "Same Q?", "entrant": "b",
                 "prob": 0.8, "market_prob": 0.5},
                {"question": None, "slug": [], "entrant": "a"},
            ],
            "entrants": [
                {"id": "a", "label": "A", "kind": "ai"},
                {"id": "b", "label": "B", "kind": "ai"},
            ],
        }
        html = render_site(board)
        # two cards: the question-grouped pair + the keyless row
        assert html.count('<article class="duel">') == 2
        assert "A 90%" in html and "B 80%" in html
        # the keyless card still renders its title fallback
        assert '<h3 class="duel-q">?</h3>' in html

    def test_slug_and_question_keys_never_collide(self) -> None:
        board = {
            "duels": [
                {"slug": "same", "question": "First market", "entrant": "a",
                 "prob": 0.8, "market_prob": 0.5},
                {"question": "same", "entrant": "b",
                 "prob": 0.2, "market_prob": 0.5},
            ],
            "entrants": [
                {"id": "a", "label": "A", "kind": "ai"},
                {"id": "b", "label": "B", "kind": "ai"},
            ],
        }
        html = render_site(board)
        # a slug and a question fallback with equal text stay two markets
        assert html.count('<article class="duel">') == 2
        assert "First market" in html
        card = html.split('<h3 class="duel-q">same</h3>')[1].split(
            "</article>"
        )[0]
        assert "B 20%" in card

    def test_out_of_range_prob_clamped_before_gap_and_sort(self) -> None:
        board = {
            "duels": [
                {"slug": "wild", "question": "Wild?", "entrant": "a",
                 "prob": 2, "market_prob": 0.5},
                {"slug": "valid", "question": "Valid?", "entrant": "a",
                 "prob": 1.0, "market_prob": 0.35},
                {"slug": "neg", "question": "Neg?", "entrant": "a",
                 "prob": -0.4, "market_prob": 0.5},
            ],
            "entrants": [{"id": "a", "label": "A", "kind": "ai"}],
        }
        html = render_site(board)
        # the headline gap uses the clamped probability, like the dot does
        assert "+150 pts" not in html
        assert "-90 pts" not in html
        assert "200%" not in html and "-40%" not in html
        assert ">A +50 pts vs crowd</span>" in html
        assert ">A +65 pts vs crowd</span>" in html
        assert ">A -50 pts vs crowd</span>" in html
        # the clamped gap, not the raw one, decides the card order
        section = html.split('id="duels"')[1]
        assert section.index("Valid?") < section.index("Wild?")
        assert section.index("Wild?") < section.index("Neg?")
        wild = section.split("Wild?")[1].split("</article>")[0]
        assert 'class="pdot e0" style="left:100.00%"' in wild
        assert "A 100%" in wild
        assert "A 200%" not in wild

    def test_gap_em_dash_when_uncomputable(self) -> None:
        board = {
            "duels": [{"slug": "s", "entrant": "x"}],
            "entrants": [{"id": "x", "label": "X"}],
        }
        html = render_site(board)
        assert "— pts vs crowd" in html

    def test_rationale_quote_with_attribution(self) -> None:
        html = render_site(sample_board())
        assert "“Doves are underpriced.”" in html
        assert "<cite>— Jev 1.13</cite>" in html

    def test_boilerplate_rationales_hidden(self) -> None:
        html = render_site(sample_board())
        # model-id restatement is never quoted
        assert "“Jev opencode/jev-1.13-free”" not in html
        # baseline boilerplate (coin is kind=baseline) is never quoted
        assert "Uninformative 0.5 prior." not in html
        # None rationale -> no quote block on that card at all
        card2 = html.split("Will a model beat the bench?")[1].split(
            "</article>"
        )[0]
        assert "<blockquote" not in card2

    def test_resolved_outcome_shown(self) -> None:
        html = render_site(sample_board())
        assert "· resolved No" in html
        board = {
            "duels": [
                {"slug": "s", "question": "Q?", "entrant": "a",
                 "prob": 0.9, "market_prob": 0.5, "status": "won",
                 "outcome": 1},
            ],
            "entrants": [{"id": "a", "label": "A", "kind": "ai"}],
        }
        assert "· resolved Yes" in render_site(board)

    def test_status_defaults_to_open(self) -> None:
        board = {
            "duels": [{"slug": "s", "question": "Q?", "entrant": "jev"}],
            "entrants": [],
        }
        html = render_site(board)
        assert "status-open" in html
        assert "jev vs crowd" in html  # unknown entrant: raw id as label

    def test_empty_state(self) -> None:
        html = render_site({"duels": []})
        assert "No large AI-vs-crowd disagreements yet." in html


class TestHallOfWrong:
    def test_items(self) -> None:
        html = render_site(sample_board())
        assert "Hall of Wrong" in html
        assert "Will it rain in NYC on Friday?" in html
        assert '<i class="kdot e0"></i><strong>Jev 1.13 (free)</strong>' in html
        assert "91%" in html
        assert "resolved" in html
        assert "crowd was at 55%" in html
        assert "2026-09-24" in html
        assert "“Models showed a dry front.”" in html

    def test_empty_state(self) -> None:
        html = render_site({"hall_of_wrong": []})
        assert "No confident misses yet." in html


class TestMethodology:
    def test_required_claims(self) -> None:
        html = render_site(sample_board())
        assert "Methodology" in html
        assert "Full details" in html
        assert "No market price in prompts" in html
        assert "single-shot call" in html
        assert "append-only by convention" in html
        assert "not a tamper-proof guarantee" in html
        assert "arena-data" in html
        assert "(p − o)²" in html
        assert "1,000 resamples" in html
        assert "clustered by market event" in html
        assert "no multiplicity correction" in html
        assert "≥30" in html
        assert "too few events" in html
        assert "10 equal-width bins" in html
        assert "≥100 resolved forecasts" in html
        assert "at most 2 markets per event" in html
        assert "top 20 by volume" in html
        assert "[0.03, 0.97]" in html
        assert "liquidity ≥ $10k" in html
        assert "not forecasting ability in general" in html
        assert "contamination" in html
        assert "coin" in html and "50%" in html
        assert "favorite" in html and "0.9" in html


class TestFooter:
    def test_columns_and_links(self) -> None:
        html = render_site(sample_board())
        assert 'id="method"' in html
        assert "Add your agent" in html
        assert "Get started →" in html
        assert "Open source" in html
        assert "View on GitHub →" in html
        assert (
            'href="https://github.com/agent-next/polymarket-paper-trader"'
            in html
        )
        # Get started points at the arena docs anchor, not a bare link
        assert (
            '<a class="btn" '
            'href="https://github.com/agent-next/polymarket-paper-trader'
            '/blob/main/benchmark/README.md#forecast-arena">'
            "Get started →</a>" in html
        )
        # every in-page anchor target exists
        for anchor in ("leaderboard", "markets", "method"):
            assert f'href="#{anchor}"' in html
            assert f'id="{anchor}"' in html
        assert "A more open future for forecasting." in html

    def test_stamps(self) -> None:
        html = render_site(sample_board())
        assert (
            'Generated <time datetime="2026-09-26T06:00:00Z">'
            "Sep 26, 06:00 UTC</time>" in html
        )
        assert (
            'Updated <time datetime="2026-09-26T05:58:11Z">'
            "Sep 26, 05:58 UTC</time>" in html
        )
        assert "tracking since 2026-09-26" in html
        assert "2 unresolvable" in html
        assert "data branch" in html

    def test_no_timestamps_still_renders(self) -> None:
        html = render_site({})
        assert "polymarket-paper-trader" in html
        assert "Generated <time" not in html
        assert "Updated <time" not in html
        assert "data branch" in html


class TestSecurity:
    def test_escapes_all_board_strings(self) -> None:
        payload = '<script>alert("x")</script>'
        board = sample_board()
        board["open"][0]["question"] = payload
        board["duels"][0]["question"] = payload
        board["duels"][0]["rationale"] = payload
        board["hall_of_wrong"][0]["question"] = payload
        board["hall_of_wrong"][0]["rationale"] = payload
        board["entrants"][0]["label"] = payload
        board["entrants"][0]["model"] = payload
        board["entrants"][0]["cutoff"] = payload
        html = render_site(board)
        assert payload not in html
        assert "<script" not in html
        assert "&lt;script&gt;" in html

    def test_https_url_required(self) -> None:
        board = {
            "open": [
                {
                    "slug": "s1",
                    "question": "Q1",
                    "url": "javascript:alert(1)",
                    "forecasts": {},
                },
                {
                    "slug": "s2",
                    "question": "Q2",
                    "url": "//evil.example/x",
                    "forecasts": {},
                },
                {
                    "slug": "s3",
                    "question": "Q3",
                    "url": "https://polymarket.com/event/ok",
                    "forecasts": {},
                },
            ]
        }
        html = render_site(board)
        assert 'href="javascript:' not in html
        assert 'href="//evil' not in html
        assert ">Q1</a>" not in html and ">Q1</td>" in html
        assert 'href="https://polymarket.com/event/ok">Q3</a>' in html


class TestEdgeCases:
    def test_alpha_ci_malformed(self) -> None:
        board = {
            "leaderboard": [
                {
                    "entrant": "a",
                    "alpha": 0.01,
                    "alpha_ci": [0.0],
                    "significant": False,
                },
                {
                    "entrant": "b",
                    "alpha": None,
                    "alpha_ci": [None, 0.2],
                    "significant": False,
                },
                {
                    "entrant": "c",
                    "alpha": 0.02,
                    "alpha_ci": "bad",
                    "significant": True,
                },
                {
                    "entrant": None,
                    "alpha": None,
                    "alpha_ci": None,
                    "significant": True,
                },
                {
                    "entrant": "e",
                    "alpha": None,
                    "alpha_ci": [0.0, 0.1],
                    "significant": True,
                },
            ],
            "entrants": [],
        }
        html = render_site(board)
        assert "not significant" in html  # row a (CI dropped as malformed)
        assert ">?</td>" in html  # row with entrant None
        assert "+0.010" in html
        assert "+0.020" in html

    def test_malformed_entrants_and_dates(self) -> None:
        board = {
            "entrants": [
                "not-a-dict",
                {"no_id": True},
                {"id": "x", "label": "X", "kind": ""},
            ],
            "leaderboard": [
                {"entrant": "x", "alpha": 0.0, "alpha_ci": None,
                 "significant": False},
            ],
            "open": [{"slug": "s", "end_date": 12345, "forecasts": {}}],
        }
        html = render_site(board)
        assert ">X<" in html
        assert "<td>—</td>" in html  # end_date 12345 -> em dash
        section = html.split('id="leaderboard"')[1].split("</section>")[0]
        assert "chip" not in section.split("<tbody>")[1].split("</tr>")[0]

    def test_no_keys_at_all(self) -> None:
        html = render_site({})
        assert html.startswith("<!doctype html>")
        for text in (
            "First results after markets resolve.",
            "No open forecasts yet",
            "No large AI-vs-crowd disagreements yet.",
            "No confident misses yet.",
        ):
            assert text in html


class TestNonNumericValues:
    def test_bad_numbers_render_em_dash(self) -> None:
        board = {
            "leaderboard": [
                {
                    "entrant": "a",
                    "alpha": "bad",
                    "alpha_ci": ["x", 0.1],
                    "brier": "oops",
                    "ece": float("nan"),
                    "coverage": "wide",
                    "significant": False,
                }
            ],
            "open": [
                {
                    "slug": "s",
                    "question": "Q",
                    "market_prob": "high",
                    "forecasts": {"a": {"prob": "bad"}},
                }
            ],
            "duels": [
                {"slug": "d", "prob": "x", "market_prob": "y", "gap": "z"}
            ],
            "hall_of_wrong": [
                {"slug": "h", "prob": "hi", "market_prob": "lo"}
            ],
        }
        html = render_site(board)
        assert ">bad</td>" not in html and ">oops</td>" not in html
        # the malformed CI pair drops entirely (0.1 must not render alone)
        assert "+0.100" not in html
        assert ">—</td>" in html  # alpha, brier, ece, market_prob cells
        assert "crowd was at —" in html
        assert "not significant" in html
        assert "— pts vs crowd" in html

    def test_numeric_strings_coerce(self) -> None:
        board = {
            "leaderboard": [
                {
                    "entrant": "a",
                    "alpha": "-0.01",
                    "alpha_ci": ["-0.05", "0.01"],
                    "brier": "0.2",
                    "significant": True,
                }
            ],
            "open": [
                {
                    "slug": "s",
                    "market_prob": "0.3",
                    "forecasts": {"a": {"prob": "0.9"}},
                }
            ],
        }
        html = render_site(board)
        assert "-0.010" in html
        assert 'title="95% CI [-0.050, +0.010]"' in html
        assert ">0.200</td>" in html
        assert "left:90.00%" in html
        assert ">90%</td>" in html
        assert "left:30.00%" in html

    def test_non_finite_counts_render_em_dash(self) -> None:
        html = render_site(
            {"leaderboard": [{"entrant": "a", "n": float("nan"),
                              "n_markets": float("inf")}],
             "stats": {"forecasts": float("nan")}}
        )
        assert ">nan<" not in html and ">inf<" not in html

    def test_non_finite_renders_em_dash(self) -> None:
        board = {
            "leaderboard": [
                {
                    "entrant": "a",
                    "alpha": float("inf"),
                    "brier": float("nan"),
                    "significant": True,
                }
            ],
            "open": [
                {
                    "slug": "s",
                    "market_prob": float("inf"),
                    "forecasts": {"a": {"prob": float("nan")}},
                }
            ],
        }
        html = render_site(board)
        assert ">nan<" not in html and ">inf<" not in html
        assert ">—</td>" in html


class TestMalformedItems:
    def test_non_dict_items_skipped(self) -> None:
        board = {
            "entrants": [{"id": "a", "label": "A"}],
            "leaderboard": [
                "x",
                42,
                {"entrant": "a", "brier": 0.1, "significant": True},
            ],
            "open": [None, {"slug": "s", "question": "Q?", "forecasts": {}}],
            "duels": [{"skip"}, {"slug": "d", "question": "D?"}],
            "hall_of_wrong": [[1, 2], {"slug": "h", "question": "H?"}],
        }
        html = render_site(board)
        assert ">0.100</td>" in html  # the one valid leaderboard row
        assert ">A<" in html
        for question in ("Q?", "D?", "H?"):
            assert question in html

    def test_non_list_sections_fall_back_to_empty(self) -> None:
        board = {
            "entrants": "oops",
            "leaderboard": {"a": {}},
            "open": 42,
            "duels": "nope",
            "hall_of_wrong": {"x": 1},
            "stats": [1, 2, 3],
        }
        html = render_site(board)
        for text in (
            "First results after markets resolve.",
            "No open forecasts yet",
            "No large AI-vs-crowd disagreements yet.",
            "No confident misses yet.",
        ):
            assert text in html
        # a non-dict stats block renders as all em dashes
        assert html.count('<span class="stat-num">—</span>') == 4

    def test_forecasts_and_entries_not_dicts(self) -> None:
        board = {
            "open": [
                {"slug": "s1", "market_prob": 0.5, "forecasts": "oops"},
                {
                    "slug": "s2",
                    "forecasts": {"e": "bad", "f": {"prob": 0.4}},
                },
            ],
            "entrants": [{"id": "e"}, {"id": "f", "label": "F"}],
        }
        html = render_site(board)
        # a non-dict forecasts value still renders the crowd column
        assert ">50%</td>" in html
        # a non-dict forecast renders as an em dash cell, no dot
        assert '<td class="pct e0">—</td>' in html
        assert '<td class="pct e1">40%</td>' in html
        assert 'class="pdot e1" style="left:40.00%"' in html

    def test_unhashable_entrant_id(self) -> None:
        board = {
            "leaderboard": [
                {"entrant": ["x"], "brier": 0.2, "significant": True}
            ],
            "duels": [{"slug": "d", "entrant": ["y"]}],
        }
        html = render_site(board)
        # unhashable ids skip the lookup and render escaped
        assert "x&#x27;" in html and "y&#x27;" in html
        assert ">0.200</td>" in html


class TestRationaleFilter:
    def test_baseline_id_crowd_hidden(self) -> None:
        board = {
            "duels": [
                {
                    "slug": "s",
                    "question": "Q?",
                    "entrant": "crowd",
                    "prob": 0.9,
                    "market_prob": 0.5,
                    "rationale": "Market YES price at forecast time.",
                }
            ],
            "entrants": [
                {"id": "crowd", "label": "Crowd", "kind": "baseline"}
            ],
        }
        html = render_site(board)
        assert "Market YES price" not in html
        assert "<blockquote" not in html

    def test_label_restatement_hidden(self) -> None:
        board = {
            "duels": [
                {
                    "slug": "s",
                    "entrant": "m",
                    "prob": 0.9,
                    "market_prob": 0.5,
                    "rationale": "M model m-model-1",
                }
            ],
            "entrants": [
                {"id": "m", "label": "M", "kind": "ai", "model": "m-model-1"}
            ],
        }
        html = render_site(board)
        assert "<blockquote" not in html

    def test_real_rationale_shows_for_ai(self) -> None:
        board = {
            "duels": [
                {
                    "slug": "s",
                    "entrant": "m",
                    "prob": 0.9,
                    "market_prob": 0.5,
                    "rationale": "Real reasoning about the event.",
                }
            ],
            "entrants": [
                {"id": "m", "label": "M", "kind": "ai", "model": "m-1"}
            ],
        }
        html = render_site(board)
        assert "“Real reasoning about the event.”" in html

    def test_non_string_and_blank_rationales_hidden(self) -> None:
        board = {
            "duels": [
                {
                    "slug": "s1",
                    "entrant": "m",
                    "prob": 0.9,
                    "market_prob": 0.5,
                    "rationale": 42,
                },
                {
                    "slug": "s2",
                    "entrant": "m",
                    "prob": 0.9,
                    "market_prob": 0.5,
                    "rationale": "   ",
                },
            ],
            "entrants": [{"id": "m", "label": "M", "kind": "ai"}],
        }
        html = render_site(board)
        assert "<blockquote" not in html


def _one_row_board(kind: str, significant: bool | None) -> dict:
    return {
        "entrants": [{"id": "x", "label": "X", "kind": kind, "model": "m"}],
        "leaderboard": [
            {"entrant": "x", "n": 3, "brier": 0.2, "ece": None, "alpha": -0.2,
             "alpha_ci": [-0.25, -0.15], "significant": significant,
             "n_markets": 2, "coverage": 1.0, "since": "2026-09-26"}
        ],
    }


def test_ci_withheld_below_event_floor() -> None:
    assert 'class="ci-track"' not in render_site(_one_row_board("ai", None))
    assert 'class="ci-track"' in render_site(_one_row_board("ai", False))


def test_kind_badge_uppercased_before_escaping() -> None:
    html = render_site(_one_row_board('a"i', None))
    assert "A&quot;I" in html
    assert "&QUOT;" not in html


def test_fmt_ci_rejects_malformed_shapes() -> None:
    assert _fmt_ci("bad") is None
    assert _fmt_ci([0.0]) is None
    assert _fmt_ci([None, 0.2]) is None
    assert _fmt_ci(["x", 0.1]) is None
    assert _fmt_ci([-0.05, 0.01]) == "[-0.050, +0.010]"
