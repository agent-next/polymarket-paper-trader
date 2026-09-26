"""Tests for pm_benchmark.arena — the Forecast Arena core."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml
from click.testing import CliRunner

import pm_benchmark.arena as arena
from pm_benchmark.arena import (
    ArenaError,
    Entrant,
    _baseline_prob,
    _bootstrap_alpha_ci,
    _default_config,
    _fetch_market_prob,
    _parse_ts,
    _yes_prob,
    append_forecasts,
    build_board,
    build_forecast_prompt,
    forecast_market,
    load_entrants,
    load_forecasts,
    load_resolutions,
    run_build,
    run_predict,
    run_resolve,
    select_markets,
)
from pm_benchmark.cli import main
from pm_benchmark.market_info import MarketInfo, Resolution
from pm_benchmark.providers import LLMError

NOW = datetime(2026, 9, 26, 12, 0, 0, tzinfo=timezone.utc)
END_IN_5D = "2026-10-01T12:00:00Z"


def _info(
    slug: str = "m1",
    *,
    price: float = 0.5,
    volume: float = 1_000_000.0,
    liquidity: float = 50_000.0,
    end_date: str = END_IN_5D,
    active: bool = True,
    closed: bool = False,
    outcomes: tuple[str, ...] = ("Yes", "No"),
    event_id: str | None = None,
) -> MarketInfo:
    return MarketInfo(
        slug=slug,
        question=f"Will {slug} happen?",
        description=f"Description for {slug}.",
        outcomes=list(outcomes),
        outcome_prices=[price, round(1 - price, 6)],
        volume=volume,
        liquidity=liquidity,
        end_date=end_date,
        active=active,
        closed=closed,
        event_id=event_id,
    )


def _entrant(
    id: str = "gpt",
    kind: str = "ai",
    model: str | None = "openai/gpt-4.1",
    **kwargs,
) -> Entrant:
    return Entrant(id=id, label=id.upper(), kind=kind, model=model, **kwargs)


def _write_config(path: Path, entrants: list[dict]) -> Path:
    path.write_text(yaml.safe_dump({"entrants": entrants}))
    return path


def _forecast_row(slug: str, entrant: str, prob, *, ts: str, **kw) -> dict:
    row = {
        "ts": ts,
        "slug": slug,
        "event_id": slug,
        "question": f"Will {slug} happen?",
        "end_date": END_IN_5D,
        "closed": False,
        "market_prob": 0.5,
        "price_ts": ts,
        "url": f"https://polymarket.com/event/{slug}",
        "entrant": entrant,
        "model": "m" if entrant != "crowd" else None,
        "prob": prob,
        "rationale": "r",
        "status": "ok",
    }
    row.update(kw)
    return row


def _write_forecasts(data_dir: Path, date: str, rows: list[dict]) -> Path:
    fdir = data_dir / "forecasts"
    fdir.mkdir(parents=True, exist_ok=True)
    path = fdir / f"{date}.jsonl"
    with path.open("a") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    return path


def _mock_price(monkeypatch, value: float = 0.5):
    """Mock the fresh YES-price fetch used before each model call."""
    mock = MagicMock(return_value=value)
    monkeypatch.setattr(arena, "_fetch_market_prob", mock)
    return mock


class TestLoadEntrants:
    def test_loads_bundled_config(self):
        entrants = load_entrants(_default_config())
        ids = [e.id for e in entrants]
        assert ids == ["jev", "space-bunny", "crowd", "coin", "favorite"]
        by_id = {e.id: e for e in entrants}
        assert by_id["jev"].model == "opencode/jev-1.13-free"
        assert by_id["jev"].web_access is None  # undocumented upstream
        assert by_id["jev"].cutoff is None
        bunny = by_id["space-bunny"]
        assert bunny.model == "openai/space-bunny-free"
        assert bunny.api_base == "https://opencode.ai/zen/v1"
        assert bunny.api_key_env == "ZEN_API_KEY"
        assert by_id["crowd"].kind == "baseline"
        assert by_id["crowd"].model is None

    def test_missing_file(self, tmp_path):
        with pytest.raises(ArenaError, match="not found"):
            load_entrants(tmp_path / "nope.yaml")

    def test_non_dict_yaml(self, tmp_path):
        path = tmp_path / "bad.yaml"
        path.write_text("- just\n- a\n- list\n")
        with pytest.raises(ArenaError, match="Invalid entrants config"):
            load_entrants(path)

    def test_missing_entrants_key(self, tmp_path):
        path = tmp_path / "c.yaml"
        path.write_text("foo: bar\n")
        with pytest.raises(ArenaError, match="Invalid entrants config"):
            load_entrants(path)

    def test_empty_entrants(self, tmp_path):
        path = _write_config(tmp_path / "c.yaml", [])
        with pytest.raises(ArenaError, match="No entrants"):
            load_entrants(path)

    def test_entry_not_a_dict(self, tmp_path):
        path = tmp_path / "c.yaml"
        path.write_text("entrants:\n  - 42\n")
        with pytest.raises(ArenaError, match="Invalid entrant entry"):
            load_entrants(path)

    def test_duplicate_id(self, tmp_path):
        path = _write_config(
            tmp_path / "c.yaml",
            [
                {"id": "a", "kind": "ai", "model": "m"},
                {"id": "a", "kind": "ai", "model": "m"},
            ],
        )
        with pytest.raises(ArenaError, match="Duplicate entrant id 'a'"):
            load_entrants(path)

    def test_label_defaults_to_id(self, tmp_path):
        path = _write_config(
            tmp_path / "c.yaml", [{"id": "x", "kind": "ai", "model": "m"}]
        )
        assert load_entrants(path)[0].label == "x"

    def test_web_access_undeclared_is_none(self, tmp_path):
        path = _write_config(
            tmp_path / "c.yaml", [{"id": "x", "kind": "ai", "model": "m"}]
        )
        e = load_entrants(path)[0]
        assert e.web_access is None
        assert e.cutoff is None


class TestEntrantValidation:
    def test_empty_id(self):
        with pytest.raises(ArenaError, match="id cannot be empty"):
            Entrant(id="", label="L", kind="ai", model="m")

    def test_empty_label(self):
        with pytest.raises(ArenaError, match="label cannot be empty"):
            Entrant(id="x", label="", kind="ai", model="m")

    def test_bad_kind(self):
        with pytest.raises(ArenaError, match="kind must be one of"):
            Entrant(id="x", label="L", kind="robot", model="m")

    def test_ai_requires_model(self):
        with pytest.raises(ArenaError, match="requires a model"):
            Entrant(id="x", label="L", kind="ai")

    def test_unknown_baseline(self):
        with pytest.raises(ArenaError, match="Unknown baseline 'weather'"):
            Entrant(id="weather", label="L", kind="baseline")


class TestSelectMarkets:
    def _run(self, markets, taken=set(), **kw):
        return select_markets(markets, taken, now=NOW, **kw)

    def test_happy_path(self):
        assert [m.slug for m in self._run([_info("a")])] == ["a"]

    def test_closed_excluded(self):
        assert self._run([_info("a", closed=True)]) == []

    def test_inactive_excluded(self):
        assert self._run([_info("a", active=False)]) == []

    def test_empty_slug_excluded(self):
        assert self._run([_info("")]) == []

    def test_taken_slug_excluded(self):
        assert self._run([_info("a")], taken={"a"}) == []

    def test_non_binary_excluded(self):
        m = _info("a", outcomes=("Alice", "Bob"))
        assert self._run([m]) == []

    def test_empty_prices_excluded(self):
        m = _info("a")
        m = MarketInfo(**{**m.__dict__, "outcome_prices": []})
        assert self._run([m]) == []

    def test_price_bounds(self):
        assert self._run([_info("lo", price=0.029)]) == []
        assert self._run([_info("hi", price=0.971)]) == []
        assert len(self._run([_info("edge-lo", price=0.03)])) == 1
        assert len(self._run([_info("edge-hi", price=0.97)])) == 1

    def test_yes_price_mapped_by_label(self):
        """Price filter reads the YES price by label, not list position."""
        # ["No","Yes"] ordering is still a binary Yes/No market (M7), and
        # YES is at index 1: label mapping reads 0.99 -> out of range, while a
        # positional read would see 0.5 and wrongly keep it.
        excluded = MarketInfo(**{
            **_info("b").__dict__,
            "outcomes": ["No", "Yes"],
            "outcome_prices": [0.5, 0.99],
        })
        assert self._run([excluded]) == []
        included = MarketInfo(**{
            **_info("c").__dict__,
            "outcomes": ["No", "Yes"],
            "outcome_prices": [0.5, 0.5],
        })
        assert len(self._run([included])) == 1

    def test_prices_shorter_than_outcomes_excluded(self):
        """YES at index 1 with only one price -> no price -> excluded."""
        m = MarketInfo(**{
            **_info("d").__dict__,
            "outcomes": ["No", "Yes"],
            "outcome_prices": [0.5],
        })
        assert self._run([m]) == []

    def test_yes_prob_falls_back_to_index_zero(self):
        """No 'yes' label -> index 0 fallback (defensive; selection has
        already enforced Yes/No outcomes)."""
        m = MarketInfo(**{
            **_info("e").__dict__,
            "outcomes": ["A", "B"],
            "outcome_prices": [0.6, 0.4],
        })
        assert _yes_prob(m) == 0.6

    def test_liquidity_floor(self):
        assert self._run([_info("a", liquidity=9_999.0)]) == []
        assert len(self._run([_info("b", liquidity=10_000.0)])) == 1

    def test_end_date_window(self):
        too_soon = (NOW + timedelta(hours=12)).strftime("%Y-%m-%dT%H:%M:%SZ")
        too_late = (NOW + timedelta(days=15)).strftime("%Y-%m-%dT%H:%M:%SZ")
        assert self._run([_info("soon", end_date=too_soon)]) == []
        assert self._run([_info("late", end_date=too_late)]) == []
        boundary = (NOW + timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%SZ")
        assert len(self._run([_info("edge", end_date=boundary)])) == 1

    def test_unparseable_end_date_excluded(self):
        assert self._run([_info("a", end_date="not-a-date")]) == []

    def test_top_n_by_volume(self):
        markets = [_info(f"m{i}", volume=float(i)) for i in range(5)]
        got = self._run(markets, top_n=2)
        assert [m.slug for m in got] == ["m4", "m3"]

    def test_max_two_markets_per_event(self):
        """v1.2: at most 2 markets per Gamma event, highest volume kept."""
        markets = [
            _info(f"ev1-m{i}", event_id="ev1", volume=float(100 - i))
            for i in range(10)
        ]
        markets.append(_info("solo", event_id="ev2", volume=50.0))
        got = self._run(markets)
        assert [m.slug for m in got] == ["ev1-m0", "ev1-m1", "solo"]

    def test_event_cap_before_top_n(self):
        """The per-event cap is applied before top_n, so one event with 10
        markets cannot crowd the rest off a top-3 board."""
        markets = [
            _info(f"ev1-m{i}", event_id="ev1", volume=float(100 - i))
            for i in range(10)
        ]
        markets.append(_info("solo", event_id="ev2", volume=1.0))
        got = self._run(markets, top_n=3)
        assert [m.slug for m in got] == ["ev1-m0", "ev1-m1", "solo"]

    def test_no_event_id_keyed_by_slug(self):
        """Markets without an event id cap at the slug — always distinct."""
        markets = [_info(f"m{i}", volume=float(i)) for i in range(5)]
        assert len(self._run(markets, top_n=5)) == 5


class TestListCandidateMarkets:
    def test_pages_until_short_page(self, monkeypatch):
        page1 = [_info(f"m{i}") for i in range(3)]
        page2 = [_info("m3")]
        mock = MagicMock(side_effect=[page1, page2])
        monkeypatch.setattr(arena, "list_markets", mock)
        got = arena._list_candidate_markets(page_size=3, max_pages=5, now=NOW)
        assert [m.slug for m in got] == ["m0", "m1", "m2", "m3"]
        first = mock.call_args_list[0].kwargs
        assert first["end_date_min"] == "2026-09-27T12:00:00Z"
        assert first["end_date_max"] == "2026-10-10T12:00:00Z"
        assert first["liquidity_min"] == arena.MIN_LIQUIDITY
        assert mock.call_count == 2
        assert mock.call_args_list[0].kwargs["offset"] == 0
        assert mock.call_args_list[1].kwargs["offset"] == 3
        assert mock.call_args_list[1].kwargs["limit"] == 3

    def test_stops_at_page_bound(self, monkeypatch):
        """Never fetches more than max_pages even if pages stay full."""
        mock = MagicMock(
            side_effect=lambda **kw: [
                _info(f"p{kw['offset']}a"), _info(f"p{kw['offset']}b"),
            ]
        )
        monkeypatch.setattr(arena, "list_markets", mock)
        got = arena._list_candidate_markets(page_size=2, max_pages=3)
        assert mock.call_count == 3
        assert len(got) == 6
        assert mock.call_args_list[-1].kwargs["offset"] == 4

    def test_dedupes_overlapping_pages(self, monkeypatch):
        """Offset pages can overlap when ordering shifts — dedupe by slug."""
        page1 = [_info("a"), _info("b")]
        page2 = [_info("b"), _info("c")]
        mock = MagicMock(side_effect=[page1, page2, []])
        monkeypatch.setattr(arena, "list_markets", mock)
        got = arena._list_candidate_markets(page_size=2, max_pages=5)
        assert [m.slug for m in got] == ["a", "b", "c"]

    def test_empty_first_page(self, monkeypatch):
        mock = MagicMock(return_value=[])
        monkeypatch.setattr(arena, "list_markets", mock)
        assert arena._list_candidate_markets(page_size=3, max_pages=5) == []
        assert mock.call_count == 1


class TestForecastPrompt:
    def test_contains_market_fields(self):
        m = _info("x", price=0.62)
        prompt = build_forecast_prompt(m)
        assert m.question in prompt
        assert m.description in prompt
        assert m.end_date in prompt

    def test_no_market_price(self):
        m = _info("x", price=0.62, volume=1_234_567.0, liquidity=88_888.0)
        prompt = build_forecast_prompt(m)
        for token in ("0.62", "0.38", "price", "Price", "volume", "liquidity"):
            assert token not in prompt

    def test_no_trading_language(self):
        """The arena prompt must not borrow the trading envelope (M6)."""
        m = _info("x")
        system = arena._FORECAST_SYSTEM_PROMPT
        user = build_forecast_prompt(m)
        for token in (
            "buy", "sell", "position", "amount", "action",
            "confidence", "trade", "budget",
        ):
            assert token not in system.lower()
            assert token not in user.lower()


class TestForecastMarket:
    def test_ai_row(self, monkeypatch):
        raw = json.dumps({"probability": 0.7, "reasoning": "looks likely"})
        mock = MagicMock(return_value=raw)
        monkeypatch.setattr(arena, "query_model", mock)
        row = forecast_market(
            _entrant(), _info("m1", event_id="ev1"), now=NOW,
            price_fetch=lambda m: 0.4,
        )
        assert row == {
            "ts": "2026-09-26T12:00:00Z",
            "slug": "m1",
            "event_id": "ev1",
            "question": "Will m1 happen?",
            "end_date": END_IN_5D,
            "closed": False,
            "market_prob": 0.4,
            "price_ts": "2026-09-26T12:00:00Z",
            "url": "https://polymarket.com/event/m1",
            "entrant": "gpt",
            "model": "openai/gpt-4.1",
            "prob": 0.7,
            "rationale": "looks likely",
            "status": "ok",
        }
        cfg = mock.call_args.args[0]
        assert cfg.model == "openai/gpt-4.1"
        assert cfg.num_retries == arena.LLM_NUM_RETRIES

    def test_ai_entrant_passes_api_base_and_key_env(self, monkeypatch):
        entrant = _entrant(
            api_base="https://models.github.ai/inference",
            api_key_env="GITHUB_TOKEN",
        )
        mock = MagicMock(return_value='{"probability": 0.5, "reasoning": "x"}')
        monkeypatch.setattr(arena, "query_model", mock)
        forecast_market(
            entrant, _info(), now=NOW, timeout=9.0,
            price_fetch=lambda m: 0.5,
        )
        cfg = mock.call_args.args[0]
        assert cfg.api_base == "https://models.github.ai/inference"
        assert cfg.api_key_env == "GITHUB_TOKEN"
        assert mock.call_args.kwargs["timeout"] == 9.0

    def test_price_fetched_before_model_call(self, monkeypatch):
        """market_prob snapshot must predate the model's answer (M8)."""
        calls = []
        monkeypatch.setattr(
            arena, "query_model",
            lambda *a, **k: calls.append("model") or '{"probability": 0.5}',
        )
        forecast_market(
            _entrant(), _info(), now=NOW,
            price_fetch=lambda m: calls.append("price") or 0.6,
        )
        assert calls == ["price", "model"]

    def test_prompt_excludes_price(self, monkeypatch):
        captured = {}

        def fake_query(cfg, prompt, system, **kw):
            captured["prompt"] = prompt
            return '{"probability": 0.5, "reasoning": "x"}'

        monkeypatch.setattr(arena, "query_model", fake_query)
        forecast_market(
            _entrant(), _info("m", price=0.62), now=NOW,
            price_fetch=lambda m: 0.62,
        )
        assert "0.62" not in captured["prompt"]

    def test_rationale_truncated_to_280(self, monkeypatch):
        long_reasoning = "x" * 400
        monkeypatch.setattr(
            arena, "query_model",
            MagicMock(return_value=json.dumps(
                {"probability": 0.5, "reasoning": long_reasoning}
            )),
        )
        row = forecast_market(_entrant(), _info(), now=NOW,
                              price_fetch=lambda m: 0.5)
        assert len(row["rationale"]) == 280

    def test_null_reasoning_becomes_empty(self, monkeypatch):
        monkeypatch.setattr(
            arena, "query_model",
            MagicMock(return_value='{"probability": 0.5, "reasoning": null}'),
        )
        row = forecast_market(_entrant(), _info(), now=NOW,
                              price_fetch=lambda m: 0.5)
        assert row["rationale"] == ""

    def test_parse_failure_retries_once(self, monkeypatch):
        """A malformed answer gets exactly one fixed retry (L1)."""
        mock = MagicMock(side_effect=[
            "not json at all",
            '{"probability": 0.6, "reasoning": "ok"}',
        ])
        monkeypatch.setattr(arena, "query_model", mock)
        row = forecast_market(_entrant(), _info(), now=NOW,
                              price_fetch=lambda m: 0.5)
        assert row["prob"] == 0.6
        assert mock.call_count == 2

    def test_parse_failure_twice_propagates(self, monkeypatch):
        mock = MagicMock(return_value="still not json")
        monkeypatch.setattr(arena, "query_model", mock)
        with pytest.raises(LLMError):
            forecast_market(_entrant(), _info(), now=NOW,
                            price_fetch=lambda m: 0.5)
        assert mock.call_count == 2

    def test_api_error_propagates_immediately(self, monkeypatch):
        """Call-level failures are not retried a second time here."""
        mock = MagicMock(side_effect=LLMError("LLM call failed: 429"))
        monkeypatch.setattr(arena, "query_model", mock)
        with pytest.raises(LLMError):
            forecast_market(_entrant(), _info(), now=NOW,
                            price_fetch=lambda m: 0.5)
        assert mock.call_count == 1

    def test_jev_single_noul_question(self, monkeypatch):
        """Jev is asked one noul question, not the trading envelope (M6)."""
        q_mock = MagicMock(return_value={"probability": {"noul": 0.73}})
        monkeypatch.setattr(arena, "query_jev", q_mock)
        monkeypatch.setattr(
            arena, "query_model",
            MagicMock(side_effect=AssertionError("litellm path used")),
        )
        entrant = _entrant("jev", model="opencode/jev-1.13-free")
        row = forecast_market(entrant, _info(), now=NOW,
                              price_fetch=lambda m: 0.5)
        assert row["prob"] == 0.73
        assert row["rationale"] == ""
        _, kwargs = q_mock.call_args
        assert q_mock.call_args.args[2] == {
            "probability": {
                "type": "noul",
                "instructions": "Will this market resolve YES?",
            }
        }

    def test_jev_bad_noul_retries_once(self, monkeypatch):
        q_mock = MagicMock(side_effect=[
            {"unexpected": {}},
            {"probability": {"noul": 0.4}},
        ])
        monkeypatch.setattr(arena, "query_jev", q_mock)
        entrant = _entrant("jev", model="jev-1.13-free")
        row = forecast_market(entrant, _info(), now=NOW,
                              price_fetch=lambda m: 0.5)
        assert row["prob"] == 0.4
        assert q_mock.call_count == 2

    def test_baselines(self):
        crowd = Entrant(id="crowd", label="C", kind="baseline")
        coin = Entrant(id="coin", label="C", kind="baseline")
        fav = Entrant(id="favorite", label="F", kind="baseline")
        assert forecast_market(crowd, _info(), now=NOW,
                               price_fetch=lambda m: 0.65)["prob"] == 0.65
        assert forecast_market(coin, _info(), now=NOW,
                               price_fetch=lambda m: 0.65)["prob"] == 0.5
        assert forecast_market(fav, _info(), now=NOW,
                               price_fetch=lambda m: 0.65)["prob"] == 0.9
        assert forecast_market(fav, _info(), now=NOW,
                               price_fetch=lambda m: 0.3)["prob"] == 0.1
        assert forecast_market(fav, _info(), now=NOW,
                               price_fetch=lambda m: 0.5)["prob"] == 0.9

    def test_expired_market_rejected(self):
        """Expired markets raise before any price fetch or model call."""
        past = _info("old", end_date="2026-09-20T00:00:00Z")
        fetch = MagicMock(side_effect=AssertionError("price fetched"))
        with pytest.raises(ArenaError, match="already ended"):
            forecast_market(_entrant(), past, now=NOW, price_fetch=fetch)
        fetch.assert_not_called()

    def test_fetch_market_prob_requires_yes_label(self, monkeypatch):
        """A prices payload without a 'Yes' label is an error, not 0.5."""
        monkeypatch.setattr(
            arena, "fetch_prices",
            MagicMock(return_value={"Maybe": 0.5}),
        )
        with pytest.raises(arena.MarketInfoError, match="no YES outcome"):
            _fetch_market_prob(_info("m1"))


