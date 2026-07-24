"""Unit tests for trading strategies — pure decision logic."""

import pytest
from src.quant.indicators import MomentumScore
from src.trading.stockarena_client import Position
from src.trading.strategies import (
    Action,
    AdxTrend,
    BollingerReversion,
    Breakout,
    CrossAsset,
    DipBuyer,
    DonchianBreakout,
    DoubleSeven,
    DualMomentum,
    FiftyTwoWeekHigh,
    GapMomentum,
    GoldenCross,
    KeltnerBreakout,
    LowVolTrend,
    MACDCross,
    MeanReversion,
    MomentumDaily,
    MultiTimeframe,
    PullbackSMA50,
    RSI2Reversion,
    Seasonality,
    SectorRotation,
    StrategyContext,
    Supertrend,
    TrendFollowing,
    TSMomentum,
    VolContraction,
    VolTargetTrend,
    default_strategies,
    experimental_strategies,
)


def make_context(
    price: float = 100.0,
    rsi: float = 50.0,
    rsi_signal: str = "NEUTRAL",
    sma20: float | None = None,
    sma50: float | None = 95.0,
    sma100: float | None = None,
    sma200: float | None = 90.0,
    volume_spike: bool = False,
    support: float | None = 90.0,
    resistance: float | None = 110.0,
    high_52w: float = 120.0,
    momentum: int | None = 50,
    weekly_bullish: bool | None = True,
    monthly_bullish: bool | None = True,
    position: Position | None = None,
    prev_close: float | None = None,
    macd_hist: float | None = None,
    macd_hist_prev: float | None = None,
    rsi_2: float | None = None,
    sma5: float | None = None,
    bb_lower: float | None = None,
    bb_middle: float | None = None,
    donchian_high_20: float | None = None,
    donchian_low_10: float | None = None,
    ret_1m: float | None = None,
    ret_6m: float | None = None,
    ret_12m: float | None = None,
    low_7d_close: float | None = None,
    high_7d_close: float | None = None,
    sma_50_prev: float | None = None,
    sma_200_prev: float | None = None,
    bb_upper: float | None = None,
    vol_20d: float | None = None,
    ema20: float | None = None,
    adx_14: float | None = None,
    plus_di: float | None = None,
    minus_di: float | None = None,
    supertrend_dir: float | None = None,
    keltner_upper: float | None = None,
) -> StrategyContext:
    return StrategyContext(
        ticker="TEST",
        signals={
            "price": price,
            "prev_close": prev_close,
            "rsi": rsi,
            "rsi_signal": rsi_signal,
            "volume_spike": volume_spike,
            "macd_histogram": macd_hist,
            "macd_histogram_prev": macd_hist_prev,
            "rsi_2": rsi_2,
            "sma_5": sma5,
            "bb_lower": bb_lower,
            "bb_middle": bb_middle,
            "donchian_high_20": donchian_high_20,
            "donchian_low_10": donchian_low_10,
            "ret_1m": ret_1m,
            "ret_6m": ret_6m,
            "ret_12m": ret_12m,
            "low_7d_close": low_7d_close,
            "high_7d_close": high_7d_close,
            "sma_50_prev": sma_50_prev,
            "sma_200_prev": sma_200_prev,
            "bb_upper": bb_upper,
            "vol_20d": vol_20d,
            "adx_14": adx_14,
            "plus_di": plus_di,
            "minus_di": minus_di,
            "supertrend_dir": supertrend_dir,
            "keltner_upper": keltner_upper,
            "moving_averages": {
                "SMA_20": sma20,
                "SMA_50": sma50,
                "SMA_100": sma100,
                "SMA_200": sma200,
                "EMA_20": ema20,
            },
        },
        fibonacci={
            "nearest_support": support,
            "nearest_resistance": resistance,
            "high_52w": high_52w,
            "low_52w": 80.0,
            "trend": "UPTREND",
        },
        weekly={"interval": "1wk", "macd_bullish": weekly_bullish},
        monthly={"interval": "1mo", "macd_bullish": monthly_bullish},
        momentum=(
            MomentumScore(score=momentum, label="x", emoji="", breakdown={})
            if momentum is not None
            else None
        ),
        position=position,
    )


_HELD = Position(ticker="TEST", quantity=10, avg_price=100.0, current_price=100.0)


# ── MomentumDaily ──────────────────────────────────────────────────────


@pytest.mark.unit
def test_momentum_buys_on_strong_score():
    sig = MomentumDaily().evaluate(make_context(momentum=80))
    assert sig.action == Action.BUY
    assert sig.confidence == pytest.approx(0.8)


@pytest.mark.unit
def test_momentum_sells_held_position_on_collapse():
    sig = MomentumDaily().evaluate(make_context(momentum=20, position=_HELD))
    assert sig.action == Action.SELL


