"""Hebrew browser report + cumulative index page + weight-seed export.

The report is written for someone with no trading background: every strategy
and every metric gets a one-line plain-Hebrew explanation, and results are
framed as "what $10,000 would have become" next to simply holding SPY.
Everything is local and self-contained (Plotly inlined, no CDN).
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go

from src.backtest.metrics import Metrics
from src.utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_REPORTS_DIR = Path("reports")
DEFAULT_WEIGHTS_PATH = Path("data/backtest_weights.json")

# Categorical palette — strategies get fixed hues; the "summary" curves
# (full agent, walk forward) use dark neutrals so they read as a different
# kind of line. With 24 strategies some hues repeat at different lightness.
SERIES_COLORS: dict[str, str] = {
    "momentum_daily": "#2a78d6",
    "mean_reversion": "#1baf7a",
    "trend_following": "#eda100",
    "breakout": "#008300",
    "macd_cross": "#4a3aa7",
    "dip_buyer": "#e34948",
    "gap_momentum": "#e87ba4",
    "multi_timeframe": "#eb6834",
    "rsi2_reversion": "#7db7f0",
    "bollinger_reversion": "#0f7a54",
    "donchian_breakout": "#b98200",
    "ts_momentum": "#5cb85c",
    "high_52w": "#8377c9",
    "double_seven": "#a33231",
    "pullback_sma50": "#c05684",
    "golden_cross": "#b04e26",
    "vol_contraction": "#3d9aa1",
    "low_vol_trend": "#946bb5",
    "supertrend": "#3aa99f",
    "adx_trend": "#c98a2b",
    "keltner_breakout": "#6b8e23",
    "sector_rotation": "#d1495b",
    "cross_asset": "#3f88c5",
    "seasonality": "#b08968",
    "full_agent": "#555553",
    "full_agent_live": "#8a6d3b",
    "full_agent_live_full": "#a08050",
    "full_agent_live_gated": "#6a4c93",
    "walk_forward": "#1c1c1a",
    "walk_forward_live": "#4d3b24",
}
_BENCHMARK_COLOR = "#8a8a85"

STRATEGY_HEBREW: dict[str, dict[str, str]] = {
    "momentum_daily": {
        "name": "מומנטום יומי",
        "explain": "קונה מניה שנמצאת בתנופת עלייה חזקה — שילוב של RSI, MACD, ממוצעים נעים ונפח מסחר.",
    },
    "mean_reversion": {
        "name": "חזרה לממוצע",
        "explain": "קונה מניה שירדה חזק מדי (מכירת יתר) ליד רמת תמיכה, בהנחה שתתקן חזרה למעלה.",
    },
    "trend_following": {
        "name": "עוקב מגמה",
        "explain": "קונה רק כשמגמת עלייה ארוכת טווח מאושרת (ממוצע 50 יום מעל ממוצע 200 יום + אישור שבועי).",
    },
    "breakout": {
        "name": "פריצה",
        "explain": "קונה כשמניה פורצת שיא או רמת התנגדות עם נפח מסחר חריג ואישור חודשי.",
    },
    "macd_cross": {
        "name": "חציית MACD",
        "explain": "קונה ברגע שמד המומנטום (MACD) מתהפך משלילי לחיובי — איתות מוקדם למפנה מגמה, רק כשהמחיר מעל ממוצע 50 יום.",
    },
    "dip_buyer": {
        "name": "קניית דיפ",
        "explain": "קונה מניה במגמת עלייה ארוכה שירדה זמנית — 'לקנות בזול מניה טובה' — ומוכר כשהיא מתאוששת.",
    },
    "gap_momentum": {
        "name": "מומנטום פערים",
        "explain": "קונה מניה שפתחה בקפיצה של 2%+ עם נפח מסחר חריג ואישור שבועי — רוכב על חדשות ודוחות חזקים.",
    },
    "multi_timeframe": {
        "name": "יישור רב-זמני",
        "explain": "קונה רק כשכל הטווחים מסכימים — שבועי, חודשי ויומי כולם חיוביים. השיטה הסלקטיבית ביותר.",
    },
    "rsi2_reversion": {
        "name": "RSI-2 (קונורס)",
        "explain": "קונה נפילה חדה מאוד לטווח קצר (RSI של יומיים מתחת ל-10) בתוך מגמת עלייה ארוכה.",
    },
    "bollinger_reversion": {
        "name": "חזרה לרצועת בולינגר",
        "explain": "קונה סגירה מתחת לרצועת בולינגר התחתונה במגמת עלייה, ומוכר בחזרה לרצועה האמצעית.",
    },
    "donchian_breakout": {
        "name": "פריצת דונצ'יאן (Turtles)",
        "explain": "קונה פריצת שיא 20 יום עם אישור שבועי; יוצא בשבירת שפל 10 ימים — שיטת ה-Turtles הקלאסית.",
    },
    "ts_momentum": {
        "name": "מומנטום 12 חודשים",
        "explain": "קונה מניה שעלתה ב-12 החודשים האחרונים (בניכוי החודש האחרון) ונסחרת מעל ממוצע 200 יום.",
    },
    "high_52w": {
        "name": "שיא 52 שבועות",
        "explain": "קונה מניה שנסחרת קרוב לשיא השנתי שלה עם מומנטום חצי-שנתי חיובי ואישור חודשי.",
    },
    "double_seven": {
        "name": "Double-7 (קונורס)",
        "explain": "קונה כשהסגירה היא השפל של 7 הימים האחרונים בתוך מגמת עלייה; מוכר בשיא 7 ימים.",
    },
    "pullback_sma50": {
        "name": "נסיגה לממוצע 50",
        "explain": "קונה נגיעה בממוצע 50 יום בתוך מגמת עלייה מבוססת — כניסה 'על הרכבת' בתחנה.",
    },
    "golden_cross": {
        "name": "חציית זהב",
        "explain": "קונה ברגע שממוצע 50 יום חוצה מעל ממוצע 200 יום (אירוע טרי); מוכר בחציית מוות.",
    },
    "vol_contraction": {
        "name": "פריצת התכווצות",
        "explain": "קונה פריצה שמגיעה אחרי תקופת שקט חריגה (רצועות בולינגר צרות) — קפיץ דרוך שמשתחרר.",
    },
    "low_vol_trend": {
        "name": "תנודתיות נמוכה",
        "explain": "קונה מניות 'שקטות' שעולות בהתמדה — אנומליית התנודתיות הנמוכה המוכרת מהמחקר האקדמי.",
    },
    "supertrend": {
        "name": "סופר-טרנד",
        "explain": "עוקב מגמה מבוסס ATR — רוכב על קו ה-Supertrend כל עוד הוא חיובי והמחיר מעל ממוצע 200 יום; יוצא בהיפוך.",
    },
    "adx_trend": {
        "name": "מגמת ADX",
        "explain": "סוחר רק מגמות חזקות ומכוונות (ADX מעל 25) עם +DI מעל −DI והמחיר מעל ממוצע 50 יום.",
    },
    "keltner_breakout": {
        "name": "פריצת קלטנר",
        "explain": "קונה פריצה מעל רצועת קלטנר העליונה (מבוססת ATR) עם אישור שבועי; יוצא בחזרה מתחת ל-EMA20.",
    },
    "sector_rotation": {
        "name": "רוטציה סקטוריאלית",
        "explain": "מדרג את 11 סקטורי ה-S&P לפי כוח יחסי וקונה את המובילים במגמת עלייה; יוצא מסקטורים שנחלשים.",
    },
    "cross_asset": {
        "name": "בין-נכסי (Intermarket)",
        "explain": 'סוחר אג"ח, זהב, סחורות ודולר לפי המגמה שלהם — פיזור מעבר למניות בלבד.',
    },
    "seasonality": {
        "name": "עונתיות",
        "explain": "הטיה קלנדרית — קונה בחלון 'מפנה החודש' ובחודשים החזקים (נוב'–אפר') כשהמניה במגמת עלייה.",
    },
    "full_agent": {
        "name": "סוכן מחקר (24 שיטות)",
        "explain": "כל 24 השיטות יחד עם שקלול לפי ביצועים, ניהול סיכונים מלא ועמלות — מודד גם שיטות ניסיוניות.",
    },
    "full_agent_live": {
        "name": "הסוכן החי (8 מקודמות)",
        "explain": "בדיוק התצורה שסוחרת בפועל: רק 8 השיטות המקודמות. זה המספר שמנבא את הביצועים האמיתיים.",
    },
    "full_agent_live_full": {
        "name": "הסוכן החי — כל המניות",
        "explain": "אותה תצורה חיה, אבל בלי סורק המניות היומי — סוחרת את כל המאגר. ההשוואה מולה מודדת כמה שווה (או עולה) הסורק.",
    },
    "full_agent_live_gated": {
        "name": "הסוכן החי + שער מאקרו",
        "explain": "התצורה החיה עם שער העיתוי המאקרו — עוצר קניות בתקופות סיכון (SPY מתחת לממוצע 200, VIX גבוה). ההשוואה מודדת כמה השער משפר תשואה/סיכון.",
    },
    "walk_forward": {
        "name": "מבחן אמת (24 שיטות)",
        "explain": "סוכן המחקר, נבחן רק על תקופות שהוא לא 'למד' מהן — המדד האמין ביותר לביצועים אמיתיים.",
    },
    "walk_forward_live": {
        "name": "מבחן אמת — הסוכן החי",
        "explain": "התצורה החיה (8 המקודמות) על תקופות שלא נלמדו — ההוכחה הסופית שהסוכן למד לסחור נכון.",
    },
}

REGIME_HEBREW = {
    "BULL": "שוק עולה 🟢",
    "BEAR": "שוק יורד 🔴",
    "VOLATILE": "שוק סוער ⚡",
}

_METRIC_ROWS: list[tuple[str, str, str]] = [
    ("final_equity", "כמה נשאר מ-$10,000", "שווי התיק בסוף התקופה, כולל עמלות."),
    ("total_return_pct", "תשואה כוללת", "כמה אחוזים הרווחנו (או הפסדנו) על כל התקופה."),
    (
        "cagr_pct",
        "תשואה שנתית ממוצעת",
        "כמה אחוז בשנה בממוצע — המספר להשוואה מול השוק.",
    ),
    (
        "max_drawdown_pct",
        "הנפילה הכי גדולה בדרך",
        "הירידה הגדולה ביותר מהשיא — כמה 'כאב' בדרך.",
    ),
    ("sharpe", "יחס שארפ", "תשואה ביחס לתנודתיות; מעל 1.0 נחשב טוב."),
    ("win_rate", "אחוז עסקאות מוצלחות", "כמה מהעסקאות שנסגרו הסתיימו ברווח."),
    ("trade_count", "מספר פעולות", "כמה קניות ומכירות בוצעו בסך הכול."),
]


def _fmt_money(value: float) -> str:
    return f"${value:,.0f}"


def _fmt_metric(key: str, m: Metrics) -> str:
    value = getattr(m, key)
    if value is None:
        return "—"
    if key == "final_equity":
        return _fmt_money(value)
    if key == "win_rate":
        return f"{value * 100:.0f}%"
    if key == "trade_count":
        return f"{value:,}"
    if key == "sharpe":
        return f"{value:.2f}"
    return f"{value:+.1f}%"


def _score_emoji(m: Metrics, benchmark: Metrics | None) -> str:
    if m.cagr_pct <= 0:
        return "🔴"
    if benchmark is not None and m.cagr_pct >= benchmark.cagr_pct:
        return "🟢"
    return "🟡"


def _equity_chart_html(curves: dict[str, pd.Series], cash: float) -> str:
    """One log-scale equity line chart; benchmark in dashed gray."""
    fig = go.Figure()
    for key, series in curves.items():
        if key == "benchmark":
            continue
        info = STRATEGY_HEBREW.get(key, {"name": key})
        fig.add_trace(
            go.Scatter(
                x=series.index,
                y=series.values,
                name=info["name"],
                mode="lines",
                line={"width": 2, "color": SERIES_COLORS.get(key, "#555")},
            )
        )
    benchmark = curves.get("benchmark")
    if benchmark is not None:
        fig.add_trace(
            go.Scatter(
                x=benchmark.index,
                y=benchmark.values,
                name="סתם להחזיק SPY",
                mode="lines",
                line={"width": 2, "color": _BENCHMARK_COLOR, "dash": "dash"},
            )
        )
    fig.update_layout(
        template="plotly_white",
        height=460,
        margin={"l": 40, "r": 20, "t": 30, "b": 40},
        hovermode="x unified",
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02},
        yaxis={
            "type": "log",
            "title": "שווי התיק ($, סקלה לוגריתמית)",
            "gridcolor": "#eeedeb",
        },
        xaxis={"gridcolor": "#f4f3f1"},
        font={"family": "system-ui, -apple-system, 'Segoe UI', sans-serif"},
    )
    return fig.to_html(full_html=False, include_plotlyjs=True, config={"locale": "he"})


def _strategy_cards(entries: list[dict], benchmark: Metrics | None, cash: float) -> str:
    cards = []
    for entry in entries:
        key, m = entry["key"], entry["metrics"]
        info = STRATEGY_HEBREW.get(key, {"name": key, "explain": ""})
        color = SERIES_COLORS.get(key, "#555")
        bench_line = (
            f"<div class='muted'>סתם להחזיק SPY: {_fmt_money(benchmark.final_equity)}</div>"
            if benchmark is not None
            else ""
        )
        cards.append(f"""
        <div class="card" style="border-inline-start: 5px solid {color}">
          <div class="card-title">{_score_emoji(m, benchmark)} {info['name']}</div>
          <div class="muted">{info['explain']}</div>
          <div class="big">{_fmt_money(cash)} ← <b>{_fmt_money(m.final_equity)}</b></div>
          {bench_line}
          <div class="muted">{entry.get('significance', '')}</div>
        </div>""")
    return "\n".join(cards)


def _metrics_table(entries: list[dict], benchmark: Metrics | None) -> str:
    headers = "".join(
        f"<th>{STRATEGY_HEBREW.get(e['key'], {'name': e['key']})['name']}</th>"
        for e in entries
    )
    bench_header = "<th>סתם להחזיק SPY</th>" if benchmark is not None else ""
    rows = []
    for key, label, hint in _METRIC_ROWS:
        cells = "".join(f"<td>{_fmt_metric(key, e['metrics'])}</td>" for e in entries)
        bench_cell = f"<td>{_fmt_metric(key, benchmark)}</td>" if benchmark else ""
        rows.append(
            f"<tr><th class='metric'>{label}"
            f"<div class='hint'>{hint}</div></th>{cells}{bench_cell}</tr>"
        )
    return (
        "<div class='table-wrap'><table>"
        f"<tr><th></th>{headers}{bench_header}</tr>{''.join(rows)}"
        "</table></div>"
    )


def _regime_table(regime_breakdown: dict[str, dict]) -> str:
    if not regime_breakdown:
        return ""
    entries = list(regime_breakdown)
    regimes = ["BULL", "BEAR", "VOLATILE"]
    headers = "".join(
        f"<th>{STRATEGY_HEBREW.get(k, {'name': k})['name']}</th>" for k in entries
    )
    rows = []
    for regime in regimes:
        cells = []
        for key in entries:
            stats = regime_breakdown[key].get(regime) or {}
            days = stats.get("days", 0)
            ret = stats.get("total_return_pct", 0.0)
            cells.append(
                f"<td>{ret:+.1f}% <span class='hint'>({days} ימים)</span></td>"
            )
        rows.append(
            f"<tr><th class='metric'>{REGIME_HEBREW[regime]}</th>{''.join(cells)}</tr>"
        )
    return (
        "<h2>ביצועים לפי מצב שוק</h2>"
        "<p class='muted'>איך כל שיטה תפקדה כשהשוק עלה, ירד או היה סוער.</p>"
        f"<div class='table-wrap'><table><tr><th></th>{headers}</tr>{''.join(rows)}</table></div>"
    )


def _attribution_table(attribution: dict[str, dict]) -> str:
    """Realized P&L per opening strategy inside each agent simulation."""
    if not attribution:
        return ""
    sections = []
    for agent_key, per_strategy in attribution.items():
        if not per_strategy:
            continue
        agent_name = STRATEGY_HEBREW.get(agent_key, {"name": agent_key})["name"]
        rows = []
        ordered = sorted(
            per_strategy.items(), key=lambda kv: kv[1]["realized_pnl"], reverse=True
        )
        for strategy, stats in ordered:
            name = STRATEGY_HEBREW.get(strategy, {"name": strategy})["name"]
            trips = stats["round_trips"]
            win_pct = f"{stats['wins'] / trips * 100:.0f}%" if trips else "—"
            rows.append(
                f"<tr><th class='metric'>{name}</th>"
                f"<td>{stats['realized_pnl']:+,.0f}$</td>"
                f"<td>{trips:,}</td><td>{win_pct}</td></tr>"
            )
        sections.append(
            f"<h3>{agent_name}</h3>"
            "<div class='table-wrap'><table>"
            "<tr><th>שיטת הכניסה</th><th>רווח/הפסד ממומש</th>"
            "<th>עסקאות סגורות</th><th>אחוז מוצלחות</th></tr>"
            f"{''.join(rows)}</table></div>"
        )
    if not sections:
        return ""
    return (
        "<h2>🔬 מי באמת הרוויח והפסיד בתוך הסוכן</h2>"
        "<p class='muted'>כל עסקה סגורה משויכת לשיטה שפתחה את הפוזיציה "
        "(גם אם יציאת הגנה סגרה אותה) — כך רואים מיד איזו שיטה תורמת ואיזו "
        "שורפת כסף בתוך השילוב.</p>" + "".join(sections)
    )


def _decade_table(curves: dict[str, pd.Series]) -> str:
    """Agent-simulation return per decade, next to the SPY benchmark."""
    agent_keys = [
        k
        for k in curves
        if k.startswith(("full_agent", "walk_forward")) and not curves[k].empty
    ]
    if not agent_keys:
        return ""
    shown = agent_keys + (["benchmark"] if "benchmark" in curves else [])

    def _per_decade(series: pd.Series) -> dict[int, float]:
        out: dict[int, float] = {}
        for decade, chunk in series.groupby(series.index.year // 10 * 10):
            if len(chunk) >= 2 and float(chunk.iloc[0]) > 0:
                out[int(decade)] = (
                    float(chunk.iloc[-1]) / float(chunk.iloc[0]) - 1
                ) * 100
        return out

    per_key = {k: _per_decade(curves[k]) for k in shown}
    decades = sorted({d for vals in per_key.values() for d in vals})
    if not decades:
        return ""
    headers = "".join(
        f"<th>{STRATEGY_HEBREW.get(k, {'name': 'סתם להחזיק SPY' if k == 'benchmark' else k})['name']}</th>"
        for k in shown
    )
    rows = []
    for decade in decades:
        cells = "".join(
            (
                f"<td>{per_key[k][decade]:+.0f}%</td>"
                if decade in per_key[k]
                else "<td>—</td>"
            )
            for k in shown
        )
        rows.append(f"<tr><th class='metric'>שנות ה-{decade}</th>{cells}</tr>")
    return (
        "<h2>📅 ביצועי הסוכן לפי עשור</h2>"
        "<p class='muted'>איפה על ציר הזמן הסוכן מרוויח ואיפה הוא נשבר — "
        "כלי האבחון המהיר ביותר לבעיות עתידיות.</p>"
        "<div class='table-wrap'><table>"
        f"<tr><th></th>{headers}</tr>{''.join(rows)}</table></div>"
    )


def _integrity_banner(data_quality: dict) -> str:
    """Honest survivorship caveat + a summary of what the data gate cleaned."""
    cleaned = data_quality.get("tickers_cleaned", 0)
    bars = data_quality.get("bars_removed", 0)
    worst = data_quality.get("worst") or []
    cleaned_line = ""
    if cleaned:
        names = ", ".join(f"{w['ticker']} (−{w['trimmed_bars']:,})" for w in worst[:6])
        cleaned_line = (
            f"<div style='margin-top:8px'>🧹 <b>שער יושרת-הנתונים ניקה "
            f"{cleaned} טיקרים</b> ({bars:,} ברים מושחתים הוסרו — רצפים "
            f"קפואים וקפיצות-רפאים): {names}.</div>"
        )
    return f"""
    <div style="background:#fff6e5; border:1px solid #f0d090; border-radius:10px;
                padding:12px 16px; margin:16px 0; font-size:0.92rem;">
    ⚠️ <b>יושרה על הנתונים:</b> כל התוצאות <b>מוטות כלפי מעלה</b> — המאגר כולל רק
    מניות ש<b>שרדו עד היום</b>. חברות שפשטו רגל או נמחקו (Lehman, Enron,
    WorldCom, Bear Stearns…) לא נסחרו כאן אף פעם, כך שהסוכן "התחמק" מהפסדים
    שהיו קורים במציאות. לאורך עשורים הניפוח משמעותי — התייחסו למספרים
    כ<b>השוואה יחסית בין שיטות</b>, לא כתשואה שניתן לשחזר.
    {cleaned_line}
    </div>"""


def _learning_table(agg: dict) -> str:
    """ "What the agent learned" — cumulative per-strategy verdict table."""
    from src.trading.strategies import default_strategies

    avg_returns: dict[str, float] = agg.get("avg_returns") or {}
    counts: dict[str, int] = agg.get("signal_counts") or {}
    if not avg_returns:
        return ""
    live_names = {s.name for s in default_strategies()}

    rows = []
    ordered = sorted(avg_returns.items(), key=lambda kv: kv[1], reverse=True)
    for key, avg in ordered:
        name = STRATEGY_HEBREW.get(key, {"name": key})["name"]
        n = counts.get(key, 0)
        status = "🟢 חיה בבוט" if key in live_names else "🧪 ניסיונית"
        if avg < 0:
            verdict = "❌ מפסידה — מנוטרלת (משקל 0)"
        elif n < 1000:
            verdict = "⚠️ רווחית אך מדגם קטן"
        else:
            verdict = "✅ רווחית ומוכחת"
        rows.append(
            f"<tr><th class='metric'>{name}</th><td>{status}</td>"
            f"<td>{avg:+.3f}%</td><td>{n:,}</td><td>{verdict}</td></tr>"
        )
    return (
        "<h2>🎓 מה הסוכן למד — פסק הדין המצטבר</h2>"
        "<p class='muted'>לפי כל האותות מכל הריצות שנעשו אי פעם. שיטה עם ממוצע "
        "שלילי מקבלת משקל 0 — הסוכן מפסיק לסחור בה אוטומטית (אך ממשיך למדוד "
        "אותה, כך שהיא חוזרת אם תשתפר).</p>"
        "<div class='table-wrap'><table>"
        "<tr><th></th><th>סטטוס</th><th>ממוצע לאות</th><th>אותות</th><th>פסיקה</th></tr>"
        f"{''.join(rows)}</table></div>"
    )


_PAGE_CSS = """
  body { font-family: system-ui, -apple-system, 'Segoe UI', sans-serif;
         margin: 0 auto; max-width: 1080px; padding: 24px; color: #1e1e1c;
         background: #fbfaf8; }
  h1 { font-size: 1.6rem; margin-bottom: 4px; }
  h2 { font-size: 1.2rem; margin-top: 36px; }
  .context { background: #eef4fc; border-radius: 10px; padding: 12px 16px;
             margin: 16px 0; font-size: 0.95rem; }
  .muted { color: #6f6e68; font-size: 0.9rem; }
  .hint { color: #8a8a85; font-size: 0.78rem; font-weight: normal; }
  .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
           gap: 14px; margin-top: 16px; }
  .card { background: #fff; border: 1px solid #e7e5e1; border-radius: 10px;
          padding: 14px 16px; }
  .card-title { font-weight: 700; font-size: 1.05rem; margin-bottom: 4px; }
  .big { font-size: 1.25rem; margin: 10px 0 4px; }
  .table-wrap { overflow-x: auto; margin-top: 12px; }
  table { border-collapse: collapse; width: 100%; background: #fff;
          border: 1px solid #e7e5e1; border-radius: 10px; }
  th, td { padding: 9px 12px; text-align: center; border-bottom: 1px solid #efeeea;
           font-size: 0.92rem; }
  th.metric { text-align: right; font-weight: 600; min-width: 170px; }
  tr:last-child td, tr:last-child th { border-bottom: none; }
  .winner { background: #eafbe7; border: 1px solid #bfe8b9; border-radius: 10px;
            padding: 14px 16px; margin-top: 28px; font-size: 1.05rem; }
  footer { margin-top: 40px; color: #8a8a85; font-size: 0.82rem; }
"""


def write_hebrew_report(
    summary: dict, curves: dict[str, pd.Series], path: Path
) -> Path:
    """Render the full Hebrew report for one run."""
    entries: list[dict] = summary["entries"]
    benchmark: Metrics | None = summary.get("benchmark")
    cash = float(summary.get("cash", 10_000))
    start, end = summary.get("start", "?"), summary.get("end", "?")
    tickers = summary.get("tickers", [])

    winner_html = ""
    agg = summary.get("history_agg") or {}
    agg_returns: dict[str, float] = agg.get("avg_returns") or {}
    if agg_returns:
        best = max(agg_returns, key=agg_returns.get)
        best_name = STRATEGY_HEBREW.get(best, {"name": best})["name"]
        ranking = " · ".join(
            f"{STRATEGY_HEBREW.get(k, {'name': k})['name']}: {v:+.2f}%"
            for k, v in sorted(agg_returns.items(), key=lambda kv: kv[1], reverse=True)
        )
        winner_html = f"""
        <div class="winner">🏆 <b>השיטה המובילה עד כה: {best_name}</b>
        — לפי כל {agg.get('runs', 1)} הריצות שנעשו (תשואה ממוצעת לאות מסחר).<br>
        <span class="muted">{ranking}</span></div>"""

    html = f"""<!DOCTYPE html>
<html dir="rtl" lang="he">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>דוח בדיקת שיטות מסחר — {end}</title>
<style>{_PAGE_CSS}</style>
</head>
<body>
<h1>📊 דוח בדיקת שיטות המסחר</h1>
<div class="context">
🖥️ הבדיקה רצה <b>על המחשב שלך בלבד</b>, על נתונים היסטוריים מ-<b>{start}</b> עד
<b>{end}</b>, ב<b>כסף וירטואלי בלבד</b> — שום פקודה אמיתית לא נשלחה לשום מקום,
והנתונים חינמיים (Yahoo Finance).
</div>

{_integrity_banner(summary.get("data_quality") or {})}

<h2>מה בדקנו</h2>
<p>כל שיטה קיבלה {_fmt_money(cash)} וירטואליים וסחרה לבד לאורך כל התקופה,
כולל עמלות ($2.5 לפעולה) והחלקת מחיר, על המניות:
<b>{", ".join(tickers)}</b>.</p>

<div class="cards">
{_strategy_cards(entries, benchmark, cash)}
</div>

<h2>עקומת ההון — מי הרוויח יותר לאורך הדרך</h2>
<p class="muted">כל קו מראה כמה היו שווים {_fmt_money(cash)} בכל נקודת זמן.
אפשר לעבור עם העכבר לפרטים, וללחוץ על שם בסקלה כדי להסתיר/להציג קו.</p>
{_equity_chart_html(curves, cash)}

<h2>כל המספרים</h2>
{_metrics_table(entries, benchmark)}

{_regime_table(summary.get("regime_breakdown") or {})}

{_attribution_table(summary.get("attribution") or {})}

{_decade_table(curves)}

{_learning_table(agg)}

{winner_html}

<footer>
נוצר {datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")} · MarketMind-Pro Backtest ·
הקובץ מקומי בלבד ולא פורסם לאינטרנט · מגבלה ידועה: המאגר כולל רק מניות שקיימות
היום (survivorship bias), כך שהתוצאות מעט אופטימיות.
</footer>
</body>
</html>"""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    logger.info("backtest_report_written", path=str(path))
    return path


def write_index(runs: list[dict], agg: dict, path: Path) -> Path:
    """Cumulative index page: every run + the all-time strategy ranking."""
    ranking_rows = ""
    for i, (key, avg) in enumerate(
        sorted(
            (agg.get("avg_returns") or {}).items(), key=lambda kv: kv[1], reverse=True
        ),
        start=1,
    ):
        name = STRATEGY_HEBREW.get(key, {"name": key})["name"]
        medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"{i}.")
        ranking_rows += f"<tr><td>{medal}</td><th class='metric'>{name}</th><td>{avg:+.2f}%</td></tr>"

    run_rows = ""
    for run in reversed(runs):
        date = str(run.get("generated_at", ""))[:16].replace("T", " ")
        report_file = run.get("report_file")
        link = f"<a href='{report_file}'>פתח דוח</a>" if report_file else "—"
        winner = run.get("winner")
        winner_name = STRATEGY_HEBREW.get(winner, {"name": winner or "—"})["name"]
        tickers = run.get("tickers") or []
        run_rows += (
            f"<tr><td>{date}</td><td>{run.get('mode', '')}</td>"
            f"<td>{len(tickers)} מניות</td>"
            f"<td>{run.get('start', '')} → {run.get('end', '')}</td>"
            f"<td>{winner_name}</td><td>{link}</td></tr>"
        )

    html = f"""<!DOCTYPE html>
<html dir="rtl" lang="he">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ריכוז כל בדיקות המסחר</title>
<style>{_PAGE_CSS}</style>
</head>
<body>
<h1>🗂️ ריכוז כל הבדיקות</h1>
<p class="muted">כל ריצה בודקת מניות ותקופות שונות; הדירוג למטה משקלל את כולן —
ככל שיש יותר ריצות, התמונה אמינה יותר.</p>

<h2>🏆 דירוג השיטות המצטבר ({agg.get("runs", 0)} ריצות)</h2>
<div class="table-wrap"><table>
<tr><th></th><th>שיטה</th><th>תשואה ממוצעת לאות מסחר</th></tr>
{ranking_rows or "<tr><td colspan='3'>עדיין אין ריצות</td></tr>"}
</table></div>

<h2>כל הריצות</h2>
<div class="table-wrap"><table>
<tr><th>מתי</th><th>מצב</th><th>מניות</th><th>תקופה</th><th>המנצחת בריצה</th><th>דוח</th></tr>
{run_rows or "<tr><td colspan='6'>עדיין אין ריצות</td></tr>"}
</table></div>

<footer>MarketMind-Pro Backtest · מקומי בלבד, לא מפורסם לאינטרנט</footer>
</body>
</html>"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return path


def render_text_report(
    rows: list[tuple[str, Metrics]], benchmark: Metrics | None = None
) -> str:
    """Aligned CLI summary table (English — terminals and logs)."""
    header = (
        f"{'strategy':<18}{'final $':>12}{'total %':>10}{'CAGR %':>9}"
        f"{'sharpe':>8}{'maxDD %':>9}{'win %':>8}{'trades':>8}"
    )
    lines = [header, "-" * len(header)]
    all_rows = rows + ([("SPY buy&hold", benchmark)] if benchmark else [])
    for name, m in all_rows:
        win = f"{m.win_rate * 100:.0f}" if m.win_rate is not None else "—"
        lines.append(
            f"{name:<18}{m.final_equity:>12,.0f}{m.total_return_pct:>10.1f}"
            f"{m.cagr_pct:>9.1f}{m.sharpe:>8.2f}{m.max_drawdown_pct:>9.1f}"
            f"{win:>8}{m.trade_count:>8}"
        )
    return "\n".join(lines)


def export_weight_seeds(
    avg_returns: dict[str, float],
    regime_avg_returns: dict[str, dict[str, float]],
    period: str,
    source: str,
    path: Path = DEFAULT_WEIGHTS_PATH,
) -> Path:
    """Write the cold-start seed file consumed by the live StrategyAllocator."""
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "period": period,
        "source": source,
        "avg_returns": {k: round(v, 4) for k, v in avg_returns.items()},
        "regime_avg_returns": {
            regime: {k: round(v, 4) for k, v in strategies.items()}
            for regime, strategies in regime_avg_returns.items()
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    logger.info("backtest_weights_exported", path=str(path))
    return path
