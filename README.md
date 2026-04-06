# MarketMind-Pro 📈

> **מערכת מסחר אוטונומית** לבורסת תל אביב (TASE) ושוק האמריקאי — מבוססת סוכנים חכמים, ניתוח כמותי בזמן אמת, פונדמנטלי, וסנטימנט חדשות.

[![Python](https://img.shields.io/badge/Python-3.12+-blue?logo=python)](https://python.org)
[![Platform](https://img.shields.io/badge/Platform-Apple%20Silicon%20M4-black?logo=apple)](https://apple.com)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)
[![Version](https://img.shields.io/badge/Version-1.1.0-orange)]()

---

## תוכן עניינים

- [מה המערכת עושה](#מה-המערכת-עושה)
- [ארכיטקטורה](#ארכיטקטורה)
- [שעות מסחר](#שעות-מסחר)
- [דרישות מוקדמות](#דרישות-מוקדמות)
- [התקנה והפעלה](#התקנה-והפעלה)
- [Dashboard לוקלי (Streamlit)](#dashboard-לוקלי-streamlit)
- [בוט Telegram](#בוט-telegram)
- [מבנה הפרויקט](#מבנה-הפרויקט)
- [משתני סביבה](#משתני-סביבה)
- [טסטים](#טסטים)
- [CI/CD](#cicd)

---

## מה המערכת עושה

MarketMind-Pro היא פלטפורמה אוטונומית לניתוח שוק ההון, המשלבת:

| יכולת | תיאור |
|---|---|
| **ניתוח כמותי** | RSI, MACD, ממוצעים נעים (SMA/EMA 20–200), זיהוי ספייק נפח |
| **פיבונאצ'י** | חישוב אוטומטי של רמות Retracement ו-Extension לפי 52 שבועות |
| **ארביטראז'** | זיהוי פערי מחיר בין מניות כפול-רישום (TASE/NYSE) עם המרת ILS/USD בזמן אמת |
| **פונדמנטלי** | P/E, EPS, דיבידנד, יעד אנליסטים, שווי שוק, עסקאות בעלי עניין, מתחרים |
| **סנטימנט חדשות** | סריקת Globes, Calcalist, Reuters, Bloomberg — ציון -1.0 עד +1.0 |
| **בוט Telegram** | ניתוח מלא בעברית, /compare, דוחות אוטומטיים 3× ביום |
| **Dashboard לוקלי** | ממשק Streamlit עם גרפים אינטראקטיביים ישירות בדפדפן |
| **גרפים + GitHub Pages** | Candlestick + Volume + RSI + Fibonacci — פרסום אוטומטי לאינטרנט |

### מניות ארביטראז' נתמכות

`TEVA`, `NICE`, `CHKP`, `AMDOCS`, `CEVA`, `GILT`, `RADCOM`, `TOWER`, `ORCL`

---

## ארכיטקטורה

```
┌──────────────────────────────────────────────────┐
│                   MarketMind-Pro                 │
│                                                  │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐  │
│  │    News    │  │   Quant    │  │  Telegram  │  │
│  │   Search   │  │   Engine  │  │ Dispatcher │  │
│  │   Agent    │  │   Agent    │  │   Agent    │  │
│  └─────┬──────┘  └─────┬──────┘  └─────┬──────┘  │
│        │               │               │          │
│  ┌─────▼───────────────▼───────────────▼──────┐   │
│  │         PostgreSQL  +  Redis Cache         │   │
│  └─────────────────────────────────────────────┘  │
│                                                  │
│  ┌────────────┐  ┌────────────┐                  │
│  │ Google MCP │  │  SQL MCP   │                  │
│  │  :8001     │  │  :8002     │                  │
│  └────────────┘  └────────────┘                  │
│                                                  │
│  ┌────────────────────────────────────────────┐  │
│  │       Streamlit Dashboard  :8501           │  │
│  └────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────┘
```

**עקרונות עיצוב:**
- **Agents First** — כל יכולת היא סוכן עצמאי עם אחריות יחידה
- **Stateless Agents** — מצב שמור ב-PostgreSQL/Redis, לא בזיכרון
- **Fail Loud** — אף חריגה לא מושתקת; הכל נרשם בלוגים
- **Security by Default** — אין סודות בקוד; הכל דרך `.env`
- **Hebrew First** — כל פלט Telegram בעברית עם `ParseMode.HTML`

---

## שעות מסחר

| שוק | ימים | שעות | הערה |
|---|---|---|---|
| **ת"א (TASE)** | שני–חמישי | 10:00–17:25 שעון ישראל | פתיחה מוקדמת (pre-open) 09:45 |
| **ת"א (TASE)** | שישי | 10:00–15:45 שעון ישראל | סגירה מוקדמת |
| **ת"א (TASE)** | שבת–ראשון | סגור | |
| **NYSE/NASDAQ** | שני–שישי | 09:30–16:00 ET | |

### דוחות אוטומטיים (Telegram)

| שעה (ישראל) | ימים | תוכן |
|---|---|---|
| **09:30** | שני–שישי | תצוגה מקדימה לפני פתיחה + RSI מהיר |
| **17:45** | שני–חמישי | סיכום סגירה יומי |
| **16:05** | שישי | סיכום סגירה מוקדמת |

---

## דרישות מוקדמות

| דרישה | גרסה מינימלית |
|---|---|
| Python | 3.12+ |
| Docker + Docker Compose | Docker 24+ |
| Apple Silicon | M1/M2/M3/M4 (arm64) |
| Telegram Bot Token | דרך [@BotFather](https://t.me/BotFather) |
| Google Custom Search API | אופציונלי — לסנטימנט חדשות |

---

## התקנה והפעלה

### אפשרות 1 — Docker (מומלץ, הכי פשוט)

```bash
# 1. שכפל את הפרויקט
git clone https://github.com/maaoor6/MarketMind-Pro.git
cd MarketMind-Pro

# 2. הגדר משתני סביבה
cp .env.example .env
# ערוך את .env והכנס את הטוקנים שלך (ראה סעיף משתני סביבה)

# 3. הפעל את כל הסרביסים
docker compose up -d

# 4. בדוק שהכל עלה תקין
docker compose ps
```

זהו — המערכת פועלת. השירותים זמינים:

| שירות | כתובת | תיאור |
|---|---|---|
| Dashboard | http://localhost:8501 | ממשק Streamlit לוקלי |
| Google MCP | http://localhost:8001 | MCP לחיפוש חדשות |
| SQL MCP | http://localhost:8002 | MCP לשאילתות DB |
| PostgreSQL | localhost:5432 | מסד נתונים |
| Redis | localhost:6379 | Cache |

---

## הרצה ועצירה יומיומית

לאחר שהפרויקט כבר מותקן, אלו הפקודות שתשתמש בהן בכל יום:

> **חשוב:** כל הפקודות הבאות מריצים מתוך תיקיית הפרויקט.

### הפעל את המערכת
```bash
docker compose up -d
```

### בדוק שהכל עלה תקין
```bash
docker compose ps
```
כל השורות צריכות להציג `healthy` או `running`.

### כנס לדשבורד
פתח דפדפן: **http://localhost:8501**

### עצור את המערכת
```bash
docker compose down
```

### לוגים — אם משהו לא עובד
```bash
# כל הסרביסים
docker compose logs -f

# רק האפליקציה
docker compose logs -f app

# רק הדשבורד
docker compose logs -f dashboard
```
יציאה מהלוגים: `Ctrl + C`

### עדכון קוד — rebuild לאחר שינויים
```bash
docker compose up -d --build
```
משתמש בזה אחרי כל שינוי בקוד כדי שה-Docker יבנה מחדש את ה-image.

### הפעלה מחדש של סרביס ספציפי
```bash
# רק האפליקציה
docker compose restart app

# רק הדשבורד
docker compose restart dashboard

# רק הבוט (telegram)
docker compose restart app
```

### עצירה זמנית (ללא מחיקת נתונים)
```bash
docker compose stop
```
שונה מ-`down` — הקונטיינרים נעצרים אך לא נמחקים. הפעלה חוזרת:
```bash
docker compose start
```

### הרצת migration (לאחר שינוי ב-DB)
```bash
docker compose run --rm migrate
```

### כניסה ישירה למסד הנתונים
```bash
docker compose exec postgres psql -U marketmind -d marketmind
```
יציאה: `\q`

### הרצת טסטים
```bash
# טסטים יחידתיים
docker compose run --rm app pytest tests/unit/ -v

# עם coverage
docker compose run --rm app pytest tests/unit/ --cov=src/quant --cov-report=term-missing
```

### מחיקת כל הנתונים (איפוס מלא)
```bash
docker compose down -v
```
> ⚠️ פקודה זו מוחקת את כל נתוני ה-DB וה-Redis. אין דרך חזרה.

---

### סיכום מהיר

| פעולה | פקודה |
|---|---|
| **הפעל** | `docker compose up -d` |
| **עצור** | `docker compose down` |
| **עצור זמנית** | `docker compose stop` |
| **הפעל חוזר** | `docker compose start` |
| **עדכן קוד** | `docker compose up -d --build` |
| **Restart סרביס** | `docker compose restart app` |
| **סטטוס** | `docker compose ps` |
| **לוגים** | `docker compose logs -f` |
| **Migrations** | `docker compose run --rm migrate` |
| **טסטים** | `docker compose run --rm app pytest tests/unit/` |
| **איפוס מלא** | `docker compose down -v` |
| **דשבורד** | http://localhost:8501 |

---

---

### אפשרות 2 — התקנה לוקלית (לפיתוח)

```bash
# 1. צור סביבה וירטואלית
python3.12 -m venv .venv
source .venv/bin/activate

# 2. התקן תלויות
pip install -r requirements.txt

# 3. הפעל PostgreSQL ו-Redis דרך Docker בלבד
docker compose up -d postgres redis

# 4. הגדר סביבה
cp .env.example .env
# ערוך .env

# 5. הרץ migrations
alembic upgrade head

# 6. הפעל את הבוט
python -m src.agents.telegram_dispatcher

# או — הפעל רק את ה-Dashboard
streamlit run src/ui/dashboard.py
```

---

## Dashboard לוקלי (Streamlit)

הדשבורד מאפשר ניתוח מניות ישירות מהדפדפן ללא צורך ב-Telegram.

**הפעלה:**
```bash
# לוקלי
streamlit run src/ui/dashboard.py

# דרך Docker
docker compose up -d dashboard
```

פתח: **http://localhost:8501**

**מה רואים:**

- בחר ticker בסרגל הצד (ברירת מחדל: `TEVA`)
- בחר תקופה: 3mo / 6mo / 1y / 2y / 5y
- בחר ממוצעים נעים להצגה
- הפעל/כבה Fibonacci ו-Arbitrage

**תכולת הדשבורד:**

```
📊 מדדים מרכזיים   → מחיר, שינוי יומי, 52W High/Low, ממוצע נפח
📉 אינדיקטורים     → RSI + אות, MACD Histogram, ספייק נפח
📈 גרף אינטראקטיבי → Candlestick + Volume + RSI subplot + Fibonacci lines
📐 טבלת פיבונאצ'י  → כל רמות Retracement + Extension + תמיכה/התנגדות
⚖️ ארביטראז'       → פער TASE/NYSE + שער USD/ILS בזמן אמת
```

---

## בוט Telegram

### הגדרת הבוט

1. פתח שיחה עם [@BotFather](https://t.me/BotFather) ב-Telegram
2. שלח `/newbot` ועקוב אחר ההוראות
3. קבל `TELEGRAM_TOKEN` והכנס ל-`.env`
4. קבל את ה-`TELEGRAM_CHAT_ID` של הצ'אט/ערוץ שלך

### פקודות הבוט (עברית מלאה)

| פקודה | תיאור |
|---|---|
| `/start` | תפריט ראשי עם כפתורים |
| `/analyze TEVA` | ניתוח מלא — מחיר, RSI, MACD, ממוצעים נעים, פיבונאצ'י, **פונדמנטלי**, **בעלי עניין**, ארביטראז', סנטימנט, לינק גרף |
| `/fibonacci AAPL` | רמות פיבונאצ'י מלאות לפי 52 שבועות |
| `/arbitrage TEVA` | פער ארביטראז' TASE/NYSE + שער ILS/USD |
| `/compare TEVA CHKP` | **חדש** — השוואה עברית צד-לצד: מחיר, RSI, MACD, P/E, EPS, שווי שוק |
| `/health` | סטטוס כל הסרביסים (Quant, News, DB, Redis) |

### דוגמת פלט `/analyze TEVA`

```
📊 ניתוח TEVA
━━━━━━━━━━━━━━━━━━━
💰 מחיר נוכחי: $16.42
🕐 עדכון: 05/04/2026 14:30 UTC

🏢 Teva Pharmaceutical Industries (TEVA)
🏭 תחום: Healthcare | Drug Manufacturers
💰 שווי שוק: $18.4B

📊 מכפילים ומדדים:
  מכפיל רווח (P/E) נוכחי: 8.2
  מכפיל רווח (P/E) צפוי:  6.9
  EPS נוכחי:  $2.01
  EPS צפוי:   $2.38
  תשואת דיבידנד: לא זמין
  🎯 יעד אנליסטים: $20.50
  📈 טווח 52 שבועות: $12.50 – $21.30
  🆚 מתחרים עיקריים: MRK, PFE, AMGN

📉 אינדיקטורים טכניים:
  RSI(14): 43.2  ⚪ נייטרלי
  MACD קו: -0.0812  |  סיגנל: -0.0654
  MACD היסטוגרמה: -0.0158  📉 דובי
  ספייק נפח: ❌ לא

📏 ממוצעים נעים:
  SMA_20: $16.71  ↓ מתחת
  SMA_50: $17.14  ↓ מתחת
  SMA_200: $15.88  ↑ מעל

📐 פיבונאצ'י (52 שבועות):
  שיא: $21.30  |  שפל: $12.50
  מגמת ירידה 📉  |  מיקום: 44.5% מהשפל
  🟢 תמיכה קרובה: $15.97
  🔴 התנגדות קרובה: $16.85

⚖️ ארביטראז' TASE/NYSE:
  TASE (TEVA.TA): $16.38  |  שער: ₪3.7210
  פער: 0.24%  —  ארה"ב במחיר פרמיום

🕵️ עסקאות בעלי עניין:
  🔴 מכירה — Richard Francis (CEO)
  05/03/2026: 50,000 מניות @ $17.20  |  סה"כ: $860,000

📰 סנטימנט חדשות:
  🟡 ציון: -0.12  ▓▓▓▓░░░░░░

🌍 מצב שוק:
  🇺🇸 NYSE: 🟢 פתוח  |  🇮🇱 ת"א: 🔴 סגור

📊 צפה בגרף אינטראקטיבי
```

### דוגמת פלט `/compare TEVA CHKP`

```
⚖️ השוואה: TEVA מול CHKP
━━━━━━━━━━━━━━━━━━━
מדד             TEVA         CHKP
─────────────────────────────
💰 מחיר         $16.42       $165.30
📉 RSI(14)      43.2         58.7
📊 MACD         📉 דובי      📈 שורי
📐 פיבונאצ'י   📉 ירידה     📈 עלייה
📈 P/E          8.2          22.4
💵 EPS          2.01         7.84
🏦 שווי שוק    18.4B        16.1B
```

---

## מבנה הפרויקט

```
MarketMind-Pro/
├── src/
│   ├── agents/
│   │   ├── news_search_agent.py    # סוכן סנטימנט חדשות (Google Search MCP)
│   │   ├── quant_engine.py         # סוכן ניתוח טכני + אינדיקטורים
│   │   └── telegram_dispatcher.py  # בוט Telegram + inline keyboards
│   ├── quant/
│   │   ├── indicators.py           # RSI, MACD, SMA, EMA, Volume Spike
│   │   ├── fibonacci.py            # רמות פיבונאצ'י (52 שבועות)
│   │   ├── arbitrage.py            # זיהוי פערי ארביטראז' TASE/NYSE
│   │   └── fundamentals.py         # P/E, EPS, עסקאות פנים, מתחרים [v1.1.0]
│   ├── database/
│   │   ├── models.py               # מודלי SQLAlchemy (5 טבלאות)
│   │   ├── session.py              # ניהול חיבורי DB (async)
│   │   └── cache.py                # Redis cache layer
│   ├── mcp/
│   │   ├── google_search_mcp.py    # MCP Server — חיפוש Google
│   │   └── sql_mcp_server.py       # MCP Server — שאילתות SQL
│   ├── ui/
│   │   ├── charts.py               # גרפי Plotly (candlestick, Fibonacci)
│   │   ├── dashboard.py            # Streamlit Dashboard לוקלי
│   │   └── publisher.py            # פרסום גרפים ל-GitHub Pages [v1.1.0]
│   └── utils/
│       ├── config.py               # הגדרות (pydantic-settings)
│       ├── logger.py               # structlog logger
│       └── timezone_utils.py       # שעוני שוק, currency_symbol [v1.1.0]
├── tests/
│   ├── unit/                       # טסטים ללא I/O
│   │   ├── test_indicators.py
│   │   ├── test_fibonacci.py
│   │   ├── test_arbitrage.py
│   │   └── test_timezone_utils.py
│   └── integration/                # טסטים עם Docker
│       └── test_database.py
├── alembic/
│   └── versions/
│       └── 0001_initial_schema.py
├── scripts/
│   ├── init_db.sql
│   └── install_hooks.sh
├── .github/
│   └── workflows/
│       └── ci.yml
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml
├── requirements.txt
├── .env.example
└── CLAUDE.md
```

---

## משתני סביבה

העתק `.env.example` ל-`.env` ומלא את הערכים:

```bash
cp .env.example .env
```

| משתנה | חובה | תיאור |
|---|---|---|
| `DATABASE_URL` | ✅ | PostgreSQL (asyncpg) |
| `DATABASE_URL_SYNC` | ✅ | PostgreSQL (psycopg2 — לAlembic) |
| `REDIS_URL` | ✅ | Redis connection string |
| `TELEGRAM_TOKEN` | ✅ | טוקן בוט מ-@BotFather |
| `TELEGRAM_CHAT_ID` | ✅ | ID של הצ'אט/ערוץ |
| `EXCHANGERATE_API_KEY` | ⚠️ | שער USD/ILS בזמן אמת (ברירת מחדל: 3.72) |
| `GOOGLE_API_KEY` | ⚠️ | לסנטימנט חדשות |
| `GOOGLE_SEARCH_ENGINE_ID` | ⚠️ | Google Custom Search Engine |
| `ALPHA_VANTAGE_KEY` | ❌ | מקור נתונים חלופי |
| `GITHUB_TOKEN` | ⚠️ | לפרסום גרפים ב-GitHub Pages (נדרש ל-/analyze) |
| `GITHUB_PAGES_REPO` | ⚠️ | שם ריפו ל-GitHub Pages (ברירת מחדל: maaoor6/MarketMind-Pro) |

**⚠️ = מומלץ | ❌ = אופציונלי**

---

## טסטים

```bash
# טסטים יחידתיים (ללא Docker — מהיר)
pytest tests/unit/ -v

# עם דוח coverage
pytest tests/unit/ --cov=src/quant --cov-report=term-missing

# טסטים אינטגרציה (דורשים Docker פעיל)
docker compose up -d postgres redis
pytest tests/integration/ -m integration -v

# הרצת כל הטסטים
pytest
```

**כיסוי נדרש:** מינימום 80% על `src/quant/`.

**מצב טסטים נוכחי:** 44 טסטים יחידתיים, כולם עוברים ✅

---

## CI/CD

הפרויקט כולל GitHub Actions workflow (`.github/workflows/ci.yml`) שמריץ בכל push:

1. `ruff check` — בדיקת linting
2. `black --check` — בדיקת פורמט
3. `bandit -r src/` — סריקת אבטחה
4. `pytest tests/unit/` — טסטים יחידתיים

### Git Hooks (pre-commit)

```bash
# התקן hooks מקומית
bash scripts/install_hooks.sh
```

הhooks מריצים לפני כל commit: ruff → black → bandit → pytest unit.

---

## אבטחה

- **אין סודות בקוד** — כל credentials דרך `.env` בלבד
- **`.env` לא עולה ל-git** — מוגן ב-`.gitignore`
- **Bandit** — סריקת MEDIUM+ severity לפני כל commit
- **SQL** — SQLAlchemy ORM בלבד, אין f-string SQL
- **אין `eval()` / `exec()`** בקוד הפרויקט

---

## רישיון

MIT © 2026 MarketMind-Pro

---

> **שים לב:** המערכת מיועדת לצרכי מחקר וניתוח בלבד. אינה מהווה ייעוץ השקעות.
