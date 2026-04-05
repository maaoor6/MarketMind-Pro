"""Interactive dark-mode Plotly charts for MarketMind-Pro."""

from pathlib import Path
from typing import Any

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from src.quant.fibonacci import FibonacciLevels
from src.utils.logger import get_logger

logger = get_logger(__name__)

DARK_TEMPLATE = "plotly_dark"
CHART_OUTPUT_DIR = Path("data/processed/charts")
CHART_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def candlestick_with_indicators(
    df: pd.DataFrame,
    ticker: str,
    fib_levels: FibonacciLevels | None = None,
    mas: dict[str, pd.Series] | None = None,
    volume: pd.Series | None = None,
) -> go.Figure:
    """Build a full-featured dark-mode candlestick chart.

    Args:
        df: DataFrame with Open, High, Low, Close columns.
        ticker: Ticker symbol for title.
        fib_levels: Optional Fibonacci levels to overlay.
        mas: Optional dict of moving average series (e.g., {'SMA_20': ...}).
        volume: Optional volume series for subplot.

    Returns:
        Plotly Figure object.
    """
    has_volume = volume is not None and not volume.empty

    fig = make_subplots(
        rows=2 if has_volume else 1,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.03,
        row_heights=[0.75, 0.25] if has_volume else [1.0],
        subplot_titles=(f"{ticker} — OHLCV", "Volume") if has_volume else (f"{ticker}",),
    )

    # ── Candlestick ──────────────────────────────────────────────────
    fig.add_trace(
        go.Candlestick(
            x=df.index,
            open=df["Open"],
            high=df["High"],
            low=df["Low"],
            close=df["Close"],
            name=ticker,
            increasing_line_color="#00e676",  # Green
            decreasing_line_color="#ff1744",  # Red
        ),
        row=1,
        col=1,
    )

    # ── Moving Averages ──────────────────────────────────────────────
    ma_colors = {
        "SMA_20": "#ffeb3b",
        "EMA_20": "#ffd54f",
        "SMA_50": "#29b6f6",
        "EMA_50": "#4fc3f7",
        "SMA_100": "#ab47bc",
        "EMA_100": "#ce93d8",
        "SMA_150": "#ff7043",
        "EMA_150": "#ff8a65",
        "SMA_200": "#ef5350",
        "EMA_200": "#e57373",
    }
    if mas:
        for ma_name, ma_series in mas.items():
            fig.add_trace(
                go.Scatter(
                    x=ma_series.index,
                    y=ma_series,
                    name=ma_name,
                    line={"color": ma_colors.get(ma_name, "#ffffff"), "width": 1.5},
                    opacity=0.8,
                ),
                row=1,
                col=1,
            )

    # ── Fibonacci Levels ─────────────────────────────────────────────
    if fib_levels:
        fib_colors = {
            "23.6%": "rgba(255,235,59,0.6)",
            "38.2%": "rgba(102,187,106,0.6)",
            "50.0%": "rgba(41,182,246,0.6)",
            "61.8%": "rgba(255,112,67,0.8)",   # Golden ratio — most important
            "78.6%": "rgba(239,83,80,0.6)",
        }
        for label, price in fib_levels.retracements.items():
            if label in fib_colors:
                fig.add_hline(
                    y=price,
                    line_dash="dash",
                    line_color=fib_colors[label],
                    annotation_text=f"Fib {label}: ${price:,.2f}",
                    annotation_position="left",
                    row=1,
                    col=1,
                )

    # ── Volume ───────────────────────────────────────────────────────
    if has_volume:
        vol_colors = [
            "#00e676" if c >= o else "#ff1744"
            for c, o in zip(df["Close"], df["Open"])
        ]
        fig.add_trace(
            go.Bar(x=df.index, y=volume, name="Volume", marker_color=vol_colors, showlegend=False),
            row=2,
            col=1,
        )

    # ── Layout ───────────────────────────────────────────────────────
    fig.update_layout(
        template=DARK_TEMPLATE,
        title={
            "text": f"<b>{ticker}</b> — MarketMind-Pro Analysis",
            "font": {"size": 20, "color": "#ffffff"},
        },
        xaxis_rangeslider_visible=False,
        paper_bgcolor="#0d1117",
        plot_bgcolor="#161b22",
        legend={
            "bgcolor": "rgba(0,0,0,0)",
            "bordercolor": "#30363d",
            "borderwidth": 1,
        },
        margin={"l": 60, "r": 60, "t": 80, "b": 40},
        height=700,
    )
    fig.update_xaxes(
        gridcolor="#21262d",
        zerolinecolor="#30363d",
        showgrid=True,
    )
    fig.update_yaxes(
        gridcolor="#21262d",
        zerolinecolor="#30363d",
        showgrid=True,
    )

    return fig


def save_chart_html(fig: go.Figure, ticker: str) -> Path:
    """Save interactive chart as standalone HTML file."""
    output_path = CHART_OUTPUT_DIR / f"{ticker.lower()}_chart.html"
    fig.write_html(
        str(output_path),
        include_plotlyjs="cdn",
        full_html=True,
        config={"displayModeBar": True, "scrollZoom": True},
    )
    logger.info("chart_saved", ticker=ticker, path=str(output_path))
    return output_path


def save_chart_png(fig: go.Figure, ticker: str) -> Path:
    """Save chart as static PNG for Telegram image sharing."""
    output_path = CHART_OUTPUT_DIR / f"{ticker.lower()}_chart.png"
    fig.write_image(str(output_path), width=1400, height=700, scale=2)
    logger.info("chart_png_saved", ticker=ticker, path=str(output_path))
    return output_path


def generate_full_chart(
    df: pd.DataFrame,
    ticker: str,
    fib_levels: FibonacciLevels | None = None,
    mas: dict[str, pd.Series] | None = None,
    save_html: bool = True,
    save_png: bool = False,
) -> dict[str, Any]:
    """Generate and optionally save a full analysis chart.

    Returns dict with figure and file paths.
    """
    volume = df["Volume"] if "Volume" in df.columns else None
    fig = candlestick_with_indicators(df, ticker, fib_levels=fib_levels, mas=mas, volume=volume)

    result: dict[str, Any] = {"figure": fig}

    if save_html:
        result["html_path"] = str(save_chart_html(fig, ticker))

    if save_png:
        result["png_path"] = str(save_chart_png(fig, ticker))

    return result
