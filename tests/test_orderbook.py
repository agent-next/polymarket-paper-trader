"""Comprehensive tests for the order book fill simulation engine.

Every expected value is hand-calculated to verify exact mathematical
correctness.  This is the most critical test file in the project — any
error in fill calculation makes the entire benchmark invalid.
"""

from __future__ import annotations

import pytest

from pm_trader.models import OrderBook, OrderBookLevel
from pm_trader.orderbook import (
    _best_ask,
    _best_bid,
    calculate_fee,
    calculate_fee_schedule,
    simulate_buy_fill,
    simulate_sell_fill,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def simple_book() -> OrderBook:
    """A book with a single level on each side."""
    return OrderBook(
        bids=[OrderBookLevel(price=0.64, size=200.0)],
        asks=[OrderBookLevel(price=0.66, size=200.0)],
    )


@pytest.fixture
def multi_level_book() -> OrderBook:
    """A multi-level book matching the design doc example."""
    return OrderBook(
        bids=[
            OrderBookLevel(price=0.64, size=150.0),
            OrderBookLevel(price=0.63, size=200.0),
            OrderBookLevel(price=0.62, size=300.0),
            OrderBookLevel(price=0.60, size=500.0),
        ],
        asks=[
            OrderBookLevel(price=0.66, size=80.0),
            OrderBookLevel(price=0.67, size=120.0),
            OrderBookLevel(price=0.68, size=200.0),
            OrderBookLevel(price=0.70, size=400.0),
        ],
    )


@pytest.fixture
def thin_book() -> OrderBook:
    """A thin book with limited liquidity."""
    return OrderBook(
        bids=[OrderBookLevel(price=0.64, size=30.0)],
        asks=[OrderBookLevel(price=0.66, size=20.0)],
    )


@pytest.fixture
def empty_book() -> OrderBook:
    """An order book with no levels on either side."""
    return OrderBook(bids=[], asks=[])


@pytest.fixture
def low_price_book() -> OrderBook:
    """A book priced below 0.50, where share- and notional-based fees differ."""
    return OrderBook(
        bids=[OrderBookLevel(price=0.28, size=1_000.0)],
        asks=[OrderBookLevel(price=0.30, size=1_000.0)],
    )


# =========================================================================
# BUY TESTS
# =========================================================================

class TestBuySingleLevelFill:
    """$50 buy fills entirely at the first ask level."""

    def test_fills_at_single_level(self, simple_book: OrderBook) -> None:
        # Ask: 0.66, size=200.  $50 / 0.66 = 75.7575... shares
        result = simulate_buy_fill(simple_book, 50.0, fee_rate_bps=0)

        assert result.filled is True
        assert result.is_partial is False
        assert result.levels_filled == 1
        assert len(result.fills) == 1

        assert result.total_cost == pytest.approx(50.0)
        assert result.total_shares == pytest.approx(50.0 / 0.66)
        assert result.avg_price == pytest.approx(0.66)
        assert result.fee == 0.0

    def test_fill_details(self, simple_book: OrderBook) -> None:
        result = simulate_buy_fill(simple_book, 50.0, fee_rate_bps=0)
        fill = result.fills[0]

        assert fill.price == pytest.approx(0.66)
        assert fill.shares == pytest.approx(50.0 / 0.66)
        assert fill.cost == pytest.approx(50.0)
        assert fill.level == 1


class TestBuyMultiLevelFill:
    """$100 buy crosses 2 ask levels."""

    def test_crosses_two_levels(self, multi_level_book: OrderBook) -> None:
        # Ask level 1: price=0.66, size=80 -> cost = 80 * 0.66 = $52.80
        # Remaining: $100 - $52.80 = $47.20
        # Ask level 2: price=0.67, shares = $47.20 / 0.67 = 70.44776...
        # Total shares: 80 + 70.44776... = 150.44776...
        # Avg price: 100 / 150.44776... = 0.66468...
        result = simulate_buy_fill(multi_level_book, 100.0, fee_rate_bps=0)

        assert result.filled is True
        assert result.is_partial is False
        assert result.levels_filled == 2

        expected_remaining = 100.0 - (80.0 * 0.66)
        expected_shares_l2 = expected_remaining / 0.67
        expected_total_shares = 80.0 + expected_shares_l2

        assert result.total_cost == pytest.approx(100.0)
        assert result.total_shares == pytest.approx(expected_total_shares)
        assert result.avg_price == pytest.approx(100.0 / expected_total_shares)

    def test_per_level_fill_details(self, multi_level_book: OrderBook) -> None:
        result = simulate_buy_fill(multi_level_book, 100.0, fee_rate_bps=0)

        assert len(result.fills) == 2

        # Level 1: entire level consumed
        assert result.fills[0].price == pytest.approx(0.66)
        assert result.fills[0].shares == pytest.approx(80.0)
        assert result.fills[0].cost == pytest.approx(52.80)
        assert result.fills[0].level == 1

        # Level 2: partial fill
        remaining = 100.0 - 52.80
        assert result.fills[1].price == pytest.approx(0.67)
        assert result.fills[1].shares == pytest.approx(remaining / 0.67)
        assert result.fills[1].cost == pytest.approx(remaining)
        assert result.fills[1].level == 2


class TestBuyExactLevelBoundary:
    """Buy exactly consumes one level with zero remainder."""

    def test_exact_level_consumption(self, multi_level_book: OrderBook) -> None:
        # Ask level 1: price=0.66, size=80.  Exact cost = 80 * 0.66 = $52.80
        amount = 80.0 * 0.66  # $52.80
        result = simulate_buy_fill(multi_level_book, amount, fee_rate_bps=0)

        assert result.filled is True
        assert result.levels_filled == 1
        assert result.total_cost == pytest.approx(52.80)
        assert result.total_shares == pytest.approx(80.0)
        assert result.avg_price == pytest.approx(0.66)


class TestBuyFokInsufficientLiquidity:
    """FOK order with insufficient book depth is rejected."""

    def test_fok_rejected(self, thin_book: OrderBook) -> None:
        # Ask: price=0.66, size=20.  Max capacity = 20 * 0.66 = $13.20
        # $50 > $13.20 -> FOK rejected
        result = simulate_buy_fill(thin_book, 50.0, fee_rate_bps=0, order_type="fok")

        assert result.filled is False
        assert result.total_cost == 0.0
        assert result.total_shares == 0.0
        assert result.levels_filled == 0
        assert result.fills == []


class TestBuyFakPartialFill:
    """FAK order fills partially when book is thin."""

    def test_fak_partial(self, thin_book: OrderBook) -> None:
        # Ask: price=0.66, size=20.  Max capacity = 20 * 0.66 = $13.20
        # FAK fills $13.20 out of $50 requested
        result = simulate_buy_fill(thin_book, 50.0, fee_rate_bps=0, order_type="fak")

        assert result.filled is False
        assert result.is_partial is True
        assert result.levels_filled == 1
        assert result.total_cost == pytest.approx(20.0 * 0.66)
        assert result.total_shares == pytest.approx(20.0)
        assert result.avg_price == pytest.approx(0.66)


class TestBuyEmptyBook:
    """No asks means no possible fill."""

    def test_fok_rejected(self, empty_book: OrderBook) -> None:
        result = simulate_buy_fill(empty_book, 100.0, fee_rate_bps=0, order_type="fok")

        assert result.filled is False
        assert result.total_shares == 0.0
        assert result.levels_filled == 0

    def test_fak_zero_shares(self, empty_book: OrderBook) -> None:
        result = simulate_buy_fill(empty_book, 100.0, fee_rate_bps=0, order_type="fak")

        assert result.filled is False
        assert result.total_shares == 0.0
        assert result.levels_filled == 0


class TestBuySlippageCalculation:
    """Verify slippage metrics vs best quote and midpoint."""

    def test_single_level_slippage(self, multi_level_book: OrderBook) -> None:
        # $50 fills entirely at best ask 0.66 (level size 80).
        # slippage_bps is vs the touch: (0.66 - 0.66) / 0.66 * 10000 = 0
        # slippage_bps_midpoint keeps the old midpoint basis:
        # midpoint = (0.64 + 0.66) / 2 = 0.65 -> (0.66 - 0.65) / 0.65 * 10000
        result = simulate_buy_fill(multi_level_book, 50.0, fee_rate_bps=0)

        assert result.slippage_bps == pytest.approx(0.0)

        midpoint = 0.65
        expected_midpoint = (0.66 - midpoint) / midpoint * 10_000
        assert result.slippage_bps_midpoint == pytest.approx(expected_midpoint)
        assert result.slippage_bps_midpoint > 0

    def test_multi_level_slippage(self, multi_level_book: OrderBook) -> None:
        # $100 buy: avg_price = 100 / (80 + 47.20/0.67) = 0.66468...
        # Best ask = 0.66, midpoint = 0.65 -> the two metrics must differ.
        result = simulate_buy_fill(multi_level_book, 100.0, fee_rate_bps=0)

        expected_quote = (result.avg_price - 0.66) / 0.66 * 10_000
        expected_midpoint = (result.avg_price - 0.65) / 0.65 * 10_000
        assert result.slippage_bps == pytest.approx(expected_quote)
        assert result.slippage_bps_midpoint == pytest.approx(expected_midpoint)
        assert result.slippage_bps > 0  # buying pushes price up
        assert result.slippage_bps < result.slippage_bps_midpoint


# =========================================================================
# SELL TESTS
# =========================================================================

class TestSellSingleLevelFill:
    """Sell 50 shares, fills at top bid."""

    def test_fills_at_top_bid(self, multi_level_book: OrderBook) -> None:
        # Best bid: price=0.64, size=150.  Selling 50 shares at 0.64
        # Proceeds = 50 * 0.64 = $32.00
        result = simulate_sell_fill(multi_level_book, 50.0, fee_rate_bps=0)

        assert result.filled is True
        assert result.is_partial is False
        assert result.levels_filled == 1
        assert result.total_shares == pytest.approx(50.0)
        assert result.total_cost == pytest.approx(50.0 * 0.64)
        assert result.avg_price == pytest.approx(0.64)
        assert result.fee == 0.0

    def test_sell_fill_details(self, multi_level_book: OrderBook) -> None:
        result = simulate_sell_fill(multi_level_book, 50.0, fee_rate_bps=0)
        fill = result.fills[0]

        assert fill.price == pytest.approx(0.64)
        assert fill.shares == pytest.approx(50.0)
        assert fill.cost == pytest.approx(32.0)
        assert fill.level == 1


class TestSellMultiLevelFill:
    """Sell 200 shares, crosses 2 bid levels."""

    def test_crosses_two_levels(self, multi_level_book: OrderBook) -> None:
        # Bid level 1: price=0.64, size=150 -> sell 150 at 0.64 = $96.00
        # Remaining: 200 - 150 = 50 shares
        # Bid level 2: price=0.63, size=200 -> sell 50 at 0.63 = $31.50
        # Total proceeds: 96.00 + 31.50 = $127.50
        # Avg price: 127.50 / 200 = 0.6375
        result = simulate_sell_fill(multi_level_book, 200.0, fee_rate_bps=0)

        assert result.filled is True
        assert result.is_partial is False
        assert result.levels_filled == 2
        assert result.total_shares == pytest.approx(200.0)
        assert result.total_cost == pytest.approx(127.50)
        assert result.avg_price == pytest.approx(0.6375)

    def test_per_level_fill_details(self, multi_level_book: OrderBook) -> None:
        result = simulate_sell_fill(multi_level_book, 200.0, fee_rate_bps=0)

        assert len(result.fills) == 2

        # Level 1: entire bid level consumed
        assert result.fills[0].price == pytest.approx(0.64)
        assert result.fills[0].shares == pytest.approx(150.0)
        assert result.fills[0].cost == pytest.approx(96.0)

        # Level 2: partial fill
        assert result.fills[1].price == pytest.approx(0.63)
        assert result.fills[1].shares == pytest.approx(50.0)
        assert result.fills[1].cost == pytest.approx(31.50)

    def test_sell_slippage_vs_quote_and_midpoint(
        self, multi_level_book: OrderBook,
    ) -> None:
        # Sell 200 shares -> avg 0.6375, best_bid 0.64, midpoint 0.65.
        # slippage_bps vs the touch: (0.64 - 0.6375) / 0.64 * 10000 = +39.0625
        # (positive = worse than the touch on both sides).
        # slippage_bps_midpoint keeps the old basis and stays negative.
        result = simulate_sell_fill(multi_level_book, 200.0, fee_rate_bps=0)

        expected_quote = (0.64 - 0.6375) / 0.64 * 10_000
        assert result.slippage_bps == pytest.approx(expected_quote)
        assert result.slippage_bps > 0

        expected_midpoint = (0.6375 - 0.65) / 0.65 * 10_000
        assert result.slippage_bps_midpoint == pytest.approx(expected_midpoint)
        assert result.slippage_bps_midpoint < 0


class TestSellFokInsufficientLiquidity:
    """FOK sell rejected when not enough bids."""

    def test_fok_rejected(self, thin_book: OrderBook) -> None:
        # Only 30 shares on the bid side, trying to sell 100
        result = simulate_sell_fill(thin_book, 100.0, fee_rate_bps=0, order_type="fok")

        assert result.filled is False
        assert result.total_cost == 0.0
        assert result.total_shares == 0.0
        assert result.levels_filled == 0


class TestSellFakPartialFill:
    """FAK sells what's available."""

    def test_fak_partial(self, thin_book: OrderBook) -> None:
        # Bid: price=0.64, size=30.  Trying to sell 100 shares.
        # FAK fills 30 shares at 0.64 = $19.20
        result = simulate_sell_fill(thin_book, 100.0, fee_rate_bps=0, order_type="fak")

        assert result.filled is False
        assert result.is_partial is True
        assert result.levels_filled == 1
        assert result.total_shares == pytest.approx(30.0)
        assert result.total_cost == pytest.approx(30.0 * 0.64)
        assert result.avg_price == pytest.approx(0.64)


class TestSellEmptyBook:
    """No bids means no possible sell."""

    def test_fok_rejected(self, empty_book: OrderBook) -> None:
        result = simulate_sell_fill(empty_book, 50.0, fee_rate_bps=0, order_type="fok")

        assert result.filled is False
        assert result.total_shares == 0.0

    def test_fak_zero_shares(self, empty_book: OrderBook) -> None:
        result = simulate_sell_fill(empty_book, 50.0, fee_rate_bps=0, order_type="fak")

        assert result.filled is False
        assert result.total_shares == 0.0


class TestFokFloatNoise:
    """Exact-depth FOK fills must not be rejected by binary float residue."""

    def test_buy_exact_depth_float_noise(self) -> None:
        # 0.1 + 0.2 leaves a ~2.8e-17 binary residue after subtracting the
        # two level costs; it must still count as fully filled.
        assert (0.1 + 0.2) - 0.1 - 0.2 > 0  # residue exists in binary float
        book = OrderBook(
            bids=[OrderBookLevel(price=0.05, size=1.0)],
            asks=[
                OrderBookLevel(price=0.1, size=1.0),
                OrderBookLevel(price=0.2, size=1.0),
            ],
        )
        result = simulate_buy_fill(book, 0.1 + 0.2, 0, "fok")

        assert result.filled is True
        assert result.is_partial is False
        assert result.total_shares == pytest.approx(2.0)
        assert result.levels_filled == 2

    def test_sell_exact_depth_float_noise(self) -> None:
        # Same residue on the share count: 0.1 + 0.2 sold into bids of
        # exactly 0.1 and 0.2 shares.
        assert (0.1 + 0.2) - 0.1 - 0.2 > 0
        book = OrderBook(
            bids=[
                OrderBookLevel(price=0.5, size=0.1),
                OrderBookLevel(price=0.4, size=0.2),
            ],
            asks=[OrderBookLevel(price=0.9, size=1.0)],
        )
        result = simulate_sell_fill(book, 0.1 + 0.2, 0, "fok")

        assert result.filled is True
        assert result.is_partial is False
        assert result.total_shares == pytest.approx(0.3)

    def test_residue_does_not_consume_extra_level(self) -> None:
        # A sub-epsilon residue must not spawn a phantom micro-fill on the
        # next level: only the two real levels are consumed.
        book = OrderBook(
            bids=[OrderBookLevel(price=0.05, size=1.0)],
            asks=[
                OrderBookLevel(price=0.1, size=1.0),
                OrderBookLevel(price=0.2, size=1.0),
                OrderBookLevel(price=0.9, size=100.0),
            ],
        )
        result = simulate_buy_fill(book, 0.1 + 0.2, 0, "fok")

        assert result.filled is True
        assert result.levels_filled == 2
        assert len(result.fills) == 2

    @pytest.mark.parametrize("delta,expected_filled", [
        (5e-10, True),   # shortfall within FILL_EPSILON: tolerated as noise
        (2e-9, False),   # shortfall beyond FILL_EPSILON: genuine rejection
    ])
    def test_buy_fok_deficit_around_epsilon(
        self, delta: float, expected_filled: bool
    ) -> None:
        # The tolerance cuts both ways: an order exceeding book depth by
        # less than FILL_EPSILON still fills; anything more is rejected.
        book = OrderBook(
            bids=[OrderBookLevel(price=0.05, size=1.0)],
            asks=[OrderBookLevel(price=0.5, size=1.0)],
        )
        result = simulate_buy_fill(book, 0.5 + delta, 0, "fok")

        assert result.filled is expected_filled
        # A rejected FOK kills the whole order: nothing is consumed.
        if not expected_filled:
            assert result.total_shares == 0.0

    @pytest.mark.parametrize("delta,expected_filled", [
        (5e-10, True),
        (2e-9, False),
    ])
    def test_sell_fok_deficit_around_epsilon(
        self, delta: float, expected_filled: bool
    ) -> None:
        book = OrderBook(
            bids=[OrderBookLevel(price=0.5, size=1.0)],
            asks=[OrderBookLevel(price=0.9, size=1.0)],
        )
        result = simulate_sell_fill(book, 1.0 + delta, 0, "fok")

        assert result.filled is expected_filled
        if not expected_filled:
            assert result.total_shares == 0.0


# =========================================================================
# FEE TESTS
# =========================================================================

class TestFeeZeroBps:
    """fee_rate_bps=0 always produces zero fee."""

    def test_zero_fee(self) -> None:
        assert calculate_fee(0, 0.50, 100.0) == 0.0
        assert calculate_fee(0, 0.65, 50.0) == 0.0
        assert calculate_fee(0, 0.99, 1000.0) == 0.0


class TestFeeAtMidpoint:
    """Price=0.50 gives maximum fee (min(0.5, 0.5) = 0.5)."""

    def test_midpoint_fee(self) -> None:
        # 200 bps at price 0.50, size $100
        # fee = (200/10000) * min(0.50, 0.50) * 100 = 0.02 * 0.50 * 100 = 1.00
        fee = calculate_fee(200, 0.50, 100.0)
        assert fee == pytest.approx(1.00)


class TestFeeAtExtreme:
    """Price=0.95 gives near-zero fee (min(0.95, 0.05) = 0.05)."""

    def test_extreme_fee(self) -> None:
        # 200 bps at price 0.95, size $100
        # fee = (200/10000) * min(0.95, 0.05) * 100 = 0.02 * 0.05 * 100 = 0.10
        fee = calculate_fee(200, 0.95, 100.0)
        assert fee == pytest.approx(0.10)


class TestFee200BpsBuy:
    """200 bps on a $100 buy at price 0.65."""

    def test_exact_calculation(self) -> None:
        # fee = (200/10000) * min(0.65, 0.35) * 100 = 0.02 * 0.35 * 100 = 0.70
        fee = calculate_fee(200, 0.65, 100.0)
        assert fee == pytest.approx(0.70)

    def test_buy_with_fee(self, multi_level_book: OrderBook) -> None:
        # $100 buy with 200 bps fee
        result = simulate_buy_fill(multi_level_book, 100.0, fee_rate_bps=200)

        # avg_price ~0.66468, so min(0.66468, 0.33532) = 0.33532
        # fee = 0.02 * 0.33532 * 100 = 0.67064
        expected_fee = (200 / 10_000) * min(result.avg_price, 1 - result.avg_price) * 100.0
        assert result.fee == pytest.approx(expected_fee)
        assert result.fee > 0


class TestFee175BpsSell:
    """175 bps on a 100-share sell at price 0.64."""

    def test_exact_calculation(self) -> None:
        # fee = (175/10000) * min(0.64, 0.36) * 100 = 0.0175 * 0.36 * 100 = 0.63
        fee = calculate_fee(175, 0.64, 100.0)
        assert fee == pytest.approx(0.63)

    def test_sell_with_fee(self, multi_level_book: OrderBook) -> None:
        # Sell 100 shares with 175 bps fee
        result = simulate_sell_fill(multi_level_book, 100.0, fee_rate_bps=175)

        # 100 shares fills entirely at bid level 1 (price=0.64, size=150)
        # avg_price = 0.64, fee = 0.0175 * min(0.64, 0.36) * 100 = 0.63
        assert result.avg_price == pytest.approx(0.64)
        assert result.fee == pytest.approx(0.63)


class TestFeeMinimum:
    """Minimum fee of 0.0001 is enforced when fee_rate_bps > 0."""

    def test_very_small_trade(self) -> None:
        # Tiny trade: 1 bps at price 0.50, size 0.001
        # fee = (1/10000) * 0.50 * 0.001 = 0.00000005 -> clamped to 0.0001
        fee = calculate_fee(1, 0.50, 0.001)
        assert fee == pytest.approx(0.0001)


class TestFeeSymmetry:
    """Fee at price p equals fee at price (1-p)."""

    def test_symmetric_prices(self) -> None:
        fee_low = calculate_fee(200, 0.30, 100.0)
        fee_high = calculate_fee(200, 0.70, 100.0)
        assert fee_low == pytest.approx(fee_high)

    def test_symmetric_at_various_prices(self) -> None:
        for price in [0.10, 0.20, 0.35, 0.45, 0.50]:
            fee_a = calculate_fee(150, price, 50.0)
            fee_b = calculate_fee(150, 1.0 - price, 50.0)
            assert fee_a == pytest.approx(fee_b), f"Asymmetric fee at price={price}"


# =========================================================================
# OFFICIAL feeSchedule CURVE
# =========================================================================
#
# fee = C × feeRate × p × (1 - p), C = shares, p = price — verbatim from
# https://docs.polymarket.com/trading/fees.  Every expected value below is
# copied from that page's "Fee Tables (100 Shares)" tabs (C = 100).


class TestFeeScheduleDocsTable:
    """The curve reproduces the published per-category fee table."""

    @pytest.mark.parametrize(
        "rate, price, expected",
        [
            # Crypto (rate 0.07)
            (0.07, 0.01, 0.07),
            (0.07, 0.05, 0.33),
            (0.07, 0.10, 0.63),
            (0.07, 0.20, 1.12),
            (0.07, 0.30, 1.47),
            (0.07, 0.45, 1.73),
            (0.07, 0.50, 1.75),
            (0.07, 0.70, 1.47),
            (0.07, 0.99, 0.07),
            # Sports (rate 0.05)
            (0.05, 0.05, 0.24),
            (0.05, 0.10, 0.45),
            (0.05, 0.30, 1.05),
            (0.05, 0.50, 1.25),
            (0.05, 0.90, 0.45),
            # Finance / Politics / Mentions / Tech (rate 0.04)
            (0.04, 0.10, 0.36),
            (0.04, 0.30, 0.84),
            (0.04, 0.45, 0.99),
            (0.04, 0.50, 1.00),
        ],
    )
    def test_docs_table_100_shares(
        self, rate: float, price: float, expected: float,
    ) -> None:
        # The published table shows USDC to the cent, so allow half a cent.
        assert calculate_fee_schedule(rate, 1, price, 100.0) == pytest.approx(
            expected, abs=5e-3
        )

    def test_symmetric_around_half(self) -> None:
        """p and 1-p incur the same dollar fee (docs: symmetric at 50%)."""
        for price in [0.05, 0.20, 0.35, 0.45]:
            assert calculate_fee_schedule(
                0.07, 1, price, 100.0
            ) == pytest.approx(calculate_fee_schedule(0.07, 1, 1 - price, 100.0))

    def test_charged_on_shares_not_notional(self) -> None:
        """C is the share count: 100 shares at 0.50 pays 1.75, not 0.875."""
        assert calculate_fee_schedule(0.07, 1, 0.50, 100.0) == pytest.approx(1.75)
        # The USD notional here is 50; charging that instead would give 0.875.
        assert calculate_fee_schedule(0.07, 1, 0.50, 100.0) != pytest.approx(0.875)


class TestFeeScheduleEdges:
    """Boundary prices, dead inputs, exponent variants, 5-dp rounding."""

    @pytest.mark.parametrize("price", [0.0, 1.0])
    def test_degenerate_prices_are_free(self, price: float) -> None:
        assert calculate_fee_schedule(0.07, 1, price, 100.0) == 0.0

    @pytest.mark.parametrize(
        "rate, exponent, shares",
        [(0.0, 1, 100.0), (-0.01, 1, 100.0), (0.07, 1, 0.0), (0.07, 1, -5.0)],
    )
    def test_free_when_no_fee_inputs(
        self, rate: float, exponent: float, shares: float,
    ) -> None:
        assert calculate_fee_schedule(rate, exponent, 0.50, shares) == 0.0

    def test_exponent_identity_matches_docs_formula(self) -> None:
        """exponent=1 drops out of the published formula."""
        expected = 100 * 0.07 * 0.30 * 0.70
        assert calculate_fee_schedule(0.07, 1, 0.30, 100.0) == pytest.approx(expected)

    def test_exponent_zero_flattens_the_price_component(self) -> None:
        """(p(1-p))**0 == 1, so the fee is shares × rate anywhere."""
        assert calculate_fee_schedule(0.05, 0, 0.10, 100.0) == pytest.approx(5.0)
        assert calculate_fee_schedule(0.05, 0, 0.90, 100.0) == pytest.approx(5.0)

    def test_exponent_two_squares_the_price_component(self) -> None:
        expected = 100 * 0.05 * (0.30 * 0.70) ** 2
        assert calculate_fee_schedule(0.05, 2, 0.30, 100.0) == pytest.approx(expected)

    def test_rounded_to_five_decimals(self) -> None:
        # 100 × 0.07 × 0.05 × 0.95 = 0.3325 exactly
        assert calculate_fee_schedule(0.07, 1, 0.05, 100.0) == 0.33250

    def test_smallest_charged_fee_is_0_00001(self) -> None:
        """Anything smaller than 0.00001 rounds to zero ("Fee Precision")."""
        # 0.0002 × 0.07 × 0.5 × 0.5 = 0.0000035 → below the smallest charge
        assert calculate_fee_schedule(0.07, 1, 0.50, 0.0002) == 0.0
        # 0.001 × 0.07 × 0.5 × 0.5 = 0.0000175 → rounds to 0.00002
        assert calculate_fee_schedule(0.07, 1, 0.50, 0.001) == pytest.approx(0.00002)


class TestFillSimUsesScheduleFee:
    """simulate_*_fill charges the official curve when fee_rate is given."""

    def test_buy_charges_on_shares(self, low_price_book: OrderBook) -> None:
        """$100 at 0.30 buys 333.33 shares; the fee uses C, not the $100."""
        result = simulate_buy_fill(
            low_price_book, 100.0, fee_rate_bps=0, fee_rate=0.07,
        )
        assert result.avg_price == pytest.approx(0.30)
        assert result.total_shares == pytest.approx(100.0 / 0.30)
        # C × rate × p × (1-p) = 333.333 × 0.07 × 0.30 × 0.70 = 4.90
        assert result.fee == pytest.approx(100.0 / 0.30 * 0.07 * 0.30 * 0.70)
        # Charging the USD notional instead would give 2.10
        assert result.fee != pytest.approx(0.07 * 0.30 * 100.0)

    def test_sell_charges_on_shares(self, multi_level_book: OrderBook) -> None:
        result = simulate_sell_fill(
            multi_level_book, 100.0, fee_rate_bps=0, fee_rate=0.07,
        )
        expected = calculate_fee_schedule(
            0.07, 1, result.avg_price, result.total_shares,
        )
        assert result.fee == pytest.approx(expected, abs=1e-9)

    def test_fee_rate_takes_precedence_over_bps(self) -> None:
        book = OrderBook(
            bids=[OrderBookLevel(price=0.50, size=100.0)],
            asks=[OrderBookLevel(price=0.50, size=100.0)],
        )
        # The legacy 200 bps model would charge the $50 notional: 0.50
        result = simulate_buy_fill(book, 50.0, fee_rate_bps=200, fee_rate=0.04)
        # 100 shares × 0.04 × 0.5 × 0.5 = 1.00
        assert result.total_shares == pytest.approx(100.0)
        assert result.fee == pytest.approx(100 * 0.04 * 0.25)

    def test_fallback_to_bps_when_no_fee_rate(self) -> None:
        book = OrderBook(
            bids=[OrderBookLevel(price=0.50, size=100.0)],
            asks=[OrderBookLevel(price=0.50, size=100.0)],
        )
        result = simulate_buy_fill(book, 50.0, fee_rate_bps=200)
        # Legacy model charges the USD notional: 0.02 * 0.5 * 50 = 0.50
        assert result.fee == pytest.approx(0.50)


class TestPerLevelScheduleFee:
    """feeSchedule fees are charged per match (per level), not at the VWAP."""

    def test_buy_sums_per_level(self) -> None:
        # 10 sh @0.60 + 20 sh @0.70 = $20.00 for 30 shares (avg 0.6667).
        # Per-level: round(10*0.07*0.6*0.4, 5) + round(20*0.07*0.7*0.3, 5)
        #          = 0.168 + 0.294 = 0.462
        # Avg-based (old behavior): 30*0.07*(2/3)*(1/3) = 0.46667.
        book = OrderBook(
            bids=[OrderBookLevel(price=0.50, size=1.0)],
            asks=[
                OrderBookLevel(price=0.60, size=10.0),
                OrderBookLevel(price=0.70, size=20.0),
            ],
        )
        result = simulate_buy_fill(book, 20.0, fee_rate_bps=0, fee_rate=0.07)

        assert result.levels_filled == 2
        assert result.fee == pytest.approx(0.462)
        assert result.fee != pytest.approx(0.46667)

    def test_sell_sums_per_level(self) -> None:
        # Mirror image: 20 sh @0.70 + 10 sh @0.60 = same 0.462 per-level fee.
        book = OrderBook(
            bids=[
                OrderBookLevel(price=0.70, size=20.0),
                OrderBookLevel(price=0.60, size=10.0),
            ],
            asks=[OrderBookLevel(price=0.90, size=1.0)],
        )
        result = simulate_sell_fill(book, 30.0, fee_rate_bps=0, fee_rate=0.07)

        assert result.levels_filled == 2
        assert result.fee == pytest.approx(0.462)
        assert result.fee != pytest.approx(0.46667)

    def test_legacy_bps_stays_avg_based_buy(self) -> None:
        # The legacy bps fallback did NOT switch to per-level summation: it
        # still charges once at the average price on the USD notional.
        book = OrderBook(
            bids=[OrderBookLevel(price=0.50, size=1.0)],
            asks=[
                OrderBookLevel(price=0.60, size=10.0),
                OrderBookLevel(price=0.70, size=20.0),
            ],
        )
        result = simulate_buy_fill(book, 20.0, fee_rate_bps=200)

        assert result.levels_filled == 2
        assert result.fee == pytest.approx(
            calculate_fee(200, result.avg_price, result.total_cost)
        )

    def test_legacy_bps_stays_avg_based_sell(self) -> None:
        # Same pin on the sell path: legacy_size is the share count.
        book = OrderBook(
            bids=[
                OrderBookLevel(price=0.70, size=20.0),
                OrderBookLevel(price=0.60, size=10.0),
            ],
            asks=[OrderBookLevel(price=0.90, size=1.0)],
        )
        result = simulate_sell_fill(book, 30.0, fee_rate_bps=200)

        assert result.levels_filled == 2
        assert result.fee == pytest.approx(
            calculate_fee(200, result.avg_price, result.total_shares)
        )

    def test_buy_partial_last_level_fee(self) -> None:
        # $10 cuts level 2 mid-way: 10 sh @0.60 ($6) + 4/0.7 sh @0.70 ($4).
        # The per-level fee uses the actual partial share count:
        # round(10*0.07*0.24, 5) + round((4/0.7)*0.07*0.21, 5) = 0.252.
        book = OrderBook(
            bids=[OrderBookLevel(price=0.50, size=1.0)],
            asks=[
                OrderBookLevel(price=0.60, size=10.0),
                OrderBookLevel(price=0.70, size=20.0),
            ],
        )
        result = simulate_buy_fill(book, 10.0, fee_rate_bps=0, fee_rate=0.07)

        assert result.levels_filled == 2
        assert result.fills[1].shares == pytest.approx(4.0 / 0.7)
        assert result.fee == pytest.approx(0.252)


# =========================================================================
# MATH VERIFICATION TESTS
# =========================================================================

class TestBuyTotalCostMatchesSumOfFills:
    """Sum of per-level fill costs must equal total_cost."""

    def test_single_level(self, simple_book: OrderBook) -> None:
        result = simulate_buy_fill(simple_book, 50.0, fee_rate_bps=0)
        assert sum(f.cost for f in result.fills) == pytest.approx(result.total_cost)

    def test_multi_level(self, multi_level_book: OrderBook) -> None:
        result = simulate_buy_fill(multi_level_book, 100.0, fee_rate_bps=0)
        assert sum(f.cost for f in result.fills) == pytest.approx(result.total_cost)

    def test_three_levels(self, multi_level_book: OrderBook) -> None:
        # Enough to cross 3 levels:
        # L1: 80 * 0.66 = 52.80
        # L2: 120 * 0.67 = 80.40
        # L3: partial at 0.68
        # Total L1+L2 = 133.20, so $200 fills into L3
        result = simulate_buy_fill(multi_level_book, 200.0, fee_rate_bps=0)
        assert sum(f.cost for f in result.fills) == pytest.approx(result.total_cost)


class TestSellTotalSharesMatchesSumOfFills:
    """Sum of per-level fill shares must equal total_shares."""

    def test_single_level(self, multi_level_book: OrderBook) -> None:
        result = simulate_sell_fill(multi_level_book, 50.0, fee_rate_bps=0)
        assert sum(f.shares for f in result.fills) == pytest.approx(result.total_shares)

    def test_multi_level(self, multi_level_book: OrderBook) -> None:
        result = simulate_sell_fill(multi_level_book, 200.0, fee_rate_bps=0)
        assert sum(f.shares for f in result.fills) == pytest.approx(result.total_shares)

    def test_three_levels(self, multi_level_book: OrderBook) -> None:
        # 150 + 200 + some from level 3
        result = simulate_sell_fill(multi_level_book, 400.0, fee_rate_bps=0)
        assert sum(f.shares for f in result.fills) == pytest.approx(result.total_shares)


class TestBuySharesEqualsCostDivAvgPrice:
    """total_shares must equal total_cost / avg_price."""

    def test_single_level(self, simple_book: OrderBook) -> None:
        result = simulate_buy_fill(simple_book, 50.0, fee_rate_bps=0)
        assert result.total_shares == pytest.approx(result.total_cost / result.avg_price)

    def test_multi_level(self, multi_level_book: OrderBook) -> None:
        result = simulate_buy_fill(multi_level_book, 100.0, fee_rate_bps=0)
        assert result.total_shares == pytest.approx(result.total_cost / result.avg_price)

    def test_large_order(self, multi_level_book: OrderBook) -> None:
        result = simulate_buy_fill(multi_level_book, 300.0, fee_rate_bps=0)
        assert result.total_shares == pytest.approx(result.total_cost / result.avg_price)


class TestSellCostEqualsSharesTimesAvgPrice:
    """total_cost (proceeds) must equal total_shares * avg_price."""

    def test_single_level(self, multi_level_book: OrderBook) -> None:
        result = simulate_sell_fill(multi_level_book, 50.0, fee_rate_bps=0)
        assert result.total_cost == pytest.approx(result.total_shares * result.avg_price)

    def test_multi_level(self, multi_level_book: OrderBook) -> None:
        result = simulate_sell_fill(multi_level_book, 200.0, fee_rate_bps=0)
        assert result.total_cost == pytest.approx(result.total_shares * result.avg_price)


class TestFillLevelConsistency:
    """Each fill's cost must equal price * shares."""

    def test_buy_fill_level_math(self, multi_level_book: OrderBook) -> None:
        result = simulate_buy_fill(multi_level_book, 200.0, fee_rate_bps=0)
        for fill in result.fills:
            assert fill.cost == pytest.approx(fill.price * fill.shares)

    def test_sell_fill_level_math(self, multi_level_book: OrderBook) -> None:
        result = simulate_sell_fill(multi_level_book, 300.0, fee_rate_bps=0)
        for fill in result.fills:
            assert fill.cost == pytest.approx(fill.price * fill.shares)


# =========================================================================
# EDGE CASE TESTS
# =========================================================================

class TestUnsortedBookLevels:
    """Verify the engine sorts book levels correctly regardless of input order."""

    def test_unsorted_asks(self) -> None:
        book = OrderBook(
            bids=[OrderBookLevel(price=0.64, size=100.0)],
            asks=[
                OrderBookLevel(price=0.70, size=100.0),
                OrderBookLevel(price=0.66, size=50.0),
                OrderBookLevel(price=0.68, size=80.0),
            ],
        )
        result = simulate_buy_fill(book, 30.0, fee_rate_bps=0)
        # Should fill at lowest ask (0.66) first
        assert result.fills[0].price == pytest.approx(0.66)

    def test_unsorted_bids(self) -> None:
        book = OrderBook(
            bids=[
                OrderBookLevel(price=0.60, size=100.0),
                OrderBookLevel(price=0.64, size=50.0),
                OrderBookLevel(price=0.62, size=80.0),
            ],
            asks=[OrderBookLevel(price=0.66, size=100.0)],
        )
        result = simulate_sell_fill(book, 30.0, fee_rate_bps=0)
        # Should fill at highest bid (0.64) first
        assert result.fills[0].price == pytest.approx(0.64)


class TestBidsOnlyBook:
    """Book with only bids — buy should fail, sell should work."""

    def test_buy_fails(self) -> None:
        book = OrderBook(
            bids=[OrderBookLevel(price=0.64, size=100.0)],
            asks=[],
        )
        result = simulate_buy_fill(book, 50.0, fee_rate_bps=0)
        assert result.filled is False

    def test_sell_works(self) -> None:
        book = OrderBook(
            bids=[OrderBookLevel(price=0.64, size=100.0)],
            asks=[],
        )
        result = simulate_sell_fill(book, 50.0, fee_rate_bps=0)
        assert result.filled is True
        assert result.total_shares == pytest.approx(50.0)
        # Slippage is 0.0: filled at the touch and no midpoint can be calculated
        assert result.slippage_bps == pytest.approx(0.0)
        assert result.slippage_bps_midpoint == pytest.approx(0.0)


class TestAsksOnlyBook:
    """Book with only asks — sell should fail, buy should work."""

    def test_sell_fails(self) -> None:
        book = OrderBook(
            bids=[],
            asks=[OrderBookLevel(price=0.66, size=100.0)],
        )
        result = simulate_sell_fill(book, 50.0, fee_rate_bps=0)
        assert result.filled is False

    def test_buy_works(self) -> None:
        book = OrderBook(
            bids=[],
            asks=[OrderBookLevel(price=0.66, size=100.0)],
        )
        result = simulate_buy_fill(book, 50.0, fee_rate_bps=0)
        assert result.filled is True
        assert result.total_shares == pytest.approx(50.0 / 0.66)
        # Slippage is 0.0: filled at the touch and no midpoint can be calculated
        assert result.slippage_bps == pytest.approx(0.0)
        assert result.slippage_bps_midpoint == pytest.approx(0.0)


class TestBestQuoteHelpers:
    """_best_ask / _best_bid return the touch price or None on empty sides."""

    def test_best_ask_empty(self) -> None:
        assert _best_ask(OrderBook(asks=[], bids=[])) is None

    def test_best_bid_empty(self) -> None:
        assert _best_bid(OrderBook(bids=[], asks=[])) is None

    def test_best_ask_is_lowest(self) -> None:
        book = OrderBook(
            bids=[],
            asks=[
                OrderBookLevel(price=0.70, size=100.0),
                OrderBookLevel(price=0.60, size=50.0),
                OrderBookLevel(price=0.65, size=80.0),
            ],
        )
        assert _best_ask(book) == pytest.approx(0.60)

    def test_best_bid_is_highest(self) -> None:
        book = OrderBook(
            bids=[
                OrderBookLevel(price=0.60, size=100.0),
                OrderBookLevel(price=0.64, size=50.0),
                OrderBookLevel(price=0.62, size=80.0),
            ],
            asks=[],
        )
        assert _best_bid(book) == pytest.approx(0.64)


class TestSlippageQuoteVsMidpointDivergence:
    """The two metrics must diverge when book shape is asymmetric."""

    def test_asymmetric_book_buy(self) -> None:
        # Crossed book is intentional: best ask 0.60 sits below the lone bid
        # 0.70, giving midpoint = (0.70 + 0.60) / 2 = 0.65 exactly.
        # $65 consumes both ask levels fully (50*0.60 + 50*0.70 = 65),
        # avg_price = 0.65.
        book = OrderBook(
            bids=[OrderBookLevel(price=0.70, size=100.0)],
            asks=[
                OrderBookLevel(price=0.60, size=50.0),
                OrderBookLevel(price=0.70, size=50.0),
            ],
        )
        result = simulate_buy_fill(book, 65.0, fee_rate_bps=0)

        assert result.filled is True
        assert result.avg_price == pytest.approx(0.65)
        # vs touch: (0.65 - 0.60) / 0.60 * 10000 = 833.33...
        assert result.slippage_bps == pytest.approx(
            (0.65 - 0.60) / 0.60 * 10_000
        )
        # vs midpoint: (0.65 - 0.65) / 0.65 * 10000 = 0 (float noise ~1e-12)
        assert result.slippage_bps_midpoint == pytest.approx(0.0, abs=1e-9)
        # The metrics MUST differ on this book.
        assert result.slippage_bps != pytest.approx(result.slippage_bps_midpoint)


class TestZeroPriceSlippageEdges:
    """A zero-price touch falls back to 0.0 quote slippage."""

    def test_buy_zero_price_ask(self) -> None:
        # Ask priced at 0.0 costs nothing; FAK fills it, then best_ask=0.0
        # is falsy -> the else branch sets slippage_bps = 0.0.
        book = OrderBook(
            bids=[OrderBookLevel(price=0.01, size=100.0)],
            asks=[OrderBookLevel(price=0.0, size=50.0)],
        )
        result = simulate_buy_fill(book, 1.0, fee_rate_bps=0, order_type="fak")

        assert result.is_partial is True
        assert result.slippage_bps == pytest.approx(0.0)

    def test_sell_zero_price_bid(self) -> None:
        # Bid priced at 0.0: the fill proceeds but best_bid=0.0 is falsy
        # -> the else branch sets slippage_bps = 0.0.
        book = OrderBook(
            bids=[OrderBookLevel(price=0.0, size=100.0)],
            asks=[OrderBookLevel(price=0.99, size=100.0)],
        )
        result = simulate_sell_fill(book, 50.0, fee_rate_bps=0)

        assert result.filled is True
        assert result.slippage_bps == pytest.approx(0.0)


class TestZeroAmountBuy:
    """Buying $0 should produce an empty result."""

    def test_zero_amount(self, simple_book: OrderBook) -> None:
        result = simulate_buy_fill(simple_book, 0.0, fee_rate_bps=0)
        assert result.total_cost == pytest.approx(0.0)
        assert result.total_shares == pytest.approx(0.0)
        assert result.levels_filled == 0


class TestDesignDocExample:
    """Reproduce the exact example from the design doc, section 5."""

    def test_buy_example(self) -> None:
        # Design doc: Buy $100 of YES token
        # Level 1: 0.66 x 80 shares = $52.80
        # Level 2: 0.67 x 70.45 shares = $47.20
        # Total: 150.45 shares for $100.00
        # Avg price: 100 / 150.45 = 0.6647
        book = OrderBook(
            bids=[OrderBookLevel(price=0.64, size=150.0)],
            asks=[
                OrderBookLevel(price=0.66, size=80.0),
                OrderBookLevel(price=0.67, size=120.0),
            ],
        )
        result = simulate_buy_fill(book, 100.0, fee_rate_bps=0)

        remaining_after_l1 = 100.0 - (80.0 * 0.66)
        shares_l2 = remaining_after_l1 / 0.67
        total_shares = 80.0 + shares_l2

        assert result.total_cost == pytest.approx(100.0)
        assert result.total_shares == pytest.approx(total_shares)
        assert result.avg_price == pytest.approx(100.0 / total_shares)

    def test_sell_example(self) -> None:
        # Design doc: Sell 100 shares
        # Level 1: 0.64 x 60 shares = $38.40
        # Level 2: 0.63 x 40 shares = $25.20
        # Total: $63.60 for 100 shares
        # Avg price: 63.60 / 100 = 0.636
        book = OrderBook(
            bids=[
                OrderBookLevel(price=0.64, size=60.0),
                OrderBookLevel(price=0.63, size=200.0),
            ],
            asks=[OrderBookLevel(price=0.66, size=100.0)],
        )
        result = simulate_sell_fill(book, 100.0, fee_rate_bps=0)

        assert result.total_shares == pytest.approx(100.0)
        assert result.total_cost == pytest.approx(63.60)
        assert result.avg_price == pytest.approx(0.636)

    def test_fee_example_200bps(self) -> None:
        # Design doc: 200bps market at avg_price 0.6647
        # fee = 0.02 * min(0.6647, 0.3353) * 100 = 0.02 * 0.3353 * 100 = 0.6706
        book = OrderBook(
            bids=[OrderBookLevel(price=0.64, size=150.0)],
            asks=[
                OrderBookLevel(price=0.66, size=80.0),
                OrderBookLevel(price=0.67, size=120.0),
            ],
        )
        result = simulate_buy_fill(book, 100.0, fee_rate_bps=200)

        expected_fee = (200 / 10_000) * min(result.avg_price, 1.0 - result.avg_price) * 100.0
        assert result.fee == pytest.approx(expected_fee)
