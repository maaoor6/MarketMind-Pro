"""GitHub Pages chart publisher via GitHub Contents API."""

import base64

import httpx
import pandas as pd

from src.quant.fibonacci import FibonacciLevels
from src.ui.charts import generate_full_chart
from src.utils.config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)

GITHUB_API_BASE = "https://api.github.com"
PAGES_BASE_URL = "https://maaoor6.github.io/MarketMind-Pro"


async def _get_file_sha(
    client: httpx.AsyncClient,
    repo: str,
    path: str,
    token: str,
) -> str | None:
    """Fetch the blob SHA of an existing file in the GitHub repo.

    Required by the GitHub Contents API to update (not create) a file.

    Args:
        client: Shared httpx async client.
        repo: GitHub repo in 'owner/repo' format.
        path: File path within the repo (e.g., 'docs/teva_chart.html').
        token: GitHub personal access token.

    Returns:
        SHA string if file exists, None if not found.
    """
    url = f"{GITHUB_API_BASE}/repos/{repo}/contents/{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    response = await client.get(url, headers=headers)
    if response.status_code == 200:
        return response.json().get("sha")
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return None


async def publish_chart(ticker: str, html_content: str) -> str:
    """Publish an interactive chart HTML file to GitHub Pages.

    Uses the GitHub Contents API to create or update the file at
    docs/{ticker}_chart.html in the configured repository.

    Args:
        ticker: Ticker symbol (used in filename and URL).
        html_content: Full HTML string of the Plotly chart.

    Returns:
        Public GitHub Pages URL for the published chart.

    Raises:
        ValueError: If GITHUB_TOKEN or github_pages_repo is not configured.
        httpx.HTTPStatusError: On GitHub API failure.
    """
    token = settings.github_token
    repo = settings.github_pages_repo

    if not token:
        raise ValueError(
            "GITHUB_TOKEN not set in .env — cannot publish chart to GitHub Pages"
        )
    if not repo:
        raise ValueError(
            "GITHUB_PAGES_REPO not set in .env — cannot publish chart to GitHub Pages"
        )

    file_path = f"docs/{ticker.lower()}_chart.html"
    encoded = base64.b64encode(html_content.encode("utf-8")).decode("ascii")

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    async with httpx.AsyncClient(timeout=30) as client:
        existing_sha = await _get_file_sha(client, repo, file_path, token)

        payload: dict = {
            "message": f"chore: update chart for {ticker.upper()}",
            "content": encoded,
        }
        if existing_sha:
            payload["sha"] = existing_sha

        api_url = f"{GITHUB_API_BASE}/repos/{repo}/contents/{file_path}"
        response = await client.put(api_url, json=payload, headers=headers)
        if not response.is_success:
            logger.error(
                "github_api_error",
                status=response.status_code,
                body=response.text[:500],
                repo=repo,
                path=file_path,
            )
        response.raise_for_status()

    page_url = f"{PAGES_BASE_URL}/{ticker.lower()}_chart.html"
    logger.info("chart_published", ticker=ticker, url=page_url)
    return page_url


async def publish_ticker_chart(
    ticker: str,
    df: pd.DataFrame,
    fib_levels: FibonacciLevels | None = None,
) -> str:
    """Generate a Plotly chart and publish it to GitHub Pages.

    Convenience wrapper around generate_full_chart() + publish_chart().

    Args:
        ticker: Ticker symbol.
        df: OHLCV DataFrame from yfinance.
        fib_levels: Optional pre-computed Fibonacci levels to overlay.

    Returns:
        Public GitHub Pages URL for the published chart.
    """
    mas = None
    try:
        from src.quant.indicators import all_moving_averages

        closes = df["Close"].squeeze()
        mas = all_moving_averages(closes)
    except Exception as exc:
        logger.warning("ma_computation_failed_for_publish", error=str(exc))

    result = generate_full_chart(
        df,
        ticker,
        fib_levels=fib_levels,
        mas=mas,
        save_html=False,
        save_png=False,
    )
    fig = result["figure"]

    html_content = fig.to_html(
        include_plotlyjs="cdn",
        full_html=True,
        config={"displayModeBar": True, "scrollZoom": True},
    )

    return await publish_chart(ticker, html_content)
