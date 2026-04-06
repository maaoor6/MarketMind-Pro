"""Streamlit local dashboard for MarketMind-Pro.

Run with:
    streamlit run src/ui/dashboard.py
"""

import asyncio
from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yfinance as yf
from plotly.subplots import make_subplots

from src.quant.arbitrage import DUAL_LISTED, calculate_arbitrage
from src.quant.fibonacci import calculate_fibonacci
from src.quant.indicators import all_moving_averages, generate_signals, rsi

# ── Page config ────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="MarketMind-Pro",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Sidebar ────────────────────────────────────────────────────────────────────

st.sidebar.title("MarketMind-Pro")
st.sidebar.caption("TASE & US Market Intelligence")

ticker_input = (
    st.sidebar.text_input(
        "Ticker Symbol",
        value="TEVA",
        placeholder="e.g. AAPL, TEVA, MSFT",
    )
    .upper()
    .strip()
)

period = st.sidebar.selectbox(
    "Time Period",
    options=["3mo", "6mo", "1y", "2y", "5y"],
    index=2,
)

show_ma = st.sidebar.multiselect(
    "Moving Averages",
    options=[
        "SMA_20",
        "EMA_20",
        "SMA_50",
        "EMA_50",
        "SMA_150",
        "EMA_150",
        "SMA_200",
        "EMA_200",
    ],
    default=["SMA_50", "SMA_200"],
)

show_fibonacci = st.sidebar.checkbox("Show Fibonacci Levels", value=True)
show_arbitrage = st.sidebar.checkbox(
    "Arbitrage Analysis",
    value=ticker_input in DUAL_LISTED,
)

analyze_btn = st.sidebar.button("Analyze", type="primary", use_container_width=True)

# ── Helpers ────────────────────────────────────────────────────────────────────

MA_COLORS = {
    "SMA_20": "#ffeb3b",
    "EMA_20": "#ffd54f",
    "SMA_50": "#29b6f6",
    "EMA_50": "#4fc3f7",
    "SMA_150": "#ff7043",
    "EMA_150": "#ff8a65",
    "SMA_200": "#ef5350",
    "EMA_200": "#e57373",
}


@st.cache_data(ttl=60, show_spinner=False)
def fetch_data(ticker: str, period: str) -> pd.DataFrame:
    """Download OHLCV data from yfinance (cached 60s)."""
    df = yf.download(
        ticker, period=period, interval="1d", progress=False, auto_adjust=True
    )
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df


def build_chart(
    df: pd.DataFrame,
    ticker: str,
    selected_mas: list[str],
    fib_levels=None,
) -> go.Figure:
    """Build dark-mode candlestick + volume chart."""
    fig = make_subplots(
        rows=3,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.03,
        row_heights=[0.6, 0.2, 0.2],
        subplot_titles=(f"{ticker} — OHLCV", "Volume", "RSI (14)"),
    )

    # Candlestick
    fig.add_trace(
        go.Candlestick(
            x=df.index,
            open=df["Open"],
            high=df["High"],
            low=df["Low"],
            close=df["Close"],
            name=ticker,
            increasing_line_color="#00e676",
            decreasing_line_color="#ff1744",
            showlegend=False,
        ),
        row=1,
        col=1,
    )

    # Moving averages
    mas = all_moving_averages(df["Close"])
    for ma_name in selected_mas:
        if ma_name in mas:
            fig.add_trace(
                go.Scatter(
                    x=df.index,
                    y=mas[ma_name],
                    name=ma_name,
                    line={"color": MA_COLORS.get(ma_name, "#ffffff"), "width": 1.5},
                    opacity=0.85,
                ),
                row=1,
                col=1,
            )

    # Fibonacci horizontal lines
    if fib_levels:
        fib_color_map = {
            "23.6%": "rgba(255,235,59,0.6)",
            "38.2%": "rgba(41,182,246,0.6)",
            "50.0%": "rgba(171,71,188,0.6)",
            "61.8%": "rgba(255,112,67,0.6)",
            "78.6%": "rgba(239,83,80,0.6)",
        }
        for label, price in fib_levels.retracements.items():
            if label in ("0.0%", "100.0%"):
                continue
            fig.add_hline(
                y=price,
                line_dash="dot",
                line_color=fib_color_map.get(label, "rgba(200,200,200,0.4)"),
                annotation_text=f"Fib {label}  ${price:,.2f}",
                annotation_position="left",
                annotation_font_size=10,
                row=1,
                col=1,
            )

    # Volume bars
    colors = [
        "#00e676" if c >= o else "#ff1744"
        for c, o in zip(df["Close"], df["Open"], strict=False)
    ]
    fig.add_trace(
        go.Bar(
            x=df.index,
            y=df["Volume"],
            name="Volume",
            marker_color=colors,
            showlegend=False,
        ),
        row=2,
        col=1,
    )

    # RSI
    rsi_result = rsi(df["Close"])
    fig.add_trace(
        go.Scatter(
            x=df.index,
            y=rsi_result.values,
            name="RSI",
            line={"color": "#4fc3f7", "width": 1.5},
            showlegend=False,
        ),
        row=3,
        col=1,
    )
    fig.add_hline(
        y=70, line_dash="dash", line_color="rgba(239,83,80,0.5)", row=3, col=1
    )
    fig.add_hline(
        y=30, line_dash="dash", line_color="rgba(0,230,118,0.5)", row=3, col=1
    )

    fig.update_layout(
        template="plotly_dark",
        height=750,
        margin={"l": 60, "r": 60, "t": 40, "b": 20},
        xaxis_rangeslider_visible=False,
        legend={"orientation": "h", "y": 1.02, "x": 0},
        paper_bgcolor="#0e1117",
        plot_bgcolor="#0e1117",
    )
    fig.update_yaxes(gridcolor="#1e2d40")
    fig.update_xaxes(gridcolor="#1e2d40")

    return fig


