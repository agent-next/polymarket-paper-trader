"""Tests for pm_benchmark.arena_site."""
from __future__ import annotations

from pm_benchmark.arena_site import render_site


def sample_board() -> dict:
    """Full board fixture per team/CONTRACT-board.md + the v1.1 addendum.

    Exercises every entrant kind, every significance state, every duel
    status, and the new board keys (``last_run``, ``n_markets``,
    ``coverage``, ``since``, ``unresolvable``, ``web_access``/``cutoff``).
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
                "label": "Coin flip",
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
                "rationale": None,
                "status": "lost",
                "outcome": 0,
            },
            {
                "slug": "launch-window",
                "question": "Launch this window?",
                "url": "https://polymarket.com/event/launch-window",
                "market_prob": 0.5,
                "entrant": "gpt-oss",
                "prob": 0.8,
                "gap": 0.3,
                "rationale": None,
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

    def test_header_disclaimer_above_fold(self) -> None:
        html = render_site(sample_board())
        disclaimer = (
            "Unofficial · not affiliated with Polymarket · "
            "paper forecasts only, no real money"
        )
        assert disclaimer in html
        assert "<h1>Forecast Arena</h1>" in html
        assert "Can AI forecast real-world events better than the crowd?" in html
        assert html.index(disclaimer) < html.index("<h1")


class TestStatsStrip:
    def test_values_and_labels(self) -> None:
        html = render_site(sample_board())
        for label in (
            "forecasts",
            "resolved",
            "markets",
            "unresolvable",
            "entrants",
            "tracking since",
        ):
            assert f'<span class="stat-label">{label}</span>' in html
        assert '<span class="stat-num">180</span>' in html
        assert '<span class="stat-num">2</span>' in html
        assert '<span class="stat-num">2026-09-26</span>' in html

    def test_missing_stats_em_dash(self) -> None:
        html = render_site({"stats": {}})
        assert html.count('<span class="stat-num">—</span>') == 6


class TestLeaderboard:
    def test_columns_and_rows(self) -> None:
        html = render_site(sample_board())
        for header in (
            "Entrant",
            "Kind",
            "Alpha vs crowd (95% CI)",
            "Brier",
            "ECE",
            "N",
            "Events",
            "Coverage",
            "Since",
        ):
            assert f"<th" in html and header in html
        # raw board keys are humanized before they reach the header row
        assert "n_markets" not in html
        assert ">N</th>" in html and ">Events</th>" in html
        assert "Jev 1.13 (free)" in html
        assert "badge-ai" in html
        assert "AI" in html
        assert "badge-baseline" in html
        assert "BASELINE" in html
        assert '<tr class="baseline">' in html
        assert ">0.182</td>" in html
        assert ">0.071</td>" in html
        assert ">90%</td>" in html
        assert ">32</td>" in html
        assert ">2026-09-26</td>" in html

    def test_significance_states(self) -> None:
        html = render_site(sample_board())
        assert "-0.012" in html
        assert "[-0.031, +0.006]" in html
        assert "not significant" in html
        # significant: true renders the CI with no marker
        assert "[-0.090, -0.010]" in html
        # crowd reference row: alpha shown, no CI, no marker
        assert "+0.000" in html

    def test_too_few_markets(self) -> None:
        board = sample_board()
        board["leaderboard"].append(
            {
                "entrant": "newcomer",
                "n": 5,
                "brier": 0.21,
                "ece": None,
                "alpha": -0.04,
                "alpha_ci": [-0.1, 0.02],
                "significant": None,
                "n_markets": 4,
                "coverage": 0.2,
                "since": "2026-10-01",
            }
        )
        html = render_site(board)
        assert "too few events" in html
        # newcomer is not in entrants: label falls back to the id, no badge
        assert "newcomer" in html

    def test_entrant_web_access_and_cutoff(self) -> None:
        html = render_site(sample_board())
        assert "no web access · cutoff 2025-09" in html
        assert "web access · cutoff 2024-06" in html

    def test_entrant_model_in_meta(self) -> None:
        html = render_site(sample_board())
        # model id is part of the audit trail under the label
        assert "opencode/jev-1.13-free · no web access" in html
        assert "openai/gpt-oss-120b · web access · cutoff 2024-06" in html

    def test_auxiliary_columns_hidden_on_narrow(self) -> None:
        html = render_site(sample_board())
        assert "@media (max-width: 600px)" in html
        assert ".hide-sm" in html
        # Brier/ECE/N/Since hide below 600px in both header and cells
        assert '<th class="num hide-sm">Brier</th>' in html
        assert '<th class="num hide-sm">ECE</th>' in html
        assert '<th class="num hide-sm">N</th>' in html
        assert '<th class="hide-sm">Since</th>' in html
        assert '<td class="num hide-sm">' in html
        assert '<td class="hide-sm">' in html
        # Entrant/Kind/Alpha/Markets/Coverage stay visible
        assert '<th class="num">Events</th>' in html
        assert '<th class="num">Coverage</th>' in html
        assert '<th>Since</th>' not in html

    def test_empty_state(self) -> None:
        html = render_site({"leaderboard": []})
        assert "First results after markets resolve." in html


class TestOpenForecasts:
    def test_market_card(self) -> None:
        html = render_site(sample_board())
        assert (
            '<a href="https://polymarket.com/event/fed-cut-october">'
            "Will the Fed cut rates in October?</a>" in html
        )
        assert "closes 2026-10-01" in html
        assert "width:71.00%" in html
        assert "width:62.00%" in html
        assert ">71%</span>" in html
        assert "Recent FOMC minutes lean dovish." in html
        assert "<details><summary>" in html
        # the crowd key inside forecasts is not duplicated as an entrant bar
        assert html.count("width:62.00%") == 1

    def test_fallbacks(self) -> None:
        html = render_site(sample_board())
        # question None -> slug link text; url http -> no anchor
        assert "shutdown-over" in html
        assert 'href="http://insecure.example' not in html
        assert "closes —" in html
        assert "width:0.00%" in html
        # forecast for an id missing from entrants renders by raw id
        assert "mystery-model" in html
        # rationale present for an unknown entrant still renders
        assert "Edge." in html

    def test_untitled_market(self) -> None:
        html = render_site({"open": [{"forecasts": {}}]})
        assert "Untitled market" in html

    def test_forecast_ts_inside_details(self) -> None:
        html = render_site(sample_board())
        summary = "<details><summary>Jev 1.13 (free) — rationale</summary>"
        start = html.index(summary)
        end = html.index("</details>", start)
        assert "forecast at 2026-09-26T05:58:11Z UTC" in html[start:end]
        assert "Recent FOMC minutes lean dovish." in html[start:end]

    def test_forecast_ts_only_still_renders(self) -> None:
        board = {
            "open": [
                {
                    "slug": "s",
                    "forecasts": {
                        "e": {"prob": 0.5, "ts": "2026-09-26T01:02:03Z"}
                    },
                }
            ],
            "entrants": [{"id": "e", "label": "E"}],
        }
        html = render_site(board)
        assert "forecast at 2026-09-26T01:02:03Z UTC" in html

    def test_forecast_ts_escaped(self) -> None:
        board = {
            "open": [
                {
                    "slug": "s",
                    "forecasts": {"e": {"prob": 0.5, "ts": "<b>x</b>"}},
                }
            ]
        }
        html = render_site(board)
        assert "forecast at &lt;b&gt;x&lt;/b&gt; UTC" in html
        assert "<b>x</b>" not in html

    def test_empty_state(self) -> None:
        html = render_site({"open": []})
        assert "No open forecasts yet" in html


class TestDuels:
    def test_statuses(self) -> None:
        html = render_site(sample_board())
        for status in ("open", "won", "lost", "tie"):
            assert f'status-{status}' in html
            assert f">{status}</span>" in html
        assert ">42%</td>" in html
        assert ">30%</td>" in html
        assert "Doves are underpriced." in html
        assert ">Yes</td>" in html
        assert ">No</td>" in html
        # question falls back to slug, url None -> plain text
        assert "rain-nyc" in html

    def test_status_defaults_to_open(self) -> None:
        board = {
            "duels": [
                {"slug": "s", "question": "Q?", "entrant": "jev"}
            ],
            "entrants": [],
        }
        html = render_site(board)
        assert "status-open" in html

    def test_empty_state(self) -> None:
        html = render_site({"duels": []})
        assert "No large AI-vs-crowd disagreements yet." in html


class TestHallOfWrong:
    def test_items(self) -> None:
        html = render_site(sample_board())
        assert "Hall of Wrong" in html
        assert "Will it rain in NYC on Friday?" in html
        assert "91%" in html
        assert "resolved" in html
        assert "crowd was at 55%" in html
        assert "2026-09-24" in html
        assert "Models showed a dry front." in html

    def test_empty_state(self) -> None:
        html = render_site({"hall_of_wrong": []})
        assert "No confident misses yet." in html


class TestMethodology:
    def test_required_claims(self) -> None:
        html = render_site(sample_board())
        assert "Methodology" in html
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
    def test_links_and_stamps(self) -> None:
        html = render_site(sample_board())
        assert (
            'href="https://github.com/agent-next/polymarket-paper-trader"'
            in html
        )
        assert "Add your agent — coming soon" in html
        assert "Board generated 2026-09-26T06:00:00Z" in html
        assert "last pipeline run 2026-09-26T05:58:11Z" in html
        assert "arena-data" in html

    def test_no_timestamps_still_renders(self) -> None:
        html = render_site({})
        assert "polymarket-paper-trader on GitHub" in html
        assert "Board generated" not in html
        assert "last pipeline run" not in html
        assert "data branch" in html


class TestSecurity:
    def test_escapes_all_board_strings(self) -> None:
        payload = '<script>alert("x")</script>'
        board = sample_board()
        board["open"][0]["question"] = payload
        board["open"][0]["forecasts"]["jev"]["rationale"] = payload
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
        assert ">Q1</a>" not in html and ">Q1</h3>" in html
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
            ],
            "entrants": [],
        }
        html = render_site(board)
        assert "not significant" in html  # row a (CI dropped as malformed)
        assert ">?</td>" in html  # row with entrant None
        # row b: alpha em dash, still "not significant"
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
        assert ">X</span>" not in html  # label renders inside td, not span
        assert ">X<" in html
        assert "closes —" in html
        # empty kind -> no badge cell content
        assert "badge-" not in html.split("leaderboard")[1].split("<tbody>")[1].split("</tr>")[0]

    def test_prob_clamped(self) -> None:
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
        assert "width:100.00%" in html
        assert "width:0.00%" in html
        # out-of-range values still render as their % formatting
        assert ">150%</span>" in html
        assert ">-20%</span>" in html

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
        assert ">—</td>" in html  # alpha, brier, ece, duel cells
        assert "crowd was at —" in html
        # both prob bars fall back to the em dash with a 0-width bar
        open_html = html.split('<section id="open">')[1].split("</section>")[0]
        assert open_html.count(">—</span>") == 2
        assert "width:0.00%" in open_html
        assert "not significant" in html

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
        assert "[-0.050, +0.010]" in html
        assert ">0.200</td>" in html
        assert "width:90.00%" in html
        assert ">90%</span>" in html
        assert "width:30.00%" in html

    def test_non_finite_counts_render_em_dash(self) -> None:
        html = render_site(
            {"leaderboard": [{"entrant": "a", "n": float("nan"), "n_markets": float("inf")}],
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
        open_html = html.split('<section id="open">')[1].split("</section>")[0]
        assert open_html.count(">—</span>") == 2
        assert open_html.count("width:0.00%") == 2
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
        assert html.count('<span class="stat-num">—</span>') == 6

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
        # the crowd bar survives a non-dict forecasts value
        assert "width:50.00%" in html
        # a non-dict forecast still renders its row with an em dash prob
        assert '<span class="who">e</span>' in html
        assert ">40%</span>" in html

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
    assert 'class="ci"' not in render_site(_one_row_board("ai", None))
    assert 'class="ci"' in render_site(_one_row_board("ai", False))


def test_kind_badge_uppercased_before_escaping() -> None:
    html = render_site(_one_row_board('a"i', None))
    assert "A&quot;I" in html
    assert "&QUOT;" not in html