@pytest.mark.unit
def test_momentum_no_sell_without_position():
    sig = MomentumDaily().evaluate(make_context(momentum=20, position=None))
    assert sig.action == Action.HOLD


@pytest.mark.unit
def test_momentum_holds_on_missing_data():
    sig = MomentumDaily().evaluate(make_context(momentum=None))
    assert sig.action == Action.HOLD
    assert "insufficient" in sig.reason


# ── MeanReversion ──────────────────────────────────────────────────────


@pytest.mark.unit
def test_mean_reversion_buys_oversold_near_support():
    ctx = make_context(price=90.5, rsi=25, rsi_signal="OVERSOLD", support=90.0)
    sig = MeanReversion().evaluate(ctx)
    assert sig.action == Action.BUY
    assert sig.confidence > 0.5


@pytest.mark.unit
def test_mean_reversion_ignores_oversold_far_from_support():
    ctx = make_context(price=100.0, rsi=25, rsi_signal="OVERSOLD", support=90.0)
    sig = MeanReversion().evaluate(ctx)
    assert sig.action == Action.HOLD


@pytest.mark.unit
def test_mean_reversion_sells_overbought_position():
    ctx = make_context(rsi=75, rsi_signal="OVERBOUGHT", position=_HELD)
    sig = MeanReversion().evaluate(ctx)
    assert sig.action == Action.SELL


@pytest.mark.unit
def test_mean_reversion_holds_on_missing_rsi():
    ctx = make_context()
    ctx.signals["rsi"] = None
    assert MeanReversion().evaluate(ctx).action == Action.HOLD


# ── TrendFollowing ─────────────────────────────────────────────────────


@pytest.mark.unit
def test_trend_buys_in_confirmed_uptrend():
    ctx = make_context(price=100, sma50=95, sma200=90, weekly_bullish=True)
    sig = TrendFollowing().evaluate(ctx)
    assert sig.action == Action.BUY


@pytest.mark.unit
def test_trend_no_buy_when_weekly_macd_bearish():
    ctx = make_context(price=100, sma50=95, sma200=90, weekly_bullish=False)
    assert TrendFollowing().evaluate(ctx).action == Action.HOLD


@pytest.mark.unit
def test_trend_sells_on_sma200_break():
    ctx = make_context(price=85, sma50=95, sma200=90, position=_HELD)
    sig = TrendFollowing().evaluate(ctx)
    assert sig.action == Action.SELL
    assert "SMA200" in sig.reason


@pytest.mark.unit
def test_trend_holds_on_missing_mas():
    ctx = make_context(sma50=None, sma200=None)
    assert TrendFollowing().evaluate(ctx).action == Action.HOLD


# ── Breakout ───────────────────────────────────────────────────────────


@pytest.mark.unit
def test_breakout_buys_above_resistance_on_volume():
    ctx = make_context(
        price=111, resistance=110, volume_spike=True, monthly_bullish=True
    )
    sig = Breakout().evaluate(ctx)
    assert sig.action == Action.BUY


@pytest.mark.unit
def test_breakout_requires_volume_spike():
    ctx = make_context(
        price=111, resistance=110, volume_spike=False, monthly_bullish=True
    )
    assert Breakout().evaluate(ctx).action == Action.HOLD


@pytest.mark.unit
def test_breakout_buys_at_new_52w_high():
    ctx = make_context(
        price=120,
        resistance=None,
        high_52w=120,
        volume_spike=True,
        monthly_bullish=True,
    )
    assert Breakout().evaluate(ctx).action == Action.BUY


@pytest.mark.unit
def test_breakout_sells_on_support_break():
    ctx = make_context(price=88, support=90, position=_HELD)
    sig = Breakout().evaluate(ctx)
    assert sig.action == Action.SELL


# ── General invariants ─────────────────────────────────────────────────


@pytest.mark.unit
def test_all_strategies_hold_without_price():
    ctx = make_context()
    ctx.signals["price"] = None
    for strategy in default_strategies():
        assert strategy.evaluate(ctx).action == Action.HOLD


@pytest.mark.unit
def test_confidence_always_bounded():
    ctx = make_context(
        price=200, sma50=95, sma200=50, momentum=100, weekly_bullish=True
    )
    for strategy in default_strategies():
        sig = strategy.evaluate(ctx)
        assert 0.0 <= sig.confidence <= 1.0


# ── MACDCross (experimental) ───────────────────────────────────────────


@pytest.mark.unit
def test_macd_cross_buys_on_fresh_bullish_cross():
    sig = MACDCross().evaluate(
        make_context(price=100.0, sma50=95.0, macd_hist=0.4, macd_hist_prev=-0.2)
    )
    assert sig.action == Action.BUY
    assert sig.confidence == pytest.approx(0.65)