class TestBaselineProb:
    def test_rules(self):
        assert _baseline_prob("crowd", 0.42) == 0.42
        assert _baseline_prob("coin", 0.42) == 0.5
        assert _baseline_prob("favorite", 0.42) == 0.1
        assert _baseline_prob("favorite", 0.8) == 0.9


class TestParseTs:
    def test_z_suffix(self):
        assert _parse_ts("2026-10-01T00:00:00Z") == datetime(
            2026, 10, 1, tzinfo=timezone.utc
        )

    def test_offset(self):
        dt = _parse_ts("2026-10-01T02:00:00+02:00")
        assert dt == datetime(2026, 10, 1, tzinfo=timezone.utc)

    def test_naive_assumed_utc(self):
        assert _parse_ts("2026-10-01T00:00:00").tzinfo == timezone.utc

    def test_invalid(self):
        assert _parse_ts("junk") is None
        assert _parse_ts("") is None


class TestForecastFiles:
    def test_load_empty_dir(self, tmp_path):
        assert load_forecasts(tmp_path) == []

    def test_append_and_load_roundtrip(self, tmp_path):
        rows = [
            _forecast_row("a", "gpt", 0.6, ts="2026-09-26T01:00:00Z"),
            _forecast_row("a", "crowd", 0.5, ts="2026-09-26T01:00:00Z"),
        ]
        path = append_forecasts(tmp_path, rows, now=NOW)
        assert path == tmp_path / "forecasts" / "2026-09-26.jsonl"
        assert load_forecasts(tmp_path) == rows

    def test_load_skips_blank_lines(self, tmp_path):
        fdir = tmp_path / "forecasts"
        fdir.mkdir()
        (fdir / "2026-09-26.jsonl").write_text(
            json.dumps(_forecast_row("a", "gpt", 0.6, ts="t")) + "\n\n"
        )
        assert len(load_forecasts(tmp_path)) == 1

    def test_resolutions_roundtrip_and_missing(self, tmp_path):
        assert load_resolutions(tmp_path) == {}
        arena.save_resolutions(
            tmp_path,
            {"s": {"outcome": 1, "resolved_at": "t", "resolution_source": "price"}},
        )
        assert load_resolutions(tmp_path) == {
            "s": {"outcome": 1, "resolved_at": "t", "resolution_source": "price"}
        }

    def test_resolutions_non_dict(self, tmp_path):
        (tmp_path / "resolutions.json").write_text("[1, 2]")
        assert load_resolutions(tmp_path) == {}


    def test_load_skips_corrupt_line(self, tmp_path, capsys):
        path = _write_forecasts(
            tmp_path, "2026-09-20",
            [_forecast_row("a", "gpt", 0.6, ts="2026-09-20T06:17:00Z")],
        )
        with path.open("a") as f:
            f.write('{"ts": "2026-09-20T06:17:00Z", "slug": ')
        rows = load_forecasts(tmp_path)
        assert [r["slug"] for r in rows] == ["a"]
        assert "skipping corrupt line 2026-09-20.jsonl:2" in capsys.readouterr().err

    def test_save_resolutions_leaves_no_temp_file(self, tmp_path):
        arena.save_resolutions(tmp_path, {"m": {"outcome": 1}})
        assert sorted(p.name for p in tmp_path.iterdir()) == ["resolutions.json"]
        assert load_resolutions(tmp_path) == {"m": {"outcome": 1}}


