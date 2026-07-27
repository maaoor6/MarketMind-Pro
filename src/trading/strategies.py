"""Trading strategies — pure, deterministic signal generators.

Each strategy inspects a :class:`StrategyContext` (built once per ticker per
cycle from QuantEngine data) and returns a :class:`StrategySignal`. Strategies
never perform I/O; missing data always yields HOLD (fail-closed).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from src.quant.indicators import MomentumScore
from src.trading.stockarena_client import Position


class Action(StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass
class StrategySignal:
    strategy: str
    ticker: str
    action: Action
    confidence: float  # 0.0–1.0
    reason: str
    price: float
    # Annualized 20d realized volatility at decision time (vol-aware sizing).
    volatility: float | None = None
    # ATR as a fraction of price at decision time (equal-dollar-at-risk sizing).
    atr_pct: float | None = None


@dataclass
class StrategyContext:
    """Everything a strategy may inspect for one ticker."""

    ticker: str
    signals: dict  # generate_signals() dict with live price override
    fibonacci: dict | None
    weekly: dict  # analyze_timeframe(ticker, "1wk")
    monthly: dict  # analyze_timeframe(ticker, "1mo")
    momentum: MomentumScore | None
    position: Position | None  # current holding, if any
    # Bar/decision date — set live from the US market date, in the backtest
    # from the bar timestamp. Calendar strategies (Seasonality) read it.
    as_of: date | None = None
    # Cross-sectional context the Orchestrator/backtest injects once per cycle:
    #   {"sector_rank": {ticker: rank_pct}, "risk_on": bool, ...}. Cross-sectional
    #   strategies (SectorRotation, CrossAsset) use it when present and fail-closed
    #   to their own absolute signals otherwise, so they stay backtestable per-ticker.
    cross_section: dict | None = None

    @property
    def price(self) -> float | None:
        price = self.signals.get("price")
        return float(price) if price is not None else None


class Strategy(ABC):
    """Base class for all trading strategies."""

    name: str = "base"
    timeframe: str = "1d"
    # Hours after which a recorded signal becomes scorable by the allocator.
    eval_horizon_hours: int = 24

    @abstractmethod
    def evaluate(self, ctx: StrategyContext) -> StrategySignal:
        """Return BUY/SELL/HOLD for this ticker."""

    def _hold(self, ctx: StrategyContext, reason: str) -> StrategySignal:
        return StrategySignal(
            strategy=self.name,
            ticker=ctx.ticker,
            action=Action.HOLD,
            confidence=0.0,
            reason=reason,
            price=ctx.price or 0.0,
        )

    def _signal(
        self, ctx: StrategyContext, action: Action, confidence: float, reason: str
    ) -> StrategySignal:
        vol = ctx.signals.get("vol_20d")
        atr_pct = ctx.signals.get("atr_pct")
        return StrategySignal(
            strategy=self.name,
            ticker=ctx.ticker,
            action=action,
            confidence=max(0.0, min(1.0, confidence)),
            reason=reason,
            price=ctx.price or 0.0,
            volatility=float(vol) if vol is not None else None,
            atr_pct=float(atr_pct) if atr_pct is not None else None,
        )


class MomentumDaily(Strategy):
    """Composite momentum (RSI + MACD + MA alignment + volume + 5d change)."""

    name = "momentum_daily"
    timeframe = "1d"
    eval_horizon_hours = 24
    # Tunable thresholds (overridden by the backtest parameter sweep).
    buy_score: int = 70
    sell_score: int = 30

    def evaluate(self, ctx: StrategyContext) -> StrategySignal:
        if ctx.momentum is None or ctx.price is None:
            return self._hold(ctx, "insufficient data")
        score = ctx.momentum.score
        if score >= self.buy_score:
            return self._signal(
                ctx,
                Action.BUY,
                score / 100,
                f"momentum score {score}/100 ({ctx.momentum.label})",
            )
        if score <= self.sell_score and ctx.position is not None:
            return self._signal(
                ctx,
                Action.SELL,
                (100 - score) / 100,
                f"momentum collapsed to {score}/100 ({ctx.momentum.label})",
            )
        return self._hold(ctx, f"momentum neutral ({score}/100)")


class MeanReversion(Strategy):
    """RSI extremes near Fibonacci support/resistance."""

    name = "mean_reversion"
    timeframe = "1d"
    eval_horizon_hours = 24
    # Tunable RSI thresholds (match the generate_signals 30/70 defaults;
    # overridden by the backtest parameter sweep).
    rsi_oversold: float = 30.0
    rsi_overbought: float = 70.0

    def evaluate(self, ctx: StrategyContext) -> StrategySignal:
        price = ctx.price
        rsi = ctx.signals.get("rsi")
        fib = ctx.fibonacci or {}
        support = fib.get("nearest_support")
        resistance = fib.get("nearest_resistance")
        if price is None or rsi is None:
            return self._hold(ctx, "insufficient data")
        rsi = float(rsi)

        if rsi <= self.rsi_oversold and support is not None:
            if support <= price <= support * 1.02:
                depth = min(1.0, (self.rsi_oversold - rsi) / self.rsi_oversold + 0.5)
                return self._signal(
                    ctx,
                    Action.BUY,
                    depth,
                    f"RSI {rsi:.0f} oversold near Fib support {support:.2f}",
                )

        if ctx.position is not None:
            if rsi >= self.rsi_overbought:
                return self._signal(
                    ctx, Action.SELL, 0.7, f"RSI {rsi:.0f} overbought — take profit"
                )
            if resistance is not None and price >= resistance * 0.99:
                return self._signal(
                    ctx,
                    Action.SELL,
                    0.65,
                    f"price at Fib resistance {resistance:.2f}",
                )
        return self._hold(ctx, "no mean-reversion setup")


class TrendFollowing(Strategy):
    """Golden-cross trend with weekly MACD confirmation."""

    name = "trend_following"
    timeframe = "1wk"
    eval_horizon_hours = 24 * 7

    def evaluate(self, ctx: StrategyContext) -> StrategySignal:
        price = ctx.price
        mas = ctx.signals.get("moving_averages") or {}
        sma50 = mas.get("SMA_50")
        sma200 = mas.get("SMA_200")
        weekly_bullish = ctx.weekly.get("macd_bullish")
        if price is None or sma50 is None or sma200 is None:
            return self._hold(ctx, "insufficient data")

        if ctx.position is not None:
            if price < sma200:
                return self._signal(
                    ctx, Action.SELL, 0.8, f"price broke below SMA200 ({sma200:.2f})"
                )
            if weekly_bullish is False:
                return self._signal(ctx, Action.SELL, 0.6, "weekly MACD turned bearish")

        if sma50 > sma200 and price > sma50 and weekly_bullish:
            strength = min(1.0, 0.6 + (price - sma200) / sma200)
            return self._signal(
                ctx,
                Action.BUY,
                strength,
                "uptrend: SMA50 above SMA200, price above SMA50, weekly MACD bullish",
            )
        return self._hold(ctx, "no trend setup")


class Breakout(Strategy):
    """Resistance breakout on volume with monthly MACD confirmation."""

    name = "breakout"
    timeframe = "1mo"
    eval_horizon_hours = 24 * 20

    def evaluate(self, ctx: StrategyContext) -> StrategySignal:
        price = ctx.price
        fib = ctx.fibonacci or {}
        support = fib.get("nearest_support")
        resistance = fib.get("nearest_resistance")
        volume_spike = bool(ctx.signals.get("volume_spike"))
        monthly_bullish = ctx.monthly.get("macd_bullish")
        if price is None:
            return self._hold(ctx, "insufficient data")

        if ctx.position is not None and support is not None and price < support * 0.99:
            return self._signal(
                ctx, Action.SELL, 0.8, f"support {support:.2f} broken — structural exit"
            )

        # A fresh breakout leaves the old resistance just below the price:
        # 52w-high breakouts show up as resistance=None with price at the high.
        high_52w = fib.get("high_52w")
        at_new_high = (
            resistance is None and high_52w is not None and price >= high_52w * 0.995
        )
        broke_resistance = resistance is not None and price > resistance
        if (broke_resistance or at_new_high) and volume_spike and monthly_bullish:
            level = resistance if broke_resistance else high_52w
            return self._signal(
                ctx,
                Action.BUY,
                0.75,
                f"breakout above {level:.2f} on volume spike, monthly MACD bullish",
            )
        return self._hold(ctx, "no breakout setup")


class MACDCross(Strategy):
    """Fresh bullish/bearish MACD histogram cross, trend-filtered by SMA50."""

    name = "macd_cross"
    timeframe = "1d"
    eval_horizon_hours = 24

    def evaluate(self, ctx: StrategyContext) -> StrategySignal:
        price = ctx.price
        hist = ctx.signals.get("macd_histogram")
        hist_prev = ctx.signals.get("macd_histogram_prev")
        sma50 = (ctx.signals.get("moving_averages") or {}).get("SMA_50")
        if price is None or hist is None or hist_prev is None or sma50 is None:
            return self._hold(ctx, "insufficient data")

        if hist > 0 and hist_prev <= 0 and price > sma50:
            return self._signal(
                ctx,
                Action.BUY,
                0.65,
                f"fresh MACD bullish cross ({hist_prev:.3f}→{hist:.3f}) above SMA50",
            )
        if ctx.position is not None and hist < 0 and hist_prev >= 0:
            return self._signal(
                ctx,
                Action.SELL,
                0.65,
                f"fresh MACD bearish cross ({hist_prev:.3f}→{hist:.3f})",
            )
        return self._hold(ctx, "no fresh MACD cross")


class DipBuyer(Strategy):
    """Buy temporary dips inside a long-term uptrend ("buy low in a good trend")."""

    name = "dip_buyer"
    timeframe = "1d"
    eval_horizon_hours = 72
    # Tunable thresholds (overridden by the backtest parameter sweep).
    dip_rsi: float = 45.0
    exit_rsi: float = 65.0

    def evaluate(self, ctx: StrategyContext) -> StrategySignal:
        price = ctx.price
        rsi = ctx.signals.get("rsi")
        mas = ctx.signals.get("moving_averages") or {}
        sma20 = mas.get("SMA_20")
        sma200 = mas.get("SMA_200")
        if price is None or rsi is None or sma20 is None or sma200 is None:
            return self._hold(ctx, "insufficient data")
        rsi = float(rsi)

        if ctx.position is not None:
            if price < sma200:
                return self._signal(
                    ctx,
                    Action.SELL,
                    0.75,
                    f"uptrend broken: price below SMA200 ({sma200:.2f})",
                )
            if rsi > self.exit_rsi:
                return self._signal(
                    ctx,
                    Action.SELL,
                    0.6,
                    f"dip recovered: RSI {rsi:.0f} > {self.exit_rsi:.0f}",
                )

        if price > sma200 and price < sma20 and rsi < self.dip_rsi:
            confidence = 0.55 + (self.dip_rsi - rsi) / 100
            return self._signal(
                ctx,
                Action.BUY,
                confidence,
                f"dip in uptrend: RSI {rsi:.0f}, price under SMA20 but above SMA200",
            )
        return self._hold(ctx, "no dip setup")


class GapMomentum(Strategy):
    """Buy strong gap-ups confirmed by volume and the weekly trend."""

    name = "gap_momentum"
    timeframe = "1d"
    eval_horizon_hours = 24
    # Tunable thresholds (overridden by the backtest parameter sweep).
    gap_buy_pct: float = 0.02
    gap_exit_pct: float = -0.03

    def evaluate(self, ctx: StrategyContext) -> StrategySignal:
        price = ctx.price
        prev_close = ctx.signals.get("prev_close")
        if price is None or prev_close is None or float(prev_close) <= 0:
            return self._hold(ctx, "insufficient data")
        gap = (price - float(prev_close)) / float(prev_close)
        volume_spike = bool(ctx.signals.get("volume_spike"))
        weekly_bullish = ctx.weekly.get("macd_bullish")

        if ctx.position is not None and gap <= self.gap_exit_pct:
            return self._signal(
                ctx, Action.SELL, 0.7, f"panic gap down {gap:+.1%} — protective exit"
            )
        if gap >= self.gap_buy_pct and volume_spike and weekly_bullish:
            confidence = 0.6 + min(gap * 10, 0.3)
            return self._signal(
                ctx,
                Action.BUY,
                confidence,
                f"gap up {gap:+.1%} on volume spike, weekly MACD bullish",
            )
        return self._hold(ctx, "no gap setup")


class MultiTimeframe(Strategy):
    """Buy only when weekly, monthly, and daily trends all agree."""

    name = "multi_timeframe"
    timeframe = "1wk"
    eval_horizon_hours = 24 * 7

    def evaluate(self, ctx: StrategyContext) -> StrategySignal:
        price = ctx.price
        sma100 = (ctx.signals.get("moving_averages") or {}).get("SMA_100")
        weekly_bullish = ctx.weekly.get("macd_bullish")
        monthly_bullish = ctx.monthly.get("macd_bullish")
        score = ctx.momentum.score if ctx.momentum is not None else None
        if price is None or sma100 is None or score is None:
            return self._hold(ctx, "insufficient data")

        if ctx.position is not None:
            if weekly_bullish is False and monthly_bullish is False:
                return self._signal(
                    ctx, Action.SELL, 0.7, "weekly and monthly MACD both bearish"
                )
            if price < sma100:
                return self._signal(
                    ctx, Action.SELL, 0.6, f"price broke below SMA100 ({sma100:.2f})"
                )

        if weekly_bullish and monthly_bullish and price > sma100 and score >= 50:
            confidence = 0.6 + score / 500
            return self._signal(
                ctx,
                Action.BUY,
                confidence,
                f"weekly+monthly MACD bullish, price above SMA100, momentum {score}/100",
            )
        return self._hold(ctx, "timeframes not aligned")


class RSI2Reversion(Strategy):
    """Larry Connors' RSI(2): buy deep short-term pullbacks in a long uptrend."""

    name = "rsi2_reversion"
    timeframe = "1d"
    eval_horizon_hours = 72
    # Tunable thresholds (overridden by the backtest parameter sweep).
    buy_rsi2: float = 10.0
    exit_rsi2: float = 65.0

    def evaluate(self, ctx: StrategyContext) -> StrategySignal:
        price = ctx.price
        rsi2 = ctx.signals.get("rsi_2")
        mas = ctx.signals.get("moving_averages") or {}
        sma5 = ctx.signals.get("sma_5")
        sma200 = mas.get("SMA_200")
        if price is None or rsi2 is None or sma200 is None:
            return self._hold(ctx, "insufficient data")
        rsi2 = float(rsi2)

        if ctx.position is not None:
            if rsi2 > self.exit_rsi2:
                return self._signal(
                    ctx, Action.SELL, 0.65, f"RSI(2) recovered to {rsi2:.0f}"
                )
            if sma5 is not None and price > sma5:
                return self._signal(
                    ctx, Action.SELL, 0.6, f"price reclaimed SMA5 ({sma5:.2f})"
                )

        if rsi2 < self.buy_rsi2 and price > sma200:
            confidence = 0.6 + (self.buy_rsi2 - rsi2) / 50
            return self._signal(
                ctx,
                Action.BUY,
                confidence,
                f"RSI(2) {rsi2:.0f} washout above SMA200 — Connors pullback",
            )
        return self._hold(ctx, "no RSI(2) setup")