# ── Main content ───────────────────────────────────────────────────────────────

st.title("MarketMind-Pro Dashboard")

if not ticker_input:
    st.info("Enter a ticker symbol in the sidebar and click **Analyze**.")
    st.stop()

# Auto-run on first load or when button pressed
if analyze_btn or "last_ticker" not in st.session_state:
    st.session_state["last_ticker"] = ticker_input
    st.session_state["last_period"] = period

with st.spinner(f"Fetching data for {ticker_input}..."):
    df = fetch_data(ticker_input, period)

if df.empty:
    st.error(f"No data found for **{ticker_input}**. Check the ticker symbol.")
    st.stop()

# ── Key metrics row ────────────────────────────────────────────────────────────

latest_close = float(df["Close"].iloc[-1])
prev_close = float(df["Close"].iloc[-2])
change = latest_close - prev_close
change_pct = (change / prev_close) * 100
high_52w = float(df["High"].max())
low_52w = float(df["Low"].min())
avg_volume = int(df["Volume"].mean())

col1, col2, col3, col4, col5 = st.columns(5)
col1.metric(
    "Last Price", f"${latest_close:,.2f}", f"{change:+.2f} ({change_pct:+.2f}%)"
)
col2.metric("52W High", f"${high_52w:,.2f}")
col3.metric("52W Low", f"${low_52w:,.2f}")
col4.metric("Avg Volume", f"{avg_volume:,}")
col5.metric("Data Points", f"{len(df):,} days")

st.divider()

# ── Signals ────────────────────────────────────────────────────────────────────

signals = generate_signals(df["Close"], df["Volume"])

rsi_val = signals.get("rsi")
rsi_signal = signals.get("rsi_signal", "NEUTRAL")
macd_hist = signals.get("macd_histogram", 0.0)
macd_trend = "BULLISH" if macd_hist > 0 else "BEARISH"
vol_spike = signals.get("volume_spike", False)

sig_col1, sig_col2, sig_col3 = st.columns(3)
rsi_color = (
    "normal"
    if rsi_signal == "NEUTRAL"
    else ("inverse" if rsi_signal == "OVERBOUGHT" else "off")
)
sig_col1.metric(
    "RSI (14)",
    f"{rsi_val:.1f}" if rsi_val else "N/A",
    rsi_signal,
    delta_color="inverse" if rsi_signal == "OVERBOUGHT" else "normal",
)
sig_col2.metric(
    "MACD Histogram",
    f"{macd_hist:.4f}",
    macd_trend,
    delta_color="normal" if macd_trend == "BULLISH" else "inverse",
)
sig_col3.metric(
    "Volume Spike",
    "YES" if vol_spike else "NO",
    "Above 2x avg" if vol_spike else "Normal",
    delta_color="off",
)

