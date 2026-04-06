"""Company fundamentals, insider transactions, and competitor analysis via yfinance."""

import asyncio
import dataclasses
import html
from dataclasses import dataclass
from datetime import datetime

import pandas as pd
import yfinance as yf
from sqlalchemy import select

from src.database.cache import cache
from src.database.models import InsiderTransaction
from src.database.session import AsyncSessionLocal
from src.utils.logger import get_logger
from src.utils.timezone_utils import TZ_UTC, currency_symbol

logger = get_logger(__name__)

FUNDAMENTALS_CACHE_TTL = 14400  # 4 hours

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
        return "לא זמין"
    if cap >= 1_000_000_000:
        return f"${cap / 1e9:.1f}B"
    if cap >= 1_000_000:
        return f"${cap / 1e6:.0f}M"
    return f"${cap:,}"


def _fmt_float(val: float | None, suffix: str = "", precision: int = 2) -> str:
    """Format a float value or return 'לא זמין'."""
    if val is None:
        return "לא זמין"
    return f"{val:.{precision}f}{suffix}"


def _fmt_pct(val: float | None) -> str:
    """Format a decimal dividend yield as percentage string."""
    if val is None:
        return "לא זמין"
    return f"{val * 100:.2f}%"


# ── Hebrew formatters ─────────────────────────────────────────────────────────


def format_profile_hebrew(profile: CompanyProfile) -> str:
    """Format a CompanyProfile as a Hebrew HTML string for Telegram.

    Args:
        profile: CompanyProfile dataclass.

    Returns:
        HTML-formatted string.
    """
    sym = currency_symbol(profile.ticker)
    summary_short = (
        (profile.summary[:220] + "...")
        if len(profile.summary) > 220
        else profile.summary
    )
    competitors = get_competitors(profile.ticker)
    comp_str = ", ".join(competitors) if competitors else "לא זמין"

    lines = [
        f"🏢 <b>{html.escape(profile.name)}</b> ({html.escape(profile.ticker)})",
        f"🏭 <b>תחום:</b> {html.escape(profile.sector)} | {html.escape(profile.industry)}",
        f"💰 <b>שווי שוק:</b> {_fmt_cap(profile.market_cap)}",
        "",
        "📊 <b>מכפילים ומדדים:</b>",
        f"  מכפיל רווח (P/E) נוכחי: <code>{_fmt_float(profile.pe_trailing, precision=1)}</code>",
        f"  מכפיל רווח (P/E) צפוי:  <code>{_fmt_float(profile.pe_forward, precision=1)}</code>",
        f"  EPS נוכחי:  <code>{sym}{_fmt_float(profile.eps_trailing)}</code>",
        f"  EPS צפוי:   <code>{sym}{_fmt_float(profile.eps_forward)}</code>",
        f"  תשואת דיבידנד: <code>{_fmt_pct(profile.dividend_yield)}</code>",
        f"  🎯 יעד אנליסטים: <code>{sym}{_fmt_float(profile.target_price_mean)}</code>",
        "",
        f"📈 <b>טווח 52 שבועות:</b> <code>{sym}{_fmt_float(profile.week_52_low)}</code> – <code>{sym}{_fmt_float(profile.week_52_high)}</code>",
        f"🆚 <b>מתחרים עיקריים:</b> {html.escape(comp_str)}",
    ]
    if summary_short:
        lines += ["", f"📝 <i>{html.escape(summary_short)}</i>"]

    return "\n".join(lines)


def format_insiders_hebrew(ticker: str, txns: list[InsiderTx]) -> str:
    """Format insider transactions as a Hebrew HTML string for Telegram.

    Args:
        ticker: Ticker symbol.
        txns: List of InsiderTx objects.

    Returns:
        HTML-formatted string showing up to 5 most recent transactions.
    """
    if not txns:
        return (
            f"🕵️ <b>עסקאות בעלי עניין — {html.escape(ticker)}</b>\nאין נתונים זמינים."
        )

    sym = currency_symbol(ticker)
    lines = [
        f"🕵️ <b>עסקאות בעלי עניין — {html.escape(ticker)}</b>",
        "━━━━━━━━━━━━━━━━━━━",
    ]

    for tx in txns[:5]:
        type_emoji = "🟢 קנייה" if tx.transaction_type == "BUY" else "🔴 מכירה"
        date_str = tx.transaction_date.strftime("%d/%m/%Y")
        shares_str = f"{tx.shares:,}"
        price_str = f"{sym}{tx.price_per_share:.2f}" if tx.price_per_share else "N/A"
        value_str = f"{sym}{tx.total_value:,.0f}" if tx.total_value else "N/A"
        title_str = html.escape(tx.insider_title) if tx.insider_title else "—"

        lines += [
            f"• {type_emoji} — <b>{html.escape(tx.insider_name)}</b> ({title_str})",
            f'  {date_str}: {shares_str} מניות @ {price_str}  |  סה"כ: {value_str}',
        ]

    return "\n".join(lines)