@pytest.mark.unit
def test_macd_cross_ignores_stale_positive_histogram():
    sig = MACDCross().evaluate(
        make_context(price=100.0, sma50=95.0, macd_hist=0.4, macd_hist_prev=0.3)
    )
    assert sig.action == Action.HOLD


@pytest.mark.unit
def test_macd_cross_requires_price_above_sma50():
    sig = MACDCross().evaluate(
        make_context(price=90.0, sma50=95.0, macd_hist=0.4, macd_hist_prev=-0.2)
    )
    assert sig.action == Action.HOLD


@pytest.mark.unit
def test_macd_cross_sells_held_position_on_bearish_cross():
    sig = MACDCross().evaluate(
        make_context(macd_hist=-0.3, macd_hist_prev=0.1, position=_HELD)
    )
    assert sig.action == Action.SELL


@pytest.mark.unit
def test_macd_cross_bearish_cross_without_position_holds():
    sig = MACDCross().evaluate(make_context(macd_hist=-0.3, macd_hist_prev=0.1))
    assert sig.action == Action.HOLD


@pytest.mark.unit
def test_macd_cross_fails_closed_without_prev_histogram():
    sig = MACDCross().evaluate(make_context(macd_hist=0.4, macd_hist_prev=None))
    assert sig.action == Action.HOLD


# ── DipBuyer (experimental) ────────────────────────────────────────────


@pytest.mark.unit
def test_dip_buyer_buys_dip_in_uptrend():
    sig = DipBuyer().evaluate(
        make_context(price=100.0, sma20=105.0, sma200=90.0, rsi=40.0)
    )
    assert sig.action == Action.BUY
    assert sig.confidence == pytest.approx(0.55 + (45 - 40) / 100)


@pytest.mark.unit
def test_dip_buyer_no_buy_below_sma200():
    sig = DipBuyer().evaluate(
        make_context(price=85.0, sma20=105.0, sma200=90.0, rsi=40.0)
    )
    assert sig.action == Action.HOLD


@pytest.mark.unit
def test_dip_buyer_no_buy_when_rsi_not_low():
    sig = DipBuyer().evaluate(
        make_context(price=100.0, sma20=105.0, sma200=90.0, rsi=55.0)
    )
    assert sig.action == Action.HOLD


@pytest.mark.unit
def test_dip_buyer_sells_on_recovery():
    sig = DipBuyer().evaluate(
        make_context(price=100.0, sma20=95.0, sma200=90.0, rsi=70.0, position=_HELD)
    )
    assert sig.action == Action.SELL
    assert "recovered" in sig.reason


@pytest.mark.unit
def test_dip_buyer_sells_on_broken_uptrend():
    sig = DipBuyer().evaluate(
        make_context(price=85.0, sma20=95.0, sma200=90.0, rsi=50.0, position=_HELD)
    )
    assert sig.action == Action.SELL
    assert "SMA200" in sig.reason


@pytest.mark.unit
def test_dip_buyer_fails_closed_without_sma20():
    sig = DipBuyer().evaluate(make_context(price=100.0, sma20=None, rsi=40.0))
    assert sig.action == Action.HOLD


# ── GapMomentum (experimental) ─────────────────────────────────────────


@pytest.mark.unit
def test_gap_momentum_buys_confirmed_gap_up():
    sig = GapMomentum().evaluate(
        make_context(
            price=103.0, prev_close=100.0, volume_spike=True, weekly_bullish=True
        )
    )
    assert sig.action == Action.BUY
    assert sig.confidence == pytest.approx(0.6 + 0.3)  # 3% gap → capped bonus


@pytest.mark.unit
def test_gap_momentum_needs_volume_spike():
    sig = GapMomentum().evaluate(
        make_context(
            price=103.0, prev_close=100.0, volume_spike=False, weekly_bullish=True
        )
    )
    assert sig.action == Action.HOLD


@pytest.mark.unit
def test_gap_momentum_needs_weekly_confirmation():
    sig = GapMomentum().evaluate(
        make_context(
            price=103.0, prev_close=100.0, volume_spike=True, weekly_bullish=False
        )
    )
    assert sig.action == Action.HOLD


@pytest.mark.unit
def test_gap_momentum_sells_held_on_panic_gap_down():
    sig = GapMomentum().evaluate(
        make_context(price=96.0, prev_close=100.0, position=_HELD)
    )
    assert sig.action == Action.SELL


@pytest.mark.unit
def test_gap_momentum_fails_closed_without_prev_close():
    sig = GapMomentum().evaluate(make_context(price=103.0, prev_close=None))
    assert sig.action == Action.HOLD


