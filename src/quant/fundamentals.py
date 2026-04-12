"""Company fundamentals, insider transactions, and competitor analysis via yfinance."""

import asyncio
import dataclasses
import html
from dataclasses import dataclass
from datetime import UTC, datetime

import pandas as pd
import yfinance as yf
from sqlalchemy import select

from src.database.cache import cache
from src.database.models import InsiderTransaction
from src.database.session import AsyncSessionLocal
from src.utils.logger import get_logger
from src.utils.timezone_utils import TZ_UTC

logger = get_logger(__name__)

FUNDAMENTALS_CACHE_TTL = 14400  # 4 hours
EARNINGS_CACHE_TTL = 3600  # 1 hour

# ── Competitor static map ─────────────────────────────────────────────────────

COMPETITOR_MAP: dict[str, list[str]] = {
    # Dual-listed Israeli stocks
    "TEVA": ["MRK", "PFE", "AMGN"],
    "NICE": ["VEEV", "CRM", "WDAY"],
    "CHKP": ["PANW", "CRWD", "FTNT"],
    "AMDOCS": ["ACN", "EPAM", "CTSH"],
    "CEVA": ["MRVL", "QCOM", "ARM"],
    "GILT": ["GIL", "HBI"],
    "RADCOM": ["JNPR", "CSCO"],
    "TOWER": ["TXN", "NXPI", "ON"],
    "ORCL": ["SAP", "MSFT", "CRM"],
    # Major US stocks
    "AAPL": ["MSFT", "GOOGL", "META"],
    "MSFT": ["AAPL", "GOOGL", "AMZN"],
    "GOOGL": ["META", "MSFT", "AMZN"],
    "AMZN": ["MSFT", "GOOGL", "BABA"],
    "META": ["SNAP", "GOOGL", "PINS"],
    "NVDA": ["AMD", "INTC", "QCOM"],
    "SPY": ["QQQ", "IWM", "DIA"],
}


# ── Dataclasses ───────────────────────────────────────────────────────────────


@dataclass
class CompanyProfile:
    """Company fundamentals fetched from yfinance."""

    ticker: str
    name: str
    sector: str
    industry: str
    summary: str
    market_cap: int | None
    pe_trailing: float | None
    pe_forward: float | None
    eps_trailing: float | None
    eps_forward: float | None
    dividend_yield: float | None  # decimal, e.g. 0.023 = 2.3%
    target_price_mean: float | None
    week_52_high: float | None
    week_52_low: float | None
    currency: str  # 'USD' or 'ILS'
    employees: int | None = None
    exchange: str = "N/A"
    short_pct: float | None = None  # e.g. 0.023 = 2.3% of float shorted
    short_ratio: float | None = None  # days to cover


@dataclass
class EarningsReport:
    """Latest quarterly earnings report for a ticker."""

    ticker: str
    quarter: str  # e.g. "Q1 2026"
    report_date: str  # e.g. "Apr 3, 2026"
    eps_actual: float | None
    eps_estimate: float | None
    eps_surprise_pct: float | None  # (actual - est) / |est| * 100
    revenue_actual: float | None  # raw dollars
    revenue_estimate: float | None
    revenue_surprise_pct: float | None
    revenue_growth_yoy: float | None  # e.g. 0.051 = 5.1%
    gross_margin: float | None  # e.g. 0.463 = 46.3%
    beat_eps: bool | None  # True=beat, False=miss, None=no estimate
    beat_revenue: bool | None


@dataclass
class InsiderTx:
    """Single insider transaction from yfinance."""

    insider_name: str
    insider_title: str | None
    transaction_date: datetime
    transaction_type: str  # 'BUY' or 'SELL'
    shares: int
    price_per_share: float | None
    total_value: float | None
    filing_url: str | None


# ── Fetch functions ───────────────────────────────────────────────────────────


