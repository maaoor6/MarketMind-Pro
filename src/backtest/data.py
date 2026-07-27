"""Historical price data loading with a local parquet cache.

Data source is yfinance only (free, no API key). Each ticker's full daily
history is downloaded once and cached under ``data/cache/backtest/`` so
subsequent runs work entirely offline.
"""

import time
from pathlib import Path

import pandas as pd
import yfinance as yf

from src.backtest.validate import validate_history
from src.utils.config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_CACHE_DIR = Path("data/cache/backtest")

# Refresh a cached history when its newest bar is older than this many days
# and the requested range extends past it. Backtests rarely need bars from
# the last few days, so the cache is effectively download-once.
_CACHE_STALE_DAYS = 7

# Broad pool of liquid US large caps + major ETFs used for rotation and for
# the historical universe simulation. Based on names that exist today —
# survivorship bias is a documented limitation of the backtest.
CANDIDATE_POOL: list[str] = [
    # Index / sector ETFs
    "SPY",
    "QQQ",
    "DIA",
    "IWM",
    "XLK",
    "XLV",
    "XLF",
    "XLE",
    "XLI",
    "XLY",
    "XLP",
    # Mega-cap tech
    "AAPL",
    "MSFT",
    "NVDA",
    "GOOGL",
    "AMZN",
    "META",
    "TSLA",
    "AVGO",
    "ORCL",
    "CRM",
    "ADBE",
    "AMD",
    "INTC",
    "CSCO",
    "QCOM",
    "TXN",
    "IBM",
    "NFLX",
    "MU",
    "AMAT",
    # Financials
    "JPM",
    "BAC",
    "WFC",
    "GS",
    "MS",
    "C",
    "BLK",
    "AXP",
    "V",
    "MA",
    "SCHW",
    "USB",
    # Healthcare
    "JNJ",
    "UNH",
    "PFE",
    "MRK",
    "ABT",
    "LLY",
    "TMO",
    "BMY",
    "AMGN",
    "GILD",
    "MDT",
    "CVS",
    # Consumer
    "PG",
    "KO",
    "PEP",
    "WMT",
    "COST",
    "MCD",
    "NKE",
    "SBUX",
    "HD",
    "LOW",
    "TGT",
    "DIS",
    "CL",
    "KMB",
    "GIS",
    # Industrials / Energy / Materials
    "XOM",
    "CVX",
    "COP",
    "SLB",
    "GE",
    "CAT",
    "DE",
    "HON",
    "MMM",
    "BA",
    "LMT",
    "RTX",
    "UPS",
    "FDX",
    "UNP",
    "EMR",
    "ETN",
    "DD",
    "LIN",
    "FCX",
    # Telecom / Utilities / Real estate
    "T",
    "VZ",
    "TMUS",
    "NEE",
    "DUK",
    "SO",
    "D",
    "AEP",
    "AMT",
    "PLD",
    # Semiconductors / software (2nd tier)
    "KLAC",
    "LRCX",
    "ADI",
    "NXPI",
    "MCHP",
    "ON",
    "INTU",
    "NOW",
    "PANW",
    "ANET",
    "SNPS",
    "CDNS",
    "ADSK",
    "WDAY",
    "FTNT",
    "ACN",
    "CTSH",
    "HPQ",
    "DELL",
    "STX",
    # Financials — regional banks / insurance / exchanges
    "PNC",
    "TFC",
    "BK",
    "STT",
    "FITB",
    "MET",
    "PRU",
    "AIG",
    "ALL",
    "PGR",
    "TRV",
    "CB",
    "AFL",
    "ICE",
    "CME",
    "SPGI",
    "MCO",
    "COF",
    "DFS",
    # Healthcare / biotech / med-tech
    "CI",
    "HUM",
    "ELV",
    "VRTX",
    "REGN",
    "BIIB",
    "ZTS",
    "SYK",
    "BSX",
    "ISRG",
    "HCA",
    "MCK",
    "BAX",
    "BDX",
    "EW",
    "DHR",
    "A",
    "IDXX",
    "RMD",
    "DGX",
    # Consumer / retail / food
    "TJX",
    "ROST",
    "DG",
    "DLTR",
    "YUM",
    "MDLZ",
    "KHC",
    "HSY",
    "STZ",
    "EL",
    "ORLY",
    "AZO",
    "BBY",
    "EBAY",
    "MAR",
    "HLT",
    "CCL",
    "RCL",
    "LVS",
    "MGM",
    # Industrials / defense / transport
    "GD",
    "NOC",
    "LHX",
    "CSX",
    "NSC",
    "ITW",
    "PH",
    "CMI",
    "PCAR",
    "WM",
    "RSG",
    "ROK",
    "DOV",
    "SWK",
    "FAST",
    "GWW",
    "URI",
    "JCI",
    "IR",
    "OTIS",
    # Energy / materials
    "EOG",
    "PSX",
    "VLO",
    "OXY",
    "MPC",
    "HAL",
    "BKR",
    "KMI",
    "WMB",
    "NEM",
    "NUE",
    "APD",
    "ECL",
    "SHW",
    "VMC",
    "MLM",
    "IP",
    "MOS",
    "CF",
    "ALB",
    # Foreign ADRs (deep-history, liquid)
    "TSM",
    "ASML",
    "SAP",
    "TM",
    "HMC",
    "SONY",
    "NVO",
    "AZN",
    "GSK",
    "SHEL",
    "BP",
    "TTE",
    "UL",
    "SNY",
    "HSBC",
    "RY",
    "TD",
    "MUFG",
    "IBN",
    "BHP",
    # Israeli dual-listed (from the project's arbitrage map)
    "TEVA",
    "NICE",
    "CHKP",
    "CEVA",
    "GILT",
    "RDCM",
    "TSEM",
    # REITs / utilities (2nd tier) / extra ETFs
    "O",
    "SPG",
    "PSA",
    "CCI",
    "EQIX",
    "WELL",
    "EXC",
    "XEL",
    "ED",
    "PEG",
    "EFA",
    "EEM",
    "MDY",
    "XLC",
    "XLB",
    "XLRE",
    "XLU",
    # Growth / cloud / internet (mostly post-2015 listings — shorter history)
    "PLTR",
    "SNOW",
    "CRWD",
    "DDOG",
    "ZS",
    "NET",
    "MDB",
    "TEAM",
    "ABNB",
    "UBER",
    "DASH",
    "SHOP",
    "XYZ",
    "COIN",
    "SPOT",
    "RBLX",
    "U",
    "TTD",
    "APP",
    "HUBS",
    "VEEV",
    "ZM",
    "OKTA",
    "TWLO",
    "PATH",
    "GTLB",
    "DOCU",
    "PINS",
    "SNAP",
    "LYFT",
    # Semiconductors / hardware (2nd wave)
    "MRVL",
    "MPWR",
    "TER",
    "ENTG",
    "SWKS",
    "QRVO",
    "OLED",
    "LSCC",
    "ARM",
    "SMCI",
    "WDC",
    "NTAP",
    "KEYS",
    "GRMN",
    "ZBRA",
    # Financials — alternative asset managers / insurers / exchanges
    "KKR",
    "BX",
    "APO",
    "ARES",
    "AJG",
    "MMC",
    "AON",
    "BRO",
    "NDAQ",
    "MKTX",
    "HOOD",
    "CBOE",
    "RJF",
    "AMP",
    "SYF",
    # Healthcare / med-tech / biotech (growth tier)
    "DXCM",
    "PODD",
    "ALGN",
    "WST",
    "MTD",
    "WAT",
    "RVTY",
    "TECH",
    "CRL",
    "NBIX",
    "ALNY",
    "INCY",
    "EXEL",
    "SRPT",
    "UTHR",
    "JAZZ",
    "MRNA",
    "STE",
    "COO",
    "HOLX",
    # Consumer / restaurants / apparel
    "CMG",
    "LULU",
    "DECK",
    "DPZ",
    "WING",
    "CAVA",
    "ELF",
    "TPR",
    "RL",
    "BURL",
    "FIVE",
    "ULTA",
    "DKNG",
    "TXRH",
    "CAKE",
    "EXPE",
    "BKNG",
    "TSCO",
    "POOL",
    "WSM",
    # Industrials / defense / infrastructure (growth tier)
    "TDG",
    "HEI",
    "AXON",
    "TT",
    "LII",
    "PWR",
    "EME",
    "FIX",
    "HUBB",
    "WAB",
    "XYL",
    "AME",
    "TDY",
    "CW",
    "SAIA",
    "ODFL",
    "EXPD",
    "CHRW",
    "AOS",
    "GGG",
    # Energy / materials (2nd wave)
    "DVN",
    "FANG",
    "TRGP",
    "LNG",
    "CTRA",
    "AA",
    "CLF",
    "STLD",
    "X",
    "RS",
    "CE",
    "EMN",
    "AVY",
    "PKG",
    "BALL",
    # Extra ETFs — commodities / bonds / industry slices
    "GLD",
    "SLV",
    "TLT",
    "VNQ",
    "SMH",
    "XBI",
    "IBB",
    "ITB",
    "XHB",
    "KRE",
    "XOP",
    "GDX",
    "VGT",
    "SCHD",
    "VTV",
    "VUG",
    "IJR",
    "IJH",
    # Foreign ADRs (2nd wave)
    "BABA",
    "PDD",
    "JD",
    "MELI",
    "SE",
    "NU",
    "SAN",
    "ING",
    "DB",
    "BBVA",
    "RELX",
    "TAK",
    "STM",
    "ERIC",
    "NOK",
    "VALE",
    "RIO",
    "SCCO",
    "PBR",
]