# ── MultiTimeframe (experimental) ──────────────────────────────────────


@pytest.mark.unit
def test_multi_timeframe_buys_when_all_aligned():
    sig = MultiTimeframe().evaluate(
        make_context(
            price=100.0,
            sma100=95.0,
            weekly_bullish=True,
            monthly_bullish=True,
            momentum=60,
        )
    )
    assert sig.action == Action.BUY
    assert sig.confidence == pytest.approx(0.6 + 60 / 500)


@pytest.mark.unit
def test_multi_timeframe_holds_when_monthly_disagrees():
    sig = MultiTimeframe().evaluate(
        make_context(price=100.0, sma100=95.0, monthly_bullish=False, momentum=60)
    )
    assert sig.action == Action.HOLD


@pytest.mark.unit
def test_multi_timeframe_holds_on_weak_momentum():
    sig = MultiTimeframe().evaluate(make_context(price=100.0, sma100=95.0, momentum=40))
    assert sig.action == Action.HOLD


@pytest.mark.unit
def test_multi_timeframe_sells_when_both_timeframes_bearish():
    sig = MultiTimeframe().evaluate(
        make_context(
            price=100.0,
            sma100=95.0,
            weekly_bullish=False,
            monthly_bullish=False,
            position=_HELD,
        )
    )
    assert sig.action == Action.SELL


@pytest.mark.unit
def test_multi_timeframe_sells_below_sma100():
    sig = MultiTimeframe().evaluate(
        make_context(price=90.0, sma100=95.0, position=_HELD)
    )
    assert sig.action == Action.SELL


@pytest.mark.unit
def test_multi_timeframe_fails_closed_without_momentum():
    sig = MultiTimeframe().evaluate(
        make_context(price=100.0, sma100=95.0, momentum=None)
    )
    assert sig.action == Action.HOLD


# ── RSI2Reversion (experimental) ───────────────────────────────────────


@pytest.mark.unit
def test_rsi2_buys_washout_above_sma200():
    sig = RSI2Reversion().evaluate(make_context(price=100.0, sma200=90.0, rsi_2=5.0))
    assert sig.action == Action.BUY
    assert sig.confidence == pytest.approx(0.6 + (10 - 5) / 50)


@pytest.mark.unit
def test_rsi2_no_buy_below_sma200():
    sig = RSI2Reversion().evaluate(make_context(price=85.0, sma200=90.0, rsi_2=5.0))
    assert sig.action == Action.HOLD


@pytest.mark.unit
def test_rsi2_no_buy_when_not_washed_out():
    sig = RSI2Reversion().evaluate(make_context(price=100.0, sma200=90.0, rsi_2=25.0))
    assert sig.action == Action.HOLD


@pytest.mark.unit
def test_rsi2_sells_on_recovery():
    sig = RSI2Reversion().evaluate(
        make_context(price=100.0, sma200=90.0, rsi_2=70.0, position=_HELD)
    )
    assert sig.action == Action.SELL


@pytest.mark.unit
def test_rsi2_sells_on_sma5_reclaim():
    sig = RSI2Reversion().evaluate(
        make_context(price=100.0, sma200=90.0, rsi_2=40.0, sma5=99.0, position=_HELD)
    )
    assert sig.action == Action.SELL
    assert "SMA5" in sig.reason


@pytest.mark.unit
def test_rsi2_fails_closed_without_rsi2():
    sig = RSI2Reversion().evaluate(make_context(price=100.0, sma200=90.0, rsi_2=None))
    assert sig.action == Action.HOLD


# ── BollingerReversion (experimental) ──────────────────────────────────


@pytest.mark.unit
def test_bollinger_buys_below_lower_band_in_uptrend():
    sig = BollingerReversion().evaluate(
        make_context(price=94.0, sma200=90.0, bb_lower=95.0, bb_middle=100.0)
    )
    assert sig.action == Action.BUY
    assert sig.confidence > 0.6


@pytest.mark.unit
def test_bollinger_no_buy_below_sma200():
    sig = BollingerReversion().evaluate(
        make_context(price=85.0, sma200=90.0, bb_lower=95.0, bb_middle=100.0)
    )
    assert sig.action == Action.HOLD


@pytest.mark.unit
def test_bollinger_no_buy_inside_bands():
    sig = BollingerReversion().evaluate(
        make_context(price=97.0, sma200=90.0, bb_lower=95.0, bb_middle=100.0)
    )
    assert sig.action == Action.HOLD


@pytest.mark.unit
def test_bollinger_sells_at_middle_band():
    sig = BollingerReversion().evaluate(
        make_context(
            price=100.5, sma200=90.0, bb_lower=95.0, bb_middle=100.0, position=_HELD
        )
    )
    assert sig.action == Action.SELL


