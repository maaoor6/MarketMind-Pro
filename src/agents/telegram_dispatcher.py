"""Telegram Dispatcher Agent — bot commands, inline keyboards, automated reports."""

import asyncio
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from src.agents.news_search_agent import NewsSearchAgent
from src.agents.quant_engine import QuantEngine
from src.quant.fibonacci import format_fibonacci_message, calculate_fibonacci
from src.quant.arbitrage import format_arbitrage_message, calculate_arbitrage, DUAL_LISTED
from src.utils.config import settings
from src.utils.logger import get_logger
from src.utils.timezone_utils import market_status, now_us, now_tase

logger = get_logger(__name__)

# Module-level agent instances
_quant_engine = QuantEngine()
_news_agent = NewsSearchAgent()


# ── Command Handlers ──────────────────────────────────────────────────


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command — welcome message."""
    keyboard = [
        [
            InlineKeyboardButton("📊 Analyze Ticker", callback_data="prompt_analyze"),
            InlineKeyboardButton("🌡️ Health Check", callback_data="health"),
        ],
        [
            InlineKeyboardButton("📰 Market Open Report", callback_data="market_open"),
            InlineKeyboardButton("📐 Fibonacci Levels", callback_data="prompt_fibonacci"),
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "🤖 *MarketMind-Pro* — Autonomous Trading Intelligence\n\n"
        "Commands:\n"
        "• /analyze `[TICKER]` — Full quant analysis + sentiment\n"
        "• /fibonacci `[TICKER]` — 52-week Fibonacci levels\n"
        "• /arbitrage `[TICKER]` — USD/ILS arbitrage gap\n"
        "• /health — System health status\n\n"
        "Choose an action:",
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_analyze(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /analyze [TICKER] — full analysis."""
    if not context.args:
        await update.message.reply_text("Usage: /analyze TICKER (e.g., /analyze TEVA)")
        return

    ticker = context.args[0].upper().strip()
    await update.message.reply_text(f"⏳ Analyzing *{ticker}*... please wait.", parse_mode=ParseMode.MARKDOWN)

    try:
        # Parallel: quant + sentiment
        quant_signal, sentiment = await asyncio.gather(
            _quant_engine.analyze(ticker),
            _news_agent.analyze_sentiment(ticker),
            return_exceptions=True,
        )

        if isinstance(quant_signal, Exception):
            await update.message.reply_text(f"❌ Quant analysis failed: {quant_signal}")
            return

        signals = quant_signal.signals
        fib = quant_signal.fibonacci

        # Build report
        rsi_val = signals.get("rsi")
        rsi_signal = signals.get("rsi_signal", "NEUTRAL")
        macd_hist = signals.get("macd_histogram", 0)
        vol_spike = signals.get("volume_spike", False)

        msg = (
            f"📊 *{ticker} — Full Analysis*\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"💰 Price: `${signals.get('price', 0):,.2f}`\n\n"
            f"*Technical Signals:*\n"
            f"  RSI(14): `{rsi_val:.1f}` — {_rsi_label(rsi_signal)}\n"
            f"  MACD Histogram: `{macd_hist:+.4f}` {'📈' if macd_hist > 0 else '📉'}\n"
            f"  Volume Spike: {'⚡ YES' if vol_spike else 'No'}\n\n"
        )

        if fib:
            msg += (
                f"*Fibonacci (52W):*\n"
                f"  High: `${fib['high_52w']:,.2f}` | Low: `${fib['low_52w']:,.2f}`\n"
                f"  Trend: {'📈' if fib['trend'] == 'UPTREND' else '📉'} {fib['trend']}\n"
                f"  Support: `${fib['nearest_support']:,.2f}`\n"
                f"  Resistance: `${fib['nearest_resistance']:,.2f}`\n\n"
            )

        if quant_signal.arbitrage:
            arb = quant_signal.arbitrage
            msg += (
                f"*Arbitrage:*\n"
                f"  Gap: `{arb['gap_pct']:.2f}%` ({arb['gap_direction']})\n"
                + ("  ⚡ *OPPORTUNITY!*\n" if arb["is_opportunity"] else "")
                + "\n"
            )

        if not isinstance(sentiment, Exception):
            msg += (
                f"*News Sentiment:*\n"
                f"  {sentiment.emoji} Score: `{sentiment.score:+.2f}`\n"
                f"  {sentiment.summary_en}\n"
            )

        # Inline keyboard for follow-up actions
        keyboard = [
            [
                InlineKeyboardButton("📐 Fibonacci", callback_data=f"fib:{ticker}"),
                InlineKeyboardButton("🔄 Refresh", callback_data=f"analyze:{ticker}"),
            ],
            [InlineKeyboardButton("🔔 Set Alert", callback_data=f"alert:{ticker}")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)

    except Exception as exc:
        logger.error("analyze_command_failed", ticker=ticker, error=str(exc))
        await update.message.reply_text(f"❌ Analysis failed: {exc}")


async def cmd_fibonacci(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /fibonacci [TICKER]."""
    if not context.args:
        await update.message.reply_text("Usage: /fibonacci TICKER (e.g., /fibonacci AAPL)")
        return

    ticker = context.args[0].upper().strip()
    try:
        df_result = await _quant_engine.fetch_price_data(ticker, period="1y", interval="1d")
        closes = df_result["Close"].squeeze()
        levels = calculate_fibonacci(closes, ticker=ticker)
        msg = format_fibonacci_message(levels)
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
    except Exception as exc:
        await update.message.reply_text(f"❌ Fibonacci failed: {exc}")


async def cmd_arbitrage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /arbitrage [TICKER]."""
    if not context.args:
        tickers = ", ".join(DUAL_LISTED.keys())
        await update.message.reply_text(f"Usage: /arbitrage TICKER\nDual-listed: {tickers}")
        return

    ticker = context.args[0].upper().strip()
    if ticker not in DUAL_LISTED:
        await update.message.reply_text(
            f"❌ {ticker} is not in the dual-listed universe.\n"
            f"Available: {', '.join(DUAL_LISTED.keys())}"
        )
        return

    try:
        tase_ticker = DUAL_LISTED[ticker]
        us_df = await _quant_engine.fetch_price_data(ticker, period="5d", interval="1d")
        tase_df = await _quant_engine.fetch_price_data(tase_ticker, period="5d", interval="1d")
        us_price = float(us_df["Close"].iloc[-1])
        tase_price = float(tase_df["Close"].iloc[-1])
        signal = await calculate_arbitrage(ticker, us_price, tase_price)
        msg = format_arbitrage_message(signal)
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
    except Exception as exc:
        await update.message.reply_text(f"❌ Arbitrage calc failed: {exc}")


async def cmd_health(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /health — system status dashboard."""
    from src.database.session import check_db_connection
    from src.database.cache import cache as redis_cache

    quant_health, news_health, db_health, redis_health = await asyncio.gather(
        _quant_engine.health_check(),
        _news_agent.health_check(),
        check_db_connection(),
        redis_cache.health_check(),
    )

    def status_emoji(h: dict) -> str:
        return "✅" if h.get("status") == "ok" else "⚠️" if h.get("status") == "degraded" else "❌"

    mkt = market_status()
    msg = (
        "🏥 *MarketMind-Pro System Health*\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"{status_emoji(quant_health)} Quant Engine: {quant_health.get('detail', '')}\n"
        f"{status_emoji(news_health)} News Agent: {news_health.get('detail', '')}\n"
        f"{status_emoji(db_health)} PostgreSQL: {db_health.get('detail', '')}\n"
        f"{status_emoji(redis_health)} Redis: {redis_health.get('detail', '')}\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"🇺🇸 NYSE: {'🟢 OPEN' if mkt['nyse_open'] else '🔴 CLOSED'} ({mkt['us_time']})\n"
        f"🇮🇱 TASE: {'🟢 OPEN' if mkt['tase_open'] else '🔴 CLOSED'} ({mkt['tase_time']})\n"
    )

    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)


# ── Callback Query Handler ────────────────────────────────────────────


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline keyboard button presses."""
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "health":
        context.args = []
        update.message = query.message
        await cmd_health(update, context)

    elif data.startswith("analyze:"):
        ticker = data.split(":", 1)[1]
        context.args = [ticker]
        update.message = query.message
        await cmd_analyze(update, context)

    elif data.startswith("fib:"):
        ticker = data.split(":", 1)[1]
        context.args = [ticker]
        update.message = query.message
        await cmd_fibonacci(update, context)

    elif data == "prompt_analyze":
        await query.message.reply_text("Send: /analyze TICKER (e.g., /analyze TEVA)")

    elif data == "prompt_fibonacci":
        await query.message.reply_text("Send: /fibonacci TICKER (e.g., /fibonacci AAPL)")


# ── Market Open/Close Reports ─────────────────────────────────────────


async def send_market_open_report(app: Application) -> None:
    """Automated morning report sent at market open."""
    watchlist = ["TEVA", "NICE", "CHKP", "AAPL", "MSFT", "SPY"]
    msg_lines = ["🔔 *Market Open Report*\n━━━━━━━━━━━━━━━━━━━"]

    for ticker in watchlist:
        try:
            signal = await _quant_engine.analyze(ticker)
            rsi = signal.signals.get("rsi")
            msg_lines.append(
                f"• *{ticker}*: ${signal.price:,.2f} | RSI: {rsi:.1f}" if rsi else f"• *{ticker}*: ${signal.price:,.2f}"
            )
        except Exception as exc:
            logger.warning("market_open_report_ticker_failed", ticker=ticker, error=str(exc))

    await app.bot.send_message(
        chat_id=settings.telegram_chat_id,
        text="\n".join(msg_lines),
        parse_mode=ParseMode.MARKDOWN,
    )


async def send_market_close_report(app: Application) -> None:
    """Automated end-of-day report."""
    msg = (
        "🌙 *Market Close Summary*\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"US Close: {now_us().strftime('%H:%M %Z')}\n"
        f"TASE Close: {now_tase().strftime('%H:%M %Z')}\n\n"
        "Daily report generation complete. Review tomorrow's watchlist."
    )
    await app.bot.send_message(
        chat_id=settings.telegram_chat_id,
        text=msg,
        parse_mode=ParseMode.MARKDOWN,
    )


# ── Helpers ───────────────────────────────────────────────────────────


def _rsi_label(signal: str) -> str:
    return {"OVERSOLD": "🟢 OVERSOLD", "OVERBOUGHT": "🔴 OVERBOUGHT", "NEUTRAL": "⚪ NEUTRAL"}.get(signal, signal)


# ── Application Builder ───────────────────────────────────────────────


def build_application() -> Application:
    """Build and configure the Telegram bot application."""
    if not settings.telegram_token:
        raise ValueError("TELEGRAM_TOKEN is not set in .env")

    app = Application.builder().token(settings.telegram_token).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("analyze", cmd_analyze))
    app.add_handler(CommandHandler("fibonacci", cmd_fibonacci))
    app.add_handler(CommandHandler("arbitrage", cmd_arbitrage))
    app.add_handler(CommandHandler("health", cmd_health))
    app.add_handler(CallbackQueryHandler(callback_handler))

    return app


class TelegramDispatcher:
    """Agent wrapper for the Telegram bot."""

    def __init__(self) -> None:
        self.app: Application | None = None

    async def start(self) -> None:
        self.app = build_application()
        logger.info("telegram_dispatcher_starting")
        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling(drop_pending_updates=True)
        logger.info("telegram_dispatcher_running")

    async def stop(self) -> None:
        if self.app:
            await self.app.updater.stop()
            await self.app.stop()
            await self.app.shutdown()
        logger.info("telegram_dispatcher_stopped")

    async def health_check(self) -> dict[str, str]:
        if self.app and self.app.running:
            return {"status": "ok", "detail": "Telegram bot polling"}
        return {"status": "error", "detail": "Bot not running"}
