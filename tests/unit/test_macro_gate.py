"""Unit tests for the macro / market-timing gate (Phase 2)."""

from datetime import datetime

import pandas as pd
import pytest
import pytz
from src.trading.macro_calendar import (
    _first_friday,
    active_blackout,
    upcoming_events,
)
from src.trading.macro_data import (
    MacroData,
    _clean_closes,
    _net_liquidity_trend,
    _pct_change,
    _sma_flag,
)
from src.trading.macro_gate import (
    MacroState,
    MacroTimingGate,
    _decision_from_score,
    score_macro,
)
from src.trading.sentiment_local import SentimentScorer

_ET = pytz.timezone("US/Eastern")


# ── score_macro ────────────────────────────────────────────────────────


@pytest.mark.unit
def test_score_macro_constructive_is_bull_and_on():
    data = MacroData(
        spy_above_sma200=True,
        spy_above_sma50=True,
        qqq_above_sma200=True,
        vix=13.0,
        hyg_lqd_trend=0.02,
        net_liquidity_trend=10.0,
    )
    regime, score, _ = score_macro(data)
    assert regime == "BULL"
    assert score > 0
    assert _decision_from_score(score, regime) == ("ON", 1.0)


@pytest.mark.unit
def test_score_macro_downtrend_is_bear():
    regime, score, reasons = score_macro(MacroData(spy_above_sma200=False))
    assert regime == "BEAR"
    assert score < 0
    assert any("SMA200" in r for r in reasons)


@pytest.mark.unit
def test_score_macro_high_vix_is_volatile_and_off():
    regime, score, _ = score_macro(MacroData(spy_above_sma200=False, vix=35.0))
    assert regime == "VOLATILE"
    assert _decision_from_score(score, regime)[0] == "OFF"


@pytest.mark.unit
def test_score_macro_missing_data_is_neutral():
    regime, score, reasons = score_macro(MacroData())
    assert regime == "BULL"
    assert score == 0.0
    assert reasons == []


@pytest.mark.unit
def test_volatile_regime_never_full_size():
    # Constructive score but VIX-driven volatile regime → scaled, not ON.
    decision, size = _decision_from_score(0.5, "VOLATILE")
    assert decision == "SCALED"
    assert size == 0.5


@pytest.mark.unit
def test_scaled_band_is_graded_and_monotonic():
    # Buy multiplier shrinks smoothly as the tape weakens (no hard step).
    strong = _decision_from_score(0.05, "BULL")
    mid = _decision_from_score(-0.30, "BULL")
    weak = _decision_from_score(-0.60, "BULL")
    assert strong[0] == mid[0] == weak[0] == "SCALED"
    assert 1.0 > strong[1] > mid[1] > weak[1] >= 0.25
    # OFF only on genuinely stacked risk.
    assert _decision_from_score(-0.80, "BULL") == ("OFF", 0.0)


# ── net liquidity + returns helpers ────────────────────────────────────


@pytest.mark.unit
def test_net_liquidity_trend_rising():
    # newest-first; WALCL/$mn, RRP/$bn, TGA/$mn
    walcl = [7_000_000, 6_990_000, 6_980_000, 6_970_000, 6_900_000]
    rrp = [400.0, 405.0, 410.0, 415.0, 500.0]
    tga = [700_000, 705_000, 710_000, 715_000, 800_000]
    trend = _net_liquidity_trend(walcl, rrp, tga)
    assert trend is not None and trend > 0


@pytest.mark.unit
def test_net_liquidity_trend_missing_leg_is_none():
    assert _net_liquidity_trend(None, [1], [1]) is None


@pytest.mark.unit
def test_pct_change_too_short_is_none():
    assert _pct_change(pd.Series([1.0, 2.0]), 5) is None


# ── data-quality guards (_clean_closes / _sma_flag) ────────────────────


def _daily_df(values: list[float], *, end: datetime) -> pd.DataFrame:
    idx = pd.date_range(end=end, periods=len(values), freq="D")
    return pd.DataFrame({"Close": values}, index=idx)


@pytest.mark.unit
def test_sma_flag_insufficient_bars_is_none():
    # Fewer than `period` bars ⇒ None (neutral), never a fake bearish False.
    assert _sma_flag(pd.Series([100.0, 101.0, 102.0]), 50) is None


@pytest.mark.unit
def test_sma_flag_above_and_below():
    rising = pd.Series([float(i) for i in range(1, 61)])
    assert _sma_flag(rising, 50) is True
    falling = pd.Series([float(i) for i in range(60, 0, -1)])
    assert _sma_flag(falling, 50) is False


@pytest.mark.unit
def test_sma_flag_nan_last_is_none():
    s = pd.Series([float(i) for i in range(1, 60)] + [float("nan")])
    assert _sma_flag(s, 50) is None