async def fetch_company_profile(ticker: str) -> CompanyProfile:
    """Fetch company profile and fundamental metrics from yfinance.

    Results are cached in Redis for 4 hours to avoid rate-limiting.

    Args:
        ticker: Ticker symbol (e.g., 'TEVA', 'AAPL').

    Returns:
        CompanyProfile dataclass with all available fields.

    Raises:
        ValueError: If yfinance returns no usable data for the ticker.
    """
    cache_key = f"fundamentals:{ticker.upper()}"
    cached = await cache.get(cache_key)
    if cached:
        logger.debug("fundamentals_cache_hit", ticker=ticker)
        return CompanyProfile(**cached)

    loop = asyncio.get_event_loop()
    info: dict = await loop.run_in_executor(None, lambda: yf.Ticker(ticker).info)

    if not info or (
        info.get("regularMarketPrice") is None
        and info.get("currentPrice") is None
        and info.get("trailingPE") is None
    ):
        raise ValueError(f"No fundamentals data available for {ticker}")

    def _float(key: str) -> float | None:
        v = info.get(key)
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    def _int(key: str) -> int | None:
        v = info.get(key)
        try:
            return int(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    exchange_map = {
        "NYQ": "NYSE",
        "NYSEArca": "NYSE Arca",
        "NMS": "NASDAQ",
        "NGM": "NASDAQ",
        "NCM": "NASDAQ",
    }
    exchange = (
        exchange_map.get(info.get("exchange", ""), info.get("exchange", "N/A")) or "N/A"
    )

    profile = CompanyProfile(
        ticker=ticker.upper(),
        name=info.get("longName") or info.get("shortName") or ticker.upper(),
        sector=info.get("sector") or "",
        industry=info.get("industry") or "",
        summary=info.get("longBusinessSummary") or "",
        market_cap=_int("marketCap"),
        pe_trailing=_float("trailingPE"),
        pe_forward=_float("forwardPE"),
        eps_trailing=_float("trailingEps"),
        eps_forward=_float("forwardEps"),
        dividend_yield=_float("dividendYield"),
        target_price_mean=_float("targetMeanPrice"),
        week_52_high=_float("fiftyTwoWeekHigh"),
        week_52_low=_float("fiftyTwoWeekLow"),
        currency="ILS" if ticker.upper().endswith(".TA") else "USD",
        employees=_int("fullTimeEmployees"),
        exchange=exchange,
        short_pct=_float("shortPercentOfFloat"),
        short_ratio=_float("shortRatio"),
    )

    await cache.set(cache_key, dataclasses.asdict(profile), ttl=FUNDAMENTALS_CACHE_TTL)
    logger.info("fundamentals_fetched", ticker=ticker)
    return profile


async def fetch_insider_transactions(ticker: str) -> list[InsiderTx]:
    """Fetch recent insider transactions from yfinance.

    Args:
        ticker: Ticker symbol.

    Returns:
        List of up to 10 most recent InsiderTx objects, newest first.
        Returns empty list if no data available.
    """
    loop = asyncio.get_event_loop()

    def _fetch() -> pd.DataFrame | None:
        return yf.Ticker(ticker).insider_transactions

    df: pd.DataFrame | None = await loop.run_in_executor(None, _fetch)

    if df is None or df.empty:
        logger.debug("no_insider_transactions", ticker=ticker)
        return []

    # Normalize column names — yfinance 1.2.x uses "#Shares"
    rename_map = {
        "#Shares": "Shares",
        "Insider": "Insider",
        "Position": "Position",
        "Date": "Date",
        "Transaction": "Transaction",
        "Value": "Value",
        "URL": "URL",
    }
    df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})

    # Sort by date descending
    if "Date" in df.columns:
        df = df.sort_values("Date", ascending=False)

    txns: list[InsiderTx] = []
    for _, row in df.head(10).iterrows():
        # Parse date
        raw_date = row.get("Date")
        if pd.isna(raw_date) if not isinstance(raw_date, datetime) else False:
            continue
        if isinstance(raw_date, pd.Timestamp):
            tx_date = raw_date.to_pydatetime()
            if tx_date.tzinfo is None:
                tx_date = TZ_UTC.localize(tx_date)
        else:
            continue

        # Transaction type
        tx_str = str(row.get("Transaction", ""))
        tx_type = "BUY" if "Purchase" in tx_str or "Buy" in tx_str else "SELL"

        # Shares
        raw_shares = row.get("Shares")
        if pd.isna(raw_shares) if not isinstance(raw_shares, (int, float)) else False:
            shares = 0
        else:
            shares = abs(int(raw_shares))

        # Value and price per share
        raw_value = row.get("Value")
        total_value: float | None = None
        price_per_share: float | None = None
        if raw_value is not None and not (
            pd.isna(raw_value) if isinstance(raw_value, float) else False
        ):
            total_value = abs(float(raw_value))
            if shares > 0:
                price_per_share = round(total_value / shares, 4)

        txns.append(
            InsiderTx(
                insider_name=str(row.get("Insider", "Unknown")),
                insider_title=str(row.get("Position", "")) or None,
                transaction_date=tx_date,
                transaction_type=tx_type,
                shares=shares,
                price_per_share=price_per_share,
                total_value=total_value,
                filing_url=str(row.get("URL", "")) or None,
            )
        )

    logger.debug("insider_transactions_fetched", ticker=ticker, count=len(txns))
    return txns