@pytest.mark.unit
def test_bollinger_fails_closed_without_bands():
    sig = BollingerReversion().evaluate(make_context(price=94.0, sma200=90.0))
    assert sig.action == Action.HOLD


# ── DonchianBreakout (experimental) ────────────────────────────────────


@pytest.mark.unit
def test_donchian_buys_channel_break_with_weekly_trend():
    sig = DonchianBreakout().evaluate(
        make_context(
            price=101.0,
            donchian_high_20=100.0,
            donchian_low_10=92.0,
            weekly_bullish=True,
        )
    )
    assert sig.action == Action.BUY


@pytest.mark.unit
def test_donchian_needs_weekly_confirmation():
    sig = DonchianBreakout().evaluate(
        make_context(
            price=101.0,
            donchian_high_20=100.0,
            donchian_low_10=92.0,
            weekly_bullish=False,
        )
    )
    assert sig.action == Action.HOLD


@pytest.mark.unit
def test_donchian_no_buy_inside_channel():
    sig = DonchianBreakout().evaluate(
        make_context(price=99.0, donchian_high_20=100.0, donchian_low_10=92.0)
    )
    assert sig.action == Action.HOLD


@pytest.mark.unit
def test_donchian_sells_on_ten_day_low_break():
    sig = DonchianBreakout().evaluate(
        make_context(
            price=91.0, donchian_high_20=100.0, donchian_low_10=92.0, position=_HELD
        )
    )
    assert sig.action == Action.SELL
    assert "Turtle" in sig.reason


@pytest.mark.unit
def test_donchian_fails_closed_without_channels():
    sig = DonchianBreakout().evaluate(make_context(price=101.0))
    assert sig.action == Action.HOLD


# ── TSMomentum (experimental) ──────────────────────────────────────────


@pytest.mark.unit
def test_ts_momentum_buys_positive_12_1_momentum():
    sig = TSMomentum().evaluate(
        make_context(price=100.0, sma200=90.0, ret_12m=0.25, ret_1m=0.02)
    )
    assert sig.action == Action.BUY
    assert sig.confidence == pytest.approx(0.55 + 0.23)


@pytest.mark.unit
def test_ts_momentum_no_buy_below_sma200():
    sig = TSMomentum().evaluate(
        make_context(price=85.0, sma200=90.0, ret_12m=0.25, ret_1m=0.02)
    )
    assert sig.action == Action.HOLD


@pytest.mark.unit
def test_ts_momentum_no_buy_when_momentum_negative():
    sig = TSMomentum().evaluate(
        make_context(price=100.0, sma200=90.0, ret_12m=-0.05, ret_1m=0.02)
    )
    assert sig.action == Action.HOLD


@pytest.mark.unit
def test_ts_momentum_sells_when_momentum_flips():
    sig = TSMomentum().evaluate(
        make_context(
            price=100.0, sma200=90.0, ret_12m=-0.05, ret_1m=0.02, position=_HELD
        )
    )
    assert sig.action == Action.SELL


@pytest.mark.unit
def test_ts_momentum_fails_closed_without_returns():
    sig = TSMomentum().evaluate(make_context(price=100.0, sma200=90.0, ret_12m=0.25))
    assert sig.action == Action.HOLD


# ── FiftyTwoWeekHigh (experimental) ────────────────────────────────────


@pytest.mark.unit
def test_high_52w_buys_near_high_with_confirmation():
    sig = FiftyTwoWeekHigh().evaluate(
        make_context(price=118.0, high_52w=120.0, ret_6m=0.15, monthly_bullish=True)
    )
    assert sig.action == Action.BUY
    assert sig.confidence == pytest.approx(0.6 + 0.15 / 2)


@pytest.mark.unit
def test_high_52w_no_buy_far_from_high():
    sig = FiftyTwoWeekHigh().evaluate(
        make_context(price=110.0, high_52w=120.0, ret_6m=0.15, monthly_bullish=True)
    )
    assert sig.action == Action.HOLD


@pytest.mark.unit
def test_high_52w_needs_positive_6m_return():
    sig = FiftyTwoWeekHigh().evaluate(
        make_context(price=118.0, high_52w=120.0, ret_6m=-0.02, monthly_bullish=True)
    )
    assert sig.action == Action.HOLD


@pytest.mark.unit
def test_high_52w_sells_on_deep_fade():
    sig = FiftyTwoWeekHigh().evaluate(
        make_context(price=107.0, high_52w=120.0, ret_6m=0.15, position=_HELD)
    )
    assert sig.action == Action.SELL


