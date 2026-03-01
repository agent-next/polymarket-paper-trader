"""Tests for shareable stats card generators."""

from __future__ import annotations

from pm_trader.card import (
    _extract,
    _roi_icon,
    generate_card,
    generate_card_plain,
    generate_tweet,
)


# ---------------------------------------------------------------------------
# _roi_icon
# ---------------------------------------------------------------------------


class TestRoiIcon:
    def test_fire(self):
        assert _roi_icon(25.0) == "\U0001f525"

    def test_rocket(self):
        assert _roi_icon(15.0) == "\U0001f680"

    def test_chart_up(self):
        assert _roi_icon(5.0) == "\U0001f4c8"

    def test_flat(self):
        assert _roi_icon(0.0) == "\u2796"

    def test_chart_down(self):
        assert _roi_icon(-5.0) == "\U0001f4c9"

    def test_skull(self):
        assert _roi_icon(-15.0) == "\U0001f480"


# ---------------------------------------------------------------------------
# _extract
# ---------------------------------------------------------------------------


class TestExtract:
    def test_defaults(self):
        result = _extract({})
        assert result["roi"] == 0.0
        assert result["pnl"] == 0.0
        assert result["trades"] == 0
        assert result["pnl_sign"] == "+"
        assert result["roi_sign"] == "+"

    def test_negative_pnl(self):
        result = _extract({"pnl": -500.0, "roi_pct": -10.0})
        assert result["pnl_sign"] == ""
        assert result["roi_sign"] == ""

    def test_full_stats(self):
        stats = {
            "roi_pct": 12.5,
            "pnl": 1250.0,
            "total_value": 11250.0,
            "sharpe_ratio": 1.8,
            "win_rate": 0.65,
            "total_trades": 42,
            "max_drawdown": 0.08,
            "total_fees": 15.50,
            "starting_balance": 10000.0,
        }
        result = _extract(stats)
        assert result["roi"] == 12.5
        assert result["total"] == 11250.0
        assert result["sharpe"] == 1.8
        assert result["win"] == 0.65
        assert result["trades"] == 42
        assert result["dd"] == 0.08
        assert result["fees"] == 15.50
        assert result["starting"] == 10000.0
        assert result["icon"] == "\U0001f680"  # 10 < 12.5 < 20


# ---------------------------------------------------------------------------
# generate_tweet
# ---------------------------------------------------------------------------


class TestGenerateTweet:
    def test_basic_tweet(self):
        stats = {"roi_pct": 25.0, "pnl": 2500.0, "total_trades": 10}
        tweet = generate_tweet(stats)
        assert "ROI: +25.0%" in tweet
        assert "P&L: +$2,500" in tweet
        assert "10 trades" in tweet
        assert "#Polymarket" in tweet
        assert "#AITrading" in tweet
        assert "clawhub install" in tweet

    def test_negative_roi(self):
        stats = {"roi_pct": -8.0, "pnl": -800.0}
        tweet = generate_tweet(stats)
        assert "ROI: -8.0%" in tweet
        assert "P&L: $-800" in tweet

    def test_under_280_chars(self):
        stats = {
            "roi_pct": 99.9,
            "pnl": 9999.0,
            "sharpe_ratio": 3.5,
            "win_rate": 0.9,
            "total_trades": 100,
        }
        tweet = generate_tweet(stats)
        assert len(tweet) <= 400  # generous limit for multiline

    def test_custom_account(self):
        tweet = generate_tweet({}, account="aggressive")
        assert "Polymarket" in tweet


# ---------------------------------------------------------------------------
# generate_card (markdown)
# ---------------------------------------------------------------------------


class TestGenerateCard:
    def test_basic_card(self):
        stats = {
            "roi_pct": 15.0,
            "pnl": 1500.0,
            "sharpe_ratio": 2.1,
            "win_rate": 0.7,
            "total_trades": 30,
            "max_drawdown": 0.05,
            "total_fees": 10.0,
            "total_value": 11500.0,
            "starting_balance": 10000.0,
        }
        card = generate_card(stats)
        assert "*Polymarket Paper Trading*" in card
        assert "ROI: *+15.0%*" in card
        assert "Sharpe: *2.10*" in card
        assert "Win Rate: *70%*" in card
        assert "Trades: *30*" in card
        assert "Max DD: *5.0%*" in card
        assert "P&L: *+$1,500.00*" in card
        assert "Portfolio: *$11,500.00*" in card
        assert "clawhub" in card

    def test_zero_stats(self):
        card = generate_card({})
        assert "ROI: *+0.0%*" in card
        assert "Trades: *0*" in card


# ---------------------------------------------------------------------------
# generate_card_plain
# ---------------------------------------------------------------------------


class TestGenerateCardPlain:
    def test_basic_plain(self):
        stats = {
            "roi_pct": -3.0,
            "pnl": -300.0,
            "sharpe_ratio": -0.5,
            "win_rate": 0.4,
            "total_trades": 15,
            "max_drawdown": 0.12,
            "total_fees": 5.0,
            "total_value": 9700.0,
            "starting_balance": 10000.0,
        }
        card = generate_card_plain(stats)
        assert "Polymarket Paper Trading" in card
        assert "ROI:       -3.0%" in card
        assert "Sharpe:    -0.50" in card
        assert "Win Rate:  40%" in card
        assert "P&L:       $-300.00" in card
        assert "Portfolio: $9,700.00" in card
        # No markdown formatting
        assert "*" not in card

    def test_zero_stats(self):
        card = generate_card_plain({})
        assert "ROI:       +0.0%" in card
        assert "clawhub" in card