@pytest.mark.unit
def test_clean_closes_drops_partial_today_bar(monkeypatch):
    # Pin "now" to 10:00 ET on a fixed date so the test is wall-clock-independent.
    fixed = _ET.localize(datetime(2026, 3, 4, 10, 0))
    monkeypatch.setattr("src.trading.macro_data.now_us", lambda: fixed)
    df = _daily_df([100.0, 101.0, 999.0], end=datetime(2026, 3, 4))
    cleaned = _clean_closes(df)
    assert len(cleaned) == 2  # the still-forming 999.0 bar is dropped
    assert 999.0 not in cleaned.values


@pytest.mark.unit
def test_clean_closes_keeps_bar_after_close(monkeypatch):
    # Same today-dated bar, but the session has closed (16:30 ET) ⇒ keep it.
    fixed = _ET.localize(datetime(2026, 3, 4, 16, 30))
    monkeypatch.setattr("src.trading.macro_data.now_us", lambda: fixed)
    df = _daily_df([100.0, 101.0, 102.0], end=datetime(2026, 3, 4))
    assert len(_clean_closes(df)) == 3


@pytest.mark.unit
def test_clean_closes_keeps_completed_history():
    # All bars dated in the past ⇒ nothing dropped.
    df = _daily_df([100.0, 101.0, 102.0], end=datetime(2020, 1, 3))
    assert len(_clean_closes(df)) == 3


# ── calendar ───────────────────────────────────────────────────────────


@pytest.mark.unit
def test_first_friday_known_values():
    assert _first_friday(2026, 8) == __import__("datetime").date(2026, 8, 7)
    assert _first_friday(2026, 1) == __import__("datetime").date(2026, 1, 2)


@pytest.mark.unit
def test_active_blackout_inside_fomc_window():
    ref = _ET.localize(datetime(2026, 7, 29, 15, 0))  # 1h after 2026-07-29 FOMC
    event = active_blackout(12.0, ref)
    assert event is not None and event.kind == "FOMC"


@pytest.mark.unit
def test_active_blackout_clear_day_is_none():
    ref = _ET.localize(datetime(2026, 7, 22, 12, 0))
    assert active_blackout(6.0, ref) is None


@pytest.mark.unit
def test_upcoming_events_sorted():
    events = upcoming_events(_ET.localize(datetime(2026, 7, 1, 12, 0)))
    times = [e.when for e in events]
    assert times == sorted(times)


# ── sentiment scorer (backend-agnostic) ────────────────────────────────


@pytest.mark.unit
def test_sentiment_scorer_directional():
    sc = SentimentScorer(allow_finbert=False)
    assert sc.score_text("stocks surge rally beats record profit") > 0
    assert sc.score_text("market crash plunge recession losses") < 0


@pytest.mark.unit
def test_sentiment_scorer_empty_and_mean():
    sc = SentimentScorer(allow_finbert=False)
    assert sc.score_text("") == 0.0
    assert sc.score_many([]) is None


# ── hysteresis (in-memory cache fake) ──────────────────────────────────


class _FakeCache:
    def __init__(self) -> None:
        self.store: dict = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ttl=None):
        self.store[key] = value

    async def delete(self, key):
        self.store.pop(key, None)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_hysteresis_off_applies_immediately(monkeypatch):
    fake = _FakeCache()
    monkeypatch.setattr("src.trading.macro_gate.cache", fake)
    gate = MacroTimingGate(quant=None)
    off = MacroState(decision="OFF", size_mult=0.0, regime="BEAR")
    result = await gate._apply_hysteresis(off)
    assert result.decision == "OFF"
    assert fake.store["trading:macro:stable_streak"] == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_hysteresis_recovery_needs_stable_cycles(monkeypatch):
    fake = _FakeCache()
    fake.store["trading:macro:state"] = {"decision": "OFF"}
    fake.store["trading:macro:stable_streak"] = 0
    monkeypatch.setattr("src.trading.macro_gate.cache", fake)
    monkeypatch.setattr("src.trading.macro_gate.settings.macro_hysteresis_cycles", 3)
    gate = MacroTimingGate(quant=None)
    on = MacroState(decision="ON", size_mult=1.0, regime="BULL")

    # Cycle 1 & 2: still held OFF while recovering.
    r1 = await gate._apply_hysteresis(on)
    assert r1.decision == "OFF" and "recovering" in r1.rationale
    fake.store["trading:macro:state"] = {"decision": "OFF"}  # not yet ON
    r2 = await gate._apply_hysteresis(on)
    assert r2.decision == "OFF"
    # Cycle 3: streak reaches 3 → ON.
    fake.store["trading:macro:state"] = {"decision": "OFF"}
    r3 = await gate._apply_hysteresis(on)
    assert r3.decision == "ON"