@pytest.mark.unit
def test_high_52w_sells_below_sma100():
    sig = FiftyTwoWeekHigh().evaluate(
        make_context(
            price=112.0, high_52w=120.0, sma100=115.0, ret_6m=0.15, position=_HELD
        )
    )
    assert sig.action == Action.SELL
    assert "SMA100" in sig.reason


@pytest.mark.unit
def test_high_52w_fails_closed_without_ret_6m():
    sig = FiftyTwoWeekHigh().evaluate(make_context(price=118.0, high_52w=120.0))
    assert sig.action == Action.HOLD


# ── DoubleSeven (experimental) ─────────────────────────────────────────


@pytest.mark.unit
def test_double_seven_buys_7day_low_in_uptrend():
    sig = DoubleSeven().evaluate(
        make_context(price=100.0, sma200=90.0, low_7d_close=100.0, high_7d_close=110.0)
    )
    assert sig.action == Action.BUY


@pytest.mark.unit
def test_double_seven_no_buy_below_sma200():
    sig = DoubleSeven().evaluate(
        make_context(price=85.0, sma200=90.0, low_7d_close=85.0, high_7d_close=95.0)
    )
    assert sig.action == Action.HOLD


@pytest.mark.unit
def test_double_seven_no_buy_mid_range():
    sig = DoubleSeven().evaluate(
        make_context(price=105.0, sma200=90.0, low_7d_close=100.0, high_7d_close=110.0)
    )
    assert sig.action == Action.HOLD


@pytest.mark.unit
def test_double_seven_sells_at_7day_high():
    sig = DoubleSeven().evaluate(
        make_context(
            price=110.0,
            sma200=90.0,
            low_7d_close=100.0,
            high_7d_close=110.0,
            position=_HELD,
        )
    )
    assert sig.action == Action.SELL


@pytest.mark.unit
def test_double_seven_fails_closed_without_extremes():
    sig = DoubleSeven().evaluate(make_context(price=100.0, sma200=90.0))
    assert sig.action == Action.HOLD


# ── PullbackSMA50 (experimental) ───────────────────────────────────────


@pytest.mark.unit
def test_pullback_sma50_buys_at_the_average():
    sig = PullbackSMA50().evaluate(
        make_context(price=100.0, sma50=100.5, sma200=90.0, rsi=42.0)
    )
    assert sig.action == Action.BUY


@pytest.mark.unit
def test_pullback_sma50_no_buy_far_from_average():
    sig = PullbackSMA50().evaluate(
        make_context(price=110.0, sma50=100.0, sma200=90.0, rsi=42.0)
    )
    assert sig.action == Action.HOLD


@pytest.mark.unit
def test_pullback_sma50_no_buy_when_rsi_high():
    sig = PullbackSMA50().evaluate(
        make_context(price=100.0, sma50=100.5, sma200=90.0, rsi=60.0)
    )
    assert sig.action == Action.HOLD


@pytest.mark.unit
def test_pullback_sma50_sells_on_recovery():
    sig = PullbackSMA50().evaluate(
        make_context(price=105.0, sma50=100.0, sma200=90.0, rsi=70.0, position=_HELD)
    )
    assert sig.action == Action.SELL


@pytest.mark.unit
def test_pullback_sma50_sells_on_sma200_break():
    sig = PullbackSMA50().evaluate(
        make_context(price=85.0, sma50=100.0, sma200=90.0, rsi=40.0, position=_HELD)
    )
    assert sig.action == Action.SELL
    assert "SMA200" in sig.reason


# ── GoldenCross (experimental) ─────────────────────────────────────────


@pytest.mark.unit
def test_golden_cross_buys_fresh_cross():
    sig = GoldenCross().evaluate(
        make_context(
            price=100.0, sma50=95.0, sma200=94.0, sma_50_prev=93.0, sma_200_prev=94.0
        )
    )
    assert sig.action == Action.BUY
    assert "golden cross" in sig.reason


@pytest.mark.unit
def test_golden_cross_ignores_stale_cross():
    sig = GoldenCross().evaluate(
        make_context(
            price=100.0, sma50=95.0, sma200=90.0, sma_50_prev=94.0, sma_200_prev=90.0
        )
    )
    assert sig.action == Action.HOLD


@pytest.mark.unit
def test_golden_cross_sells_on_death_cross():
    sig = GoldenCross().evaluate(
        make_context(
            price=88.0,
            sma50=89.0,
            sma200=90.0,
            sma_50_prev=91.0,
            sma_200_prev=90.0,
            position=_HELD,
        )
    )
    assert sig.action == Action.SELL
    assert "death cross" in sig.reason


@pytest.mark.unit
def test_golden_cross_fails_closed_without_prev_smas():
    sig = GoldenCross().evaluate(make_context(price=100.0, sma50=95.0, sma200=94.0))
    assert sig.action == Action.HOLD