class TestRunPredict:
    def _config(self, tmp_path, extra_ai=None):
        entrants = [
            {"id": "gpt", "kind": "ai", "model": "openai/gpt-4.1"},
            {"id": "crowd", "kind": "baseline"},
        ]
        if extra_ai:
            entrants = extra_ai + entrants
        return _write_config(tmp_path / "arena.yaml", entrants)

    def test_writes_rows_for_entrants_and_markets(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            arena, "list_markets",
            MagicMock(return_value=[_info("m1"), _info("m2")]),
        )
        monkeypatch.setattr(
            arena, "query_model",
            MagicMock(return_value='{"probability": 0.7, "reasoning": "r"}'),
        )
        _mock_price(monkeypatch, 0.4)
        config = self._config(tmp_path)
        summary = run_predict(tmp_path, config, now=NOW)
        assert summary == {
            "date": "2026-09-26",
            "markets": 2,
            "forecasts": 4,
            "skips": 0,
            "file": str(tmp_path / "forecasts" / "2026-09-26.jsonl"),
        }
        rows = load_forecasts(tmp_path)
        assert {(r["entrant"], r["slug"]) for r in rows} == {
            ("gpt", "m1"), ("crowd", "m1"), ("gpt", "m2"), ("crowd", "m2"),
        }
        gpt = next(r for r in rows if r["entrant"] == "gpt")
        assert gpt["prob"] == 0.7
        assert gpt["market_prob"] == 0.4
        assert gpt["event_id"] == "m1"
        crowd = next(r for r in rows if r["entrant"] == "crowd")
        assert crowd["prob"] == crowd["market_prob"] == 0.4

    def test_idempotent_same_day(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            arena, "list_markets", MagicMock(return_value=[_info("m1")])
        )
        monkeypatch.setattr(
            arena, "query_model",
            MagicMock(return_value='{"probability": 0.7, "reasoning": "r"}'),
        )
        _mock_price(monkeypatch, 0.5)
        config = self._config(tmp_path)
        run_predict(tmp_path, config, now=NOW)
        path = tmp_path / "forecasts" / "2026-09-26.jsonl"
        before = path.read_text()
        summary = run_predict(tmp_path, config, now=NOW)
        assert summary["forecasts"] == 0
        assert summary["file"] is None
        assert path.read_text() == before

    def test_done_today_skips_entrant_on_new_markets(self, tmp_path, monkeypatch):
        """A re-run the same day never re-queries an entrant already filed (A3),
        even when the selection picked up a new market."""
        monkeypatch.setattr(
            arena, "query_model",
            MagicMock(return_value='{"probability": 0.7, "reasoning": "r"}'),
        )
        _mock_price(monkeypatch, 0.5)
        config = self._config(tmp_path)
        monkeypatch.setattr(
            arena, "list_markets", MagicMock(return_value=[_info("m1")])
        )
        run_predict(tmp_path, config, now=NOW)
        monkeypatch.setattr(
            arena, "list_markets",
            MagicMock(return_value=[_info("m1"), _info("m2")]),
        )
        summary = run_predict(tmp_path, config, now=NOW)
        assert summary["markets"] == 2
        assert summary["forecasts"] == 0
        assert summary["file"] is None
        slugs = {(r["entrant"], r["slug"]) for r in load_forecasts(tmp_path)}
        assert slugs == {("gpt", "m1"), ("crowd", "m1")}

    def test_all_skip_entrant_reruns_same_day(self, tmp_path, monkeypatch):
        _write_forecasts(
            tmp_path, "2026-09-26",
            [_forecast_row("m1", "gpt", None, ts="2026-09-26T07:00:00Z",
                           status="skip"),
             _forecast_row("m1", "crowd", 0.5, ts="2026-09-26T07:00:00Z")],
        )
        monkeypatch.setattr(
            arena, "list_markets", MagicMock(return_value=[_info("m1")])
        )
        monkeypatch.setattr(
            arena, "query_model",
            MagicMock(return_value='{"probability": 0.7, "reasoning": "r"}'),
        )
        _mock_price(monkeypatch, 0.5)
        summary = run_predict(tmp_path, self._config(tmp_path), now=NOW)
        assert summary["forecasts"] == 1
        new = [r for r in load_forecasts(tmp_path) if r["ts"] == "2026-09-26T12:00:00Z"]
        assert [(r["entrant"], r["prob"]) for r in new] == [("gpt", 0.7)]

    def test_prior_days_slugs_excluded(self, tmp_path, monkeypatch):
        """A slug forecast yesterday is not re-selected today."""
        _write_forecasts(
            tmp_path, "2026-09-25",
            [_forecast_row("m1", "gpt", 0.6, ts="2026-09-25T01:00:00Z")],
        )
        monkeypatch.setattr(
            arena, "list_markets",
            MagicMock(return_value=[_info("m1"), _info("m2")]),
        )
        monkeypatch.setattr(
            arena, "query_model",
            MagicMock(return_value='{"probability": 0.7, "reasoning": "r"}'),
        )
        _mock_price(monkeypatch, 0.5)
        config = self._config(tmp_path)
        summary = run_predict(tmp_path, config, now=NOW)
        assert summary["markets"] == 1
        slugs = {r["slug"] for r in load_forecasts(tmp_path) if r["ts"].startswith("2026-09-26")}
        assert slugs == {"m2"}

    def test_prior_day_skip_row_does_not_consume_market(self, tmp_path, monkeypatch):
        """A market only skipped (API outage) on a prior day is re-selected."""
        _write_forecasts(
            tmp_path, "2026-09-25",
            [_forecast_row("m1", "gpt", None, ts="2026-09-25T01:00:00Z",
                           status="skip")],
        )
        monkeypatch.setattr(
            arena, "list_markets",
            MagicMock(return_value=[_info("m1"), _info("m2")]),
        )
        monkeypatch.setattr(
            arena, "query_model",
            MagicMock(return_value='{"probability": 0.7, "reasoning": "r"}'),
        )
        _mock_price(monkeypatch, 0.5)
        run_predict(tmp_path, self._config(tmp_path), now=NOW)
        gpt_today = {
            r["slug"] for r in load_forecasts(tmp_path)
            if r["ts"].startswith("2026-09-26") and r["entrant"] == "gpt"
        }
        assert gpt_today == {"m1", "m2"}

    def test_resolved_slug_excluded(self, tmp_path, monkeypatch):
        """A market already resolved is never forecast again (L2)."""
        arena.save_resolutions(
            tmp_path, {"m1": {"outcome": 1, "resolved_at": "t"}}
        )
        monkeypatch.setattr(
            arena, "list_markets",
            MagicMock(return_value=[_info("m1"), _info("m2")]),
        )
        _mock_price(monkeypatch, 0.5)
        config = _write_config(
            tmp_path / "arena.yaml", [{"id": "crowd", "kind": "baseline"}]
        )
        summary = run_predict(tmp_path, config, now=NOW)
        assert summary["markets"] == 1
        rows = load_forecasts(tmp_path)
        assert [r["slug"] for r in rows] == ["m2"]

    def test_api_error_records_skip_not_half(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            arena, "list_markets", MagicMock(return_value=[_info("m1")])
        )
        monkeypatch.setattr(
            arena, "query_model",
            MagicMock(side_effect=LLMError("LLM call failed: boom")),
        )
        _mock_price(monkeypatch, 0.5)
        config = self._config(tmp_path)
        summary = run_predict(tmp_path, config, now=NOW)
        assert summary["skips"] == 1
        rows = load_forecasts(tmp_path)
        skip = next(r for r in rows if r["entrant"] == "gpt")
        assert skip["status"] == "skip"
        assert skip["prob"] is None
        assert "boom" in skip["error"]
        crowd = next(r for r in rows if r["entrant"] == "crowd")
        assert crowd["status"] == "ok"

    def test_failure_cap_skips_remaining(self, tmp_path, monkeypatch):
        """After max_entrant_failures the entrant is cut off, no more calls."""
        monkeypatch.setattr(
            arena, "list_markets",
            MagicMock(return_value=[_info(f"m{i}") for i in range(5)]),
        )
        call_count = {"n": 0}

        def boom(*a, **k):
            call_count["n"] += 1
            raise LLMError("LLM call failed: 429")

        monkeypatch.setattr(arena, "query_model", boom)
        _mock_price(monkeypatch, 0.5)
        config = self._config(tmp_path)
        summary = run_predict(
            tmp_path, config, now=NOW, max_entrant_failures=2
        )
        assert call_count["n"] == 2
        rows = [r for r in load_forecasts(tmp_path) if r["entrant"] == "gpt"]
        assert len(rows) == 5
        assert all(r["status"] == "skip" for r in rows)
        capped = [r for r in rows if "failure cap" in r["error"]]
        assert len(capped) == 3
        assert summary["skips"] == 5
        # baselines unaffected
        crowd = [r for r in load_forecasts(tmp_path) if r["entrant"] == "crowd"]
        assert len(crowd) == 5 and all(r["status"] == "ok" for r in crowd)

    def test_error_redacted_and_truncated(self, tmp_path, monkeypatch):
        """Secrets never reach a committed row; errors are truncated (S3)."""
        secret = "ghp_very-secret-token"
        monkeypatch.setenv("GITHUB_TOKEN", secret)
        long_err = f"LLM call failed: token {secret} " + "x" * 500
        monkeypatch.setattr(
            arena, "list_markets", MagicMock(return_value=[_info("m1")])
        )
        monkeypatch.setattr(
            arena, "query_model", MagicMock(side_effect=LLMError(long_err))
        )
        _mock_price(monkeypatch, 0.5)
        config = _write_config(
            tmp_path / "arena.yaml",
            [{"id": "gpt", "kind": "ai", "model": "m",
              "api_key_env": "GITHUB_TOKEN"}],
        )
        run_predict(tmp_path, config, now=NOW)
        raw_text = (tmp_path / "forecasts" / "2026-09-26.jsonl").read_text()
        assert secret not in raw_text
        row = json.loads(raw_text.strip())
        assert "<redacted>" in row["error"]
        assert len(row["error"]) <= arena.ERROR_LIMIT

    def test_short_public_key_not_redacted(self, tmp_path, monkeypatch):
        """Zen's anonymous key "public" is not a secret: ordinary words in
        error text stay intact."""
        monkeypatch.setenv("ZEN_API_KEY", "public")
        monkeypatch.setattr(
            arena, "list_markets", MagicMock(return_value=[_info("m1")])
        )
        monkeypatch.setattr(
            arena, "query_model",
            MagicMock(side_effect=LLMError("model is not publicly listed")),
        )
        _mock_price(monkeypatch, 0.5)
        config = _write_config(
            tmp_path / "arena.yaml",
            [{"id": "gpt", "kind": "ai", "model": "m",
              "api_key_env": "ZEN_API_KEY"}],
        )
        run_predict(tmp_path, config, now=NOW)
        raw_text = (tmp_path / "forecasts" / "2026-09-26.jsonl").read_text()
        assert "publicly listed" in raw_text
        assert "<redacted>" not in raw_text

    def test_jev_key_redacted(self, tmp_path, monkeypatch):
        """Jev reads its key from the environment, not from api_key_env."""
        secret = "jev_very-secret-key"
        monkeypatch.setenv("OPENCODE_API_KEY", secret)
        monkeypatch.setattr(
            arena, "list_markets", MagicMock(return_value=[_info("m1")])
        )
        monkeypatch.setattr(
            arena, "query_model",
            MagicMock(side_effect=LLMError(f"401 bearer {secret}")),
        )
        _mock_price(monkeypatch, 0.5)
        config = _write_config(
            tmp_path / "arena.yaml",
            [{"id": "gpt", "kind": "ai", "model": "m"}],
        )
        run_predict(tmp_path, config, now=NOW)
        raw_text = (tmp_path / "forecasts" / "2026-09-26.jsonl").read_text()
        assert secret not in raw_text
        assert "<redacted>" in raw_text

    def test_price_fetch_failure_records_skip(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            arena, "list_markets", MagicMock(return_value=[_info("m1")])
        )
        monkeypatch.setattr(
            arena, "_fetch_market_prob",
            MagicMock(side_effect=arena.MarketInfoError("gamma down")),
        )
        config = self._config(tmp_path)
        summary = run_predict(tmp_path, config, now=NOW)
        assert summary["forecasts"] == 0
        assert summary["skips"] == 2
        rows = load_forecasts(tmp_path)
        assert all(r["status"] == "skip" for r in rows)

    def test_expired_market_records_skip(self, tmp_path, monkeypatch):
        """A market whose end_date passed is never forecast."""
        expired = _info("old", end_date="2026-09-20T00:00:00Z")
        # Force selection by bypassing the date filter via monkeypatched select.
        monkeypatch.setattr(
            arena, "list_markets", MagicMock(return_value=[expired])
        )
        monkeypatch.setattr(
            arena, "query_model",
            MagicMock(return_value='{"probability": 0.7, "reasoning": "r"}'),
        )
        _mock_price(monkeypatch, 0.5)
        config = self._config(tmp_path)
        summary = run_predict(tmp_path, config, now=NOW)
        # Selection already filters it out — nothing is written.
        assert summary["markets"] == 0
        assert summary["file"] is None

    def test_empty_selection_no_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(arena, "list_markets", MagicMock(return_value=[]))
        config = self._config(tmp_path)
        summary = run_predict(tmp_path, config, now=NOW)
        assert summary["file"] is None
        assert not (tmp_path / "forecasts").exists()

    def test_taken_pair_blocks_reforecast(self, tmp_path, monkeypatch):
        """The (entrant, slug) pair guard holds even when a stored row's ts
        is unusable — the ts-based slug/entrant filters miss it but the pair
        dedupe still prevents a second forecast for that pair."""
        corrupt = _forecast_row("m1", "gpt", 0.6, ts="2026-09-25T01:00:00Z")
        corrupt["ts"] = None
        _write_forecasts(tmp_path, "2026-09-25", [corrupt])
        monkeypatch.setattr(
            arena, "list_markets", MagicMock(return_value=[_info("m1")])
        )
        _mock_price(monkeypatch, 0.5)
        config = self._config(tmp_path)
        summary = run_predict(tmp_path, config, now=NOW)
        assert summary["forecasts"] == 1
        gpt_rows = [r for r in load_forecasts(tmp_path) if r["entrant"] == "gpt"]
        assert gpt_rows == [corrupt]  # no new row for the taken pair
        assert any(
            r["entrant"] == "crowd" and r["slug"] == "m1"
            for r in load_forecasts(tmp_path)
        )