class BollingerReversion(Strategy):
    """Buy closes below the lower Bollinger band inside a long-term uptrend."""

    name = "bollinger_reversion"
    timeframe = "1d"
    eval_horizon_hours = 72

    def evaluate(self, ctx: StrategyContext) -> StrategySignal:
        price = ctx.price
        bb_lower = ctx.signals.get("bb_lower")
        bb_middle = ctx.signals.get("bb_middle")
        sma200 = (ctx.signals.get("moving_averages") or {}).get("SMA_200")
        if price is None or bb_lower is None or bb_middle is None or sma200 is None:
            return self._hold(ctx, "insufficient data")

        if ctx.position is not None and price >= bb_middle:
            return self._signal(
                ctx,
                Action.SELL,
                0.6,
                f"price back at Bollinger middle band ({bb_middle:.2f})",
            )
        if price < bb_lower and price > sma200:
            stretch = min(0.3, (bb_lower - price) / bb_lower * 10)
            return self._signal(
                ctx,
                Action.BUY,
                0.6 + stretch,
                f"close below lower Bollinger ({bb_lower:.2f}) in uptrend",
            )
        return self._hold(ctx, "no Bollinger setup")


class DonchianBreakout(Strategy):
    """Turtle-style 20/10 Donchian channel breakout with weekly trend filter."""

    name = "donchian_breakout"
    timeframe = "1d"
    eval_horizon_hours = 120

    def evaluate(self, ctx: StrategyContext) -> StrategySignal:
        price = ctx.price
        high20 = ctx.signals.get("donchian_high_20")
        low10 = ctx.signals.get("donchian_low_10")
        weekly_bullish = ctx.weekly.get("macd_bullish")
        if price is None or high20 is None or low10 is None:
            return self._hold(ctx, "insufficient data")

        if ctx.position is not None and price < low10:
            return self._signal(
                ctx,
                Action.SELL,
                0.7,
                f"close below 10-day Donchian low ({low10:.2f}) — Turtle exit",
            )
        if price > high20 and weekly_bullish:
            strength = min(0.3, (price - high20) / high20 * 10)
            return self._signal(
                ctx,
                Action.BUY,
                0.65 + strength,
                f"close above 20-day Donchian high ({high20:.2f}), weekly MACD bullish",
            )
        return self._hold(ctx, "no Donchian breakout")


