"""Telegram Dispatcher Agent — bot commands, inline keyboards, automated reports."""

import asyncio
import html
import re
from datetime import UTC, datetime
from datetime import time as dt_time

import pytz
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from src.agents.news_search_agent import NewsSearchAgent
from src.agents.quant_engine import QuantEngine
from src.database.models import UserAlert
from src.database.session import AsyncSessionLocal
from src.quant.fibonacci import calculate_fibonacci
from src.quant.fundamentals import (
    CompanyProfile,
    EarningsReport,
    fetch_company_profile,
    fetch_earnings_report,
    fetch_insider_transactions,
    format_earnings_english,
    format_insiders_english,
    format_profile_english,
    is_reporting_today,
    save_insider_transactions,
    was_reported_today,
)
from src.quant.indicators import MomentumScore, momentum_score
from src.ui.publisher import PAGES_BASE_URL, publish_ticker_chart
from src.utils.config import settings
from src.utils.logger import get_logger
from src.utils.timezone_utils import (
    market_status,
    now_us,
    time_to_nyse_open,
)

logger = get_logger(__name__)

_ET = pytz.timezone("America/New_York")

# Module-level agent instances
_quant_engine = QuantEngine()
_news_agent = NewsSearchAgent()

# Fire-and-forget background task registry (prevents GC before completion)
_background_tasks: set[asyncio.Task] = set()

# ── HTML helpers ────────────────────────────────────────────────────────────────


def b(text: str) -> str:
    """Bold HTML."""
    return f"<b>{html.escape(str(text))}</b>"


def code(text: str) -> str:
    """Monospace HTML."""
    return f"<code>{html.escape(str(text))}</code>"


def link(text: str, url: str) -> str:
    """Inline HTML link."""
    return f'<a href="{url}">{html.escape(text)}</a>'


def _session_label(mkt: dict) -> str:
    """Return an italicised session tag for display next to prices.

    Returns '' during regular hours, ' <i>(pre-market)</i>' before open,
    and ' <i>(after-hours)</i>' after close.
    """
    if mkt.get("nyse_open"):
        return ""
    us_now = now_us()
    market_open_time = us_now.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close_time = us_now.replace(hour=16, minute=0, second=0, microsecond=0)
    if us_now < market_open_time:
        return " <i>(pre-market)</i>"
    if us_now >= market_close_time:
        return " <i>(after-hours)</i>"
    return ""