async def save_insider_transactions(ticker: str, txns: list[InsiderTx]) -> int:
    """Upsert insider transactions to PostgreSQL via SQLAlchemy ORM.

    Deduplicates by (ticker, insider_name, transaction_date, shares).

    Args:
        ticker: Ticker symbol.
        txns: List of InsiderTx objects.

    Returns:
        Number of new rows inserted.
    """
    if not txns:
        return 0

    inserted = 0
    async with AsyncSessionLocal() as session:
        for tx in txns:
            stmt = select(InsiderTransaction).where(
                InsiderTransaction.ticker == ticker.upper(),
                InsiderTransaction.insider_name == tx.insider_name,
                InsiderTransaction.transaction_date == tx.transaction_date,
                InsiderTransaction.shares == tx.shares,
            )
            existing = (await session.execute(stmt)).scalar_one_or_none()
            if existing is not None:
                continue

            row = InsiderTransaction(
                ticker=ticker.upper(),
                insider_name=tx.insider_name,
                insider_title=tx.insider_title,
                transaction_date=tx.transaction_date,
                transaction_type=tx.transaction_type,
                shares=tx.shares,
                price_per_share=tx.price_per_share,
                total_value=tx.total_value,
                filing_url=tx.filing_url,
            )
            session.add(row)
            inserted += 1

        await session.commit()

    logger.info("insider_transactions_saved", ticker=ticker, inserted=inserted)
    return inserted


# ── Earnings ─────────────────────────────────────────────────────────────────


def _fetch_earnings_sync(ticker: str) -> EarningsReport | None:
    """Synchronous yfinance fetch — must be called via run_in_executor."""
    t = yf.Ticker(ticker)

    # --- report date from earningsTimestamp ---
    info = t.info or {}
    ts = info.get("earningsTimestamp")
    if ts:
        try:
            report_dt = datetime.fromtimestamp(ts, tz=UTC)
            report_date = report_dt.strftime("%b %-d, %Y")
        except Exception:
            report_date = "N/A"
    else:
        report_date = "N/A"

    # --- EPS + Revenue from quarterly_income_stmt (new yfinance API) ---
    eps_actual: float | None = None
    eps_estimate: float | None = None
    revenue_actual: float | None = None
    quarter_label = "Latest Quarter"
    try:
        qi = t.quarterly_income_stmt
        if qi is not None and not qi.empty:
            # EPS actual
            if "Diluted EPS" in qi.index:
                v = qi.loc["Diluted EPS"].iloc[0]
                if pd.notna(v):
                    eps_actual = float(v)
            # Revenue actual
            for rev_key in ("Total Revenue", "Operating Revenue"):
                if rev_key in qi.index:
                    v = qi.loc[rev_key].iloc[0]
                    if pd.notna(v):
                        revenue_actual = float(v)
                        break
            # Quarter label from column timestamp
            try:
                q_dt = pd.Timestamp(qi.columns[0])
                q_num = (q_dt.month - 1) // 3 + 1
                quarter_label = f"Q{q_num} {q_dt.year}"
            except Exception:  # noqa: BLE001
                quarter_label = str(qi.columns[0])
    except Exception:  # noqa: BLE001
        logger.debug("earnings_income_stmt_failed", ticker=ticker)

    # --- EPS estimate: prefer trailingEps as quarterly proxy (annual / 4) ---
    # Note: yfinance does not expose per-quarter analyst EPS estimates freely.
    # We use trailingEps / 4 as a rough quarterly benchmark only when no better source exists.
    try:
        trailing = info.get("trailingEps")
        if trailing is not None:
            eps_estimate = round(float(trailing) / 4, 2)
    except Exception:  # noqa: BLE001
        logger.debug("earnings_eps_estimate_failed", ticker=ticker)

    # --- revenue estimate from calendar ---
    revenue_estimate: float | None = None
    try:
        cal = t.calendar
        if cal is not None and not cal.empty and "Revenue Estimate" in cal.index:
            v = cal.loc["Revenue Estimate"].iloc[0]
            if pd.notna(v):
                revenue_estimate = float(v)
    except Exception:  # noqa: BLE001
        logger.debug("earnings_calendar_revenue_failed", ticker=ticker)

    # --- macro metrics from info ---
    def _f(key: str) -> float | None:
        v = info.get(key)
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    revenue_growth_yoy = _f("revenueGrowth")
    gross_margin = _f("grossMargins")

    # --- beat/miss and surprise ---
    eps_surprise_pct: float | None = None
    beat_eps: bool | None = None
    if eps_actual is not None and eps_estimate is not None and eps_estimate != 0:
        eps_surprise_pct = round(
            (eps_actual - eps_estimate) / abs(eps_estimate) * 100, 2
        )
        beat_eps = eps_actual >= eps_estimate

    revenue_surprise_pct: float | None = None
    beat_revenue: bool | None = None
    if (
        revenue_actual is not None
        and revenue_estimate is not None
        and revenue_estimate != 0
    ):
        revenue_surprise_pct = round(
            (revenue_actual - revenue_estimate) / abs(revenue_estimate) * 100, 2
        )
        beat_revenue = revenue_actual >= revenue_estimate

    # Require at least EPS or Revenue to return a report
    if eps_actual is None and revenue_actual is None:
        return None

    return EarningsReport(
        ticker=ticker.upper(),
        quarter=quarter_label,
        report_date=report_date,
        eps_actual=eps_actual,
        eps_estimate=eps_estimate,
        eps_surprise_pct=eps_surprise_pct,
        revenue_actual=revenue_actual,
        revenue_estimate=revenue_estimate,
        revenue_surprise_pct=revenue_surprise_pct,
        revenue_growth_yoy=revenue_growth_yoy,
        gross_margin=gross_margin,
        beat_eps=beat_eps,
        beat_revenue=beat_revenue,
    )