class TestRunResolve:
    def _detail(self, outcome, closed=True, source="price"):
        return MagicMock(
            side_effect=lambda slug, **kw: Resolution(
                outcome=outcome.get(slug) if isinstance(outcome, dict) else outcome,
                closed=closed,
                source=source,
            )
        )

    def test_records_resolutions(self, tmp_path, monkeypatch):
        _write_forecasts(
            tmp_path, "2026-09-20",
            [
                _forecast_row("yes-m", "gpt", 0.9, ts="2026-09-20T00:00:00Z"),
                _forecast_row("open-m", "gpt", 0.4, ts="2026-09-20T00:00:00Z"),
            ],
        )
        mock = self._detail({"yes-m": 1.0, "open-m": None}, closed=True)
        monkeypatch.setattr(arena, "fetch_resolution_detail", mock)

        summary = run_resolve(tmp_path, now=NOW)
        assert summary == {"checked": 2, "resolved": 1, "unresolvable": 1, "total": 2}
        res = load_resolutions(tmp_path)
        assert res["yes-m"] == {
            "outcome": 1,
            "resolved_at": "2026-09-26T12:00:00Z",
            "resolution_source": "price",
        }
        # closed-but-unresolvable is recorded, not silently dropped (M7)
        assert res["open-m"]["outcome"] is None
        assert res["open-m"]["resolution_source"] == "closed_unresolvable"

    def test_open_market_stays_pending(self, tmp_path, monkeypatch):
        _write_forecasts(
            tmp_path, "2026-09-20", [_forecast_row("m", "gpt", 0.9, ts="t")]
        )
        mock = MagicMock(
            return_value=Resolution(outcome=None, closed=False, source=None)
        )
        monkeypatch.setattr(arena, "fetch_resolution_detail", mock)
        summary = run_resolve(tmp_path, now=NOW)
        assert summary == {"checked": 1, "resolved": 0, "unresolvable": 0, "total": 0}
        assert load_resolutions(tmp_path) == {}

    def test_unresolvable_retried_then_resolved(self, tmp_path, monkeypatch):
        """A closed-unresolvable entry keeps being retried until resolved."""
        _write_forecasts(
            tmp_path, "2026-09-20", [_forecast_row("m", "gpt", 0.9, ts="t")]
        )
        arena.save_resolutions(
            tmp_path,
            {"m": {"outcome": None, "resolved_at": "old",
                   "resolution_source": "closed_unresolvable"}},
        )
        mock = MagicMock(
            return_value=Resolution(outcome=0.0, closed=True, source="uma")
        )
        monkeypatch.setattr(arena, "fetch_resolution_detail", mock)
        summary = run_resolve(tmp_path, now=NOW)
        assert summary["resolved"] == 1
        res = load_resolutions(tmp_path)
        assert res["m"]["outcome"] == 0
        assert res["m"]["resolution_source"] == "uma"

    def test_already_resolved_not_refetched(self, tmp_path, monkeypatch):
        _write_forecasts(
            tmp_path, "2026-09-20",
            [_forecast_row("done", "gpt", 0.9, ts="t")],
        )
        arena.save_resolutions(
            tmp_path, {"done": {"outcome": 0, "resolved_at": "old"}}
        )
        mock = MagicMock()
        monkeypatch.setattr(arena, "fetch_resolution_detail", mock)
        summary = run_resolve(tmp_path, now=NOW)
        assert summary["checked"] == 0
        mock.assert_not_called()
        # Unchanged file is not rewritten
        assert load_resolutions(tmp_path)["done"]["resolved_at"] == "old"

    def test_fetch_error_leaves_pending(self, tmp_path, monkeypatch):
        _write_forecasts(
            tmp_path, "2026-09-20", [_forecast_row("m", "gpt", 0.9, ts="t")]
        )
        monkeypatch.setattr(
            arena, "fetch_resolution_detail",
            MagicMock(side_effect=arena.MarketInfoError("down")),
        )
        summary = run_resolve(tmp_path, now=NOW)
        assert summary == {
            "checked": 1, "resolved": 0, "unresolvable": 0, "total": 0,
        }

    def test_no_forecasts_writes_empty(self, tmp_path, monkeypatch):
        summary = run_resolve(tmp_path, now=NOW)
        assert summary == {
            "checked": 0, "resolved": 0, "unresolvable": 0, "total": 0,
        }
        assert (tmp_path / "resolutions.json").exists()