# ── VolContraction (experimental) ──────────────────────────────────────


@pytest.mark.unit
def test_vol_contraction_buys_squeeze_breakout():
    sig = VolContraction().evaluate(
        make_context(
            price=103.0,
            bb_upper=102.0,
            bb_middle=100.0,
            bb_lower=98.0,  # width 4% ≤ 6%
            donchian_high_20=102.5,
            donchian_low_10=95.0,
            weekly_bullish=True,
        )
    )
    assert sig.action == Action.BUY
    assert "squeeze" in sig.reason


@pytest.mark.unit
def test_vol_contraction_no_buy_when_bands_wide():
    sig = VolContraction().evaluate(
        make_context(
            price=103.0,
            bb_upper=110.0,
            bb_middle=100.0,
            bb_lower=90.0,  # width 20%
            donchian_high_20=102.5,
            donchian_low_10=95.0,
            weekly_bullish=True,
        )
    )
    assert sig.action == Action.HOLD


@pytest.mark.unit
def test_vol_contraction_needs_channel_break():
    sig = VolContraction().evaluate(
        make_context(
            price=101.0,
            bb_upper=102.0,
            bb_middle=100.0,
            bb_lower=98.0,
            donchian_high_20=102.5,
            donchian_low_10=95.0,
            weekly_bullish=True,
        )
    )
    assert sig.action == Action.HOLD


@pytest.mark.unit
def test_vol_contraction_sells_on_low_break():
    sig = VolContraction().evaluate(
        make_context(
            price=94.0,
            bb_upper=102.0,
            bb_middle=100.0,
            bb_lower=98.0,
            donchian_high_20=102.5,
            donchian_low_10=95.0,
            position=_HELD,
        )
    )
    assert sig.action == Action.SELL


@pytest.mark.unit
def test_vol_contraction_fails_closed_without_bands():
    sig = VolContraction().evaluate(
        make_context(price=103.0, donchian_high_20=102.5, donchian_low_10=95.0)
    )
    assert sig.action == Action.HOLD


# ── LowVolTrend (experimental) ─────────────────────────────────────────


@pytest.mark.unit
def test_low_vol_trend_buys_quiet_uptrend():
    sig = LowVolTrend().evaluate(
        make_context(price=100.0, sma200=90.0, vol_20d=0.15, ret_6m=0.10)
    )
    assert sig.action == Action.BUY


@pytest.mark.unit
def test_low_vol_trend_no_buy_when_volatile():
    sig = LowVolTrend().evaluate(
        make_context(price=100.0, sma200=90.0, vol_20d=0.40, ret_6m=0.10)
    )
    assert sig.action == Action.HOLD


@pytest.mark.unit
def test_low_vol_trend_no_buy_with_negative_6m():
    sig = LowVolTrend().evaluate(
        make_context(price=100.0, sma200=90.0, vol_20d=0.15, ret_6m=-0.05)
    )
    assert sig.action == Action.HOLD


@pytest.mark.unit
def test_low_vol_trend_sells_on_vol_spike():
    sig = LowVolTrend().evaluate(
        make_context(
            price=100.0, sma200=90.0, vol_20d=0.60, ret_6m=0.10, position=_HELD
        )
    )
    assert sig.action == Action.SELL
    assert "spiked" in sig.reason


@pytest.mark.unit
def test_low_vol_trend_sells_on_sma200_break():
    sig = LowVolTrend().evaluate(
        make_context(price=85.0, sma200=90.0, vol_20d=0.15, ret_6m=0.10, position=_HELD)
    )
    assert sig.action == Action.SELL


@pytest.mark.unit
def test_low_vol_trend_fails_closed_without_vol():
    sig = LowVolTrend().evaluate(make_context(price=100.0, sma200=90.0, ret_6m=0.10))
    assert sig.action == Action.HOLD


# ── Supertrend (experimental) ──────────────────────────────────────────


@pytest.mark.unit
def test_supertrend_buys_uptrend_above_sma200():
    sig = Supertrend().evaluate(
        make_context(price=100.0, sma200=90.0, supertrend_dir=1.0)
    )
    assert sig.action == Action.BUY


@pytest.mark.unit
def test_supertrend_no_buy_below_sma200():
    sig = Supertrend().evaluate(
        make_context(price=85.0, sma200=90.0, supertrend_dir=1.0)
    )
    assert sig.action == Action.HOLD


@pytest.mark.unit
def test_supertrend_sells_on_flip_down():
    sig = Supertrend().evaluate(
        make_context(price=100.0, sma200=90.0, supertrend_dir=-1.0, position=_HELD)
    )
    assert sig.action == Action.SELL


