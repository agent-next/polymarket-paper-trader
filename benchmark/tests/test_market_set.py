"""Tests for pm_benchmark.market_set."""
from __future__ import annotations

import pytest

from pm_benchmark.market_set import (
    MarketEntry,
    MarketSet,
    list_market_sets,
    load_market_set,
)


class TestMarketEntry:
    def test_valid(self):
        e = MarketEntry(slug="test-market", category="crypto", difficulty="easy")
        assert e.slug == "test-market"
        assert e.category == "crypto"
        assert e.difficulty == "easy"
        assert e.notes == ""

    def test_defaults(self):
        e = MarketEntry(slug="test")
        assert e.category == "general"
        assert e.difficulty == "medium"

    def test_empty_slug(self):
        with pytest.raises(ValueError, match="slug cannot be empty"):
            MarketEntry(slug="")

    def test_invalid_difficulty(self):
        with pytest.raises(ValueError, match="Invalid difficulty"):
            MarketEntry(slug="test", difficulty="impossible")


class TestMarketSet:
    def test_valid(self):
        ms = MarketSet(
            name="test",
            description="desc",
            version="1.0",
            markets=[MarketEntry(slug="m1")],
        )
        assert ms.name == "test"
        assert len(ms.markets) == 1

    def test_empty_name(self):
        with pytest.raises(ValueError, match="name cannot be empty"):
            MarketSet(name="", description="", version="1.0", markets=[MarketEntry(slug="m1")])

    def test_empty_markets(self):
        with pytest.raises(ValueError, match="at least one market"):
            MarketSet(name="test", description="", version="1.0", markets=[])


class TestLoadMarketSet:
    def test_load_mini(self):
        ms = load_market_set("mini")
        assert ms.name == "mini"
        assert len(ms.markets) == 3
        assert ms.markets[0].slug == "will-leverkusen-win-the-202526-champions-league"

    def test_load_mixed(self):
        ms = load_market_set("mixed")
        assert ms.name == "mixed"
        assert len(ms.markets) == 15

    def test_load_by_path(self, tmp_path):
        yaml_file = tmp_path / "custom.yaml"
        yaml_file.write_text(
            "name: custom\n"
            "description: test\n"
            "version: '1.0'\n"
            "markets:\n"
            "  - slug: test-market\n"
        )
        ms = load_market_set(str(yaml_file))
        assert ms.name == "custom"
        assert len(ms.markets) == 1

    def test_not_found(self):
        with pytest.raises(FileNotFoundError, match="not found"):
            load_market_set("nonexistent")

    def test_invalid_format(self, tmp_path):
        yaml_file = tmp_path / "bad.yaml"
        yaml_file.write_text("just a string")
        with pytest.raises(ValueError, match="Invalid market set format"):
            load_market_set(str(yaml_file))


class TestListMarketSets:
    def test_lists_bundled(self):
        sets = list_market_sets()
        assert "mini" in sets
        assert "mixed" in sets

    def test_empty_dir(self, monkeypatch):
        monkeypatch.setattr(
            "pm_benchmark.market_set._BUNDLE_DIR",
            type("FakePath", (), {"exists": lambda self: False})(),
        )
        assert list_market_sets() == []
