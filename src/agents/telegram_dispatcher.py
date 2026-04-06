"""Telegram Dispatcher Agent — bot commands, inline keyboards, automated reports."""

import asyncio
import html
from datetime import datetime
from datetime import time as dt_time

import pytz
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
from src.quant.arbitrage import DUAL_LISTED, calculate_arbitrage
from src.quant.fibonacci import calculate_fibonacci
from src.quant.fundamentals import (
    CompanyProfile,
    fetch_company_profile,
    fetch_insider_transactions,
    format_insiders_hebrew,
    format_profile_hebrew,
    save_insider_transactions,
)
from src.ui.publisher import publish_ticker_chart
from src.utils.config import settings
from src.utils.logger import get_logger
from src.utils.timezone_utils import (
    currency_symbol,
    is_friday_session,
    market_status,
    now_tase,
    now_us,
)

logger = get_logger(__name__)

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


# ── Command Handlers ────────────────────────────────────────────────────────────


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command — Hebrew welcome message with inline keyboard."""
    keyboard = [
        [
            InlineKeyboardButton("📊 ניתוח מניה", callback_data="prompt_analyze"),
            InlineKeyboardButton("🌡️ בדיקת מערכת", callback_data="health"),
        ],
        [
            InlineKeyboardButton("📰 דוח פתיחת שוק", callback_data="market_open"),
            InlineKeyboardButton("📐 רמות פיבונאצ'י", callback_data="prompt_fibonacci"),
        ],
        [
            InlineKeyboardButton("⚖️ ארביטראז'", callback_data="prompt_arbitrage"),
            InlineKeyboardButton("🆚 השוואת מניות", callback_data="prompt_compare"),
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    mkt = market_status()
    nyse_status = "🟢 פתוח" if mkt["nyse_open"] else "🔴 סגור"
    tase_status = "🟢 פתוח" if mkt["tase_open"] else "🔴 סגור"
    friday_note = (
        "\n⚠️ <i>יום שישי — סגירה מוקדמת 15:45</i>" if mkt.get("tase_friday") else ""
    )

    msg = (
        "🤖 <b>MarketMind-Pro</b> — מערכת מסחר אוטונומית\n\n"
        "📡 <b>מצב שוק:</b>\n"
        f"  🇺🇸 NYSE: {nyse_status} ({mkt['us_time']})\n"
        f"  🇮🇱 ת\"א: {tase_status} ({mkt['tase_time']}){friday_note}\n\n"
        "📋 <b>פקודות זמינות:</b>\n"
        "  • /analyze <code>[מניה]</code> — ניתוח מלא + פונדמנטלי + סנטימנט\n"
        "  • /fibonacci <code>[מניה]</code> — רמות פיבונאצ'י 52 שבועות\n"
        "  • /arbitrage <code>[מניה]</code> — פער ארביטראז' TASE/NYSE\n"
        "  • /compare <code>[מניה1] [מניה2]</code> — השוואת מניות\n"
        "  • /health — סטטוס מערכת\n\n"
        "בחר פעולה:"
    )

    await update.message.reply_text(
        msg, reply_markup=reply_markup, parse_mode=ParseMode.HTML
    )


async def cmd_analyze(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /analyze [TICKER] — full Hebrew analysis with fundamentals and chart link."""
    msg_obj = update.message or (
        update.callback_query.message if update.callback_query else None
    )
    if not msg_obj:
        return

    if not context.args:
        await msg_obj.reply_text(
            "❗ שימוש: /analyze מניה\nדוגמה: <code>/analyze TEVA</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    ticker = context.args[0].upper().strip()
    await msg_obj.reply_text(
        f"⏳ מנתח את <b>{html.escape(ticker)}</b>... אנא המתן.",
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
                f"❌ ניתוח נכשל: <code>{html.escape(str(quant_signal))}</code>",
                parse_mode=ParseMode.HTML,
            )
            return

        # ── Phase 2: Fundamentals + Insiders (parallel, best-effort) ──
        profile_result: CompanyProfile | Exception | None = None
        insiders_result: list = []
        chart_url: str | None = None

        try:
            profile_result, insiders_raw = await asyncio.gather(
                fetch_company_profile(ticker),
                fetch_insider_transactions(ticker),
                return_exceptions=True,
            )
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
        except Exception as pub_exc:
            logger.warning("chart_publish_failed", ticker=ticker, error=str(pub_exc))

        # ── Build message ──
        signals = quant_signal.signals
        fib = quant_signal.fibonacci
        arb = quant_signal.arbitrage
        mkt = quant_signal.market_status or {}

        price = signals.get("price", 0)
        sym = currency_symbol(ticker)
        rsi_val = signals.get("rsi")
        rsi_signal = signals.get("rsi_signal", "NEUTRAL")
        macd_line = signals.get("macd_line", 0)
        macd_sig = signals.get("macd_signal", 0)
        macd_hist = signals.get("macd_histogram", 0)
        vol_spike = signals.get("volume_spike", False)
        mas = signals.get("moving_averages", {})

        lines = [
            f"📊 <b>ניתוח {html.escape(ticker)}</b>",
            "━━━━━━━━━━━━━━━━━━━",
        ]

        # ── Company header (TOP) ──
        if isinstance(profile_result, CompanyProfile):
            lines.append(format_profile_hebrew(profile_result))
            lines.append("")

        lines += [
            f"💰 <b>מחיר נוכחי:</b> <code>{sym}{price:,.2f}</code>",
            f"🕐 <b>עדכון:</b> {datetime.utcnow().strftime('%d/%m/%Y %H:%M')} UTC",
            "",
        ]

        # ── Technical indicators ──
        rsi_emoji, rsi_he = _rsi_hebrew(rsi_signal)
        lines += [
            "📉 <b>אינדיקטורים טכניים:</b>",
            f"  RSI(14): <code>{rsi_val:.1f}</code>  {rsi_emoji} {rsi_he}",
            f"  MACD קו: <code>{macd_line:+.4f}</code>  |  סיגנל: <code>{macd_sig:+.4f}</code>",
            f"  MACD היסטוגרמה: <code>{macd_hist:+.4f}</code>  {'📈 שורי' if macd_hist > 0 else '📉 דובי'}",
            f"  ספייק נפח: {'⚡ כן — נפח חריג!' if vol_spike else '❌ לא'}",
            "",
        ]

        # ── Moving averages ──
        if mas:
            lines.append("📏 <b>ממוצעים נעים:</b>")
            for ma_key in ["SMA_20", "SMA_50", "SMA_150", "SMA_200"]:
                val = mas.get(ma_key)
                if val:
                    relation = "↑ מעל" if price > val else "↓ מתחת"
                    lines.append(
                        f"  {ma_key}: <code>{sym}{val:,.2f}</code>  {relation}"
                    )
            lines.append("")

        # ── Fibonacci ──
        if fib:
            trend_he = "מגמת עלייה 📈" if fib["trend"] == "UPTREND" else "מגמת ירידה 📉"
            high_52w = fib["high_52w"]
            low_52w = fib["low_52w"]
            pct_from_low = (
                ((price - low_52w) / (high_52w - low_52w) * 100)
                if (high_52w - low_52w) > 0
                else 0
            )

            lines += [
                "📐 <b>פיבונאצ'י (52 שבועות):</b>",
                f"  שיא: <code>{sym}{high_52w:,.2f}</code>  |  שפל: <code>{sym}{low_52w:,.2f}</code>",
                f"  {trend_he}  |  מיקום: {pct_from_low:.1f}% מהשפל",
                f"  🟢 תמיכה קרובה: <code>{sym}{fib['nearest_support']:,.2f}</code>",
                f"  🔴 התנגדות קרובה: <code>{sym}{fib['nearest_resistance']:,.2f}</code>",
            ]
            retr = fib.get("retracements", {})
            key_levels = ["23.6%", "38.2%", "50.0%", "61.8%"]
            for lvl in key_levels:
                if lvl in retr:
                    marker = (
                        " ◀ מחיר כאן"
                        if abs(retr[lvl] - price) / max(price, 0.001) < 0.015
                        else ""
                    )
                    lines.append(
                        f"    {lvl}: <code>{sym}{retr[lvl]:,.2f}</code>{marker}"
                    )
            lines.append("")

        # ── Arbitrage ──
        if arb:
            arb_emoji = "⚡" if arb["is_opportunity"] else "⚖️"
            direction_he = (
                'ארה"ב במחיר פרמיום'
                if arb["gap_direction"] == "US_PREMIUM"
                else 'ת"א במחיר פרמיום'
            )
            lines += [
                f"{arb_emoji} <b>ארביטראז' TASE/NYSE:</b>",
                f"  TASE ({html.escape(arb['ticker_tase'])}): <code>${arb['price_tase_in_usd']:,.3f}</code>  |  שער: <code>₪{arb['usd_ils_rate']:.4f}</code>",
                f"  פער: <code>{arb['gap_pct']:.2f}%</code>  —  {direction_he}",
            ]
            if arb["is_opportunity"]:
                lines.append("  ⚡ <b>הזדמנות ארביטראז' זוהתה!</b>")
            lines.append("")

        # ── Insiders ──
        if insiders_result:
            lines.append(format_insiders_hebrew(ticker, insiders_result))
            lines.append("")

        # ── Sentiment ──
        if not isinstance(sentiment, Exception) and sentiment:
            score_bar = _sentiment_bar(sentiment.score)
            lines += [
                "📰 <b>סנטימנט חדשות:</b>",
                f"  {sentiment.emoji} ציון: <code>{sentiment.score:+.2f}</code>  {score_bar}",
            ]
            if sentiment.summary_he:
                lines.append(f"  <i>{html.escape(sentiment.summary_he)}</i>")
            if sentiment.recent_headlines:
                for item in sentiment.recent_headlines[:3]:
                    src = item.get("source", "")
                    title = item.get("title", "")
                    snippet = item.get("snippet", "")
                    lines.append(f"    • <b>{html.escape(title)}</b>")
                    if snippet:
                        lines.append(f"      <i>{html.escape(snippet)}</i>")
                    if src:
                        lines.append(f"      <code>{html.escape(src)}</code>")
            elif sentiment.headlines_he:
                for h_line in sentiment.headlines_he[:3]:
                    lines.append(f"    • {html.escape(h_line)}")
            elif sentiment.headlines_en:
                for h_line in sentiment.headlines_en[:2]:
                    lines.append(f"    • {html.escape(h_line)}")
            lines.append("")

        # ── Market status ──
        if mkt:
            nyse_str = "🟢 פתוח" if mkt.get("nyse_open") else "🔴 סגור"
            tase_str = "🟢 פתוח" if mkt.get("tase_open") else "🔴 סגור"
            lines += [
                "🌍 <b>מצב שוק:</b>",
                f'  🇺🇸 NYSE: {nyse_str}  |  🇮🇱 ת"א: {tase_str}',
            ]

        # ── Chart link ──
        if chart_url:
            lines += ["", f"📊 {link('צפה בגרף אינטראקטיבי', chart_url)}"]

        # ── Inline keyboard ──
        keyboard = [
            [
                InlineKeyboardButton("📐 פיבונאצ'י", callback_data=f"fib:{ticker}"),
                InlineKeyboardButton("🔄 רענן", callback_data=f"analyze:{ticker}"),
            ],
        ]
        if ticker in DUAL_LISTED:
            keyboard[0].append(
                InlineKeyboardButton("⚖️ ארביטראז'", callback_data=f"arb:{ticker}")
            )

        await msg_obj.reply_text(
            "\n".join(lines),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    except Exception as exc:
        logger.error("analyze_command_failed", ticker=ticker, error=str(exc))
        await msg_obj.reply_text(
            f"❌ הניתוח נכשל: <code>{html.escape(str(exc))}</code>",
            parse_mode=ParseMode.HTML,
        )


async def cmd_fibonacci(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /fibonacci [TICKER] — Hebrew Fibonacci report."""
    msg_obj = update.message or (
        update.callback_query.message if update.callback_query else None
    )
    if not msg_obj:
        return

    if not context.args:
        await msg_obj.reply_text(
            "❗ שימוש: /fibonacci מניה\nדוגמה: <code>/fibonacci AAPL</code>",
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
        sym = currency_symbol(ticker)
        spread = levels.high_52w - levels.low_52w
        pct_from_low = ((price - levels.low_52w) / spread * 100) if spread > 0 else 0
        trend_he = "מגמת עלייה 📈" if levels.trend == "UPTREND" else "מגמת ירידה 📉"

        lines = [
            f"📐 <b>רמות פיבונאצ'י — {html.escape(ticker)}</b>",
            "━━━━━━━━━━━━━━━━━━━",
            f"  {trend_he}",
            f"  שיא 52 שב': <code>{sym}{levels.high_52w:,.2f}</code>",
            f"  שפל 52 שב': <code>{sym}{levels.low_52w:,.2f}</code>",
            f"  מחיר נוכחי: <code>{sym}{price:,.2f}</code>  ({pct_from_low:.1f}% מהשפל)",
            "",
            "📉 <b>רמות חיזור (Retracement):</b>",
        ]

        for label, lvl_price in levels.retracements.items():
            relation = (
                "◀ מחיר כאן"
                if abs(lvl_price - price) / max(price, 0.001) < 0.015
                else ("↑ מעל" if price > lvl_price else "↓ מתחת")
            )
            lines.append(f"  {label}: <code>{sym}{lvl_price:,.2f}</code>  {relation}")

        lines += ["", "📈 <b>רמות הרחבה (Extension):</b>"]
        for label, lvl_price in levels.extensions.items():
            lines.append(f"  {label}: <code>{sym}{lvl_price:,.2f}</code>")

        lines += [
            "",
            f"🟢 <b>תמיכה קרובה:</b> <code>{sym}{levels.nearest_support:,.2f}</code>",
            f"🔴 <b>התנגדות קרובה:</b> <code>{sym}{levels.nearest_resistance:,.2f}</code>",
        ]

        keyboard = [
            [InlineKeyboardButton("📊 ניתוח מלא", callback_data=f"analyze:{ticker}")]
        ]
        await msg_obj.reply_text(
            "\n".join(lines),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except Exception as exc:
        await msg_obj.reply_text(
            f"❌ חישוב פיבונאצ'י נכשל: <code>{html.escape(str(exc))}</code>",
            parse_mode=ParseMode.HTML,
        )


async def cmd_arbitrage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /arbitrage [TICKER] — Hebrew arbitrage report."""
    msg_obj = update.message or (
        update.callback_query.message if update.callback_query else None
    )
    if not msg_obj:
        return

    if not context.args:
        tickers = ", ".join(DUAL_LISTED.keys())
        await msg_obj.reply_text(
            f"❗ שימוש: /arbitrage מניה\nמניות כפול-רישום: <code>{tickers}</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    ticker = context.args[0].upper().strip()
    if ticker not in DUAL_LISTED:
        await msg_obj.reply_text(
            f"❌ {html.escape(ticker)} אינה ברשימת הכפול-רישום.\n"
            f"זמינות: <code>{', '.join(DUAL_LISTED.keys())}</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    try:
        tase_ticker = DUAL_LISTED[ticker]
        us_df, tase_df = await asyncio.gather(
            _quant_engine.fetch_price_data(ticker, period="5d", interval="1d"),
            _quant_engine.fetch_price_data(tase_ticker, period="5d", interval="1d"),
        )
        us_price = float(us_df["Close"].iloc[-1])
        tase_price = float(tase_df["Close"].iloc[-1])
        signal = await calculate_arbitrage(ticker, us_price, tase_price)

        direction_he = (
            'ארה"ב במחיר פרמיום 🇺🇸'
            if signal.gap_direction == "US_PREMIUM"
            else 'ת"א במחיר פרמיום 🇮🇱'
        )
        opp_line = (
            "⚡ <b>הזדמנות ארביטראז' זוהתה!</b>"
            if signal.is_opportunity
            else "ℹ️ אין הזדמנות ארביטראז' משמעותית"
        )
        emoji = "⚡" if signal.is_opportunity else "⚖️"

        lines = [
            f"{emoji} <b>ארביטראז' — {html.escape(ticker)} / {html.escape(tase_ticker)}</b>",
            "━━━━━━━━━━━━━━━━━━━",
            f'  🇺🇸 מחיר ארה"ב: <code>${signal.price_us_usd:,.3f}</code>',
            f'  🇮🇱 מחיר ת"א: <code>₪{signal.price_tase_ils:,.3f}</code>  (<code>${signal.price_tase_in_usd:,.3f}</code>)',
            f"  💱 שער USD/ILS: <code>₪{signal.usd_ils_rate:.4f}</code>",
            f"  📊 פער: <code>{signal.gap_pct:.2f}%</code>  —  {direction_he}",
            "",
            opp_line,
        ]

        keyboard = [
            [InlineKeyboardButton("📊 ניתוח מלא", callback_data=f"analyze:{ticker}")]
        ]
        await msg_obj.reply_text(
            "\n".join(lines),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except Exception as exc:
        await msg_obj.reply_text(
            f"❌ חישוב ארביטראז' נכשל: <code>{html.escape(str(exc))}</code>",
            parse_mode=ParseMode.HTML,
        )


async def cmd_compare(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /compare TICKER1 TICKER2 — side-by-side Hebrew comparison table."""
    msg_obj = update.message or (
        update.callback_query.message if update.callback_query else None
    )
    if not msg_obj:
        return

    if not context.args or len(context.args) < 2:
        await msg_obj.reply_text(
            "❗ שימוש: /compare מניה1 מניה2\nדוגמה: <code>/compare TEVA CHKP</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    t1 = context.args[0].upper().strip()
    t2 = context.args[1].upper().strip()

    await msg_obj.reply_text(
        f"⏳ משווה <b>{html.escape(t1)}</b> מול <b>{html.escape(t2)}</b>...",
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
                return "שגיאה"
            sym = currency_symbol(sig.ticker)  # type: ignore[union-attr]
            return f"{sym}{sig.price:,.2f}"  # type: ignore[union-attr]

        def _rsi(sig: object) -> str:
            if isinstance(sig, Exception):
                return "—"
            v = sig.signals.get("rsi")  # type: ignore[union-attr]
            return f"{v:.1f}" if v else "—"

        def _macd(sig: object) -> str:
            if isinstance(sig, Exception):
                return "—"
            h = sig.signals.get("macd_histogram", 0)  # type: ignore[union-attr]
            return "📈 שורי" if h > 0 else "📉 דובי"

        def _fib(sig: object) -> str:
            if isinstance(sig, Exception):
                return "—"
            fib = sig.fibonacci  # type: ignore[union-attr]
            if not fib:
                return "—"
            return "📈 עלייה" if fib["trend"] == "UPTREND" else "📉 ירידה"

        def _pe(prof: object) -> str:
            if isinstance(prof, Exception):
                return "—"
            v = getattr(prof, "pe_trailing", None)
            return f"{v:.1f}" if v else "לא זמין"

        def _eps(prof: object) -> str:
            if isinstance(prof, Exception):
                return "—"
            v = getattr(prof, "eps_trailing", None)
            return f"{v:.2f}" if v else "לא זמין"

        def _cap(prof: object) -> str:
            if isinstance(prof, Exception):
                return "—"
            cap = getattr(prof, "market_cap", None)
            if not cap:
                return "לא זמין"
            return f"{cap / 1e9:.1f}B" if cap >= 1e9 else f"{cap / 1e6:.0f}M"

        rows = [
            ("💰 מחיר", _price(sig1), _price(sig2)),
            ("📉 RSI(14)", _rsi(sig1), _rsi(sig2)),
            ("📊 MACD", _macd(sig1), _macd(sig2)),
            ("📐 פיבונאצ'י", _fib(sig1), _fib(sig2)),
            ("📈 P/E", _pe(prof1), _pe(prof2)),
            ("💵 EPS", _eps(prof1), _eps(prof2)),
            ("🏦 שווי שוק", _cap(prof1), _cap(prof2)),
        ]

        lines = [
            f"⚖️ <b>השוואה: {html.escape(t1)} מול {html.escape(t2)}</b>",
            "━━━━━━━━━━━━━━━━━━━",
            f"<b>{'מדד':<14} {t1:<12} {t2}</b>",
            "─────────────────────────────",
        ]
        for label, v1, v2 in rows:
            lines.append(
                f"{html.escape(label):<14}  "
                f"<code>{html.escape(str(v1)):<12}</code>  "
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
            f"❌ ההשוואה נכשלה: <code>{html.escape(str(exc))}</code>",
            parse_mode=ParseMode.HTML,
        )


async def cmd_health(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /health — Hebrew system status dashboard."""
    from src.database.cache import cache as redis_cache
    from src.database.session import check_db_connection

    msg_obj = update.message or (
        update.callback_query.message if update.callback_query else None
    )
    if not msg_obj:
        return

    quant_health, news_health, db_health, redis_health = await asyncio.gather(
        _quant_engine.health_check(),
        _news_agent.health_check(),
        check_db_connection(),
        redis_cache.health_check(),
    )

    def _s(h: dict) -> str:
        return (
            "✅"
            if h.get("status") == "ok"
            else "⚠️" if h.get("status") == "degraded" else "❌"
        )

    mkt = market_status()
    tase_extra = " (שישי — סגירה 15:45)" if mkt.get("tase_friday") else ""
    lines = [
        "🏥 <b>סטטוס מערכת — MarketMind-Pro</b>",
        "━━━━━━━━━━━━━━━━━━━",
        f"{_s(quant_health)} מנוע כמותי: {html.escape(quant_health.get('detail', ''))}",
        f"{_s(news_health)} סוכן חדשות: {html.escape(news_health.get('detail', ''))}",
        f"{_s(db_health)} PostgreSQL: {html.escape(db_health.get('detail', ''))}",
        f"{_s(redis_health)} Redis: {html.escape(redis_health.get('detail', ''))}",
        "━━━━━━━━━━━━━━━━━━━",
        f"🇺🇸 NYSE: {'🟢 פתוח' if mkt['nyse_open'] else '🔴 סגור'}  ({mkt['us_time']})",
        f"🇮🇱 ת\"א: {'🟢 פתוח' if mkt['tase_open'] else '🔴 סגור'}  ({mkt['tase_time']}){tase_extra}",
    ]

    await msg_obj.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


# ── Callback Query Handler ──────────────────────────────────────────────────────


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline keyboard button presses."""
    query = update.callback_query
    await query.answer()
    data = query.data

    update.message = query.message

    if data == "health":
        await cmd_health(update, context)

    elif data.startswith("analyze:"):
        context.args = [data.split(":", 1)[1]]
        await cmd_analyze(update, context)

    elif data.startswith("fib:"):
        context.args = [data.split(":", 1)[1]]
        await cmd_fibonacci(update, context)

    elif data.startswith("arb:"):
        context.args = [data.split(":", 1)[1]]
        await cmd_arbitrage(update, context)

    elif data == "prompt_analyze":
        await query.message.reply_text(
            "שלח: /analyze מניה\nדוגמה: <code>/analyze TEVA</code>",
            parse_mode=ParseMode.HTML,
        )

    elif data == "prompt_fibonacci":
        await query.message.reply_text(
            "שלח: /fibonacci מניה\nדוגמה: <code>/fibonacci AAPL</code>",
            parse_mode=ParseMode.HTML,
        )

    elif data == "prompt_arbitrage":
        tickers = ", ".join(DUAL_LISTED.keys())
        await query.message.reply_text(
            f"שלח: /arbitrage מניה\nמניות כפול-רישום: <code>{tickers}</code>",
            parse_mode=ParseMode.HTML,
        )

    elif data == "prompt_compare":
        await query.message.reply_text(
            "שלח: /compare מניה1 מניה2\nדוגמה: <code>/compare TEVA CHKP</code>",
            parse_mode=ParseMode.HTML,
        )

    elif data == "market_open":
        await send_market_open_report(context.application)


# ── Scheduled Job Functions ─────────────────────────────────────────────────────


async def _job_market_preview(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Scheduled job: Hebrew market preview 30 min before TASE open (09:30 IL, Mon–Fri)."""
    mkt = market_status()
    friday_note = (
        "\n⚠️ <b>יום שישי — סגירה מוקדמת 15:45</b>" if mkt.get("tase_friday") else ""
    )

    watchlist_preview = ["TEVA", "NICE", "CHKP"]
    preview_lines: list[str] = []
    for t in watchlist_preview:
        try:
            sig = await _quant_engine.analyze(t)
            rsi_v = sig.signals.get("rsi")
            rsi_emoji, _ = _rsi_hebrew(sig.signals.get("rsi_signal", "NEUTRAL"))
            sym = currency_symbol(t)
            preview_lines.append(
                f"  • <b>{t}</b>: <code>{sym}{sig.price:,.2f}</code>  RSI: {rsi_v:.1f} {rsi_emoji}"
                if rsi_v
                else f"  • <b>{t}</b>: <code>{sym}{sig.price:,.2f}</code>"
            )
        except Exception:
            preview_lines.append(f"  • <b>{t}</b>: ❌")

    msg = (
        '🌅 <b>תצוגה מקדימה לפני פתיחת שוק ת"א</b>\n'
        f"🕙 {now_tase().strftime('%H:%M')} שעון ישראל — פתיחה בעוד ~30 דקות"
        + friday_note
        + "\n\n"
        "📋 <b>מצב מניות מובילות:</b>\n" + "\n".join(preview_lines)
    )
    await context.bot.send_message(
        chat_id=settings.telegram_chat_id,
        text=msg,
        parse_mode=ParseMode.HTML,
    )


async def _job_market_close_regular(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Scheduled job: end-of-day summary Mon–Thu (17:45 IL time)."""
    await send_market_close_report(context.application)


async def _job_market_close_friday(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Scheduled job: Friday early-close summary (16:05 IL time)."""
    await send_market_close_report(context.application)


# ── Automated Reports ───────────────────────────────────────────────────────────


async def send_market_open_report(app: Application) -> None:
    """Automated Hebrew morning report at market open."""
    watchlist = ["TEVA", "NICE", "CHKP", "AAPL", "MSFT", "SPY"]
    lines = [
        "🔔 <b>דוח פתיחת שוק</b>",
        f"🕐 {datetime.utcnow().strftime('%d/%m/%Y %H:%M')} UTC",
        "━━━━━━━━━━━━━━━━━━━",
    ]

    for ticker in watchlist:
        try:
            signal = await _quant_engine.analyze(ticker)
            rsi_val = signal.signals.get("rsi")
            rsi_signal = signal.signals.get("rsi_signal", "NEUTRAL")
            macd_hist = signal.signals.get("macd_histogram", 0)
            rsi_emoji, _ = _rsi_hebrew(rsi_signal)
            sym = currency_symbol(ticker)
            lines.append(
                f"• <b>{ticker}</b>: <code>{sym}{signal.price:,.2f}</code>  "
                f"RSI: {rsi_val:.1f} {rsi_emoji}  "
                f"MACD: {'📈' if macd_hist > 0 else '📉'}"
                if rsi_val
                else f"• <b>{ticker}</b>: <code>{sym}{signal.price:,.2f}</code>"
            )
        except Exception as exc:
            logger.warning(
                "market_open_report_ticker_failed", ticker=ticker, error=str(exc)
            )
            lines.append(f"• <b>{ticker}</b>: ❌ נכשל")

    await app.bot.send_message(
        chat_id=settings.telegram_chat_id,
        text="\n".join(lines),
        parse_mode=ParseMode.HTML,
    )


async def send_market_close_report(app: Application) -> None:
    """Automated end-of-day Hebrew report."""
    friday_note = " (שישי — סגירה מוקדמת)" if is_friday_session() else ""
    msg = (
        "🌙 <b>סיכום סגירת שוק</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"🇺🇸 סגירת NYSE: {now_us().strftime('%H:%M %Z')}\n"
        f"🇮🇱 סגירת ת\"א: {now_tase().strftime('%H:%M %Z')}{friday_note}\n\n"
        "✅ דוח יומי הושלם. בדוק את רשימת המעקב למחר."
    )
    await app.bot.send_message(
        chat_id=settings.telegram_chat_id,
        text=msg,
        parse_mode=ParseMode.HTML,
    )


# ── Helpers ─────────────────────────────────────────────────────────────────────


def _rsi_hebrew(signal: str) -> tuple[str, str]:
    """Return (emoji, Hebrew label) for RSI signal."""
    return {
        "OVERSOLD": ("🟢", "מכור יתר — אות קנייה"),
        "OVERBOUGHT": ("🔴", "קנוי יתר — אות מכירה"),
        "NEUTRAL": ("⚪", "נייטרלי"),
    }.get(signal, ("⚪", signal))


def _sentiment_bar(score: float) -> str:
    """Convert sentiment score to a 10-char visual bar."""
    filled = max(0, min(10, round((score + 1) / 2 * 10)))
    return "▓" * filled + "░" * (10 - filled)


# ── Application Builder ──────────────────────────────────────────────────────────


def build_application() -> Application:
    """Build and configure the Telegram bot application with scheduled jobs."""
    if not settings.telegram_token:
        raise ValueError("TELEGRAM_TOKEN לא הוגדר ב-.env")

    app = Application.builder().token(settings.telegram_token).build()

    # ── Command handlers ──────────────────────────────────────────────
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("analyze", cmd_analyze))
    app.add_handler(CommandHandler("fibonacci", cmd_fibonacci))
    app.add_handler(CommandHandler("arbitrage", cmd_arbitrage))
    app.add_handler(CommandHandler("compare", cmd_compare))
    app.add_handler(CommandHandler("health", cmd_health))
    app.add_handler(CallbackQueryHandler(callback_handler))

    # ── Scheduled jobs via JobQueue (APScheduler under the hood) ─────
    tz_il = pytz.timezone("Asia/Jerusalem")
    jq = app.job_queue
    if jq is not None:
        # 09:30 IL — pre-open preview, Mon–Fri
        jq.run_daily(
            _job_market_preview,
            time=dt_time(9, 30, tzinfo=tz_il),
            days=(0, 1, 2, 3, 4),
            name="tase_preview",
        )
        # 17:45 IL — post-close summary, Mon–Thu
        jq.run_daily(
            _job_market_close_regular,
            time=dt_time(17, 45, tzinfo=tz_il),
            days=(0, 1, 2, 3),
            name="tase_close_regular",
        )
        # 16:05 IL — Friday early-close summary
        jq.run_daily(
            _job_market_close_friday,
            time=dt_time(16, 5, tzinfo=tz_il),
            days=(4,),
            name="tase_close_friday",
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
            return {"status": "ok", "detail": "Telegram bot פעיל"}
        return {"status": "error", "detail": "Bot לא פועל"}