class TSMomentum(Strategy):
    """12−1 month time-series momentum (Moskowitz/AQR) with SMA200 filter."""

    name = "ts_momentum"
    timeframe = "1mo"
    eval_horizon_hours = 480

    def evaluate(self, ctx: StrategyContext) -> StrategySignal:
        price = ctx.price
        ret_12m = ctx.signals.get("ret_12m")
        ret_1m = ctx.signals.get("ret_1m")
        sma200 = (ctx.signals.get("moving_averages") or {}).get("SMA_200")
        if price is None or ret_12m is None or ret_1m is None or sma200 is None:
            return self._hold(ctx, "insufficient data")
        momentum_12_1 = float(ret_12m) - float(ret_1m)

        if ctx.position is not None and momentum_12_1 < 0:
            return self._signal(
                ctx,
                Action.SELL,
                0.65,
                f"12−1m momentum turned negative ({momentum_12_1:+.1%})",
            )
        if momentum_12_1 > 0 and price > sma200:
            confidence = 0.55 + min(momentum_12_1, 0.35)
            return self._signal(
                ctx,
                Action.BUY,
                confidence,
                f"12−1m momentum {momentum_12_1:+.1%}, price above SMA200",
            )
        return self._hold(ctx, "no time-series momentum")


class FiftyTwoWeekHigh(Strategy):
    """George & Hwang: buy strength near the 52-week high, confirmed long-term."""

    name = "high_52w"
    timeframe = "1mo"
    eval_horizon_hours = 480
    # Tunable thresholds (overridden by the backtest parameter sweep).
    buy_proximity: float = 0.98
    exit_proximity: float = 0.90

    def evaluate(self, ctx: StrategyContext) -> StrategySignal:
        price = ctx.price
        fib = ctx.fibonacci or {}
        high_52w = fib.get("high_52w")
        ret_6m = ctx.signals.get("ret_6m")
        sma100 = (ctx.signals.get("moving_averages") or {}).get("SMA_100")
        monthly_bullish = ctx.monthly.get("macd_bullish")
        if price is None or high_52w is None or ret_6m is None:
            return self._hold(ctx, "insufficient data")

        if ctx.position is not None:
            if price < high_52w * self.exit_proximity:
                return self._signal(
                    ctx,
                    Action.SELL,
                    0.65,
                    f"faded {1 - self.exit_proximity:.0%}+ off the 52w high "
                    f"({high_52w:.2f})",
                )
            if sma100 is not None and price < sma100:
                return self._signal(
                    ctx, Action.SELL, 0.6, f"price broke below SMA100 ({sma100:.2f})"
                )

        if (
            price >= high_52w * self.buy_proximity
            and float(ret_6m) > 0
            and monthly_bullish
        ):
            confidence = 0.6 + min(float(ret_6m), 0.3) / 2
            return self._signal(
                ctx,
                Action.BUY,
                confidence,
                f"within {1 - self.buy_proximity:.0%} of 52w high {high_52w:.2f}, "
                f"6m return {float(ret_6m):+.1%}, monthly MACD bullish",
            )
        return self._hold(ctx, "not near 52w high")