async def fetch_earnings_report(ticker: str) -> EarningsReport | None:
    """Fetch latest quarterly earnings report for a ticker.

    Results cached in Redis for 1 hour.

    Args:
        ticker: Ticker symbol (e.g., 'AAPL').

    Returns:
        EarningsReport or None if no data available.
    """
    cache_key = f"earnings:{ticker.upper()}"
    cached = await cache.get(cache_key)
    if cached:
        logger.debug("earnings_cache_hit", ticker=ticker)
        return EarningsReport(**cached)

    loop = asyncio.get_event_loop()
    try:
        report = await loop.run_in_executor(None, _fetch_earnings_sync, ticker)
    except Exception as exc:
        logger.warning("earnings_fetch_failed", ticker=ticker, error=str(exc))
        return None

    if report is None:
        return None

    await cache.set(cache_key, dataclasses.asdict(report), ttl=EARNINGS_CACHE_TTL)
    logger.info("earnings_fetched", ticker=ticker, quarter=report.quarter)
    return report


def was_reported_today(ticker: str) -> bool:
    """Return True if the ticker reported earnings today (sync — use run_in_executor).

    Args:
        ticker: Ticker symbol.

    Returns:
        True if earningsTimestamp matches today's date.
    """
    try:
        info = yf.Ticker(ticker).info or {}
        ts = info.get("earningsTimestamp")
        if not ts:
            return False
        report_dt = datetime.fromtimestamp(ts, tz=UTC)
        today = datetime.now(tz=UTC).date()
        return report_dt.date() == today
    except Exception:
        return False


def is_reporting_today(ticker: str) -> tuple[bool, float | None, float | None]:
    """Return (is_today, eps_estimate, revenue_estimate) from ticker.calendar (sync).

    Args:
        ticker: Ticker symbol.

    Returns:
        Tuple of (is_reporting_today, eps_estimate, revenue_estimate).
    """
    try:
        t = yf.Ticker(ticker)
        cal = t.calendar
        if cal is None or cal.empty:
            return False, None, None
        # calendar index: "Earnings Date", "Earnings Average", "Revenue Average", etc.
        date_row = cal.loc["Earnings Date"] if "Earnings Date" in cal.index else None
        if date_row is None:
            return False, None, None
        # date_row may have multiple columns (low/high estimate)
        today = datetime.now(tz=UTC).date()
        for val in date_row.values:
            try:
                if pd.Timestamp(val).date() == today:
                    eps_est = None
                    rev_est = None
                    for key in ["Earnings Average", "EPS Estimate"]:
                        if key in cal.index:
                            v = cal.loc[key].iloc[0]
                            if pd.notna(v):
                                eps_est = float(v)
                                break
                    for key in ["Revenue Average", "Revenue Estimate"]:
                        if key in cal.index:
                            v = cal.loc[key].iloc[0]
                            if pd.notna(v):
                                rev_est = float(v)
                                break
                    return True, eps_est, rev_est
            except Exception:  # noqa: BLE001, S112
                continue
        return False, None, None
    except Exception:  # noqa: BLE001
        return False, None, None