# Regime data — always loaded alongside the trading universe.
VIX_TICKER = "^VIX"


def candidate_pool() -> list[str]:
    """Full candidate pool: static large caps + the live agent's watchlist."""
    pool = list(CANDIDATE_POOL)
    for ticker in settings.trading_watchlist:
        if ticker not in pool:
            pool.append(ticker)
    return pool


def _cache_path(ticker: str, cache_dir: Path) -> Path:
    # ^VIX → _VIX.parquet — keep filenames shell-safe.
    return cache_dir / f"{ticker.replace('^', '_')}.parquet"


def _flatten_columns(df: pd.DataFrame) -> pd.DataFrame:
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df


def _download(ticker: str) -> pd.DataFrame:
    """Download the full daily history for one ticker from yfinance."""
    df = yf.download(
        ticker,
        period="max",
        interval="1d",
        auto_adjust=True,
        progress=False,
    )
    if df is None or df.empty:
        raise ValueError(f"No price data returned for {ticker}")
    df = _flatten_columns(df)
    df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
    df.index = pd.to_datetime(df.index).tz_localize(None)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df


def load_history(
    ticker: str,
    start: str | None = None,
    end: str | None = None,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    use_cache: bool = True,
    reports: list | None = None,
) -> pd.DataFrame:
    """Return the daily OHLCV frame for ``ticker``, sliced to [start, end].

    The full history is cached as parquet after the first download; a cached
    file is refreshed only when the requested range extends meaningfully past
    its newest bar.

    Args:
        ticker: Symbol as understood by yfinance (e.g. ``AAPL``, ``^VIX``).
        start: Inclusive ISO date; clamped to the first available bar.
        end: Inclusive ISO date; defaults to the last available bar.
        cache_dir: Parquet cache directory.
        use_cache: When False, always re-download.

    Returns:
        DataFrame with Open/High/Low/Close/Volume and a tz-naive DatetimeIndex.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = _cache_path(ticker, cache_dir)

    df: pd.DataFrame | None = None
    if use_cache and path.exists():
        try:
            df = pd.read_parquet(path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("backtest_cache_corrupt", ticker=ticker, error=str(exc))
            df = None
        if df is not None and end is not None:
            wanted_end = pd.Timestamp(end)
            stale_cutoff = df.index.max() + pd.Timedelta(days=_CACHE_STALE_DAYS)
            if wanted_end > stale_cutoff:
                df = None  # cache doesn't cover the requested range

    if df is None or df.empty:
        df = _download(ticker)
        df.to_parquet(path)
        logger.info("backtest_history_downloaded", ticker=ticker, bars=len(df))

    # Data-quality gate: strip corrupt leading segments (frozen runs, adjustment
    # artifacts) before anything downstream computes indicators on them. The raw
    # download stays cached untouched; cleaning happens on every load.
    df, quality = validate_history(df, ticker)
    if reports is not None:
        reports.append(quality)
    if quality.trimmed_bars > 0:
        logger.info(
            "backtest_history_cleaned",
            ticker=ticker,
            trimmed_bars=quality.trimmed_bars,
            reasons=quality.reasons,
        )

    if start is not None:
        df = df[df.index >= pd.Timestamp(start)]
    if end is not None:
        df = df[df.index <= pd.Timestamp(end)]
    if df.empty:
        raise ValueError(f"No bars for {ticker} in range {start}..{end}")
    return df


def load_universe(
    tickers: list[str],
    start: str | None = None,
    end: str | None = None,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    use_cache: bool = True,
    throttle_seconds: float = 0.2,
    reports: list | None = None,
) -> dict[str, pd.DataFrame]:
    """Load history for many tickers, skipping the ones that fail.

    A short pause between fresh downloads keeps Yahoo happy; cached loads
    are not throttled. When ``reports`` is provided, each ticker's
    :class:`~src.backtest.validate.DataQualityReport` is appended to it.
    """
    frames: dict[str, pd.DataFrame] = {}
    for ticker in tickers:
        cached = use_cache and _cache_path(ticker, cache_dir).exists()
        try:
            frames[ticker] = load_history(
                ticker,
                start=start,
                end=end,
                cache_dir=cache_dir,
                use_cache=use_cache,
                reports=reports,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("backtest_ticker_skipped", ticker=ticker, error=str(exc))
            continue
        if not cached and throttle_seconds:
            time.sleep(throttle_seconds)
    if not frames:
        raise ValueError("No ticker histories could be loaded")
    return frames