class DoubleSeven(Strategy):
    """Connors' Double-7: buy 7-day low closes in a long-term uptrend."""

    name = "double_seven"
    timeframe = "1d"
    eval_horizon_hours = 72

    def evaluate(self, ctx: StrategyContext) -> StrategySignal:
        price = ctx.price
        low_7d = ctx.signals.get("low_7d_close")
        high_7d = ctx.signals.get("high_7d_close")
        sma200 = (ctx.signals.get("moving_averages") or {}).get("SMA_200")
        if price is None or low_7d is None or high_7d is None or sma200 is None:
            return self._hold(ctx, "insufficient data")

        if ctx.position is not None and price >= high_7d:
            return self._signal(
                ctx, Action.SELL, 0.6, f"close at 7-day high ({high_7d:.2f})"
            )
        if price <= low_7d and price > sma200:
            return self._signal(
                ctx,
                Action.BUY,
                0.6,
                f"close at 7-day low ({low_7d:.2f}) above SMA200 — Double-7",
            )
        return self._hold(ctx, "no Double-7 setup")


class PullbackSMA50(Strategy):
    """Buy pullbacks to the 50-day SMA inside an established uptrend."""

    name = "pullback_sma50"
    timeframe = "1d"
    eval_horizon_hours = 120
    # Tunable thresholds (overridden by the backtest parameter sweep).
    band_pct: float = 0.02
    buy_rsi_max: float = 50.0
    exit_rsi: float = 65.0

    def evaluate(self, ctx: StrategyContext) -> StrategySignal:
        price = ctx.price
        rsi = ctx.signals.get("rsi")
        mas = ctx.signals.get("moving_averages") or {}
        sma50 = mas.get("SMA_50")
        sma200 = mas.get("SMA_200")
        if price is None or rsi is None or sma50 is None or sma200 is None:
            return self._hold(ctx, "insufficient data")
        rsi = float(rsi)

        if ctx.position is not None:
            if price < sma200:
                return self._signal(
                    ctx, Action.SELL, 0.7, f"uptrend broken below SMA200 ({sma200:.2f})"
                )
            if rsi > self.exit_rsi:
                return self._signal(
                    ctx, Action.SELL, 0.6, f"pullback recovered: RSI {rsi:.0f}"
                )

        in_uptrend = sma50 > sma200 and price > sma200
        at_sma50 = abs(price / sma50 - 1) <= self.band_pct
        if in_uptrend and at_sma50 and rsi < self.buy_rsi_max:
            return self._signal(
                ctx,
                Action.BUY,
                0.6 + (self.buy_rsi_max - rsi) / 200,
                f"pullback to SMA50 ({sma50:.2f}) in uptrend, RSI {rsi:.0f}",
            )
        return self._hold(ctx, "no SMA50 pullback setup")