# ── Command Handlers ────────────────────────────────────────────────────────────


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command — English welcome with NYSE countdown."""
    keyboard = [
        [
            InlineKeyboardButton("📊 Analyze Stock", callback_data="prompt_analyze"),
            InlineKeyboardButton("🌡️ System Health", callback_data="health"),
        ],
        [
            InlineKeyboardButton("📰 Market Report", callback_data="market_open"),
            InlineKeyboardButton("📐 Fibonacci", callback_data="prompt_fibonacci"),
        ],
        [
            InlineKeyboardButton("🆚 Compare Stocks", callback_data="prompt_compare"),
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    nyse_countdown = time_to_nyse_open()
    mkt = market_status()
    nyse_status = "🟢 Open" if mkt["nyse_open"] else "🔴 Closed"

    msg = (
        "🤖 <b>MarketMind-Pro</b> — Institutional Trading Intelligence\n\n"
        "📡 <b>Market Status:</b>\n"
        f"  🇺🇸 NYSE: {nyse_status}\n"
        f"  ⏱ {nyse_countdown}\n\n"
        "📋 <b>Available Commands:</b>\n"
        "  • /analyze <code>[TICKER]</code> — Full technical + fundamental + news report\n"
        "  • /news <code>[TICKER]</code> — Top 5 live news headlines with summaries\n"
        "  • /compare <code>[T1] [T2]</code> — Side-by-side stock comparison\n"
        "  • /fibonacci <code>[TICKER]</code> — 52-week Fibonacci levels\n"
        "  • /sectors — S&P 500 sector rotation (daily performance)\n"
        "  • /setalert <code>[TICKER] [PRICE]</code> — Set a price alert\n"
        "  • /myalerts — View your active alerts\n"
        "  • /cancelalert <code>[TICKER]</code> — Cancel an alert\n"
        "  • /health — System status\n\n"
        "Choose an action:"
    )

    await update.message.reply_text(
        msg, reply_markup=reply_markup, parse_mode=ParseMode.HTML
    )


async def _wait_for_pages(url: str, timeout: int = 60) -> bool:
    """Poll a GitHub Pages URL until it returns HTTP 200 or timeout expires.

    GitHub Pages can take 10–60 seconds to propagate after a Contents API push.
    Returns True if the page became available, False on timeout.
    """
    import httpx as _httpx

    deadline = asyncio.get_event_loop().time() + timeout
    async with _httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
        while asyncio.get_event_loop().time() < deadline:
            try:
                resp = await client.head(url)
                if resp.status_code == 200:
                    logger.debug("pages_ready", url=url)
                    return True
            except Exception as exc:  # noqa: BLE001
                logger.debug("pages_poll_error", url=url, error=str(exc))
            await asyncio.sleep(4)
    logger.warning("pages_timeout", url=url, timeout=timeout)
    return False


async def cmd_analyze(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /analyze [TICKER] — institutional-grade English analysis report."""
    msg_obj = update.message or (
        update.callback_query.message if update.callback_query else None
    )
    if not msg_obj:
        return

    if not context.args:
        await msg_obj.reply_text(
            "❗ Usage: /analyze TICKER\nExample: <code>/analyze AAPL</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    ticker = context.args[0].upper().strip()
    await msg_obj.reply_text(
        f"⏳ Analyzing <b>{html.escape(ticker)}</b>... please wait.",
        parse_mode=ParseMode.HTML,
    )

    try:
        # ── Phase 1: Quant + Sentiment (parallel) ──
        quant_signal, sentiment = await asyncio.gather(
            _quant_engine.analyze(ticker),
            _news_agent.analyze_sentiment(ticker),
            return_exceptions=True,
        )

        if isinstance(quant_signal, Exception):
            await msg_obj.reply_text(
                f"❌ Analysis failed: <code>{html.escape(str(quant_signal))}</code>",
                parse_mode=ParseMode.HTML,
            )
            return

        # ── Phase 2: Fundamentals + Insiders + Earnings (parallel, best-effort) ──
        profile_result: CompanyProfile | Exception | None = None
        insiders_result: list = []
        earnings_result: EarningsReport | None = None
        chart_url: str | None = None

        try:
            profile_result, insiders_raw, earnings_result = await asyncio.gather(
                fetch_company_profile(ticker),
                fetch_insider_transactions(ticker),
                fetch_earnings_report(ticker),
                return_exceptions=True,
            )
            if isinstance(earnings_result, Exception):
                earnings_result = None
            if not isinstance(insiders_raw, Exception) and insiders_raw:
                insiders_result = insiders_raw
                task = asyncio.create_task(
                    save_insider_transactions(ticker, insiders_result)
                )
                _background_tasks.add(task)
                task.add_done_callback(_background_tasks.discard)
        except Exception as fund_exc:
            logger.warning(
                "fundamentals_fetch_failed", ticker=ticker, error=str(fund_exc)
            )

        # ── Phase 3: Publish chart to GitHub Pages (best-effort) ──
        # Pre-construct the expected URL so the button always shows even if publish fails
        chart_url = f"{PAGES_BASE_URL}/{ticker.lower()}_chart.html"
        chart_published = False
        df_for_chart = None
        chart_error: str | None = None
        try:
            df_for_chart = await _quant_engine.fetch_price_data(
                ticker, period="1y", interval="1d"
            )
            fib_for_chart = calculate_fibonacci(
                df_for_chart["Close"].squeeze(), ticker=ticker
            )
            chart_url = await publish_ticker_chart(
                ticker, df_for_chart, fib_levels=fib_for_chart
            )
            # Poll until GitHub Pages serves the file (avoids user seeing 404)
            chart_published = await _wait_for_pages(chart_url, timeout=60)
        except Exception as pub_exc:
            chart_error = str(pub_exc)
            logger.warning("chart_publish_failed", ticker=ticker, error=chart_error)

        # ── Build message ──
        signals = quant_signal.signals
        fib = quant_signal.fibonacci
        mkt = quant_signal.market_status or {}

        price = signals.get("price", 0)
        rsi_val = signals.get("rsi")
        rsi_signal = signals.get("rsi_signal", "NEUTRAL")
        macd_line = signals.get("macd_line", 0)
        macd_sig = signals.get("macd_signal", 0)
        macd_hist = signals.get("macd_histogram", 0)
        vol_spike = signals.get("volume_spike", False)
        mas = signals.get("moving_averages", {})

        # Daily % change — use live prev_close from signals if available, else history
        day_pct_str = ""
        prev_close_live = signals.get("prev_close")
        if prev_close_live is not None and prev_close_live > 0:
            day_pct = (price - prev_close_live) / prev_close_live * 100
            pct_sign = "+" if day_pct >= 0 else ""
            pct_arrow = (
                "📈" if day_pct > 0.005 else ("📉" if day_pct < -0.005 else "➡️")
            )
            day_pct_str = f"  {pct_arrow} {pct_sign}{day_pct:.2f}%"
        elif df_for_chart is not None and len(df_for_chart) >= 2:
            prev_close_hist = float(df_for_chart["Close"].iloc[-2])
            curr_close_hist = float(df_for_chart["Close"].iloc[-1])
            day_pct = (curr_close_hist - prev_close_hist) / prev_close_hist * 100
            pct_sign = "+" if day_pct >= 0 else ""
            pct_arrow = (
                "📈" if day_pct > 0.005 else ("📉" if day_pct < -0.005 else "➡️")
            )
            day_pct_str = f"  {pct_arrow} {pct_sign}{day_pct:.2f}%"

        # Session label (pre-market / after-hours / live)
        session_label = _session_label(mkt)

        lines = [
            f"📊 <b>ANALYSIS — <code>{html.escape(ticker)}</code></b>",
            "━━━━━━━━━━━━━━━━━━━",
            f"💰 <b>Current Price:</b> <code>${price:,.2f}</code>{day_pct_str}{session_label}",
            f"🕐 <b>Updated:</b> {datetime.now(tz=_ET).strftime('%d/%m/%Y %H:%M')} ET",
            "",
        ]

        # ── Company profile ──
        if isinstance(profile_result, CompanyProfile):
            lines.append(format_profile_english(profile_result))
            lines.append("")

        # ── Technical indicators ──
        rsi_emoji, rsi_label = _rsi_label(rsi_signal)

        # Momentum Score (uses already-fetched signals + daily price series)
        ms: MomentumScore | None = None
        if df_for_chart is not None:
            try:
                ms = momentum_score(signals, df_for_chart["Close"].squeeze())
            except Exception:  # noqa: BLE001
                logger.debug("momentum_score_failed", ticker=ticker)

        lines += [
            "📉 <b>Technical Indicators:</b>",
            f"  RSI(14): <code>{rsi_val:.1f}</code>  {rsi_emoji} {rsi_label}",
            f"  MACD Line: <code>{macd_line:+.4f}</code>  |  Signal: <code>{macd_sig:+.4f}</code>",
            f"  MACD Histogram: <code>{macd_hist:+.4f}</code>  {'📈 Bullish' if macd_hist > 0 else '📉 Bearish'}",
            f"  Volume Spike: {'⚡ YES — Unusual Volume!' if vol_spike else '❌ No'}",
        ]
        if ms is not None:
            lines.append(
                f"  ⚡ Momentum Score: <code>{ms.score}/100</code>  {ms.emoji} {ms.label}"
            )
        lines.append("")

        # ── Multi-Timeframe Analysis ──
        try:
            tf_weekly, tf_monthly = await asyncio.gather(
                _quant_engine.analyze_timeframe(ticker, "1wk"),
                _quant_engine.analyze_timeframe(ticker, "1mo"),
                return_exceptions=True,
            )
            tf_rows = [
                (
                    "Daily  (1d)",
                    signals.get("rsi"),
                    signals.get("rsi_signal", "NEUTRAL"),
                    macd_hist > 0,
                ),
            ]
            for tf_label, tf_data in [
                ("Weekly (1wk)", tf_weekly),
                ("Monthly(1mo)", tf_monthly),
            ]:
                if isinstance(tf_data, Exception) or tf_data is None:
                    continue
                tf_rows.append(
                    (
                        tf_label,
                        tf_data.get("rsi"),
                        tf_data.get("rsi_signal", "NEUTRAL"),
                        tf_data.get("macd_bullish"),
                    )
                )
            tf_lines = ["📊 <b>Multi-Timeframe:</b>"]
            for tf_lbl, tf_rsi, tf_rsi_sig, tf_macd_bull in tf_rows:
                rsi_e, _ = _rsi_label(tf_rsi_sig or "NEUTRAL")
                rsi_str = (
                    f"RSI <code>{tf_rsi:.1f}</code> {rsi_e}" if tf_rsi else "RSI N/A"
                )
                macd_str = (
                    "MACD 📈"
                    if tf_macd_bull
                    else ("MACD 📉" if tf_macd_bull is not None else "MACD —")
                )
                tf_lines.append(f"  {tf_lbl}:  {rsi_str}  |  {macd_str}")
            lines += tf_lines + [""]
        except Exception:  # noqa: BLE001
            logger.debug("multitf_failed", ticker=ticker)

        # ── Moving averages ──
        if mas:
            lines.append("📏 <b>Moving Averages:</b>")
            for ma_key in ["SMA_20", "SMA_50", "SMA_150", "SMA_200"]:
                val = mas.get(ma_key)
                if val:
                    relation = "↑ Above" if price > val else "↓ Below"
                    lines.append(f"  {ma_key}: <code>${val:,.2f}</code>  {relation}")
            lines.append("")

        # ── Fibonacci ──
        if fib:
            trend_label = "Uptrend 📈" if fib["trend"] == "UPTREND" else "Downtrend 📉"
            high_52w = fib["high_52w"]
            low_52w = fib["low_52w"]
            pct_from_low = (
                ((price - low_52w) / (high_52w - low_52w) * 100)
                if (high_52w - low_52w) > 0
                else 0
            )

            lines += [
                "📐 <b>Fibonacci (52-Week):</b>",
                f"  {trend_label}  |  Position: {pct_from_low:.1f}% from Low",
                f"  High: <code>${high_52w:,.2f}</code>  |  Low: <code>${low_52w:,.2f}</code>",
                f"  🟢 Nearest Support:    <code>${fib['nearest_support']:,.2f}</code>",
                f"  🔴 Nearest Resistance: <code>${fib['nearest_resistance']:,.2f}</code>",
            ]
            retr = fib.get("retracements", {})
            for lvl in ["23.6%", "38.2%", "50.0%", "61.8%"]:
                if lvl in retr:
                    marker = (
                        " ◀ Price here"
                        if abs(retr[lvl] - price) / max(price, 0.001) < 0.015
                        else ""
                    )
                    lines.append(f"    {lvl}: <code>${retr[lvl]:,.2f}</code>{marker}")
            lines.append("")

        # ── Insiders ──
        if insiders_result:
            lines.append(format_insiders_english(ticker, insiders_result))
            lines.append("")

        # ── Earnings Report ──
        if earnings_result:
            _earnings_kw = {
                "earnings",
                "beats",
                "misses",
                "results",
                "revenue",
                "eps",
                "quarterly",
                "profit",
            }
            earnings_headlines: list[dict] = []
            if not isinstance(sentiment, Exception) and sentiment:
                earnings_headlines = [
                    h
                    for h in sentiment.recent_headlines
                    if any(kw in h.get("title", "").lower() for kw in _earnings_kw)
                ][:5]
            lines.append(format_earnings_english(earnings_result, earnings_headlines))
            lines.append("")

        # ── Sentiment / News ──
        if not isinstance(sentiment, Exception) and sentiment:
            lines += _format_news_block(ticker, sentiment)
            lines.append("")

        # ── Market status ──
        if mkt:
            nyse_str = "🟢 Open" if mkt.get("nyse_open") else "🔴 Closed"
            lines += [
                "🌍 <b>Market Status:</b>",
                f"  🇺🇸 NYSE: {nyse_str}",
            ]

        # ── Chart link ──
        if chart_error:
            short_err = chart_error[:80]
            lines += ["", f"⚠️ <i>Chart not published: {html.escape(short_err)}</i>"]
        elif not chart_published:
            # Publish succeeded but Pages propagation timed out — link still works shortly
            lines += ["", "<i>📊 Chart published — may take a few seconds to load.</i>"]

        # ── Inline keyboard — always show Interactive Chart button ──
        chart_btn = InlineKeyboardButton("📊 Interactive Chart", url=chart_url)
        keyboard = [
            [
                chart_btn,
                InlineKeyboardButton("🔄 Refresh", callback_data=f"analyze:{ticker}"),
                InlineKeyboardButton("📰 News", callback_data=f"news:{ticker}"),
            ],
        ]

        await msg_obj.reply_text(
            "\n".join(lines),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    except Exception as exc:
        logger.error("analyze_command_failed", ticker=ticker, error=str(exc))
        await msg_obj.reply_text(
            f"❌ Analysis failed: <code>{html.escape(str(exc))}</code>",
            parse_mode=ParseMode.HTML,
        )


async def cmd_news(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /news [TICKER] — top 5 recent headlines with summaries and links."""
    msg_obj = update.message or (
        update.callback_query.message if update.callback_query else None
    )
    if not msg_obj:
        return

    if not context.args:
        # No ticker → show global market snapshot
        await msg_obj.reply_text(
            "⏳ Fetching market data...", parse_mode=ParseMode.HTML
        )
        try:
            snapshot, sectors = await asyncio.gather(
                _fetch_market_snapshot(),
                _fetch_sector_data(),
            )
            header = (
                "📰 <b>MARKET SNAPSHOT</b>\n"
                f"🕐 {datetime.now(tz=_ET).strftime('%d/%m/%Y %H:%M')} ET\n"
                "━━━━━━━━━━━━━━━━━━━"
            )
            body = header + snapshot + _format_sector_block(sectors)
            await msg_obj.reply_text(body, parse_mode=ParseMode.HTML)
        except Exception as exc:
            await msg_obj.reply_text(
                f"❌ Failed to fetch market data: <code>{html.escape(str(exc))}</code>",
                parse_mode=ParseMode.HTML,
            )
        return

    ticker = context.args[0].upper().strip()
    await msg_obj.reply_text(
        f"⏳ Fetching news for <b>{html.escape(ticker)}</b>...",
        parse_mode=ParseMode.HTML,
    )

    try:
        sentiment = await _news_agent.analyze_sentiment(ticker)
        lines = _format_news_block(ticker, sentiment)
        keyboard = [
            [
                InlineKeyboardButton(
                    "📊 Full Analysis", callback_data=f"analyze:{ticker}"
                ),
                InlineKeyboardButton("🔄 Refresh", callback_data=f"news:{ticker}"),
            ]
        ]
        await msg_obj.reply_text(
            "\n".join(lines),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(keyboard),
            disable_web_page_preview=True,
        )

    except Exception as exc:
        await msg_obj.reply_text(
            f"❌ News fetch failed: <code>{html.escape(str(exc))}</code>",
            parse_mode=ParseMode.HTML,
        )


async def cmd_fibonacci(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /fibonacci [TICKER] — English Fibonacci report."""
    msg_obj = update.message or (
        update.callback_query.message if update.callback_query else None
    )
    if not msg_obj:
        return

    if not context.args:
        await msg_obj.reply_text(
            "❗ Usage: /fibonacci TICKER\nExample: <code>/fibonacci AAPL</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    ticker = context.args[0].upper().strip()
    try:
        df_result = await _quant_engine.fetch_price_data(
            ticker, period="1y", interval="1d"
        )
        closes = df_result["Close"].squeeze()
        levels = calculate_fibonacci(closes, ticker=ticker)
        price = levels.current_price
        spread = levels.high_52w - levels.low_52w
        pct_from_low = ((price - levels.low_52w) / spread * 100) if spread > 0 else 0
        trend_label = "Uptrend 📈" if levels.trend == "UPTREND" else "Downtrend 📉"

        lines = [
            f"📐 <b>FIBONACCI LEVELS — <code>{html.escape(ticker)}</code></b>",
            "━━━━━━━━━━━━━━━━━━━",
            f"  {trend_label}",
            f"  52W High: <code>${levels.high_52w:,.2f}</code>",
            f"  52W Low:  <code>${levels.low_52w:,.2f}</code>",
            f"  Current:  <code>${price:,.2f}</code>  ({pct_from_low:.1f}% from Low)",
            "",
            "📉 <b>Retracement Levels:</b>",
        ]

        for label, lvl_price in levels.retracements.items():
            relation = (
                "◀ Price here"
                if abs(lvl_price - price) / max(price, 0.001) < 0.015
                else ("↑ Above" if price > lvl_price else "↓ Below")
            )
            lines.append(f"  {label}: <code>${lvl_price:,.2f}</code>  {relation}")

        lines += ["", "📈 <b>Extension Levels:</b>"]
        for label, lvl_price in levels.extensions.items():
            lines.append(f"  {label}: <code>${lvl_price:,.2f}</code>")

        lines += [
            "",
            f"🟢 <b>Nearest Support:</b>    <code>${levels.nearest_support:,.2f}</code>",
            f"🔴 <b>Nearest Resistance:</b> <code>${levels.nearest_resistance:,.2f}</code>",
        ]

        keyboard = [
            [
                InlineKeyboardButton(
                    "📊 Full Analysis", callback_data=f"analyze:{ticker}"
                )
            ]
        ]
        await msg_obj.reply_text(
            "\n".join(lines),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except Exception as exc:
        await msg_obj.reply_text(
            f"❌ Fibonacci calculation failed: <code>{html.escape(str(exc))}</code>",
            parse_mode=ParseMode.HTML,
        )


async def cmd_compare(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /compare TICKER1 TICKER2 — side-by-side English comparison table."""
    msg_obj = update.message or (
        update.callback_query.message if update.callback_query else None
    )
    if not msg_obj:
        return

    if not context.args or len(context.args) < 2:
        await msg_obj.reply_text(
            "❗ Usage: /compare TICKER1 TICKER2\nExample: <code>/compare AAPL MSFT</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    t1 = context.args[0].upper().strip()
    t2 = context.args[1].upper().strip()

    await msg_obj.reply_text(
        f"⏳ Comparing <b>{html.escape(t1)}</b> vs <b>{html.escape(t2)}</b>...",
        parse_mode=ParseMode.HTML,
    )

    try:
        sig1, sig2, prof1, prof2 = await asyncio.gather(
            _quant_engine.analyze(t1),
            _quant_engine.analyze(t2),
            fetch_company_profile(t1),
            fetch_company_profile(t2),
            return_exceptions=True,
        )

        def _price(sig: object) -> str:
            if isinstance(sig, Exception):
                return "Error"
            return f"${sig.price:,.2f}"  # type: ignore[union-attr]

        def _rsi(sig: object) -> str:
            if isinstance(sig, Exception):
                return "—"
            v = sig.signals.get("rsi")  # type: ignore[union-attr]
            return f"{v:.1f}" if v else "—"

        def _macd(sig: object) -> str:
            if isinstance(sig, Exception):
                return "—"
            h = sig.signals.get("macd_histogram", 0)  # type: ignore[union-attr]
            return "📈 Bullish" if h > 0 else "📉 Bearish"

        def _fib(sig: object) -> str:
            if isinstance(sig, Exception):
                return "—"
            fib = sig.fibonacci  # type: ignore[union-attr]
            if not fib:
                return "—"
            return "📈 Uptrend" if fib["trend"] == "UPTREND" else "📉 Downtrend"

        def _pe(prof: object) -> str:
            if isinstance(prof, Exception):
                return "—"
            v = getattr(prof, "pe_trailing", None)
            return f"{v:.1f}x" if v else "N/A"

        def _eps(prof: object) -> str:
            if isinstance(prof, Exception):
                return "—"
            v = getattr(prof, "eps_trailing", None)
            return f"${v:.2f}" if v else "N/A"

        def _cap(prof: object) -> str:
            if isinstance(prof, Exception):
                return "—"
            cap = getattr(prof, "market_cap", None)
            if not cap:
                return "N/A"
            return f"${cap / 1e9:.1f}B" if cap >= 1e9 else f"${cap / 1e6:.0f}M"

        def _momentum(sig: object) -> str:
            if isinstance(sig, Exception):
                return "—"
            try:
                import pandas as _pd

                ms = momentum_score(sig.signals, _pd.Series(dtype=float))  # type: ignore[union-attr]
                return f"{ms.score}/100 {ms.emoji}"
            except Exception:  # noqa: BLE001
                return "—"

        rows = [
            ("💰 Price", _price(sig1), _price(sig2)),
            ("📉 RSI(14)", _rsi(sig1), _rsi(sig2)),
            ("📊 MACD", _macd(sig1), _macd(sig2)),
            ("⚡ Momentum", _momentum(sig1), _momentum(sig2)),
            ("📐 Fibonacci", _fib(sig1), _fib(sig2)),
            ("📈 P/E", _pe(prof1), _pe(prof2)),
            ("💵 EPS", _eps(prof1), _eps(prof2)),
            ("🏦 Market Cap", _cap(prof1), _cap(prof2)),
        ]

        lines = [
            f"⚖️ <b>COMPARISON: <code>{html.escape(t1)}</code> vs <code>{html.escape(t2)}</code></b>",
            "━━━━━━━━━━━━━━━━━━━",
            f"<b>{'Metric':<16} {t1:<14} {t2}</b>",
            "─────────────────────────────",
        ]
        for label, v1, v2 in rows:
            lines.append(
                f"{html.escape(label):<16}  "
                f"<code>{html.escape(str(v1)):<14}</code>  "
                f"<code>{html.escape(str(v2))}</code>"
            )

        keyboard = [
            [
                InlineKeyboardButton(f"📊 {t1}", callback_data=f"analyze:{t1}"),
                InlineKeyboardButton(f"📊 {t2}", callback_data=f"analyze:{t2}"),
            ]
        ]
        await msg_obj.reply_text(
            "\n".join(lines),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    except Exception as exc:
        logger.error("compare_command_failed", t1=t1, t2=t2, error=str(exc))
        await msg_obj.reply_text(
            f"❌ Comparison failed: <code>{html.escape(str(exc))}</code>",
            parse_mode=ParseMode.HTML,
        )


async def cmd_health(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /health — English system status dashboard."""
    from src.database.cache import cache as redis_cache
    from src.database.session import check_db_connection

    msg_obj = update.message or (
        update.callback_query.message if update.callback_query else None
    )
    if not msg_obj:
        return

    quant_health, db_health, redis_health = await asyncio.gather(
        _quant_engine.health_check(),
        check_db_connection(),
        redis_cache.health_check(),
    )

    def _s(h: dict) -> str:
        return (
            "✅"
            if h.get("status") == "ok"
            else "⚠️" if h.get("status") == "degraded" else "❌"
        )

    # Test news RSS connectivity
    import httpx as _httpx

    _browser_headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    try:
        async with _httpx.AsyncClient(
            timeout=5, headers=_browser_headers, follow_redirects=True
        ) as client:
            r = await client.get(
                "https://news.google.com/rss/search?q=AAPL+stock&hl=en&gl=US&ceid=US:en"
            )
        news_rss_status = "✅" if r.status_code == 200 else f"⚠️ HTTP {r.status_code}"
    except Exception as exc:
        news_rss_status = f"❌ {str(exc)[:40]}"

    mkt = market_status()
    nyse_time = now_us().strftime("%I:%M %p ET")
    lines = [
        "🏥 <b>SYSTEM STATUS — MarketMind-Pro</b>",
        "━━━━━━━━━━━━━━━━━━━",
        f"{_s(quant_health)} Quant Engine: {html.escape(quant_health.get('detail', ''))}",
        f"{news_rss_status} News RSS:     Google News reachable",
        f"{_s(db_health)} PostgreSQL:   {html.escape(db_health.get('detail', ''))}",
        f"{_s(redis_health)} Redis:        {html.escape(redis_health.get('detail', ''))}",
        "━━━━━━━━━━━━━━━━━━━",
        f"🇺🇸 NYSE: {'🟢 Open' if mkt['nyse_open'] else '🔴 Closed'}  ({nyse_time})",
    ]

    await msg_obj.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


_TICKER_RE = re.compile(r"^[A-Z.\-]{1,10}$")


async def cmd_setalert(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /setalert TICKER PRICE — set a price alert for a ticker.

    Auto-detects PRICE_ABOVE vs PRICE_BELOW based on current price.
    Stores alert in PostgreSQL UserAlert table.
    """
    msg_obj = update.message
    if not msg_obj:
        return

    if not context.args or len(context.args) < 2:
        await msg_obj.reply_text(
            "❗ Usage: /setalert TICKER PRICE\nExample: <code>/setalert AAPL 220</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    raw_ticker = context.args[0].upper().strip()
    if not _TICKER_RE.match(raw_ticker):
        await msg_obj.reply_text("❗ Invalid ticker symbol.", parse_mode=ParseMode.HTML)
        return

    try:
        threshold = float(context.args[1])
        if threshold <= 0:
            raise ValueError("non-positive")
    except (ValueError, IndexError):
        await msg_obj.reply_text(
            "❗ Invalid price. Use a positive number.\nExample: <code>/setalert AAPL 220</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    chat_id = str(msg_obj.chat_id)

    # Determine alert type by comparing to current price
    try:
        signal = await _quant_engine.analyze(raw_ticker)
        current_price = signal.price
    except Exception as exc:
        await msg_obj.reply_text(
            f"❌ Failed to fetch current price for <b>{html.escape(raw_ticker)}</b>: "
            f"<code>{html.escape(str(exc)[:80])}</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    alert_type = "PRICE_ABOVE" if threshold > current_price else "PRICE_BELOW"
    direction = "rises above" if alert_type == "PRICE_ABOVE" else "falls below"

    from decimal import Decimal

    async with AsyncSessionLocal() as session:
        from sqlalchemy import select as _select

        # Deactivate any existing alert for same chat + ticker
        stmt = _select(UserAlert).where(
            UserAlert.chat_id == chat_id,
            UserAlert.ticker == raw_ticker,
            UserAlert.is_active.is_(True),
        )
        existing = (await session.execute(stmt)).scalars().all()
        for old_alert in existing:
            old_alert.is_active = False

        new_alert = UserAlert(
            chat_id=chat_id,
            ticker=raw_ticker,
            alert_type=alert_type,
            threshold=Decimal(str(threshold)),
            is_active=True,
        )
        session.add(new_alert)
        await session.commit()

    await msg_obj.reply_text(
        f"🔔 <b>Alert set for {html.escape(raw_ticker)}</b>\n"
        f"  I'll notify you when the price {direction} <code>${threshold:,.2f}</code>\n"
        f"  (Current price: <code>${current_price:,.2f}</code>)",
        parse_mode=ParseMode.HTML,
    )


async def cmd_myalerts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /myalerts — list active price alerts for this chat."""
    msg_obj = update.message
    if not msg_obj:
        return

    chat_id = str(msg_obj.chat_id)
    from sqlalchemy import select as _select

    async with AsyncSessionLocal() as session:
        stmt = _select(UserAlert).where(
            UserAlert.chat_id == chat_id,
            UserAlert.is_active.is_(True),
        )
        alerts = (await session.execute(stmt)).scalars().all()

    if not alerts:
        await msg_obj.reply_text(
            "📭 You have no active price alerts.\n\nSet one with: <code>/setalert AAPL 220</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    lines = ["🔔 <b>Your Active Alerts:</b>", ""]
    for alert in alerts:
        direction = "↑ Above" if alert.alert_type == "PRICE_ABOVE" else "↓ Below"
        lines.append(
            f"  • <b>{html.escape(alert.ticker)}</b>  {direction}  "
            f"<code>${float(alert.threshold):,.2f}</code>"
        )
    lines += ["", "To cancel: <code>/cancelalert TICKER</code>"]
    await msg_obj.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def cmd_cancelalert(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /cancelalert TICKER — deactivate price alert for a ticker."""
    msg_obj = update.message
    if not msg_obj:
        return

    if not context.args:
        await msg_obj.reply_text(
            "❗ Usage: /cancelalert TICKER\nExample: <code>/cancelalert AAPL</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    raw_ticker = context.args[0].upper().strip()
    if not _TICKER_RE.match(raw_ticker):
        await msg_obj.reply_text("❗ Invalid ticker symbol.", parse_mode=ParseMode.HTML)
        return

    chat_id = str(msg_obj.chat_id)
    from sqlalchemy import select as _select

    async with AsyncSessionLocal() as session:
        stmt = _select(UserAlert).where(
            UserAlert.chat_id == chat_id,
            UserAlert.ticker == raw_ticker,
            UserAlert.is_active.is_(True),
        )
        alerts = (await session.execute(stmt)).scalars().all()
        if not alerts:
            await msg_obj.reply_text(
                f"❌ No active alert found for <b>{html.escape(raw_ticker)}</b>.",
                parse_mode=ParseMode.HTML,
            )
            return
        for alert in alerts:
            alert.is_active = False
        await session.commit()

    await msg_obj.reply_text(
        f"✅ Alert cancelled for <b>{html.escape(raw_ticker)}</b>.",
        parse_mode=ParseMode.HTML,
    )


async def _job_check_alerts(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Scheduled job: check all active price alerts every 5 min during market hours."""
    mkt = market_status()
    if not mkt.get("nyse_open"):
        return

    from sqlalchemy import select as _select

    async with AsyncSessionLocal() as session:
        stmt = _select(UserAlert).where(UserAlert.is_active.is_(True))
        alerts = (await session.execute(stmt)).scalars().all()

    if not alerts:
        return

    # Group by ticker to avoid redundant fetches
    ticker_alerts: dict[str, list[UserAlert]] = {}
    for alert in alerts:
        ticker_alerts.setdefault(alert.ticker, []).append(alert)

    for ticker, ticker_alert_list in ticker_alerts.items():
        try:
            signal = await _quant_engine.analyze(ticker)
            price = signal.price
        except Exception as _exc:  # noqa: BLE001
            logger.debug("alert_price_fetch_failed", ticker=ticker, error=str(_exc))
            continue

        for alert in ticker_alert_list:
            try:
                threshold_f = float(alert.threshold)
                triggered = (
                    alert.alert_type == "PRICE_ABOVE" and price >= threshold_f
                ) or (alert.alert_type == "PRICE_BELOW" and price <= threshold_f)

                if not triggered:
                    continue

                direction = (
                    "risen above"
                    if alert.alert_type == "PRICE_ABOVE"
                    else "fallen below"
                )
                await context.bot.send_message(
                    chat_id=alert.chat_id,
                    text=(
                        f"🔔 <b>Price Alert — {html.escape(ticker)}</b>\n"
                        f"  Price has <b>{direction}</b> your target of "
                        f"<code>${threshold_f:,.2f}</code>\n"
                        f"  Current price: <code>${price:,.2f}</code>"
                    ),
                    parse_mode=ParseMode.HTML,
                )

                # Mark as triggered
                async with AsyncSessionLocal() as upd_session:
                    from sqlalchemy import select as _sel2

                    stmt2 = _sel2(UserAlert).where(UserAlert.id == alert.id)
                    row = (await upd_session.execute(stmt2)).scalar_one_or_none()
                    if row:
                        row.is_active = False
                        row.triggered_at = datetime.now(tz=UTC)
                    await upd_session.commit()

            except Exception:  # noqa: BLE001
                logger.warning("alert_check_failed", ticker=ticker, alert_id=alert.id)


async def cmd_fallback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle unrecognized text messages with a helpful menu."""
    msg_obj = update.message or (
        update.callback_query.message if update.callback_query else None
    )
    if not msg_obj:
        return

    keyboard = [
        [
            InlineKeyboardButton("📊 Analyze Stock", callback_data="prompt_analyze"),
            InlineKeyboardButton("📰 News", callback_data="prompt_news"),
        ],
        [
            InlineKeyboardButton("🆚 Compare", callback_data="prompt_compare"),
            InlineKeyboardButton("📐 Fibonacci", callback_data="prompt_fibonacci"),
        ],
        [InlineKeyboardButton("🏥 System Health", callback_data="health")],
    ]
    await msg_obj.reply_text(
        "I didn't recognize that command.\n\nHere's what I can do for you:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML,
    )


# ── Callback Query Handler ──────────────────────────────────────────────────────


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline keyboard button presses."""
    query = update.callback_query
    await query.answer()
    data = query.data

    # NOTE: Do NOT assign update.message — Update is immutable in PTB v22.
    # Sub-handlers resolve the message via:
    #   msg_obj = update.message or (update.callback_query.message if update.callback_query else None)

    if data == "health":
        await cmd_health(update, context)

    elif data.startswith("analyze:"):
        context.args = [data.split(":", 1)[1]]
        await cmd_analyze(update, context)

    elif data.startswith("fib:"):
        context.args = [data.split(":", 1)[1]]
        await cmd_fibonacci(update, context)

    elif data.startswith("news:"):
        context.args = [data.split(":", 1)[1]]
        await cmd_news(update, context)

    elif data == "prompt_analyze":
        await query.message.reply_text(
            "Send: /analyze TICKER\nExample: <code>/analyze AAPL</code>",
            parse_mode=ParseMode.HTML,
        )

    elif data == "prompt_news":
        await query.message.reply_text(
            "Send: /news TICKER\nExample: <code>/news TSLA</code>",
            parse_mode=ParseMode.HTML,
        )

    elif data == "prompt_fibonacci":
        await query.message.reply_text(
            "Send: /fibonacci TICKER\nExample: <code>/fibonacci AAPL</code>",
            parse_mode=ParseMode.HTML,
        )

    elif data == "prompt_compare":
        await query.message.reply_text(
            "Send: /compare TICKER1 TICKER2\nExample: <code>/compare AAPL MSFT</code>",
            parse_mode=ParseMode.HTML,
        )

    elif data == "market_open":
        await send_market_open_report(context.application)


# ── Scheduled Job Functions ─────────────────────────────────────────────────────


async def _job_market_preview(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Scheduled job: pre-market preview 30 min before NYSE open (9:00 AM ET, Mon–Fri)."""
    watchlist_preview = ["AAPL", "MSFT", "NVDA", "SPY", "QQQ"]

    # Fetch quant signals for all tickers
    ticker_data: dict[str, dict] = {}
    preview_lines: list[str] = []
    for t in watchlist_preview:
        try:
            sig = await _quant_engine.analyze(t)
            rsi_v = sig.signals.get("rsi")
            rsi_emoji, _ = _rsi_label(sig.signals.get("rsi_signal", "NEUTRAL"))
            ohlcv = sig.ohlcv or {}
            close = ohlcv.get("close", sig.price)
            open_ = ohlcv.get("open", close)
            pct = (close - open_) / open_ * 100 if open_ else 0.0
            pct_sign = "+" if pct >= 0 else ""
            pct_arrow = "📈" if pct > 0.005 else ("📉" if pct < -0.005 else "➡️")
            ticker_data[t] = {"price": sig.price, "pct": pct, "rsi": rsi_v}
            preview_lines.append(
                f"  • <b>{t}</b>: <code>${sig.price:,.2f}</code>  "
                f"{pct_arrow} {pct_sign}{pct:.2f}%"
                + (f"  RSI: {rsi_v:.1f} {rsi_emoji}" if rsi_v else "")
            )
        except Exception:
            ticker_data[t] = {}
            preview_lines.append(f"  • <b>{t}</b>: ❌")

    # Fetch top news headline per ticker in parallel
    async def _one_headline(t: str) -> str:
        try:
            report = await _news_agent.analyze_sentiment(t)
            if report.recent_headlines:
                h = report.recent_headlines[0]
                title = html.escape(h.get("title", "")[:75])
                src = html.escape(h.get("source", ""))
                time_ago = h.get("time_ago", "")
                suffix = f" · {time_ago}" if time_ago else ""
                return f"    📰 {title}… <i>({src}{suffix})</i>"
        except Exception:  # noqa: BLE001
            logger.debug("preview_headline_failed", ticker=t)
        return ""

    headlines = await asyncio.gather(*[_one_headline(t) for t in watchlist_preview])

    # Interleave ticker lines with headlines
    ticker_with_news: list[str] = []
    for line, headline in zip(preview_lines, headlines, strict=False):
        ticker_with_news.append(line)
        if headline:
            ticker_with_news.append(headline)

    # Fetch VIX for market mood
    vix_price: float | None = None
    try:
        vix_data = await _quant_engine.fetch_price_data(
            "^VIX", period="1d", interval="1m"
        )
        if not vix_data.empty:
            vix_price = float(vix_data["close"].iloc[-1])
    except Exception:  # noqa: BLE001
        logger.debug("vix_fetch_failed")

    spy_pct = ticker_data.get("SPY", {}).get("pct")
    mood_parts = []
    if spy_pct is not None:
        spy_sign = "+" if spy_pct >= 0 else ""
        mood_parts.append(f"SPY {spy_sign}{spy_pct:.2f}%")
    if vix_price is not None:
        mood_parts.append(f"VIX {vix_price:.1f} ({_vix_label(vix_price)})")
    mood_line = "  " + "   ·   ".join(mood_parts) if mood_parts else ""

    # Check which watchlist stocks report earnings today
    loop = asyncio.get_event_loop()

    async def _earnings_warning(t: str) -> str:
        try:
            reporting, eps_est, rev_est = await loop.run_in_executor(
                None, is_reporting_today, t
            )
            if not reporting:
                return ""
            parts = [f"⚠️ <b>Earnings Today:</b> {t} reports today"]
            est_parts = []
            if eps_est is not None:
                est_parts.append(f"EPS est: <code>${eps_est:.2f}</code>")
            if rev_est is not None:
                est_parts.append(f"Revenue est: <code>{_fmt_rev(rev_est)}</code>")
            if est_parts:
                parts.append("   " + "  |  ".join(est_parts))
            return "\n".join(parts)
        except Exception:  # noqa: BLE001
            return ""

    earnings_warnings = await asyncio.gather(
        *[_earnings_warning(t) for t in watchlist_preview]
    )
    earnings_warning_lines = [w for w in earnings_warnings if w]

    snapshot, sectors = await asyncio.gather(
        _fetch_market_snapshot(),
        _fetch_sector_data(),
    )
    nyse_open_str = now_us().strftime("%I:%M %p ET")
    sections = [
        "🌅 <b>Pre-Market Preview — NYSE opens in ~30 minutes</b>",
        f"🕙 {nyse_open_str}",
        "",
        "📋 <b>Watchlist:</b>",
        *ticker_with_news,
    ]
    if mood_line:
        sections += ["", "📊 <b>Market Mood:</b>", mood_line]
    if earnings_warning_lines:
        sections += [""] + earnings_warning_lines
    sections.append(snapshot)
    sector_block = _format_sector_block(sectors)
    if sector_block:
        sections.append(sector_block)
    msg = "\n".join(sections)

    await context.bot.send_message(
        chat_id=settings.telegram_chat_id,
        text=msg,
        parse_mode=ParseMode.HTML,
    )


async def _job_market_close_regular(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Scheduled job: post-close summary (4:15 PM ET, Mon–Fri)."""
    await send_market_close_report(context.application)


# ── Automated Reports ───────────────────────────────────────────────────────────


async def send_market_open_report(app: Application) -> None:
    """Automated English morning report at market open."""
    watchlist = ["AAPL", "MSFT", "NVDA", "GOOGL", "SPY"]
    lines = [
        "🔔 <b>MARKET OPEN REPORT</b>",
        f"🕐 {datetime.now(tz=_ET).strftime('%d/%m/%Y %H:%M')} ET",
        "━━━━━━━━━━━━━━━━━━━",
    ]

    for ticker in watchlist:
        try:
            signal = await _quant_engine.analyze(ticker)
            rsi_val = signal.signals.get("rsi")
            rsi_signal = signal.signals.get("rsi_signal", "NEUTRAL")
            macd_hist = signal.signals.get("macd_histogram", 0)
            rsi_emoji, _ = _rsi_label(rsi_signal)
            ohlcv = signal.ohlcv or {}
            close = ohlcv.get("close", signal.price)
            open_ = ohlcv.get("open", close)
            pct = (close - open_) / open_ * 100 if open_ else 0.0
            pct_sign = "+" if pct >= 0 else ""
            pct_arrow = "📈" if pct > 0.005 else ("📉" if pct < -0.005 else "➡️")
            line = (
                f"• <b>{ticker}</b>: <code>${signal.price:,.2f}</code>  "
                f"{pct_arrow} {pct_sign}{pct:.2f}%"
            )
            if rsi_val:
                line += f"  RSI: {rsi_val:.1f} {rsi_emoji}  MACD: {'📈' if macd_hist > 0 else '📉'}"
            lines.append(line)
        except Exception as exc:
            logger.warning(
                "market_open_report_ticker_failed", ticker=ticker, error=str(exc)
            )
            lines.append(f"• <b>{ticker}</b>: ❌ Failed")

    # Earnings reported after-hours (yesterday/this morning)
    open_loop = asyncio.get_event_loop()

    async def _check_reported_open(t: str) -> tuple[str, EarningsReport] | None:
        try:
            reported = await open_loop.run_in_executor(None, was_reported_today, t)
            if not reported:
                return None
            rep = await fetch_earnings_report(t)
            if rep:
                return (t, rep)
        except Exception:  # noqa: BLE001
            logger.debug("earnings_check_failed", ticker=t)
        return None

    open_reported = await asyncio.gather(
        *[_check_reported_open(t) for t in watchlist], return_exceptions=True
    )
    earnings_just_in = [r for r in open_reported if r and not isinstance(r, Exception)]
    if earnings_just_in:
        lines += ["", "🔔 <b>Earnings Just In:</b>"]
        for _ticker, rep in earnings_just_in:
            lines.append(format_earnings_english(rep))

    snapshot, sectors = await asyncio.gather(
        _fetch_market_snapshot(),
        _fetch_sector_data(),
    )
    lines.append(snapshot)
    sector_block = _format_sector_block(sectors)
    if sector_block:
        lines.append(sector_block)

    await app.bot.send_message(
        chat_id=settings.telegram_chat_id,
        text="\n".join(lines),
        parse_mode=ParseMode.HTML,
    )


async def send_market_close_report(app: Application) -> None:
    """Automated end-of-day English report with watchlist prices and global snapshot."""
    watchlist = ["AAPL", "MSFT", "NVDA", "GOOGL", "SPY"]
    nyse_close_str = now_us().strftime("%I:%M %p ET")
    lines = [
        "🌙 <b>MARKET CLOSE SUMMARY</b>",
        "━━━━━━━━━━━━━━━━━━━",
        f"🇺🇸 NYSE Closed: {nyse_close_str}",
        "",
        "📋 <b>End-of-Day Recap:</b>",
    ]

    pct_map: dict[str, float] = {}
    rsi_map: dict[str, float | None] = {}
    macd_map: dict[str, float] = {}
    signals_list = []
    df_map: dict[str, object] = {}  # ticker → DataFrame for momentum score

    for ticker in watchlist:
        try:
            signal = await _quant_engine.analyze(ticker)
            ohlcv = signal.ohlcv or {}
            close = ohlcv.get("close", signal.price)
            open_ = ohlcv.get("open", close)
            pct = (close - open_) / open_ * 100 if open_ else 0.0
            pct_sign = "+" if pct >= 0 else ""
            pct_arrow = "📈" if pct > 0.005 else ("📉" if pct < -0.005 else "➡️")
            rsi_v = signal.signals.get("rsi")
            rsi_emoji, _ = _rsi_label(signal.signals.get("rsi_signal", "NEUTRAL"))
            macd_hist = signal.signals.get("macd_histogram", 0) or 0
            pct_map[ticker] = pct
            rsi_map[ticker] = rsi_v
            macd_map[ticker] = macd_hist
            signals_list.append(signal)
            try:
                df_map[ticker] = await _quant_engine.fetch_price_data(
                    ticker, period="1y", interval="1d"
                )
            except Exception as _df_exc:  # noqa: BLE001
                logger.debug("close_df_fetch_failed", ticker=ticker, error=str(_df_exc))
            line = (
                f"  • <b>{ticker}</b>: <code>${signal.price:,.2f}</code>  "
                f"{pct_arrow} {pct_sign}{pct:.2f}%"
            )
            if rsi_v:
                line += f"  RSI: {rsi_v:.1f} {rsi_emoji}"
            lines.append(line)
        except Exception:
            pct_map[ticker] = 0.0
            rsi_map[ticker] = None
            macd_map[ticker] = 0.0
            signals_list.append(None)
            lines.append(f"  • <b>{ticker}</b>: ❌")

    # Winner / Loser
    valid_pct = {t: p for t, p in pct_map.items() if p != 0.0}
    if valid_pct:
        winner = max(valid_pct, key=valid_pct.get)  # type: ignore[arg-type]
        loser = min(valid_pct, key=valid_pct.get)  # type: ignore[arg-type]
        w_sign = "+" if valid_pct[winner] >= 0 else ""
        l_sign = "+" if valid_pct[loser] >= 0 else ""
        lines += [
            "",
            f"🏆 <b>Best:</b> {winner} {w_sign}{valid_pct[winner]:.2f}%   "
            f"💔 <b>Worst:</b> {loser} {l_sign}{valid_pct[loser]:.2f}%",
        ]

    # Best Momentum Today
    try:
        import pandas as _pd_close

        momentum_scores: dict[str, int] = {}
        for ticker, sig in zip(watchlist, signals_list, strict=False):
            if sig is None:
                continue
            df_c = df_map.get(ticker)
            price_series = (
                df_c["Close"].squeeze()
                if df_c is not None
                else _pd_close.Series(dtype=float)
            )
            ms = momentum_score(sig.signals, price_series)
            momentum_scores[ticker] = ms.score
        if momentum_scores:
            best_ms_ticker = max(momentum_scores, key=momentum_scores.get)  # type: ignore[arg-type]
            best_ms_score = momentum_scores[best_ms_ticker]
            best_ms_obj = momentum_score(
                next(s for t, s in zip(watchlist, signals_list, strict=False) if t == best_ms_ticker and s is not None).signals,  # type: ignore[union-attr]
                (
                    df_map.get(best_ms_ticker, _pd_close.DataFrame())["Close"].squeeze()
                    if best_ms_ticker in df_map
                    else _pd_close.Series(dtype=float)
                ),
            )
            lines += [
                "",
                f"🏆 <b>Best Momentum Today:</b> {best_ms_ticker} — <code>{best_ms_score}/100</code> {best_ms_obj.emoji} {best_ms_obj.label}",
            ]
    except Exception:  # noqa: BLE001
        logger.debug("close_momentum_failed")

    # RSI summary
    overbought = [t for t, r in rsi_map.items() if r and r > 70]
    oversold = [t for t, r in rsi_map.items() if r and r < 30]
    neutral_count = len(watchlist) - len(overbought) - len(oversold)
    lines += [
        "",
        f"📊 <b>RSI Summary:</b> {len(overbought)} overbought · {len(oversold)} oversold · {neutral_count} neutral",
    ]

    # News sentiment per ticker (parallel)
    try:
        sentiment_reports = await asyncio.gather(
            *[_news_agent.analyze_sentiment(t) for t in watchlist],
            return_exceptions=True,
        )
        sent_parts = []
        for ticker, rep in zip(watchlist, sentiment_reports, strict=False):
            if isinstance(rep, Exception) or rep is None:
                continue
            sent_parts.append(f"  • <b>{ticker}</b> {rep.emoji} {rep.score:+.2f}")
        if sent_parts:
            lines += ["", "📰 <b>News Sentiment:</b>", *sent_parts]
    except Exception:  # noqa: BLE001
        logger.debug("close_sentiment_failed")

    # Watch Tomorrow signals
    watch_lines: list[str] = []
    for ticker, sig in zip(watchlist, signals_list, strict=False):
        if sig is None:
            continue
        rsi = rsi_map.get(ticker)
        macd = macd_map.get(ticker, 0)
        if rsi and rsi < 35:
            watch_lines.append(
                f"  👀 <b>{ticker}</b> — RSI {rsi:.1f}, potential bounce zone"
            )
        elif rsi and rsi > 70:
            watch_lines.append(
                f"  ⚠️ <b>{ticker}</b> — RSI {rsi:.1f}, overbought — watch for pullback"
            )
        if abs(macd) < 0.05 and macd != 0:
            watch_lines.append(f"  📶 <b>{ticker}</b> — MACD near zero cross")
    if watch_lines:
        lines += ["", "👀 <b>Watch Tomorrow:</b>", *watch_lines]

    # Earnings reported today
    close_loop = asyncio.get_event_loop()

    async def _check_reported(t: str) -> tuple[str, EarningsReport] | None:
        try:
            reported = await close_loop.run_in_executor(None, was_reported_today, t)
            if not reported:
                return None
            rep = await fetch_earnings_report(t)
            if rep:
                return (t, rep)
        except Exception:  # noqa: BLE001
            logger.debug("earnings_check_failed", ticker=t)
        return None

    reported_results = await asyncio.gather(
        *[_check_reported(t) for t in watchlist], return_exceptions=True
    )
    earnings_today = [r for r in reported_results if r and not isinstance(r, Exception)]
    if earnings_today:
        lines += ["", "📋 <b>Earnings Today:</b>"]
        for _ticker, rep in earnings_today:
            lines.append(format_earnings_english(rep))

    snapshot, sectors = await asyncio.gather(
        _fetch_market_snapshot(),
        _fetch_sector_data(),
    )
    lines.append(snapshot)
    sector_block = _format_sector_block(sectors)
    if sector_block:
        lines.append(sector_block)

    await app.bot.send_message(
        chat_id=settings.telegram_chat_id,
        text="\n".join(lines),
        parse_mode=ParseMode.HTML,
    )


# ── Market Snapshot ──────────────────────────────────────────────────────────────

_SNAPSHOT_SYMBOLS: list[tuple[str, str, str, bool]] = [
    # (yfinance_symbol, display_label, emoji, has_dollar_prefix)
    # Equities / ETFs
    ("SPY", "S&P 500", "📊", True),
    ("VOO", "Vanguard S&P", "📊", True),
    ("QQQ", "Nasdaq", "📊", True),
    ("DIA", "Dow Jones", "📊", True),
    ("IWM", "Russell 2000", "📊", True),
    ("RSP", "S&P EW", "📊", True),
    # Currency & Volatility
    ("DX-Y.NYB", "DXY (USD)", "💵", False),
    ("^VIX", "VIX", "😱", False),
    # Fixed Income
    ("TLT", "20Y Treasury", "🏦", True),
    ("AGG", "Bonds (AGG)", "🏦", True),
    # Commodities
    ("GLD", "Gold", "🥇", True),
    ("SLV", "Silver", "🥈", True),
    ("USO", "WTI Oil", "🛢️", True),
    # Crypto
    ("BTC-USD", "Bitcoin", "₿ ", True),
    ("ETH-USD", "Ethereum", "Ξ ", True),
]

_EQUITY_ETFS = {"SPY", "VOO", "QQQ", "DIA", "IWM", "RSP"}
_CURRENCY_VOL = {"DX-Y.NYB", "^VIX"}
_FIXED_INCOME = {"TLT", "AGG"}
_COMMODITY = {"GLD", "SLV", "USO"}
_CRYPTO = {"BTC-USD", "ETH-USD"}

# S&P 500 SPDR sector ETFs — used by /sectors command
_SECTOR_ETFS: list[tuple[str, str]] = [
    ("XLK", "Technology"),
    ("XLV", "Healthcare"),
    ("XLF", "Financials"),
    ("XLE", "Energy"),
    ("XLI", "Industrials"),
    ("XLY", "Cons. Discretionary"),
    ("XLP", "Cons. Staples"),
    ("XLC", "Communication"),
    ("XLB", "Materials"),
    ("XLRE", "Real Estate"),
    ("XLU", "Utilities"),
]


def _snapshot_group(sym: str) -> str:
    if sym in _EQUITY_ETFS:
        return "equities"
    if sym in _CURRENCY_VOL:
        return "currency"
    if sym in _FIXED_INCOME:
        return "bonds"
    if sym in _COMMODITY:
        return "commodities"
    return "crypto"


async def _fetch_market_snapshot() -> str:
    """Fetch major indices, crypto, and commodities with daily % change.

    Returns an HTML-formatted string for inclusion in Telegram messages.
    Each symbol is fetched independently via yfinance Ticker.history();
    failures are silently skipped so one bad symbol never blocks the rest.
    """
    import yfinance as yf

    loop = asyncio.get_event_loop()

    def _fetch_sync(sym: str) -> tuple[float, float] | None:
        """Synchronous fetch — returns (live_price, prev_close) via fast_info.

        Falls back to history-based close if fast_info returns None (e.g. some indices).
        """
        try:
            fi = yf.Ticker(sym).fast_info
            curr = getattr(fi, "last_price", None)
            prev = getattr(fi, "previous_close", None)
            if curr is not None and prev is not None:
                return float(curr), float(prev)
            # Fallback: last 2 daily closes
            df = yf.Ticker(sym).history(period="5d", interval="1d", auto_adjust=True)
            df = df.dropna(subset=["Close"])
            if len(df) < 2:
                return None
            return float(df["Close"].iloc[-1]), float(df["Close"].iloc[-2])
        except Exception:
            return None

    # Run all fetches concurrently (each in its own thread to avoid yfinance locks)
    tasks = [
        loop.run_in_executor(None, _fetch_sync, sym)
        for sym, _, _, _ in _SNAPSHOT_SYMBOLS
    ]
    results = await asyncio.gather(*tasks)

    lines = ["", "🌍 <b>Global Markets Snapshot</b>"]
    prev_group: str | None = None
    for (sym, label, emoji, has_dollar), data in zip(
        _SNAPSHOT_SYMBOLS, results, strict=True
    ):
        # Insert blank line between groups
        group = _snapshot_group(sym)
        if prev_group and group != prev_group:
            lines.append("")
        prev_group = group

        if data is None:
            lines.append(f"{emoji} {label}: <code>N/A</code>")
            continue

        curr, prev = data
        pct = (curr - prev) / prev * 100 if prev else 0.0
        pct_arrow = "📈" if pct > 0.005 else ("📉" if pct < -0.005 else "➡️")
        pct_sign = "+" if pct >= 0 else ""

        # Format value
        if sym in ("^VIX", "DX-Y.NYB"):
            val_str = f"{curr:.2f}"
        elif sym in ("BTC-USD", "ETH-USD"):
            val_str = f"${curr:,.0f}"
        else:
            val_str = f"${curr:,.2f}" if has_dollar else f"{curr:.2f}"

        lines.append(
            f"{emoji} {label}: <code>{val_str}</code>  {pct_arrow} {pct_sign}{pct:.2f}%"
        )

    return "\n".join(lines)


async def _fetch_sector_data() -> list[dict]:
    """Fetch daily % change for all 11 S&P 500 sector ETFs.

    Results are cached in Redis for 10 minutes. Each ETF is fetched concurrently;
    failures are skipped so one bad symbol never blocks the rest.

    Returns:
        List of dicts sorted by pct_change descending:
        [{"symbol": "XLK", "name": "Technology", "pct_change": 1.84, "price": 198.3}, ...]
    """
    import yfinance as _yf

    from src.database.cache import cache as _redis_cache

    cache_key = "sectors:daily"
    cached = await _redis_cache.get(cache_key)
    if cached and isinstance(cached, list):
        logger.debug("sectors_cache_hit")
        return cached

    loop = asyncio.get_event_loop()

    def _fetch_sync(sym: str) -> tuple[float, float] | None:
        try:
            fi = _yf.Ticker(sym).fast_info
            curr = getattr(fi, "last_price", None)
            prev = getattr(fi, "previous_close", None)
            if curr is not None and prev is not None:
                return float(curr), float(prev)
            # Fallback: last 2 daily closes
            df = _yf.Ticker(sym).history(period="5d", interval="1d", auto_adjust=True)
            df = df.dropna(subset=["Close"])
            if len(df) < 2:
                return None
            return float(df["Close"].iloc[-1]), float(df["Close"].iloc[-2])
        except Exception:  # noqa: BLE001
            return None

    tasks = [loop.run_in_executor(None, _fetch_sync, sym) for sym, _ in _SECTOR_ETFS]
    raw = await asyncio.gather(*tasks)

    sectors = []
    for (sym, name), data in zip(_SECTOR_ETFS, raw, strict=True):
        if data is None:
            logger.warning("sector_fetch_failed", symbol=sym)
            continue
        curr, prev = data
        pct = (curr - prev) / prev * 100 if prev else 0.0
        sectors.append(
            {
                "symbol": sym,
                "name": name,
                "pct_change": round(pct, 2),
                "price": round(curr, 2),
            }
        )

    sectors.sort(key=lambda x: x["pct_change"], reverse=True)
    await _redis_cache.set(cache_key, sectors, ttl=600)
    return sectors


async def cmd_sectors(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /sectors — S&P 500 sector rotation ranked by daily performance."""
    msg_obj = update.message or (
        update.callback_query.message if update.callback_query else None
    )
    if not msg_obj:
        return

    await msg_obj.reply_text("⏳ Fetching sector data...", parse_mode=ParseMode.HTML)

    try:
        sectors = await _fetch_sector_data()
        if not sectors:
            await msg_obj.reply_text(
                "❌ No sector data available right now.", parse_mode=ParseMode.HTML
            )
            return

        date_str = datetime.now(tz=_ET).strftime("%a %b %d")
        lines = [
            f"🏭 <b>Sector Rotation</b> — {date_str}",
            "━━━━━━━━━━━━━━━━━━━",
            "",
        ]

        advancing = 0
        declining = 0
        for s in sectors:
            pct = s["pct_change"]
            if pct > 1.5:
                emoji = "🔥"
            elif pct >= 0:
                emoji = "🟢"
                advancing += 1
            elif pct >= -0.5:
                emoji = "🟡"
                declining += 1
            else:
                emoji = "🔴"
                declining += 1

            if pct > 1.5:
                advancing += 1

            sign = "+" if pct >= 0 else ""
            name_padded = html.escape(s["name"]).ljust(20)
            sym_padded = s["symbol"].ljust(4)
            lines.append(
                f"{emoji} <code>{name_padded} {sym_padded}  {sign}{pct:.2f}%</code>"
            )

        lines.append("")
        lines.append(
            f"📊 Breadth: <b>{advancing} advancing</b> / <b>{declining} declining</b>"
        )

        await msg_obj.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

    except Exception as exc:
        await msg_obj.reply_text(
            f"❌ Sector data failed: <code>{html.escape(str(exc))}</code>",
            parse_mode=ParseMode.HTML,
        )


def _format_sector_block(sectors: list[dict]) -> str:
    """Format sector rotation data as a compact HTML block for reports.

    Args:
        sectors: List of sector dicts sorted by pct_change (from _fetch_sector_data).

    Returns:
        HTML string with header, ranked rows, and breadth line.
    """
    if not sectors:
        return ""

    lines = ["", "🏭 <b>Sector Rotation:</b>"]
    advancing = 0
    declining = 0
    for s in sectors:
        pct = s["pct_change"]
        if pct > 1.5:
            emoji = "🔥"
            advancing += 1
        elif pct >= 0:
            emoji = "🟢"
            advancing += 1
        elif pct >= -0.5:
            emoji = "🟡"
            declining += 1
        else:
            emoji = "🔴"
            declining += 1
        sign = "+" if pct >= 0 else ""
        name_padded = html.escape(s["name"]).ljust(20)
        sym_padded = s["symbol"].ljust(4)
        lines.append(
            f"  {emoji} <code>{name_padded} {sym_padded}  {sign}{pct:.2f}%</code>"
        )
    lines.append(
        f"  📊 Breadth: <b>{advancing} advancing</b> / <b>{declining} declining</b>"
    )
    return "\n".join(lines)


# ── Helpers ─────────────────────────────────────────────────────────────────────


def _format_news_block(ticker: str, sentiment: object) -> list[str]:
    """Return HTML lines for the institutional news block.

    Format per item:
        • <b><a href="URL">Headline</a></b>
        Summary sentence (plain text).
        <code>[Source]</code> | <code>2h ago</code>
    """
    # Import here to avoid circular — SentimentReport is from news_search_agent
    bar = _sentiment_bar(sentiment.score)  # type: ignore[attr-defined]
    lines = [
        f"📰 <b>Latest Market News &amp; Sentiment — <code>{html.escape(ticker)}</code></b>",
        "━━━━━━━━━━━━━━━━━━━",
        f"{sentiment.emoji} <b>Sentiment:</b> <code>{sentiment.score:+.2f}</code>  {bar}",  # type: ignore[attr-defined]
        "━━━━━━━━━━━━━━━━━━━",
        "",
    ]

    headlines = sentiment.recent_headlines  # type: ignore[attr-defined]
    if not headlines:
        lines.append("ℹ️ No recent headlines found.")
        return lines

    for item in headlines[:5]:
        title = item.get("title", "")
        snippet = item.get("snippet", "")
        url_item = item.get("url", "")
        src = item.get("source", "")
        time_ago = item.get("time_ago", "")

        # High-impact keyword detector (🚨 alert)
        alert = ""
        if any(
            kw in title.lower()
            for kw in ("earnings", "acquisition", "merger", "fda", "deal", "buyout")
        ):
            alert = "🚨 "

        # Headline line — clickable if URL present
        if url_item:
            lines.append(
                f'• {alert}<b><a href="{url_item}">{html.escape(title)}</a></b>'
            )
        else:
            lines.append(f"• {alert}<b>{html.escape(title)}</b>")

        # Snippet (1-2 sentences)
        if snippet:
            lines.append(html.escape(snippet[:160]))

        # Meta: source + time
        meta_parts = []
        if src:
            meta_parts.append(f"<code>[{html.escape(src)}]</code>")
        if time_ago:
            meta_parts.append(f"<code>{html.escape(time_ago)}</code>")
        if meta_parts:
            lines.append(" | ".join(meta_parts))

        lines.append("")

    return lines


def _rsi_label(signal: str) -> tuple[str, str]:
    """Return (emoji, English label) for RSI signal."""
    return {
        "OVERSOLD": ("🟢", "Oversold — Buy Signal"),
        "OVERBOUGHT": ("🔴", "Overbought — Sell Signal"),
        "NEUTRAL": ("⚪", "Neutral"),
    }.get(signal, ("⚪", signal))


def _vix_label(vix: float) -> str:
    """Return a human-readable fear label for a given VIX level."""
    if vix < 15:
        return "😌 Low Fear"
    if vix < 25:
        return "😐 Moderate"
    if vix < 35:
        return "😬 Elevated"
    return "😱 Extreme Fear"


def _fmt_rev(v: float) -> str:
    """Format raw revenue value for display in messages."""
    if v >= 1e9:
        return f"${v / 1e9:.1f}B"
    if v >= 1e6:
        return f"${v / 1e6:.0f}M"
    return f"${v:,.0f}"


def _sentiment_bar(score: float) -> str:
    """Convert sentiment score to a 10-char visual bar."""
    filled = max(0, min(10, round((score + 1) / 2 * 10)))
    return "▓" * filled + "░" * (10 - filled)


# ── Application Builder ──────────────────────────────────────────────────────────


def build_application() -> Application:
    """Build and configure the Telegram bot application with scheduled jobs."""
    if not settings.telegram_token:
        raise ValueError("TELEGRAM_TOKEN not set in .env")

    app = Application.builder().token(settings.telegram_token).build()

    # ── Command handlers ──────────────────────────────────────────────
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("analyze", cmd_analyze))
    app.add_handler(CommandHandler("news", cmd_news))
    app.add_handler(CommandHandler("fibonacci", cmd_fibonacci))
    app.add_handler(CommandHandler("compare", cmd_compare))
    app.add_handler(CommandHandler("health", cmd_health))
    app.add_handler(CommandHandler("setalert", cmd_setalert))
    app.add_handler(CommandHandler("myalerts", cmd_myalerts))
    app.add_handler(CommandHandler("cancelalert", cmd_cancelalert))
    app.add_handler(CommandHandler("sectors", cmd_sectors))
    app.add_handler(CallbackQueryHandler(callback_handler))
    # Fallback handler must be LAST — catches all unrecognized text messages
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, cmd_fallback))

    # ── Scheduled jobs via JobQueue (APScheduler under the hood) ─────
    tz_us = pytz.timezone("America/New_York")
    jq = app.job_queue
    if jq is not None:
        # 9:00 AM ET — pre-open preview, Mon–Fri
        jq.run_daily(
            _job_market_preview,
            time=dt_time(9, 0, tzinfo=tz_us),
            days=(0, 1, 2, 3, 4),
            name="nyse_preview",
        )
        # 4:15 PM ET — post-close summary, Mon–Fri
        jq.run_daily(
            _job_market_close_regular,
            time=dt_time(16, 15, tzinfo=tz_us),
            days=(0, 1, 2, 3, 4),
            name="nyse_close",
        )
        # Every 5 min Mon–Fri — check active price alerts during market hours
        jq.run_repeating(
            _job_check_alerts,
            interval=300,
            first=10,
            name="price_alerts",
        )

    return app


# ── TelegramDispatcher Agent Wrapper ────────────────────────────────────────────


class TelegramDispatcher:
    """Agent wrapper for the Telegram bot."""

    def __init__(self) -> None:
        self.app: Application | None = None

    async def start(self) -> None:
        """Initialize and start polling."""
        self.app = build_application()
        logger.info("telegram_dispatcher_starting")
        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling(drop_pending_updates=True)
        logger.info("telegram_dispatcher_running")

    async def stop(self) -> None:
        """Gracefully stop the bot."""
        if self.app:
            await self.app.updater.stop()
            await self.app.stop()
            await self.app.shutdown()
        logger.info("telegram_dispatcher_stopped")

    async def health_check(self) -> dict[str, str]:
        """Return bot health status."""
        if self.app and self.app.running:
            return {"status": "ok", "detail": "Telegram bot running"}
        return {"status": "error", "detail": "Bot not running"}