# ── Chart ──────────────────────────────────────────────────────────────────────

fib_levels = None
if show_fibonacci:
    try:
        fib_levels = calculate_fibonacci(df["Close"], ticker=ticker_input)
    except ValueError:
        pass

fig = build_chart(df, ticker_input, show_ma, fib_levels=fib_levels)
st.plotly_chart(fig, use_container_width=True)

# ── Fibonacci table ────────────────────────────────────────────────────────────

if show_fibonacci and fib_levels:
    st.subheader(f"Fibonacci Levels — {ticker_input}")
    fib_col1, fib_col2 = st.columns(2)

    with fib_col1:
        st.caption(
            f"Trend: {'UPTREND' if fib_levels.trend == 'UPTREND' else 'DOWNTREND'}"
        )
        st.caption(f"Position: {fib_levels.price_position()}")
        retr_data = [
            {
                "Level": label,
                "Price ($)": f"{price:,.2f}",
                "Status": (
                    "Support" if price < fib_levels.current_price else "Resistance"
                ),
            }
            for label, price in fib_levels.retracements.items()
        ]
        st.dataframe(pd.DataFrame(retr_data), hide_index=True, use_container_width=True)

    with fib_col2:
        st.caption("Extension Levels")
        ext_data = [
            {"Level": label, "Price ($)": f"{price:,.2f}"}
            for label, price in fib_levels.extensions.items()
        ]
        st.dataframe(pd.DataFrame(ext_data), hide_index=True, use_container_width=True)

        if fib_levels.nearest_support:
            st.success(f"Nearest Support: **${fib_levels.nearest_support:,.2f}**")
        if fib_levels.nearest_resistance:
            st.error(f"Nearest Resistance: **${fib_levels.nearest_resistance:,.2f}**")

# ── Arbitrage ──────────────────────────────────────────────────────────────────

if show_arbitrage:
    st.subheader(f"Arbitrage Analysis — {ticker_input}")

    if ticker_input not in DUAL_LISTED:
        st.warning(
            f"**{ticker_input}** is not in the dual-listed watchlist. "
            f"Tracked tickers: {', '.join(DUAL_LISTED.keys())}"
        )
    else:
        tase_ticker = DUAL_LISTED[ticker_input]
        with st.spinner(f"Fetching TASE price for {tase_ticker}..."):
            df_tase = fetch_data(tase_ticker, "5d")

        if df_tase.empty:
            st.error(f"Could not fetch TASE data for {tase_ticker}.")
        else:
            price_tase_ils = float(df_tase["Close"].iloc[-1])
            price_us_usd = latest_close

            arb = asyncio.run(
                calculate_arbitrage(
                    ticker_us=ticker_input,
                    price_us_usd=price_us_usd,
                    price_tase_ils=price_tase_ils,
                )
            )

            arb_col1, arb_col2, arb_col3, arb_col4 = st.columns(4)
            arb_col1.metric("US Price (USD)", f"${arb.price_us_usd:,.3f}")
            arb_col2.metric("TASE Price (ILS)", f"₪{arb.price_tase_ils:,.3f}")
            arb_col3.metric("TASE in USD", f"${arb.price_tase_in_usd:,.3f}")
            arb_col4.metric(
                "Gap",
                f"{arb.gap_pct:.2f}%",
                arb.gap_direction.replace("_", " "),
                delta_color="off",
            )

            arb_col1.metric("USD/ILS Rate", f"{arb.usd_ils_rate:.4f}")

            if arb.is_opportunity:
                st.success(f"ARBITRAGE OPPORTUNITY DETECTED — {arb.gap_pct:.2f}% gap")
            else:
                st.info(
                    f"No significant arbitrage (gap {arb.gap_pct:.2f}% < 0.5% threshold)"
                )

# ── Footer ─────────────────────────────────────────────────────────────────────

st.divider()
st.caption(
    f"MarketMind-Pro | Data: Yahoo Finance | Last update: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
)