class GoldenCross(Strategy):
    """Trade the fresh SMA50/SMA200 golden cross / death cross events."""

    name = "golden_cross"
    timeframe = "1mo"
    eval_horizon_hours = 480

    def evaluate(self, ctx: StrategyContext) -> StrategySignal:
        price = ctx.price
        mas = ctx.signals.get("moving_averages") or {}
        sma50 = mas.get("SMA_50")
        sma200 = mas.get("SMA_200")
        sma50_prev = ctx.signals.get("sma_50_prev")
        sma200_prev = ctx.signals.get("sma_200_prev")
        if (
            price is None
            or sma50 is None
            or sma200 is None
            or sma50_prev is None
            or sma200_prev is None
        ):
            return self._hold(ctx, "insufficient data")

        crossed_up = sma50 > sma200 and sma50_prev <= sma200_prev
        crossed_down = sma50 < sma200 and sma50_prev >= sma200_prev
        if ctx.position is not None and crossed_down:
            return self._signal(
                ctx, Action.SELL, 0.7, "death cross: SMA50 crossed below SMA200"
            )
        if crossed_up and price > sma50:
            return self._signal(
                ctx,
                Action.BUY,
                0.7,
                f"fresh golden cross: SMA50 ({sma50:.2f}) above SMA200 ({sma200:.2f})",
            )
        return self._hold(ctx, "no fresh cross")


class VolContraction(Strategy):
    """VCP-style squeeze breakout: tight Bollinger bands + channel break."""

    name = "vol_contraction"
    timeframe = "1d"
    eval_horizon_hours = 120
    # Tunable thresholds (overridden by the backtest parameter sweep).
    max_width_pct: float = 0.06

    def evaluate(self, ctx: StrategyContext) -> StrategySignal:
        price = ctx.price
        bb_upper = ctx.signals.get("bb_upper")
        bb_lower = ctx.signals.get("bb_lower")
        bb_middle = ctx.signals.get("bb_middle")
        high20 = ctx.signals.get("donchian_high_20")
        low10 = ctx.signals.get("donchian_low_10")
        weekly_bullish = ctx.weekly.get("macd_bullish")
        if (
            price is None
            or bb_upper is None
            or bb_lower is None
            or bb_middle is None
            or bb_middle <= 0
            or high20 is None
            or low10 is None
        ):
            return self._hold(ctx, "insufficient data")

        if ctx.position is not None and price < low10:
            return self._signal(
                ctx, Action.SELL, 0.7, f"break below 10-day low ({low10:.2f})"
            )
        width = (bb_upper - bb_lower) / bb_middle
        if width <= self.max_width_pct and price > high20 and weekly_bullish:
            return self._signal(
                ctx,
                Action.BUY,
                0.7,
                f"squeeze breakout: BB width {width:.1%} ≤ {self.max_width_pct:.0%}, "
                f"close above {high20:.2f}",
            )
        return self._hold(ctx, "no squeeze breakout")


class LowVolTrend(Strategy):
    """Low-volatility anomaly: quiet names grinding up in a long uptrend."""

    name = "low_vol_trend"
    timeframe = "1mo"
    eval_horizon_hours = 480
    # Tunable thresholds (overridden by the backtest parameter sweep).
    max_vol: float = 0.25
    exit_vol: float = 0.50

    def evaluate(self, ctx: StrategyContext) -> StrategySignal:
        price = ctx.price
        vol = ctx.signals.get("vol_20d")
        ret_6m = ctx.signals.get("ret_6m")
        sma200 = (ctx.signals.get("moving_averages") or {}).get("SMA_200")
        if price is None or vol is None or ret_6m is None or sma200 is None:
            return self._hold(ctx, "insufficient data")
        vol = float(vol)

        if ctx.position is not None:
            if price < sma200:
                return self._signal(
                    ctx, Action.SELL, 0.7, f"trend broken below SMA200 ({sma200:.2f})"
                )
            if vol > self.exit_vol:
                return self._signal(
                    ctx,
                    Action.SELL,
                    0.6,
                    f"volatility spiked to {vol:.0%} (> {self.exit_vol:.0%})",
                )

        if vol < self.max_vol and price > sma200 and float(ret_6m) > 0:
            return self._signal(
                ctx,
                Action.BUY,
                0.6 + min(self.max_vol - vol, 0.1),
                f"low vol {vol:.0%} in uptrend, 6m return {float(ret_6m):+.1%}",
            )
        return self._hold(ctx, "no low-vol setup")


