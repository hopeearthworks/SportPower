# Potential Improvements: Sports Monitor

This document lists ways to improve the Sports Monitor at **every stage** of the
automation, from data collection to deployment. Each idea says *why it matters*, *how to
do it*, and roughly how much effort it takes.

The centrepiece is **Stage 3: a historical SQL database with 10 years of results**. It
would turn the Sport Oracle from a current-season guess into a model that can be trained,
tested and proven.

**Effort:** 🟢 small (≤1 day) · 🟡 medium (days) · 🔴 large (weeks)
**Impact:** ⭐ nice to have · ⭐⭐ useful · ⭐⭐⭐ significant

---

## Contents

1. [Stage 1: Definition and governance](#stage-1-definition-and-governance)
2. [Stage 2: Data acquisition](#stage-2-data-acquisition)
3. [Stage 3: Data storage: historical SQL database](#stage-3-data-storage-historical-sql-database)
4. [Stage 4: Processing and business rules](#stage-4-processing-and-business-rules)
5. [Stage 5: Prediction model (Sport Oracle)](#stage-5-prediction-model-sport-oracle)
6. [Stage 6: Configuration](#stage-6-configuration)
7. [Stage 7: Testing and validation](#stage-7-testing-and-validation)
8. [Stage 8: Logging, monitoring and alerting](#stage-8-logging-monitoring-and-alerting)
9. [Stage 9: Outputs and reporting](#stage-9-outputs-and-reporting)
10. [Stage 10: Web dashboard (UI/UX)](#stage-10-web-dashboard-uiux)
11. [Stage 11: Security](#stage-11-security)
12. [Stage 12: Deployment and operations](#stage-12-deployment-and-operations)
13. [Stage 13: Documentation and knowledge sharing](#stage-13-documentation-and-knowledge-sharing)
14. [Suggested roadmap](#suggested-roadmap)

---

## Stage 1: Definition and governance

| Improvement | Why | How | Effort / Impact |
|---|---|---|---|
| **Measurable success targets** | "Saves time" is hard to prove | Record how long the manual process took versus the automated one. Track reports used per week. | 🟢 / ⭐⭐ |
| **Data licensing review** | ESPN's API is unofficial, and logos are hotlinked from ESPN | Ask legal or compliance whether internal use is fine and what external publication needs. Budget for a licensed feed if publishing. | 🟢 / ⭐⭐⭐ |
| **Responsible-prediction policy** | Picks could be read as betting tips | Written guidance: no odds, keep the disclaimer, editorial sign-off before publishing picks, no targeting of under-18s. | 🟢 / ⭐⭐⭐ |
| **Named owner and support rota** | Avoids single-person dependency (risk R14) | Owner, backup, escalation path recorded in the worksheet | 🟢 / ⭐⭐ |
| **Change log** | Track what changed and when | `CHANGELOG.md` following "Keep a Changelog". Version tags in git. | 🟢 / ⭐ |
| **Put the project in git** | No version history yet | `git init`, `.gitignore` (`.venv`, `__pycache__`, `*.log`, `*_report.txt`), commit, push to a private repo | 🟢 / ⭐⭐⭐ |

---

## Stage 2: Data acquisition

| Improvement | Why | How | Effort / Impact |
|---|---|---|---|
| **Retry with back-off** | Temporary network or ESPN errors fail a whole run | `requests.adapters.HTTPAdapter(max_retries=Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504]))` on a shared `Session` | 🟢 / ⭐⭐ |
| **Shared HTTP session** | Faster repeated calls (connection reuse) | One `requests.Session()` in `sports.py` used by every fetch | 🟢 / ⭐ |
| **Response caching for all sports** | Fewer calls, faster UI, protection against rate limits (R2) | Extend the Oracle's in-memory cache to `fetch_events`, or use `requests-cache` with SQLite storage | 🟢 / ⭐⭐ |
| **Contract checks on API shape** | Detect ESPN changes early (R1) | A small schema check (e.g. `pydantic` models) on key fields that logs a WARNING when fields disappear | 🟡 / ⭐⭐⭐ |
| **Second data provider** | Resilience. Fills gaps (boxing, F1 times). | Provider interface (`fetch_events(sport, league, date)`) with ESPN as one implementation. Candidates (check licences): API-Football, Sportmonks, TheSportsDB premium, Ergast-style F1 data, a licensed boxing API. | 🔴 / ⭐⭐⭐ |
| **Date ranges** | Football and rugby allow one day per call | Loop over days in parallel (as the Oracle already does), with a reusable helper | 🟢 / ⭐⭐ |
| **More detail per event** | Line-ups, stats, timelines | ESPN `summary?event=<id>` endpoint (already confirmed to include `boxscore`, `lastFiveGames`, `standings`) | 🟡 / ⭐⭐ |
| **Boxing coverage** | Currently substituted by MMA | Licensed boxing data provider behind the provider interface | 🟡 / ⭐⭐ |

---

## Stage 3: Data storage: historical SQL database

> **Goal:** keep about **10 years of results** for every supported competition in a
> relational database, then use it to (a) train and **backtest** the prediction model,
> (b) power history features in the UI (head-to-head, trends, tables on any date), and
> (c) cut down on live API calls.

### 3.1 Database choice

| Option | When to use |
|---|---|
| **SQLite** (`sports.db`) | Start here. No server, one file, part of Python (`sqlite3`). Easily handles millions of rows. |
| **PostgreSQL** | When hosting for a team, needing concurrent writes, or connecting BI tools (Power BI). |
| **SQL Server / Azure SQL** | If the organisation is Microsoft-centred (fits Power Automate / Power BI). |

Use **SQLAlchemy** (or plain SQL files) so the same schema runs on any of them.

### 3.2 Suggested schema

```sql
-- Reference data
CREATE TABLE sport (
    sport_id     TEXT PRIMARY KEY,            -- 'football', 'rugby', 'f1', 'cricket', 'combat'
    name         TEXT NOT NULL
);

CREATE TABLE competition (
    competition_id  TEXT PRIMARY KEY,         -- 'football:eng.1', 'rugby:270557'
    sport_id        TEXT NOT NULL REFERENCES sport(sport_id),
    espn_code       TEXT NOT NULL,            -- 'eng.1', '270557'
    name            TEXT NOT NULL,
    country         TEXT
);

CREATE TABLE season (
    season_id       TEXT PRIMARY KEY,         -- 'football:eng.1:2025'
    competition_id  TEXT NOT NULL REFERENCES competition(competition_id),
    label           TEXT NOT NULL,            -- '2025-26'
    start_date      DATE,
    end_date        DATE
);

CREATE TABLE team (
    team_id      TEXT PRIMARY KEY,            -- 'espn:360'
    sport_id     TEXT NOT NULL REFERENCES sport(sport_id),
    name         TEXT NOT NULL,
    short_name   TEXT,
    abbreviation TEXT,
    country      TEXT
);

CREATE TABLE venue (
    venue_id   TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    city       TEXT,
    country    TEXT
);

-- Facts
CREATE TABLE match (
    match_id       TEXT PRIMARY KEY,          -- ESPN event id
    season_id      TEXT NOT NULL REFERENCES season(season_id),
    kickoff_utc    TIMESTAMP NOT NULL,
    home_team_id   TEXT NOT NULL REFERENCES team(team_id),
    away_team_id   TEXT NOT NULL REFERENCES team(team_id),
    venue_id       TEXT REFERENCES venue(venue_id),
    neutral_site   BOOLEAN DEFAULT FALSE,
    status         TEXT NOT NULL,             -- scheduled / final / postponed / abandoned
    home_score     INTEGER,
    away_score     INTEGER,
    home_ht_score  INTEGER,
    away_ht_score  INTEGER,
    round          TEXT,
    source         TEXT NOT NULL DEFAULT 'espn',
    fetched_at     TIMESTAMP NOT NULL
);
CREATE INDEX ix_match_season_date ON match(season_id, kickoff_utc);
CREATE INDEX ix_match_home ON match(home_team_id, kickoff_utc);
CREATE INDEX ix_match_away ON match(away_team_id, kickoff_utc);

CREATE TABLE match_event (                    -- goals, tries, cards
    match_id    TEXT NOT NULL REFERENCES match(match_id),
    minute      TEXT,
    event_type  TEXT NOT NULL,                -- goal / own_goal / penalty / try / red_card ...
    team_id     TEXT REFERENCES team(team_id),
    player      TEXT
);

CREATE TABLE match_stat (                     -- shots, possession, xG if a source provides it
    match_id   TEXT NOT NULL REFERENCES match(match_id),
    team_id    TEXT NOT NULL REFERENCES team(team_id),
    stat_name  TEXT NOT NULL,
    value      REAL,
    PRIMARY KEY (match_id, team_id, stat_name)
);

-- Individual sports
CREATE TABLE athlete (
    athlete_id TEXT PRIMARY KEY,
    sport_id   TEXT NOT NULL REFERENCES sport(sport_id),
    name       TEXT NOT NULL,
    country    TEXT
);

CREATE TABLE race_result (                    -- F1
    session_id TEXT NOT NULL,                 -- event id + session code
    athlete_id TEXT NOT NULL REFERENCES athlete(athlete_id),
    position   INTEGER,
    PRIMARY KEY (session_id, athlete_id)
);

CREATE TABLE bout (                           -- MMA (and boxing later)
    bout_id        TEXT PRIMARY KEY,
    event_id       TEXT NOT NULL,
    date_utc       TIMESTAMP,
    weight_class   TEXT,
    fighter_a_id   TEXT REFERENCES athlete(athlete_id),
    fighter_b_id   TEXT REFERENCES athlete(athlete_id),
    winner_id      TEXT REFERENCES athlete(athlete_id),
    method         TEXT,                      -- KO/TKO, SUB, DEC (needs another source)
    end_round      INTEGER,
    end_time       TEXT
);

-- Model support
CREATE TABLE team_rating (                    -- Elo / model ratings over time
    team_id     TEXT NOT NULL REFERENCES team(team_id),
    as_of_date  DATE NOT NULL,
    model       TEXT NOT NULL,                -- 'elo_v1', 'dixon_coles_v1'
    rating      REAL NOT NULL,
    PRIMARY KEY (team_id, as_of_date, model)
);

CREATE TABLE prediction (                     -- every Oracle pick, for accuracy tracking
    prediction_id  INTEGER PRIMARY KEY,
    match_id       TEXT NOT NULL REFERENCES match(match_id),
    model          TEXT NOT NULL,
    created_at     TIMESTAMP NOT NULL,
    p_home         REAL NOT NULL,
    p_draw         REAL NOT NULL,
    p_away         REAL NOT NULL,
    pick           TEXT NOT NULL,             -- home / draw / away
    outcome        TEXT,                      -- filled in after the match
    brier          REAL                       -- filled in after the match
);
```

### 3.3 Getting 10 years of history (backfill)

| Source | What it gives | Notes |
|---|---|---|
| **ESPN scoreboard by date** (already used) | Results, scorers and venues for every sport we support | Single day per call, so 10 seasons × ~365 days ≈ **3,650 calls per league**. Throttle to ~1 request/second, run overnight, and resume from the last saved date. |
| **League calendars** (already used) | Only the dates that have matches | Cuts the backfill to ~40–80 calls per season per league |
| **football-data.co.uk** | Free CSVs of results and match stats for many European leagues going back decades | Fast bulk load for football. Check the terms of use. |
| **Open datasets** (e.g. openfootball, Kaggle) | Historical football results | Check the licence and coverage per dataset |
| **F1 historical APIs** (Ergast-compatible services) | Full race history, lap times, constructors | Fills the "no times/teams" gap |
| **UFCStats.com** | Fight-by-fight results including method | Check the terms before scraping |

**ETL pipeline (extract → transform → load):**

```text
backfill.py --sport football --league eng.1 --from 2016-08-01 --to 2026-06-01
  1. Read the league calendar per season to list match dates
  2. For each date (throttled, resumable): GET scoreboard?dates=YYYYMMDD
  3. Reuse sports.parse_team_events() → normalised dicts
  4. UPSERT team / venue / match / match_event   (idempotent: safe to re-run)
  5. Log progress: "[Backfill] eng.1 2019-12-26: 10 matches stored"

nightly_update.py  (Task Scheduler / Power Automate, 03:00)
  1. Fetch yesterday + today for every competition
  2. UPSERT results; mark finished predictions with outcome + Brier score
  3. Recalculate ratings (team_rating) for the next day's Oracle
```

**Data quality checks after loading:** no duplicate `match_id`; scores present for
`final` matches; each team plays about the expected number of games per season; no
kick-offs in the future marked `final`; team-name variants mapped to a single `team_id`.

### 3.4 What the database unlocks

- **Backtesting:** replay past seasons and measure how good each model would have been (Stage 5).
- **Head-to-head records** and **home/away splits** shown in "Why this pick?".
- **League table on any date:** recompute standings from `match` instead of calling ESPN.
- **Trend charts:** form over time, goals per game, rating history (`team_rating`).
- **Fewer API calls:** the UI reads history from the database and only fetches live data from ESPN.
- **Power BI / Excel** connections for editorial analysis.

---

## Stage 4: Processing and business rules

| Improvement | Why | How | Effort / Impact |
|---|---|---|---|
| **One shared match parser** | `football_monitor.extract_matches` and `sports.parse_team_events` overlap | Move the CLI football path onto `sports.py` once `run_tests.py` is updated to the new report format | 🟡 / ⭐⭐ |
| **Fuzzy team search** | "Hearts" doesn't match "Heart of Midlothian" | Alias table (`team_alias`) plus `difflib.get_close_matches` suggestions | 🟢 / ⭐⭐ |
| **Multiple teams / favourites** | Desks follow several clubs | `team_filter` as a list, e.g. `["Celtic", "Rangers"]` | 🟢 / ⭐⭐ |
| **Time-window rules** | "Only today's fixtures" (workshop example) | `window: "today" \| "next_7_days"`, using the multi-day helper | 🟢 / ⭐⭐ |
| **Configurable time zone** | Reports are UTC only | `timezone: "Europe/London"` using `zoneinfo` | 🟢 / ⭐ |
| **Rule engine** | Business users adding rules without code | Simple rules in config: `{"field": "venue", "op": "contains", "value": "Park"}` | 🟡 / ⭐ |

---

## Stage 5: Prediction model (Sport Oracle)

The current model is a transparent heuristic using **current-season data only**. With the
Stage 3 database, it can become a measured, trained model.

### 5.1 Measure first: backtesting and metrics

| Metric | Meaning | Target |
|---|---|---|
| **Accuracy** | % of picks that were correct | Report it, but don't optimise for it alone |
| **Brier score** | Mean squared error of the probabilities (lower is better) | Beat the "always pick home" baseline |
| **Log loss** | Penalises confident wrong picks | Lower than the current heuristic |
| **Calibration** | Do "60%" picks win about 60% of the time? | A calibration chart close to the diagonal |
| **Baseline comparison** | Home-always, table position, bookmaker-implied odds (for evaluation only) | The model should be competitive |

Backtest by **walking forward**: for each matchday in seasons 2016–2026, train only on
earlier data, predict that matchday, and score it. This avoids "seeing the future".

### 5.2 Better models (in order of effort)

| Model | Description | Sport | Effort / Impact |
|---|---|---|---|
| **Elo ratings** | Ratings update after every match. Margin and home advantage are learned from history. Simple and strong. | Football, rugby, MMA | 🟢 / ⭐⭐⭐ |
| **Poisson / Dixon-Coles** | Predicts goals for each side from attack and defence strengths. Gives exact-score and draw probabilities. The standard approach for football. | Football | 🟡 / ⭐⭐⭐ |
| **Logistic regression** | Features: Elo difference, form, rest days, head-to-head, home/away splits, table position | Football, rugby | 🟡 / ⭐⭐ |
| **Gradient boosting (XGBoost / LightGBM)** | Learns interactions between many features, with calibration via isotonic regression | All | 🔴 / ⭐⭐ |
| **Bradley-Terry / Glicko** | Ratings for individual athletes, accounting for uncertainty | MMA, F1 drivers | 🟡 / ⭐⭐ |
| **Ensemble** | Average of Elo, Dixon-Coles and logistic regression | Football | 🟡 / ⭐⭐ |

### 5.3 More features (inputs)

- Head-to-head record over the last N meetings (from the database)
- Home-only and away-only form and scoring
- Rest days and fixture congestion (midweek European games)
- Travel distance (venue coordinates)
- Season stage (early-season uncertainty, end-of-season motivation)
- Injuries and suspensions (needs a data source)
- Weather for outdoor sports
- Promoted-team adjustment (weaker prior for newly promoted sides)
- For MMA: reach, age, stance, recent finishes, activity, method of past wins (needs UFCStats-type data)
- For F1: grid position, qualifying gap, team pace, track history

### 5.4 Model operations

| Improvement | How |
|---|---|
| **Accuracy tracker in the UI** | Store each pick in the `prediction` table. After the match, show "Oracle record: 61% correct, Brier 0.19 this season". |
| **Model versioning** | A `model` column on `prediction` and `team_rating` makes A/B comparisons possible |
| **Explainability** | Keep "Why this pick?" and add each feature's contribution (e.g. "home advantage +6%") |
| **Retraining schedule** | Weekly retrain plus nightly rating updates (Stage 12) |
| **Predicted score** | From Dixon-Coles: "Most likely: 2-1 (11%)" |
| **F1 and cricket predictions** | F1: race-winner probabilities from qualifying and Elo. Cricket: needs a historical database first. |

---

## Stage 6: Configuration

| Improvement | Why | How | Effort / Impact |
|---|---|---|---|
| **Multiple saved profiles** | Different desks want different defaults | `profiles/football_desk.json`, `profiles/rugby_desk.json`, selectable in the UI | 🟢 / ⭐⭐ |
| **Schema-validated config** | Clearer errors | `pydantic` model for `config.json` with field-level messages | 🟢 / ⭐ |
| **Environment settings** | Port, host and log level without code changes | `.env` or environment variables (`SPORTS_MONITOR_PORT=8080`) | 🟢 / ⭐ |
| **Config backup on save** | "Save as default" overwrites the file | Write `config.json.bak` before saving | 🟢 / ⭐ |

---

## Stage 7: Testing and validation

| Improvement | Why | How | Effort / Impact |
|---|---|---|---|
| **Move to pytest** | Standard tooling, fixtures, coverage | Turn the `run_tests.py` scenarios into `tests/test_cli.py` | 🟢 / ⭐⭐ |
| **Recorded API responses** | Tests currently depend on live data (R10) | Save JSON samples per sport into `tests/fixtures/`. Mock `requests` with `responses` or `vcrpy`. | 🟡 / ⭐⭐⭐ |
| **Parser unit tests** | Sport parsers are untested (R11) | One test per sport: postponed match, live match, cricket "&" scores, missing logos | 🟡 / ⭐⭐⭐ |
| **Oracle unit tests** | Guard the model maths | Probabilities sum to 1, home advantage helps, calibration caps hold, deterministic outputs | 🟢 / ⭐⭐ |
| **API tests** | Endpoint contracts | Start the server in a thread (as in manual testing) and assert on status codes and JSON | 🟢 / ⭐⭐ |
| **Browser tests** | UI regressions | Playwright: switch sports, filter, generate a report, open the Oracle drawer, toggle the theme | 🟡 / ⭐⭐ |
| **Visual regression** | Layout breakage | Playwright screenshot comparisons at 1440 / 1920 / 390 px, light and dark | 🟡 / ⭐ |
| **Accessibility checks** | Inclusive design | axe-core in Playwright, plus keyboard-only and screen-reader passes | 🟢 / ⭐⭐ |
| **CI pipeline** | Run tests on every change | GitHub Actions: lint (ruff), type check (mypy), pytest, Playwright | 🟡 / ⭐⭐⭐ |
| **Linting and formatting** | Consistency | `ruff` + `black` for Python, `eslint` + `prettier` for JS | 🟢 / ⭐ |

---

## Stage 8: Logging, monitoring and alerting

| Improvement | Why | How | Effort / Impact |
|---|---|---|---|
| **Log rotation** | `automation.log` grows forever (R9) | `logging.handlers.RotatingFileHandler(maxBytes=5_000_000, backupCount=5)` | 🟢 / ⭐⭐ |
| **Structured (JSON) logs** | Easier to search and chart | `python-json-logger`. Fields: `sport`, `league`, `duration_ms`, `count`. | 🟢 / ⭐ |
| **Run IDs** | Group lines from one run | `run_id = uuid4()` added to every log record | 🟢 / ⭐ |
| **Timing metrics** | Spot slow API calls | Log the duration of each request and of the whole run | 🟢 / ⭐ |
| **Failure alerts** | Nobody notices a failed scheduled run | Power Automate or a Teams webhook when the exit code is 1 or an ERROR is logged | 🟢 / ⭐⭐⭐ |
| **Health endpoint** | Monitoring for a hosted version | `/api/health` returning version, uptime and last successful ESPN call | 🟢 / ⭐ |
| **Data staleness alert** | Feed silently stops updating | Warn if no new results for a live competition within N hours | 🟡 / ⭐⭐ |

---

## Stage 9: Outputs and reporting

| Improvement | Why | How | Effort / Impact |
|---|---|---|---|
| **Machine-readable reports** | Downstream systems prefer structure | `--format json` / `csv` alongside the text report | 🟢 / ⭐⭐ |
| **Dated report archive** | Reports are overwritten on each run | `reports/2026-10-29_football_sco.1.txt`, kept for N days | 🟢 / ⭐⭐ |
| **HTML / PDF report** | Shareable with non-technical readers | Jinja2 template, plus `weasyprint` for PDF | 🟡 / ⭐⭐ |
| **Email / Teams delivery** | Hands-free distribution | Power Automate flow picks up the report, or SMTP / Teams webhook | 🟢 / ⭐⭐⭐ |
| **Oracle predictions in reports** | Preview content | "Predictions" section with pick, probability and confidence | 🟢 / ⭐⭐ |
| **Social cards** | Faster publishing | Generate PNG score or prediction cards (Pillow / HTML-to-image) | 🟡 / ⭐⭐ |

---

## Stage 10: Web dashboard (UI/UX)

| Improvement | Why | How | Effort / Impact |
|---|---|---|---|
| **Match detail drawer** | More depth per match | Click a card to see line-ups, stats and a timeline from ESPN `summary` | 🟡 / ⭐⭐⭐ |
| **League table view** | Context alongside results | Standings tab, already available from the Oracle's standings call | 🟢 / ⭐⭐ |
| **Favourites and pinning** | Personalised newsroom view | Star teams. They're highlighted and sorted first (per-browser storage, or user profiles once hosted). | 🟢 / ⭐⭐ |
| **Shareable URLs** | Send a colleague the exact view | Store sport, league, date and filter in the URL query string | 🟢 / ⭐⭐ |
| **Oracle history panel** | Build trust | "Last week the Oracle went 7/10", from the `prediction` table | 🟡 / ⭐⭐ |
| **Charts** | Visual trends | Rating history, form trend, goals per round (Chart.js) | 🟡 / ⭐⭐ |
| **Live updates without reload** | Smoother live following | Server-Sent Events pushing changed scores every 30 s | 🟡 / ⭐⭐ |
| **Offline / bundled assets** | Works without CDNs (R12) | Ship Bootstrap, icons and fonts in `web/vendor/` | 🟢 / ⭐ |
| **Accessibility pass** | Inclusive design | ARIA live regions for score updates, focus management in the drawer, contrast checks on violet and teal | 🟢 / ⭐⭐ |
| **Installable app (PWA)** | Phone home-screen use | Manifest + service worker | 🟡 / ⭐ |
| **Split `app.js` into modules** | 1,500+ lines in one file | ES modules: `api.js`, `events.js`, `oracle.js`, `match-modal.js`, `report.js` | 🟡 / ⭐⭐ |
| **Bet view: hide US bookmakers when UK odds exist** | DraftKings rows are irrelevant to UK users once a key is set | Filter `region == "US"` when Odds API rows are present, with a "show all" toggle | 🟢 / ⭐⭐ |
| **Team-alias table for odds matching** | "Hearts" vs "Heart of Midlothian" won't match | `team_alias` table (Stage 3) used by `match_centre.find_odds_event` | 🟢 / ⭐⭐ |
| **Odds history and line movement** | Shows how the market moved before kick-off | Store each odds snapshot in an `odds_snapshot` table. Chart it in the modal. | 🟡 / ⭐⭐ |
| **Use market odds to evaluate the Oracle** | Bookmaker implied chances are a strong benchmark | Compare Oracle Brier score with the bookmakers' implied probabilities in backtests (evaluation only) | 🟡 / ⭐⭐⭐ |
| **Setting to turn the Bet view off** | Some desks or audiences shouldn't see odds (risk R16) | `"show_betting": false` in config hides the Bet button | 🟢 / ⭐⭐⭐ |
| **Deep links to the exact market** | "Visit site" currently opens the bookmaker's home page | Use bookmaker event links if a licensed odds plan provides them | 🟡 / ⭐ |

---

## Stage 11: Security

| Improvement | Why | How | Effort / Impact |
|---|---|---|---|
| **Security headers** | Defence in depth | `Content-Security-Policy` (allow jsdelivr and Google Fonts only), `X-Content-Type-Options: nosniff`, `Referrer-Policy` | 🟢 / ⭐⭐ |
| **Authentication if hosted** | It's single-user and local today | Entra ID (Azure AD) single sign-on behind a reverse proxy, or app-level login | 🟡 / ⭐⭐⭐ |
| **HTTPS** | Required once off-machine | Reverse proxy (Caddy, nginx or IIS) with certificates | 🟢 / ⭐⭐⭐ |
| **Rate limiting** | Protects the server and ESPN | Per-IP limits on `/api/*` | 🟢 / ⭐ |
| **Dependency scanning** | Known vulnerabilities | `pip-audit` in CI. Dependabot. | 🟢 / ⭐⭐ |
| **Secrets management** | Needed once paid APIs are added | Keys in environment variables or a key vault, never in `config.json` | 🟢 / ⭐⭐⭐ |

---

## Stage 12: Deployment and operations

| Improvement | Why | How | Effort / Impact |
|---|---|---|---|
| **Scheduled runs** | Daily reports with no human involved | Windows Task Scheduler (`.venv\Scripts\python.exe football_monitor.py`) or a Power Automate Desktop flow | 🟢 / ⭐⭐⭐ |
| **Run as a service** | Dashboard always available | NSSM (Windows service) or a `systemd` unit | 🟢 / ⭐⭐ |
| **Docker image** | Same environment everywhere | `python:3.12-slim` image, port 8080 exposed, database mounted as a volume | 🟢 / ⭐⭐ |
| **Production web server** | `http.server` is fine locally but basic | Move to FastAPI + Uvicorn (async, auto docs, validation) when hosting | 🟡 / ⭐⭐ |
| **Cloud hosting** | Team-wide access | Azure App Service / Container Apps + Azure SQL, scheduled jobs via Azure Functions | 🔴 / ⭐⭐⭐ |
| **Backups** | Protect the historical database | Nightly copy of `sports.db`, or managed backups on Azure SQL / Postgres | 🟢 / ⭐⭐⭐ |
| **Pin dependencies** | Reproducible installs | `requests==2.x.y` in `requirements.txt`, or a lock file | 🟢 / ⭐ |

---

## Stage 13: Documentation and knowledge sharing

| Improvement | Why | How | Effort / Impact |
|---|---|---|---|
| **Architecture diagram** | Quick orientation | Mermaid diagram in README: browser → `main.py` → `sports.py` / `oracle.py` → ESPN / database | 🟢 / ⭐⭐ |
| **API reference** | For integrators | Table of `/api/*` endpoints with parameters and example responses (automatic with FastAPI) | 🟢 / ⭐⭐ |
| **Runbook** | Support without the author | "If ESPN returns 400…", "If port 8080 is busy…", "How to rerun a failed report" | 🟢 / ⭐⭐ |
| **User guide with screenshots** | Onboarding editors | One page per tab, plus the Oracle | 🟢 / ⭐⭐ |
| **Model card** | Responsible AI practice | Purpose, data, metrics, limitations and intended use of the Oracle | 🟢 / ⭐⭐ |
| **Docstrings + type hints** | Maintainability | Type hints across `sports.py` and `oracle.py`, checked by mypy in CI | 🟡 / ⭐ |

---

## Suggested roadmap

| Phase | Focus | Key items | Outcome |
|---|---|---|---|
| **Now (week 1)** | Foundations | Git repo, log rotation, retry/back-off, pytest with recorded fixtures, CI | Safe to change |
| **Next (weeks 2–4)** | Data platform | SQLite schema, backfill script (10 seasons for priority leagues), nightly update | History available |
| **Then (weeks 4–6)** | Better Oracle | Elo + Dixon-Coles, walk-forward backtest, accuracy tracker in the UI, model card | Measured, trusted predictions |
| **Later (weeks 6–10)** | Reach | Scheduled delivery to Teams/email, match detail drawer, league tables, shareable URLs | Daily newsroom use |
| **Future** | Scale | FastAPI + Postgres/Azure SQL, sign-in, licensed data (incl. boxing), more sports | Team-wide production service |