# ── Helpers ───────────────────────────────────────────────────────────────────


def get_competitors(ticker: str) -> list[str]:
    """Return list of competitor ticker symbols for a given ticker.

    Args:
        ticker: Ticker symbol.

    Returns:
        List of competitor symbols (up to 4), or empty list if unknown.
    """
    return COMPETITOR_MAP.get(ticker.upper(), [])


def _fmt_cap(cap: int | None) -> str:
    """Format market cap as human-readable string."""
    if cap is None:
        return "N/A"
    if cap >= 1_000_000_000:
        return f"${cap / 1e9:.1f}B"
    if cap >= 1_000_000:
        return f"${cap / 1e6:.0f}M"
    return f"${cap:,}"


def _fmt_float(val: float | None, suffix: str = "", precision: int = 2) -> str:
    """Format a float value or return 'N/A'."""
    if val is None:
        return "N/A"
    return f"{val:.{precision}f}{suffix}"


def _fmt_pct(val: float | None) -> str:
    """Format a decimal dividend yield as percentage string."""
    if val is None:
        return "N/A"
    return f"{val * 100:.2f}%"


# ── English formatters ────────────────────────────────────────────────────────


def _fmt_revenue(v: float | None) -> str:
    """Format raw revenue value to human-readable string."""
    if v is None:
        return "N/A"
    if v >= 1e9:
        return f"${v / 1e9:.1f}B"
    if v >= 1e6:
        return f"${v / 1e6:.0f}M"
    return f"${v:,.0f}"


def format_earnings_english(
    report: EarningsReport,
    headlines: list[dict] | None = None,
) -> str:
    """Format an EarningsReport as an English HTML string for Telegram.

    Args:
        report: EarningsReport dataclass.
        headlines: Optional list of news headline dicts with keys title/source.

    Returns:
        HTML-formatted string.
    """

    def _beat(flag: bool | None, surprise: float | None) -> str:
        if flag is None:
            return ""
        sign = "✅ Beat" if flag else "❌ Miss"
        if surprise is not None:
            pct_sign = "+" if surprise >= 0 else ""
            return f"  {sign} {pct_sign}{surprise:.1f}%"
        return f"  {sign}"

    lines = [
        f"📅 <b>{html.escape(report.quarter)} Earnings</b> — Reported {html.escape(report.report_date)}",
        "━━━━━━━━━━━━━━━━━━━",
    ]

    # EPS line
    if report.eps_actual is not None:
        eps_line = f"💰 EPS:      <code>${report.eps_actual:.2f}</code> actual"
        if report.eps_estimate is not None:
            eps_line += f"  vs  <code>${report.eps_estimate:.2f}</code> est"
        eps_line += _beat(report.beat_eps, report.eps_surprise_pct)
        lines.append(eps_line)

    # Revenue line
    if report.revenue_actual is not None:
        rev_line = (
            f"📦 Revenue:  <code>{_fmt_revenue(report.revenue_actual)}</code> actual"
        )
        if report.revenue_estimate is not None:
            rev_line += (
                f"  vs  <code>{_fmt_revenue(report.revenue_estimate)}</code> est"
            )
        rev_line += _beat(report.beat_revenue, report.revenue_surprise_pct)
        lines.append(rev_line)

    # YoY Growth + Gross Margin
    growth_parts = []
    if report.revenue_growth_yoy is not None:
        sign = "+" if report.revenue_growth_yoy >= 0 else ""
        growth_parts.append(f"Revenue {sign}{report.revenue_growth_yoy * 100:.1f}%")
    if report.gross_margin is not None:
        growth_parts.append(f"Gross Margin {report.gross_margin * 100:.1f}%")
    if growth_parts:
        lines.append(f"📈 YoY Growth: {' | '.join(growth_parts)}")

    # News headlines about earnings
    if headlines:
        for h in headlines[:5]:
            title = html.escape(h.get("title", "")[:80])
            source = html.escape(h.get("source", ""))
            line = f'📰 <i>"{title}…"</i>'
            if source:
                line += f" — {source}"
            lines.append(line)

    return "\n".join(lines)