class Supertrend(Strategy):
    """ATR-based trend follower — rides the Supertrend line above SMA200."""

    name = "supertrend"
    timeframe = "1d"
    eval_horizon_hours = 120

    def evaluate(self, ctx: StrategyContext) -> StrategySignal:
        price = ctx.price
        st_dir = ctx.signals.get("supertrend_dir")
        sma200 = (ctx.signals.get("moving_averages") or {}).get("SMA_200")
        if price is None or st_dir is None or sma200 is None:
            return self._hold(ctx, "insufficient data")

        if ctx.position is not None and st_dir < 0:
            return self._signal(
                ctx, Action.SELL, 0.7, "Supertrend flipped down — trend exit"
            )
        if st_dir > 0 and price > sma200:
            return self._signal(
                ctx,
                Action.BUY,
                0.65,
                "Supertrend up and price above SMA200",
            )
        return self._hold(ctx, "no Supertrend setup")


class AdxTrend(Strategy):
    """Wilder's ADX — trade only strong, directional trends (ADX>25)."""

    name = "adx_trend"
    timeframe = "1wk"
    eval_horizon_hours = 168
    # Tunable thresholds (overridden by the backtest parameter sweep).
    adx_buy: float = 25.0
    adx_exit: float = 20.0

    def evaluate(self, ctx: StrategyContext) -> StrategySignal:
        price = ctx.price
        adx = ctx.signals.get("adx_14")
        plus_di = ctx.signals.get("plus_di")
        minus_di = ctx.signals.get("minus_di")
        sma50 = (ctx.signals.get("moving_averages") or {}).get("SMA_50")
        if (
            price is None
            or adx is None
            or plus_di is None
            or minus_di is None
            or sma50 is None
        ):
            return self._hold(ctx, "insufficient data")

        if ctx.position is not None:
            if minus_di > plus_di:
                return self._signal(
                    ctx, Action.SELL, 0.7, f"−DI crossed above +DI (ADX {adx:.0f})"
                )
            if adx < self.adx_exit:
                return self._signal(
                    ctx,
                    Action.SELL,
                    0.6,
                    f"trend faded — ADX {adx:.0f} < {self.adx_exit:.0f}",
                )

        if adx > self.adx_buy and plus_di > minus_di and price > sma50:
            confidence = 0.6 + min((adx - self.adx_buy) / 100, 0.3)
            return self._signal(
                ctx,
                Action.BUY,
                confidence,
                f"strong uptrend: ADX {adx:.0f}, +DI>−DI, price above SMA50",
            )
        return self._hold(ctx, "no ADX trend")


class KeltnerBreakout(Strategy):
    """Breakout above the ATR-based Keltner channel (complements Bollinger)."""

    name = "keltner_breakout"
    timeframe = "1d"
    eval_horizon_hours = 120

    def evaluate(self, ctx: StrategyContext) -> StrategySignal:
        price = ctx.price
        upper = ctx.signals.get("keltner_upper")
        ema20 = (ctx.signals.get("moving_averages") or {}).get("EMA_20")
        weekly_bullish = ctx.weekly.get("macd_bullish")
        if price is None or upper is None or ema20 is None:
            return self._hold(ctx, "insufficient data")

        if ctx.position is not None and price < ema20:
            return self._signal(
                ctx, Action.SELL, 0.65, f"price fell back below EMA20 ({ema20:.2f})"
            )
        if price > upper and weekly_bullish:
            return self._signal(
                ctx,
                Action.BUY,
                0.7,
                f"breakout above Keltner upper band ({upper:.2f}), weekly MACD bullish",
            )
        return self._hold(ctx, "no Keltner breakout")


# ── New-method strategies (Phase 1: sector rotation / intermarket / calendar) ──

# The 11 S&P 500 SPDR sector ETFs — SectorRotation only acts on these.
SECTOR_ROTATION_ETFS: frozenset[str] = frozenset(
    {"XLK", "XLV", "XLF", "XLE", "XLI", "XLY", "XLP", "XLC", "XLB", "XLRE", "XLU"}
)
# Bond / commodity / currency proxies — CrossAsset only acts on these.
CROSS_ASSET_ETFS: frozenset[str] = frozenset(
    {"TLT", "IEF", "AGG", "GLD", "SLV", "USO", "DBC", "UUP"}
)