class TestScoring:
    def test_brier_ece_null_below_100(self):
        """Hand-checkable: probs (0.9, 0.2), outcomes (1, 0), market 0.5.

        brier  = ((0.9-1)^2 + (0.2-0)^2) / 2 = (0.01 + 0.04) / 2 = 0.025
        alpha  = entrant_brier - crowd_brier = 0.025 - 0.25 = -0.225
        ece    = null — fewer than 100 resolved forecasts (M4).
        """
        rows = [
            _forecast_row("a", "gpt", 0.9, ts="t", market_prob=0.5),
            _forecast_row("b", "gpt", 0.2, ts="t", market_prob=0.5),
            _forecast_row("a", "crowd", 0.5, ts="t", market_prob=0.5),
            _forecast_row("b", "crowd", 0.5, ts="t", market_prob=0.5),
        ]
        resolutions = {
            "a": {"outcome": 1, "resolved_at": "t"},
            "b": {"outcome": 0, "resolved_at": "t"},
        }
        entrants = [_entrant("gpt"), Entrant(id="crowd", label="C", kind="baseline")]
        board = build_board(rows, resolutions, entrants, now=NOW)
        lb = {r["entrant"]: r for r in board["leaderboard"]}
        assert lb["gpt"]["n"] == 2
        assert lb["gpt"]["n_markets"] == 2
        assert lb["gpt"]["brier"] == pytest.approx(0.025)
        assert lb["gpt"]["ece"] is None
        assert lb["gpt"]["alpha"] == pytest.approx(-0.225)
        assert lb["gpt"]["significant"] is None   # <30 markets: too few
        # crowd brier = 0.25 over the same slugs; alpha defined as 0
        assert lb["crowd"]["brier"] == pytest.approx(0.25)
        assert lb["crowd"]["alpha"] == 0.0
        assert lb["crowd"]["alpha_ci"] is None
        assert lb["crowd"]["significant"] is None
        # sorted by brier ascending
        assert [r["entrant"] for r in board["leaderboard"]] == ["gpt", "crowd"]

    def test_ece_computed_at_100(self):
        """100 resolved rows: probs 0.9 outcomes all 1, market 0.5.

        brier = (0.9-1)^2 = 0.01; one ECE bin: |0.9 - 1.0| = 0.1.
        """
        rows = [
            _forecast_row(f"m{i}", "gpt", 0.9, ts="t", market_prob=0.5)
            for i in range(100)
        ]
        resolutions = {
            f"m{i}": {"outcome": 1, "resolved_at": "t"} for i in range(100)
        }
        board = build_board(rows, resolutions, [_entrant("gpt")], now=NOW)
        row = board["leaderboard"][0]
        assert row["n"] == 100
        assert row["ece"] == pytest.approx(0.1)

    def test_bootstrap_ci_deterministic(self):
        triples = [("e1", 0.9, 0.5, 1.0), ("e2", 0.1, 0.5, 0.0), ("e3", 0.8, 0.4, 1.0)]
        ci1 = _bootstrap_alpha_ci(triples)
        ci2 = _bootstrap_alpha_ci(triples)
        assert ci1 == ci2
        assert ci1[0] <= ci1[1]

    def test_bootstrap_clusters_by_event(self):
        """Rows sharing an event_id are resampled as one cluster — a single
        cluster resamples to itself, so the CI is a point."""
        same_event = [("e1", 1.0, 0.5, 1.0), ("e1", 0.2, 0.5, 0.0)]
        ci = _bootstrap_alpha_ci(same_event)
        # alpha = mean(model brier) - mean(crowd brier) = 0.02 - 0.25 = -0.23
        assert ci[0] == ci[1] == pytest.approx(-0.23)
        # distinct clusters vary under resampling
        distinct = [("e1", 1.0, 0.5, 1.0), ("e2", 0.2, 0.5, 0.0)]
        ci2 = _bootstrap_alpha_ci(distinct)
        assert ci2[0] < ci2[1]

    def test_significant_when_alpha_clearly_negative(self):
        """30+ resolved markets, alpha always -0.25 → CI is a point below 0."""
        rows = [
            _forecast_row(f"m{i}", "gpt", 1.0, ts="t", market_prob=0.5)
            for i in range(35)
        ]
        resolutions = {
            f"m{i}": {"outcome": 1, "resolved_at": "t"} for i in range(35)
        }
        board = build_board(rows, resolutions, [_entrant("gpt")], now=NOW)
        row = board["leaderboard"][0]
        assert row["n_markets"] == 35
        assert row["alpha"] == pytest.approx(-0.25)
        assert row["alpha_ci"][1] < 0
        assert row["significant"] is True

    def test_not_significant_when_ci_includes_zero(self):
        """prob == market_prob on every row → alpha 0, CI = [0, 0] → not
        significant even with enough markets."""
        rows = [
            _forecast_row(f"m{i}", "gpt", 0.4, ts="t", market_prob=0.4)
            for i in range(35)
        ]
        resolutions = {
            f"m{i}": {"outcome": 1, "resolved_at": "t"} for i in range(35)
        }
        board = build_board(rows, resolutions, [_entrant("gpt")], now=NOW)
        row = board["leaderboard"][0]
        assert row["alpha"] == pytest.approx(0.0)
        lo, hi = row["alpha_ci"]
        assert lo <= 0 <= hi
        assert row["significant"] is False

    def test_crowd_scored_on_ai_union(self):
        """The crowd's board row only covers markets AI entrants scored (M5)."""
        rows = [
            _forecast_row("a", "gpt", 0.9, ts="t", market_prob=0.5),
            _forecast_row("a", "crowd", 0.5, ts="t", market_prob=0.5),
            # crowd also forecast market "x" — no AI row there
            _forecast_row("x", "crowd", 0.9, ts="t", market_prob=0.9),
        ]
        resolutions = {
            "a": {"outcome": 1, "resolved_at": "t"},
            "x": {"outcome": 0, "resolved_at": "t"},
        }
        entrants = [_entrant("gpt"), Entrant(id="crowd", label="C", kind="baseline")]
        board = build_board(rows, resolutions, entrants, now=NOW)
        crowd = next(r for r in board["leaderboard"] if r["entrant"] == "crowd")
        # crowd row covers only "a": brier = (0.5-1)^2 = 0.25, not mixed with x
        assert crowd["n"] == 1
        assert crowd["brier"] == pytest.approx(0.25)

    def test_coverage_and_since(self):
        """coverage = resolved forecasts / resolved markets attempted (A1)."""
        rows = [
            _forecast_row("a", "gpt", 0.9, ts="2026-09-20T01:00:00Z"),
            _forecast_row("a", "crowd", 0.5, ts="2026-09-20T01:00:00Z"),
            _forecast_row(
                "b", "gpt", None, ts="2026-09-21T01:00:00Z",
                status="skip", error="boom",
            ),
        ]
        resolutions = {
            "a": {"outcome": 1, "resolved_at": "r"},
            "b": {"outcome": 0, "resolved_at": "r"},
        }
        entrants = [_entrant("gpt"), Entrant(id="crowd", label="C", kind="baseline")]
        board = build_board(rows, resolutions, entrants, now=NOW)
        lb = {r["entrant"]: r for r in board["leaderboard"]}
        # gpt resolved 1 of the 2 resolved markets it attempted
        assert lb["gpt"]["n"] == 1
        assert lb["gpt"]["coverage"] == pytest.approx(0.5)
        assert lb["gpt"]["since"] == "2026-09-20"
        # crowd attempted only "a" of the AI-scored universe
        assert lb["crowd"]["coverage"] == pytest.approx(1.0)

    def test_unresolved_rows_excluded_from_leaderboard(self):
        rows = [_forecast_row("open", "gpt", 0.9, ts="t")]
        board = build_board(rows, {}, [_entrant("gpt")], now=NOW)
        assert board["leaderboard"] == []