def format_profile_english(profile: CompanyProfile) -> str:
    """Format a CompanyProfile as an English HTML string for Telegram.

    Args:
        profile: CompanyProfile dataclass.

    Returns:
        HTML-formatted string.
    """
    summary_short = (
        (profile.summary[:400] + "...")
        if len(profile.summary) > 400
        else profile.summary
    )
    competitors = get_competitors(profile.ticker)
    comp_str = ", ".join(competitors) if competitors else "N/A"
    emp_str = f"👥 {profile.employees:,} employees" if profile.employees else ""
    sector_line = " | ".join(
        filter(
            None, [html.escape(profile.sector), html.escape(profile.industry), emp_str]
        )
    )

    lines = [
        f"🏢 <b>{html.escape(profile.name)}</b> (<code>{html.escape(profile.ticker)}</code>) — {html.escape(profile.exchange)}",
        f"🏭 {sector_line}" if sector_line else "",
        f"💰 Market Cap: <code>{_fmt_cap(profile.market_cap)}</code>",
        "",
        "📊 <b>Valuation Metrics:</b>",
        f"  P/E (Trailing): <code>{_fmt_float(profile.pe_trailing, 'x', 1)}</code>    P/E (Forward): <code>{_fmt_float(profile.pe_forward, 'x', 1)}</code>",
        f"  EPS (Trailing): <code>${_fmt_float(profile.eps_trailing)}</code>    EPS (Forward):  <code>${_fmt_float(profile.eps_forward)}</code>",
        f"  Dividend Yield: <code>{_fmt_pct(profile.dividend_yield)}</code>",
        f"  🎯 Analyst Target: <code>${_fmt_float(profile.target_price_mean)}</code>",
        "",
        f"📈 52-Week Range: <code>${_fmt_float(profile.week_52_low)}</code> – <code>${_fmt_float(profile.week_52_high)}</code>",
        (
            f"📉 Short Interest: <code>{_fmt_pct(profile.short_pct)}</code> of float"
            + (
                f"  |  Days to Cover: <code>{profile.short_ratio:.1f}</code>"
                if profile.short_ratio is not None
                else ""
            )
            if profile.short_pct is not None
            else ""
        ),
        f"🆚 Competitors: {html.escape(comp_str)}",
    ]
    # Filter out empty lines at start
    lines = [line for line in lines if line is not None]
    if summary_short:
        lines += ["", f"📝 <i>{html.escape(summary_short)}</i>"]

    return "\n".join(lines)


# Keep old name as alias for backwards compatibility during transition
format_profile_hebrew = format_profile_english


def format_insiders_english(ticker: str, txns: list[InsiderTx]) -> str:
    """Format insider transactions as an English HTML string for Telegram.

    Args:
        ticker: Ticker symbol.
        txns: List of InsiderTx objects.

    Returns:
        HTML-formatted string showing up to 5 most recent transactions.
    """
    if not txns:
        return f"🕵️ <b>Insider Transactions — {html.escape(ticker)}</b>\nNo data available."

    lines = [
        f"🕵️ <b>Insider Transactions — {html.escape(ticker)}</b>",
        "━━━━━━━━━━━━━━━━━━━",
    ]

    for tx in txns[:5]:
        type_emoji = "🟢 BUY" if tx.transaction_type == "BUY" else "🔴 SELL"
        date_str = tx.transaction_date.strftime("%d/%m/%Y")
        shares_str = f"{tx.shares:,}"
        price_str = f"${tx.price_per_share:.2f}" if tx.price_per_share else "N/A"
        value_str = f"${tx.total_value:,.0f}" if tx.total_value else "N/A"
        title_str = html.escape(tx.insider_title) if tx.insider_title else "—"

        lines += [
            f"• {type_emoji} — <b>{html.escape(tx.insider_name)}</b> ({title_str})",
            f"  {date_str}: {shares_str} shares @ {price_str}  |  Total: {value_str}",
        ]

    return "\n".join(lines)


# Keep old name as alias for backwards compatibility during transition
format_insiders_hebrew = format_insiders_english