class SectorRotation(Strategy):
    """Relative-momentum rotation across the 11 SPDR sector ETFs.

    Cross-sectional by nature: given ``ctx.cross_section["sector_rank"]`` (a
    0–1 rank per sector ETF, 1 = strongest) the strategy buys the leaders and
    exits laggards. When no cross-section is injected (e.g. the standalone
    per-ticker backtest) it falls back to the sector's own absolute 3-month
    momentum so it stays measurable. Fires only on sector-ETF tickers.
    """

    name = "sector_rotation"
    timeframe = "1mo"
    eval_horizon_hours = 480
    # Tunable thresholds (overridden by the backtest parameter sweep).
    buy_rank: float = 0.70
    exit_rank: float = 0.40
    abs_buy_ret: float = 0.05  # fallback 3m momentum floor without a ranking

    def evaluate(self, ctx: StrategyContext) -> StrategySignal:
        if ctx.ticker not in SECTOR_ROTATION_ETFS:
            return self._hold(ctx, "not a sector ETF")
        price = ctx.price
        sma200 = (ctx.signals.get("moving_averages") or {}).get("SMA_200")
        ret_3m = ctx.signals.get("ret_3m")
        if price is None or sma200 is None or ret_3m is None:
            return self._hold(ctx, "insufficient data")
        ret_3m = float(ret_3m)

        rank = None
        if ctx.cross_section:
            rank = (ctx.cross_section.get("sector_rank") or {}).get(ctx.ticker)

        if ctx.position is not None:
            if price < sma200:
                return self._signal(
                    ctx, Action.SELL, 0.65, f"sector below SMA200 ({sma200:.2f})"
                )
            if rank is not None and rank < self.exit_rank:
                return self._signal(
                    ctx, Action.SELL, 0.6, f"sector rank fell to {rank:.0%}"
                )
            if rank is None and ret_3m < 0:
                return self._signal(
                    ctx,
                    Action.SELL,
                    0.6,
                    f"sector 3m momentum negative ({ret_3m:+.1%})",
                )

        if price <= sma200:
            return self._hold(ctx, "sector not in uptrend")
        if rank is not None:
            if rank >= self.buy_rank:
                return self._signal(
                    ctx,
                    Action.BUY,
                    0.6 + min((rank - self.buy_rank), 0.3),
                    f"sector leader — rank {rank:.0%}, above SMA200",
                )
            return self._hold(ctx, f"sector rank {rank:.0%} below buy threshold")
        if ret_3m >= self.abs_buy_ret:
            return self._signal(
                ctx,
                Action.BUY,
                0.6 + min(ret_3m, 0.3),
                f"sector 3m momentum {ret_3m:+.1%}, above SMA200",
            )
        return self._hold(ctx, "no sector-rotation setup")


class CrossAsset(Strategy):
    """Intermarket trend on bond / gold / commodity / dollar ETFs.

    Diversifies the equity book by trading the cross-asset proxies on their own
    medium-term trend (6-month return > 0, above SMA200, monthly MACD bullish).
    Fires only on the cross-asset universe, so equities are unaffected.
    """

    name = "cross_asset"
    timeframe = "1mo"
    eval_horizon_hours = 480

    def evaluate(self, ctx: StrategyContext) -> StrategySignal:
        if ctx.ticker not in CROSS_ASSET_ETFS:
            return self._hold(ctx, "not a cross-asset ETF")
        price = ctx.price
        sma200 = (ctx.signals.get("moving_averages") or {}).get("SMA_200")
        ret_6m = ctx.signals.get("ret_6m")
        monthly_bullish = ctx.monthly.get("macd_bullish")
        if price is None or sma200 is None or ret_6m is None:
            return self._hold(ctx, "insufficient data")
        ret_6m = float(ret_6m)

        if ctx.position is not None and (price < sma200 or ret_6m < 0):
            return self._signal(
                ctx, Action.SELL, 0.65, f"cross-asset trend broke ({ret_6m:+.1%})"
            )
        if price > sma200 and ret_6m > 0 and monthly_bullish:
            return self._signal(
                ctx,
                Action.BUY,
                0.6 + min(ret_6m, 0.3),
                f"cross-asset uptrend: 6m {ret_6m:+.1%}, above SMA200, monthly MACD up",
            )
        return self._hold(ctx, "no cross-asset trend")


class Seasonality(Strategy):
    """Calendar-effect tilt: turn-of-month + 'sell in May' seasonality.

    A confirmation tilt, never a standalone entry — it only buys favorable
    calendar windows when the name is already in a long uptrend (price > SMA200),
    and trims into the weak-season start while held. Reads ``ctx.as_of``.
    """

    name = "seasonality"
    timeframe = "1d"
    eval_horizon_hours = 120
    # Favorable ("winter") months per the seasonality literature (Nov–Apr).
    favorable_months: frozenset[int] = frozenset({11, 12, 1, 2, 3, 4})

    def evaluate(self, ctx: StrategyContext) -> StrategySignal:
        price = ctx.price
        as_of = ctx.as_of
        sma200 = (ctx.signals.get("moving_averages") or {}).get("SMA_200")
        if price is None or as_of is None or sma200 is None:
            return self._hold(ctx, "insufficient data")

        # Turn-of-month window (calendar-day approximation of the last ~2 and
        # first ~3 trading days, when equity returns cluster positive).
        is_tom = as_of.day >= 28 or as_of.day <= 3
        favorable = as_of.month in self.favorable_months

        if ctx.position is not None:
            # Trim on the weak-season transition (start of May) while held.
            if as_of.month == 5 and as_of.day <= 5:
                return self._signal(
                    ctx, Action.SELL, 0.55, "entering weak season (sell in May)"
                )

        if price <= sma200:
            return self._hold(ctx, "seasonality skipped — not in uptrend")
        if is_tom:
            return self._signal(
                ctx, Action.BUY, 0.62, "turn-of-month window in uptrend"
            )
        if favorable:
            return self._signal(
                ctx, Action.BUY, 0.56, "favorable season (Nov–Apr) in uptrend"
            )
        return self._hold(ctx, "no seasonal edge")