class TestBoard:
    def _board(self, rows, resolutions, entrants=None):
        entrants = entrants or [
            _entrant("gpt"),
            _entrant("llm2", model="openai/x"),
            Entrant(id="crowd", label="Crowd", kind="baseline"),
            Entrant(id="coin", label="Coin", kind="baseline"),
        ]
        return build_board(rows, resolutions, entrants, now=NOW)

    def test_contract_keys(self):
        board = self._board([], {})
        assert set(board) == {
            "generated_at", "last_run", "entrants", "leaderboard",
            "open", "duels", "hall_of_wrong", "stats",
        }
        assert set(board["entrants"][0]) == {
            "id", "label", "kind", "model", "web_access", "cutoff",
        }
        assert set(board["stats"]) == {
            "forecasts", "resolved", "markets", "n_markets",
            "entrants", "since", "unresolvable",
        }
        assert board["generated_at"] == "2026-09-26T12:00:00Z"
        assert board["last_run"] is None

    def test_leaderboard_row_keys(self):
        rows = [_forecast_row("a", "gpt", 0.9, ts="t")]
        res = {"a": {"outcome": 1, "resolved_at": "r"}}
        board = self._board(rows, res, entrants=[_entrant("gpt")])
        assert set(board["leaderboard"][0]) == {
            "entrant", "n", "n_markets", "coverage", "since",
            "brier", "ece", "alpha", "alpha_ci", "significant",
        }

    def test_entrant_rows_shape(self):
        board = self._board([], {})
        crowd = next(e for e in board["entrants"] if e["id"] == "crowd")
        assert crowd["model"] is None
        gpt = next(e for e in board["entrants"] if e["id"] == "gpt")
        assert gpt["model"] == "openai/gpt-4.1"
        assert gpt["web_access"] is None

    def test_removed_entrant_kept_from_rows(self):
        """An entrant dropped from config still appears, pinned by row data."""
        rows = [_forecast_row("a", "oldmodel", 0.9, ts="t", model="openai/old")]
        res = {"a": {"outcome": 1, "resolved_at": "r"}}
        board = self._board(rows, res)
        ids = {e["id"] for e in board["entrants"]}
        assert "oldmodel" in ids
        e = next(e for e in board["entrants"] if e["id"] == "oldmodel")
        assert e["model"] == "openai/old"
        assert e["kind"] == "ai"
        assert any(r["entrant"] == "oldmodel" for r in board["leaderboard"])

    def test_removed_skip_only_entrant_hidden(self):
        rows = [_forecast_row("a", "gone", None, ts="t", status="skip")]
        board = self._board(rows, {})
        assert "gone" not in {e["id"] for e in board["entrants"]}

    def test_removed_entrant_with_a_forecast_kept_after_skip(self):
        rows = [
            _forecast_row("a", "gone", None, ts="t", status="skip"),
            _forecast_row("b", "gone", 0.6, ts="t"),
        ]
        board = self._board(rows, {})
        assert "gone" in {e["id"] for e in board["entrants"]}

    def test_last_run_is_latest_row_ts(self):
        rows = [
            _forecast_row("a", "gpt", 0.9, ts="2026-09-25T01:00:00Z"),
            _forecast_row(
                "b", "gpt", None, ts="2026-09-26T09:00:00Z",
                status="skip", error="x",
            ),
        ]
        board = self._board(rows, {})
        assert board["last_run"] == "2026-09-26T09:00:00Z"

    def test_open_markets_grouped(self):
        rows = [
            _forecast_row("open", "gpt", 0.9, ts="2026-09-26T01:00:00Z", rationale="hi"),
            _forecast_row("open", "crowd", 0.5, ts="2026-09-26T01:00:00Z"),
            _forecast_row("done", "gpt", 0.9, ts="2026-09-26T01:00:00Z"),
        ]
        res = {"done": {"outcome": 1, "resolved_at": "r"}}
        board = self._board(rows, res)
        assert len(board["open"]) == 1
        m = board["open"][0]
        assert m["slug"] == "open"
        assert set(m["forecasts"]) == {"gpt", "crowd"}
        assert m["forecasts"]["gpt"]["prob"] == 0.9
        assert m["forecasts"]["gpt"]["rationale"] == "hi"

    def test_unresolvable_not_in_open(self):
        rows = [_forecast_row("m", "gpt", 0.9, ts="t")]
        res = {
            "m": {"outcome": None, "resolved_at": "r",
                  "resolution_source": "closed_unresolvable"},
        }
        board = self._board(rows, res)
        assert board["open"] == []
        assert board["stats"]["unresolvable"] == 1

    def test_open_latest_forecast_wins(self):
        rows = [
            _forecast_row("m", "gpt", 0.6, ts="2026-09-25T01:00:00Z", market_prob=0.4),
            _forecast_row("m", "gpt", 0.7, ts="2026-09-26T01:00:00Z", market_prob=0.45),
        ]
        board = self._board(rows, {})
        m = board["open"][0]
        assert m["forecasts"]["gpt"]["prob"] == 0.7
        assert m["market_prob"] == 0.45

    def test_skip_rows_not_in_open(self):
        rows = [
            _forecast_row("m", "gpt", None, ts="t", status="skip", error="x"),
            _forecast_row("m", "crowd", 0.5, ts="t"),
        ]
        board = self._board(rows, {})
        assert set(board["open"][0]["forecasts"]) == {"crowd"}

    def test_duels_largest_gap_first(self):
        rows = [
            _forecast_row("a", "gpt", 0.9, ts="t", market_prob=0.3),
            _forecast_row("a", "coin", 0.5, ts="t", market_prob=0.3),
            _forecast_row("b", "gpt", 0.55, ts="t", market_prob=0.5),
            _forecast_row("c", "gpt", 0.2, ts="t", market_prob=0.4),
        ]
        res = {"a": {"outcome": 1, "resolved_at": "r"}}
        board = self._board(rows, res)
        duels = board["duels"]
        # baselines never duel; gaps: a=0.6, c=0.2, b=0.05
        assert [d["slug"] for d in duels] == ["a", "c", "b"]
        d = duels[0]
        assert d["gap"] == pytest.approx(0.6)
        assert d["status"] == "won"      # |0.9-1| < |0.3-1|
        assert d["outcome"] == 1
        assert duels[1]["status"] == "open"
        assert duels[1]["outcome"] is None
        assert set(d) == {
            "slug", "question", "url", "market_prob", "entrant",
            "prob", "gap", "rationale", "status", "outcome",
        }

    def test_duel_lost_status(self):
        rows = [_forecast_row("a", "gpt", 0.2, ts="t", market_prob=0.4)]
        res = {"a": {"outcome": 1, "resolved_at": "r"}}
        board = self._board(rows, res)
        assert board["duels"][0]["status"] == "lost"

    def test_duel_tie_status(self):
        """Equal distance to the outcome is a tie, not a loss (v1.1)."""
        rows = [_forecast_row("a", "gpt", 0.5, ts="t", market_prob=0.5)]
        res = {"a": {"outcome": 1, "resolved_at": "r"}}
        board = self._board(rows, res)
        assert board["duels"][0]["status"] == "tie"

    def test_duels_top_10(self):
        rows = [
            _forecast_row(f"m{i}", "gpt", 0.0, ts="t", market_prob=0.5)
            for i in range(12)
        ]
        board = self._board(rows, {})
        assert len(board["duels"]) == 10

    def test_hall_of_wrong_rules(self):
        rows = [
            # confident + wrong: in
            _forecast_row("w1", "gpt", 0.95, ts="t"),
            # confident + right: out
            _forecast_row("w2", "gpt", 0.9, ts="t"),
            # wrong but not confident: out
            _forecast_row("w3", "gpt", 0.6, ts="t"),
            # baselines never appear
            _forecast_row("w1", "coin", 0.05, ts="t"),
        ]
        res = {
            "w1": {"outcome": 0, "resolved_at": "r1"},
            "w2": {"outcome": 1, "resolved_at": "r2"},
            "w3": {"outcome": 0, "resolved_at": "r3"},
        }
        board = self._board(rows, res)
        hall = board["hall_of_wrong"]
        assert len(hall) == 1
        h = hall[0]
        assert h["slug"] == "w1"
        assert h["entrant"] == "gpt"
        assert h["prob"] == 0.95
        assert h["outcome"] == 0
        assert h["resolved_at"] == "r1"
        assert set(h) == {
            "slug", "question", "url", "entrant", "prob",
            "outcome", "market_prob", "rationale", "resolved_at",
        }

    def test_hall_of_wrong_confident_no_side(self):
        """prob <= 0.2 while outcome=1 counts as confidently wrong."""
        rows = [_forecast_row("m", "gpt", 0.05, ts="t")]
        res = {"m": {"outcome": 1, "resolved_at": "r"}}
        board = self._board(rows, res)
        assert [h["slug"] for h in board["hall_of_wrong"]] == ["m"]

    def test_hall_of_wrong_ordering_and_limit(self):
        rows = [
            _forecast_row(f"m{i}", "gpt", 0.8 + i * 0.008, ts="t")
            for i in range(25)
        ]
        res = {f"m{i}": {"outcome": 0, "resolved_at": "r"} for i in range(25)}
        board = self._board(rows, res)
        hall = board["hall_of_wrong"]
        assert len(hall) == 20
        confs = [max(h["prob"], 1 - h["prob"]) for h in hall]
        assert confs == sorted(confs, reverse=True)

    def test_stats(self):
        rows = [
            _forecast_row("a", "gpt", 0.9, ts="2026-09-20T01:00:00Z"),
            _forecast_row("a", "crowd", 0.5, ts="2026-09-20T01:00:00Z"),
            _forecast_row("b", "gpt", None, ts="2026-09-26T01:00:00Z",
                          status="skip", error="x"),
        ]
        res = {
            "a": {"outcome": 1, "resolved_at": "r"},
            "z": {"outcome": None, "resolved_at": "r",
                  "resolution_source": "closed_unresolvable"},
        }
        board = self._board(rows, res)
        assert board["stats"] == {
            "forecasts": 2,   # skip rows don't count
            "resolved": 2,
            "markets": 1,
            "n_markets": 1,
            "entrants": 4,
            "since": "2026-09-20",
            "unresolvable": 1,
        }

    def test_empty_board(self):
        board = self._board([], {})
        assert board["leaderboard"] == []
        assert board["open"] == []
        assert board["duels"] == []
        assert board["hall_of_wrong"] == []
        assert board["stats"]["since"] is None
        assert board["stats"]["n_markets"] == 0


