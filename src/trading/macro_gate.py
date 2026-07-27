"""Macro / market-timing gate — decides *whether* it is a good time to trade.

Absorbs the old ``TradingAgent._regime_state`` soft dampener and upgrades it to a
real gate. Each cycle it turns :class:`~src.trading.macro_data.MacroData` into a
:class:`MacroState` carrying:

* ``regime``  — BULL / BEAR / VOLATILE (selects the allocator's weight seeds),
* ``decision`` — ON / SCALED / OFF (OFF ⇒ the Orchestrator places no new buys),
* ``size_mult`` — buy-budget multiplier in [0, 1] (regime + blackout + sentiment),
* ``confidence_factor`` — legacy buy-confidence dampener, for parity.

The quantitative scoring (:func:`score_macro`) is pure so the backtest can gate
historical days with the exact same logic. Blackout windows, the local-sentiment
overlay, and hysteresis are applied live in :meth:`MacroTimingGate.evaluate`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from src.database.cache import cache
from src.trading.macro_calendar import active_blackout
from src.trading.macro_data import MacroData, MacroDataProvider
from src.trading.sentiment_local import market_sentiment, put_call_ratio
from src.utils.config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)

_STATE_KEY = "trading:macro:state"
_STREAK_KEY = "trading:macro:stable_streak"
_STATE_TTL = 3600

# Composite risk-score thresholds (higher = more risk-on).
# The gate scales buy volume *continuously* with how weak the tape is, instead
# of a binary halt. The 64-year backtest showed a hard OFF on SPY<SMA200 alone
# whipsaws and misses rebounds (gated 6.1% vs ungated 6.8% CAGR); a graded
# SCALED band that ramps buy budget from full size down toward a floor — and
# only trips OFF on genuinely stacked risk — is the fix.
#   score ≥ _SCALED_THRESHOLD .............. ON, full size
#   _OFF_THRESHOLD < score < _SCALED ....... SCALED, size ramps _SCALED_FLOOR→1
#   score ≤ _OFF_THRESHOLD ................. OFF (e.g. SPY<SMA200 −0.45 AND
#                                            VIX>30 −0.35 ⇒ −0.80)
_OFF_THRESHOLD = -0.70
_SCALED_THRESHOLD = 0.10
_SCALED_FLOOR = 0.25  # smallest non-zero buy multiplier at the weak end of SCALED
_VOLATILE_SIZE_CAP = 0.5  # never full-size in a high-vol tape
# Extreme fear overlays that can only tighten risk.
_SENTIMENT_FLOOR = -0.35  # avg headline sentiment below this ⇒ scale down
_PUT_CALL_FEAR = 1.15  # total put/call above this ⇒ scale down


@dataclass
class MacroState:
    regime: str = "BULL"
    decision: str = "ON"  # ON | SCALED | OFF
    size_mult: float = 1.0
    confidence_factor: float = 1.0
    blackout: bool = False
    risk_score: float = 0.0
    rationale: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def trade_allowed(self) -> bool:
        return self.decision != "OFF"


def score_macro(data: MacroData) -> tuple[str, float, list[str]]:
    """Pure quantitative scoring → (regime, risk_score, reasons).

    ``risk_score`` is a bounded composite in roughly [-1, 1]; positive is
    risk-on. Missing inputs contribute nothing (neutral), so the gate never
    blocks on an unreachable data source.
    """
    score = 0.0
    reasons: list[str] = []
    regime = "BULL"

    # ── Equity trend (dominant) ──
    if data.spy_above_sma200 is False:
        score -= 0.45
        regime = "BEAR"
        reasons.append("SPY below SMA200 (downtrend)")
    elif data.spy_above_sma200 is True:
        score += 0.30
    if data.spy_above_sma50 is False:
        score -= 0.10
    if data.qqq_above_sma200 is False:
        score -= 0.10

    # ── Volatility ──
    if data.vix is not None:
        if data.vix > 30:
            score -= 0.35
            regime = "VOLATILE"
            reasons.append(f"VIX {data.vix:.0f} > 30")
        elif data.vix > 20:
            score -= 0.12
    if data.vix_term_ratio is not None and data.vix_term_ratio > 1.0:
        score -= 0.12  # backwardation = near-term stress
        reasons.append("VIX term structure inverted")

    # ── Credit & rates ──
    if data.move is not None and data.move > 120:
        score -= 0.10
        reasons.append(f"MOVE {data.move:.0f} elevated")
    if data.hyg_lqd_trend is not None:
        score += max(-0.12, min(0.12, data.hyg_lqd_trend * 4))
    if data.net_liquidity_trend is not None:
        score += 0.08 if data.net_liquidity_trend > 0 else -0.08
    if data.yield_curve is not None and data.yield_curve < 0:
        score -= 0.05  # inversion: slow-moving recession signal

    # ── Cross-asset ──
    if data.dxy_trend is not None and data.dxy_trend > 0.05:
        score -= 0.06  # strong dollar = equity headwind
    if (
        data.gold_trend is not None
        and data.oil_trend is not None
        and data.gold_trend > 0.10
        and data.oil_trend > 0.15
    ):
        score -= 0.06  # commodity/geopolitical shock
        reasons.append("gold + oil spiking (macro shock)")

    return regime, max(-1.0, min(1.0, score)), reasons


def _decision_from_score(risk_score: float, regime: str) -> tuple[str, float]:
    """Map the composite score + regime to (decision, size_mult).

    Graded/dynamic: within the SCALED band the buy multiplier ramps linearly
    from ``_SCALED_FLOOR`` (weakest tape) to 1.0 (near-constructive), so buy
    volume shrinks smoothly as the market deteriorates rather than snapping to
    a hard stop. OFF is reserved for genuinely stacked risk.
    """
    if risk_score <= _OFF_THRESHOLD:
        return "OFF", 0.0
    if risk_score >= _SCALED_THRESHOLD:
        # Constructive tape — full size, except never full in a volatile regime.
        if regime == "VOLATILE":
            return "SCALED", _VOLATILE_SIZE_CAP
        return "ON", 1.0
    # SCALED band: interpolate the size multiplier across the score range.
    frac = (risk_score - _OFF_THRESHOLD) / (_SCALED_THRESHOLD - _OFF_THRESHOLD)
    size = _SCALED_FLOOR + frac * (1.0 - _SCALED_FLOOR)
    if regime == "VOLATILE":
        size = min(size, _VOLATILE_SIZE_CAP)
    return "SCALED", round(size, 3)


class MacroTimingGate:
    """Live gate: fetch macro data, score it, overlay events + sentiment, hold
    a hysteresis buffer, and persist the state for zero-downtime recovery."""

    def __init__(self, quant, news_agent=None) -> None:
        self._provider = MacroDataProvider(quant)
        self._news = news_agent

    async def evaluate(self, *, apply_hysteresis: bool = True) -> MacroState:
        data = await self._provider.fetch()
        regime, risk_score, reasons = score_macro(data)
        decision, size_mult = _decision_from_score(risk_score, regime)
        # The gate expresses all risk through the graded size_mult, so buy
        # confidence is left undampened here (no double-penalty). The legacy
        # 0.5-step confidence dampener still applies on the gate-disabled path
        # (Orchestrator._legacy_state).
        conf_factor = 1.0

        # ── Event blackout (can only tighten) ──
        blackout = False
        event = active_blackout(settings.macro_blackout_hours)
        if event is not None:
            blackout = True
            size_mult = min(size_mult, settings.macro_blackout_size_mult)
            if settings.macro_blackout_size_mult <= 0:
                decision = "OFF"
            elif decision == "ON":
                decision = "SCALED"
            reasons.append(f"{event.kind} blackout window")

        # ── Local sentiment overlay (live only, can only tighten) ──
        if settings.sentiment_local_enabled and self._news is not None:
            size_mult, decision, sent_reasons = await self._sentiment_overlay(
                size_mult, decision
            )
            reasons.extend(sent_reasons)

        raw = MacroState(
            regime=regime,
            decision=decision,
            size_mult=round(size_mult, 3),
            confidence_factor=round(conf_factor, 3),
            blackout=blackout,
            risk_score=round(risk_score, 3),
            rationale="; ".join(reasons) or "conditions constructive",
        )

        state = await self._apply_hysteresis(raw) if apply_hysteresis else raw
        await self._persist(state)
        logger.info(
            "macro_gate",
            regime=state.regime,
            decision=state.decision,
            size_mult=state.size_mult,
            risk=state.risk_score,
        )
        return state

    async def _sentiment_overlay(
        self, size_mult: float, decision: str
    ) -> tuple[float, str, list[str]]:
        reasons: list[str] = []
        try:
            sent = await market_sentiment(self._news)
            pcr = await put_call_ratio()
        except Exception as exc:  # noqa: BLE001
            logger.debug("sentiment_overlay_failed", error=str(exc))
            return size_mult, decision, reasons
        if sent is not None and sent < _SENTIMENT_FLOOR:
            size_mult *= 0.7
            if decision == "ON":
                decision = "SCALED"
            reasons.append(f"bearish news sentiment ({sent:+.2f})")
        if pcr is not None and pcr > _PUT_CALL_FEAR:
            size_mult *= 0.8
            reasons.append(f"put/call fear ({pcr:.2f})")
        return max(0.0, round(size_mult, 3)), decision, reasons

    async def _apply_hysteresis(self, raw: MacroState) -> MacroState:
        """OFF applies immediately; recovery to ON needs N stable cycles."""
        n = max(1, settings.macro_hysteresis_cycles)
        prev = await cache.get(_STATE_KEY) or {}
        prev_decision = prev.get("decision", "ON")
        streak = int(await cache.get(_STREAK_KEY) or 0)

        if raw.decision == "OFF":
            await cache.set(_STREAK_KEY, 0, ttl=_STATE_TTL)
            return raw
        # raw is ON/SCALED — count consecutive constructive cycles (the counter
        # resets to 0 only on an OFF above, so it accumulates through recovery
        # even while the effective decision is still being held at OFF).
        streak += 1
        await cache.set(_STREAK_KEY, streak, ttl=_STATE_TTL)
        if prev_decision == "OFF" and streak < n:
            held = MacroState(**{**raw.to_dict()})
            held.decision = "OFF"
            held.size_mult = 0.0
            held.rationale = (
                f"recovering ({streak}/{n} stable cycles) — {raw.rationale}"
            )
            return held
        return raw

    async def _persist(self, state: MacroState) -> None:
        await cache.set(_STATE_KEY, state.to_dict(), ttl=_STATE_TTL)

    @staticmethod
    async def load_state() -> MacroState | None:
        """Rehydrate the last persisted state (zero-downtime recovery)."""
        raw = await cache.get(_STATE_KEY)
        return MacroState(**raw) if raw else None