class DualMomentum(Strategy):
    """Antonacci dual momentum — absolute (12m) + recency, long-term filtered.

    Buys only names with positive 12-month AND positive 3-month absolute return
    while above the 200-day average (the trend filter). Differs from
    ``ts_momentum`` (12−1m signal) by requiring recent 3m confirmation, which
    cuts entries into stalling leaders. Experimental (2026-07-24) — grounded in
    ts_momentum's standalone edge; promotion needs a walk-forward win.
    """

    name = "dual_momentum"
    timeframe = "1mo"
    eval_horizon_hours = 480

    def evaluate(self, ctx: StrategyContext) -> StrategySignal:
        price = ctx.price
        ret_12m = ctx.signals.get("ret_12m")
        ret_3m = ctx.signals.get("ret_3m")
        sma200 = (ctx.signals.get("moving_averages") or {}).get("SMA_200")
        if price is None or ret_12m is None or ret_3m is None or sma200 is None:
            return self._hold(ctx, "insufficient data")
        r12 = float(ret_12m)
        r3 = float(ret_3m)

        if ctx.position is not None and (r12 < 0 or price < sma200):
            return self._signal(
                ctx, Action.SELL, 0.65, f"absolute momentum lost (12m {r12:+.1%})"
            )
        if r12 > 0 and r3 > 0 and price > sma200:
            confidence = 0.55 + min(r12, 0.35)
            return self._signal(
                ctx,
                Action.BUY,
                confidence,
                f"dual momentum: 12m {r12:+.1%}, 3m {r3:+.1%}, above SMA200",
            )
        return self._hold(ctx, "no dual-momentum setup")


class VolTargetTrend(Strategy):
    """Trend-following gated by a calm-volatility regime (vol-target style).

    Enters a confirmed uptrend (SMA50 > SMA200, price > SMA200) only when 20-day
    realized volatility is below a target, and sizes confidence inversely to
    vol — capturing the low-vol-trend anomaly through an explicit trend filter.
    Exits on a break below SMA200 or a volatility spike. Experimental
    (2026-07-24); promotion needs a walk-forward win.
    """

    name = "vol_target_trend"
    timeframe = "1wk"
    eval_horizon_hours = 168
    target_vol: float = 0.30
    exit_vol: float = 0.55

    def evaluate(self, ctx: StrategyContext) -> StrategySignal:
        price = ctx.price
        mas = ctx.signals.get("moving_averages") or {}
        sma50 = mas.get("SMA_50")
        sma200 = mas.get("SMA_200")
        vol = ctx.signals.get("vol_20d")
        if price is None or sma50 is None or sma200 is None or vol is None:
            return self._hold(ctx, "insufficient data")
        vol = float(vol)

        if ctx.position is not None:
            if price < sma200:
                return self._signal(
                    ctx, Action.SELL, 0.7, f"trend broke below SMA200 ({sma200:.2f})"
                )
            if vol > self.exit_vol:
                return self._signal(ctx, Action.SELL, 0.6, f"vol spiked to {vol:.0%}")

        if sma50 > sma200 and price > sma200 and vol < self.target_vol:
            # Lower vol → higher confidence (vol-target intuition).
            confidence = 0.55 + min(self.target_vol - vol, 0.2)
            return self._signal(
                ctx,
                Action.BUY,
                confidence,
                f"uptrend (SMA50>SMA200) at calm vol {vol:.0%}",
            )
        return self._hold(ctx, "no vol-target trend setup")


def default_strategies() -> list[Strategy]:
    """The standard strategy set the LIVE agent evaluates every cycle.

    Promoted 2026-07-09 from the full-pool 64-year backtest (254 tickers):
    every member had CAGR ≥ 5.8% and Sharpe ≥ 0.56 standalone. TSMomentum
    (11.5% CAGR) and DipBuyer (11.3%, Sharpe 0.81) led; MomentumDaily and
    MeanReversion were demoted to the experimental set (CAGR ≤ 4.5%).

    SectorRotation + CrossAsset were briefly promoted 2026-07-23 then reverted:
    despite topping the avg-per-signal metric (+1.08%, +0.99%), the full-pool
    64-year run showed they *lowered* the honest walk-forward (9.4%→8.7% CAGR)
    and worsened drawdown — they dilute the compounding ensemble, so they stay
    experimental (their weight seeds still inform the allocator's cold start).
    """
    return [
        TrendFollowing(),
        Breakout(),
        DipBuyer(),
        GapMomentum(),
        MultiTimeframe(),
        BollingerReversion(),
        TSMomentum(),
        FiftyTwoWeekHigh(),
    ]


def experimental_strategies() -> list[Strategy]:
    """Backtest-only strategies — evaluated offline, not by the live agent.

    Promoting one to live is an explicit decision: move it into
    ``default_strategies()`` (its backtest weight seeds already exist).
    Demoted live strategies keep being measured here.
    """
    return [
        MomentumDaily(),
        MeanReversion(),
        MACDCross(),
        RSI2Reversion(),
        DonchianBreakout(),
        DoubleSeven(),
        PullbackSMA50(),
        GoldenCross(),
        VolContraction(),
        LowVolTrend(),
        Supertrend(),
        AdxTrend(),
        KeltnerBreakout(),
        SectorRotation(),
        CrossAsset(),
        Seasonality(),
        DualMomentum(),
        VolTargetTrend(),
    ]


STRATEGY_HORIZON_HOURS: dict[str, int] = {
    s.name: s.eval_horizon_hours
    for s in [*default_strategies(), *experimental_strategies()]
}