class TestRunBuild:
    def _fixture(self, tmp_path):
        data_dir = tmp_path / "data"
        _write_forecasts(
            data_dir, "2026-09-25",
            [
                _forecast_row("a", "gpt", 0.9, ts="2026-09-25T01:00:00Z"),
                _forecast_row("a", "crowd", 0.5, ts="2026-09-25T01:00:00Z"),
            ],
        )
        arena.save_resolutions(
            data_dir, {"a": {"outcome": 1, "resolved_at": "2026-09-26T00:00:00Z"}}
        )
        config = _write_config(
            tmp_path / "arena.yaml",
            [
                {"id": "gpt", "kind": "ai", "model": "openai/gpt-4.1"},
                {"id": "crowd", "kind": "baseline"},
            ],
        )
        return data_dir, config

    def test_writes_site(self, tmp_path):
        data_dir, config = self._fixture(tmp_path)
        out = tmp_path / "site"
        summary = run_build(data_dir, config, out, now=NOW)
        board = json.loads((out / "data.json").read_text())
        assert board["leaderboard"][0]["entrant"] == "gpt"
        html = (out / "index.html").read_text()
        assert html.startswith("<!doctype html>")
        assert "not affiliated with Polymarket" in html
        assert (out / "CNAME").read_text().strip() == "polymarket-leaderboard.com"
        assert summary == {
            "data_json": str(out / "data.json"),
            "index_html": str(out / "index.html"),
            "markets_open": len(board["open"]),
            "leaderboard": len(board["leaderboard"]),
        }