@pytest.mark.unit
def test_supertrend_fails_closed_without_direction():
    sig = Supertrend().evaluate(make_context(price=100.0, sma200=90.0))
    assert sig.action == Action.HOLD


# ── AdxTrend (experimental) ────────────────────────────────────────────


@pytest.mark.unit
def test_adx_trend_buys_strong_uptrend():
    sig = AdxTrend().evaluate(
        make_context(price=100.0, sma50=95.0, adx_14=30.0, plus_di=28.0, minus_di=12.0)
    )
    assert sig.action == Action.BUY


@pytest.mark.unit
def test_adx_trend_no_buy_weak_trend():
    sig = AdxTrend().evaluate(
        make_context(price=100.0, sma50=95.0, adx_14=18.0, plus_di=28.0, minus_di=12.0)
    )
    assert sig.action == Action.HOLD


@pytest.mark.unit
def test_adx_trend_no_buy_when_minus_di_leads():
    sig = AdxTrend().evaluate(
        make_context(price=100.0, sma50=95.0, adx_14=30.0, plus_di=12.0, minus_di=28.0)
    )
    assert sig.action == Action.HOLD


@pytest.mark.unit
def test_adx_trend_sells_on_di_flip():
    sig = AdxTrend().evaluate(
        make_context(
            price=100.0,
            sma50=95.0,
            adx_14=30.0,
            plus_di=12.0,
            minus_di=28.0,
            position=_HELD,
        )
    )
    assert sig.action == Action.SELL


@pytest.mark.unit
def test_adx_trend_sells_when_trend_fades():
    sig = AdxTrend().evaluate(
        make_context(
            price=100.0,
            sma50=95.0,
            adx_14=15.0,
            plus_di=20.0,
            minus_di=18.0,
            position=_HELD,
        )
    )
    assert sig.action == Action.SELL


@pytest.mark.unit
def test_adx_trend_fails_closed_without_adx():
    sig = AdxTrend().evaluate(make_context(price=100.0, sma50=95.0))
    assert sig.action == Action.HOLD


# ── KeltnerBreakout (experimental) ─────────────────────────────────────


@pytest.mark.unit
def test_keltner_buys_breakout_with_weekly_trend():
    sig = KeltnerBreakout().evaluate(
        make_context(price=103.0, ema20=100.0, keltner_upper=102.0, weekly_bullish=True)
    )
    assert sig.action == Action.BUY


@pytest.mark.unit
def test_keltner_needs_weekly_confirmation():
    sig = KeltnerBreakout().evaluate(
        make_context(
            price=103.0, ema20=100.0, keltner_upper=102.0, weekly_bullish=False
        )
    )
    assert sig.action == Action.HOLD


@pytest.mark.unit
def test_keltner_no_buy_inside_channel():
    sig = KeltnerBreakout().evaluate(
        make_context(price=101.0, ema20=100.0, keltner_upper=102.0, weekly_bullish=True)
    )
    assert sig.action == Action.HOLD


@pytest.mark.unit
def test_keltner_sells_back_below_ema20():
    sig = KeltnerBreakout().evaluate(
        make_context(price=99.0, ema20=100.0, keltner_upper=102.0, position=_HELD)
    )
    assert sig.action == Action.SELL


@pytest.mark.unit
def test_keltner_fails_closed_without_band():
    sig = KeltnerBreakout().evaluate(make_context(price=103.0, ema20=100.0))
    assert sig.action == Action.HOLD


# ── Live/backtest separation ───────────────────────────────────────────


@pytest.mark.unit
def test_default_strategies_are_the_promoted_eight():
    # Live set promoted 2026-07-09 from the 64-year full-pool backtest.
    # (sector_rotation/cross_asset were briefly promoted 2026-07-23 then
    # reverted — they lowered the walk-forward; they stay experimental.)
    assert [type(s) for s in default_strategies()] == [
        TrendFollowing,
        Breakout,
        DipBuyer,
        GapMomentum,
        MultiTimeframe,
        BollingerReversion,
        TSMomentum,
        FiftyTwoWeekHigh,
    ]


@pytest.mark.unit
def test_experimental_strategies_are_the_backtest_set():
    assert [type(s) for s in experimental_strategies()] == [
        MomentumDaily,
        MeanReversion,
        MACDCross,
        RSI2Reversion,
        DonchianBreakout,
        DoubleSeven,
        PullbackSMA50,
        GoldenCross,
        VolContraction,
        LowVolTrend,
        Supertrend,
        AdxTrend,
        KeltnerBreakout,
        SectorRotation,
        CrossAsset,
        Seasonality,
        DualMomentum,
        VolTargetTrend,
    ]
    # No name collisions across the combined set.
    names = [s.name for s in [*default_strategies(), *experimental_strategies()]]
    assert len(names) == len(set(names)) == 26