class TestBoardModelDisplay:
    def test_github_models_prefix_dropped(self, tmp_path):
        config = _write_config(
            tmp_path / "arena.yaml",
            [
                {"id": "gpt", "kind": "ai", "model": "openai/openai/gpt-4.1",
                 "api_base": "https://models.github.ai/inference"},
                {"id": "jev", "kind": "ai", "model": "opencode/jev-1.13-free"},
            ],
        )
        board = build_board([], {}, load_entrants(config), now=NOW)
        models = {e["id"]: e["model"] for e in board["entrants"]}
        assert models == {"gpt": "openai/gpt-4.1", "jev": "opencode/jev-1.13-free"}


class TestCli:
    @pytest.fixture
    def cli_runner(self):
        return CliRunner()

    def _config(self, tmp_path):
        return _write_config(
            tmp_path / "arena.yaml",
            [
                {"id": "gpt", "kind": "ai", "model": "openai/gpt-4.1"},
                {"id": "crowd", "kind": "baseline"},
            ],
        )

    def test_predict(self, cli_runner, tmp_path, monkeypatch):
        monkeypatch.setattr(
            arena, "list_markets", MagicMock(return_value=[_info("m1")])
        )
        monkeypatch.setattr(
            arena, "query_model",
            MagicMock(return_value='{"probability": 0.6, "reasoning": "r"}'),
        )
        monkeypatch.setattr(
            arena, "fetch_prices",
            MagicMock(return_value={"Yes": 0.5, "No": 0.5}),
        )
        result = cli_runner.invoke(main, [
            "arena", "predict", "--data", str(tmp_path / "data"),
            "--config", str(self._config(tmp_path)),
        ])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["data"]["forecasts"] == 2

    def test_predict_error(self, cli_runner, tmp_path, monkeypatch):
        monkeypatch.setattr(
            arena, "list_markets", MagicMock(side_effect=RuntimeError("net down"))
        )
        result = cli_runner.invoke(main, [
            "arena", "predict", "--data", str(tmp_path / "data"),
            "--config", str(self._config(tmp_path)),
        ])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["ok"] is False
        assert "net down" in data["error"]

    def test_resolve(self, cli_runner, tmp_path, monkeypatch):
        monkeypatch.setattr(
            arena, "fetch_resolution_detail",
            MagicMock(return_value=Resolution(None, closed=False, source=None)),
        )
        result = cli_runner.invoke(main, [
            "arena", "resolve", "--data", str(tmp_path / "data"),
        ])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["data"]["checked"] == 0

    def test_resolve_error(self, cli_runner, tmp_path, monkeypatch):
        monkeypatch.setattr(
            arena, "load_forecasts", MagicMock(side_effect=RuntimeError("io"))
        )
        result = cli_runner.invoke(main, [
            "arena", "resolve", "--data", str(tmp_path),
        ])
        assert result.exit_code == 1
        assert json.loads(result.output)["ok"] is False

    def test_build(self, cli_runner, tmp_path):
        data_dir = tmp_path / "data"
        _write_forecasts(
            data_dir, "2026-09-25",
            [_forecast_row("a", "gpt", 0.9, ts="2026-09-25T01:00:00Z")],
        )
        out = tmp_path / "site"
        result = cli_runner.invoke(main, [
            "arena", "build", "--data", str(data_dir),
            "--config", str(self._config(tmp_path)),
            "--out", str(out),
        ])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ok"] is True
        assert (out / "data.json").exists()

    def test_build_error(self, cli_runner, tmp_path):
        result = cli_runner.invoke(main, [
            "arena", "build", "--data", str(tmp_path / "data"),
            "--config", str(tmp_path / "missing.yaml"),
        ])
        assert result.exit_code == 1
        assert json.loads(result.output)["ok"] is False
