# Automation Worksheet — Sports Monitor

> Project: **SportPower / Sports Monitor**. It began as the *AI Assisted Scripting:
> Low-code Pathway* workshop exercise "Build a football match monitor automation".
> Owner: sports media team · Last updated: 29 October 2026
>
> This worksheet records **what the automation is, why it exists, what could go wrong,
> and everything built so far**, in the order it was built. Read it alongside `README.md`
> (how to use it) and `POTENTIAL_IMPROVEMENTS.md` (where it could go next).

---

## Contents

1. [Automation definition](#1-automation-definition)
2. [Inputs, processing and outputs](#2-inputs-processing-and-outputs)
3. [Business rules](#3-business-rules)
4. [Benefits](#4-benefits)
5. [Risks and mitigations](#5-risks-and-mitigations)
6. [Assumptions and constraints](#6-assumptions-and-constraints)
7. [Feature progress log (start → finish)](#7-feature-progress-log-start--finish)
8. [Feature status summary](#8-feature-status-summary)
9. [Testing and validation record](#9-testing-and-validation-record)
10. [Key decisions](#10-key-decisions)
11. [Known limitations](#11-known-limitations)
12. [Future features](#12-future-features)
13. [Deliverables checklist](#13-deliverables-checklist)

---

## 1. Automation definition

| Item | Detail |
|---|---|
| **Problem** | The sports media team checks fixtures, results and upcoming matches by hand across several websites and sports. This is slow and repetitive, and it's easy to miss a match or copy a score wrongly. |
| **Goal** | Retrieve fixtures and results automatically, apply the team's rules, produce a shareable report, keep an activity log, and offer a visual dashboard with match predictions. |
| **Users** | Sports journalists, social media producers and editors, plus any downstream system that reads the text reports. |
| **Suitable for automation?** | **Yes.** The data is structured (JSON), the rules are clear and repeatable, and the output format is fixed. |
| **Approach** | **Hybrid.** A Python script (`football_monitor.py`) for scheduled, unattended runs, plus a local web dashboard (`main.py`) for interactive use. Power Automate or Task Scheduler can run the script and distribute the reports. |
| **Data source** | ESPN's public (undocumented) scoreboard, standings and header APIs. |
| **Sports covered** | Football, rugby (union + NRL), Formula 1, cricket, combat sports (MMA: UFC, PFL, Bellator). |

---

## 2. Inputs, processing and outputs

### Inputs

| Input | Where it comes from | Example |
|---|---|---|
| Sport | `config.json` → `sport`, or the dashboard tabs | `football` |
| Competition / league | `config.json` → `league`, or the dashboard dropdown | `sco.1`, `270557`, `ufc` |
| Search filter (team / driver / fighter) | `config.json` → `team_filter`, or the search box | `Celtic` |
| Status filter | `config.json` → `match_status`, or the status tabs | `all`, `completed`, `scheduled`, `live` |
| Date | `config.json` → `date`, or the date picker | `20261025` (blank = latest) |
| Output file | `config.json` → `output_file`, or the Report tab | `football_report.txt` |
| Oracle sport + league | Sport Oracle sidebar (remembered per browser) | Football / English Premier League |

### Processing

1. Load and validate the configuration (`football_monitor.load_config` / `validate_config`).
2. Call the ESPN API for the chosen sport, competition and date (`sports.fetch_events`, `football_monitor.get_scoreboard`).
3. Turn each sport's JSON into one of three shapes: **match** (football, rugby, cricket), **race** (F1) or **card** (MMA).
4. Apply the business rules: search filter and status filter (`sports.filter_events`).
5. Write the text report (`sports.write_report`, `football_monitor.save_report`).
6. Log every step to `automation.log`.
7. *(Oracle)* Walk forward through the league calendar to find the next 10 fixtures, fetch standings, and calculate win probabilities (`oracle.forecast`).

### Outputs

| Output | Description |
|---|---|
| `football_report.txt` (or `<sport>_report.txt`) | Human- and machine-readable report with a header (sport, competition, date, filters, time generated) and one block per event. |
| `automation.log` | Timestamped INFO / WARNING / ERROR lines: script start, config loaded, sport and league selected, API requests, counts processed, file created, errors. Dashboard actions are tagged `[UI]`, predictions `[Oracle]`. |
| `test_results.txt` | Pass/fail table of the workshop test scenarios. |
| Web dashboard | http://localhost:8080: results, stats, reports, log viewer, Sport Oracle. |

---

## 3. Business rules

| # | Rule | Workshop option | Where it's implemented |
|---|---|---|---|
| BR1 | Only show events involving a selected team / driver / fighter (case-insensitive, partial match) | **A** | `filter_matches`, `sports.filter_events` |
| BR2 | Only show completed events (postponed matches are **not** completed) | **B** | `matches_status` (`completed` flag, not `state`) |
| BR3 | Only show scheduled (not yet started) events | **C** | `matches_status` (`state == "pre"`) |
| BR4 | Only show live events | **D** (own rule) | `matches_status` (`state == "in"`) |
| BR5 | For fight cards, a fighter search keeps only that fighter's bouts | **D** (own rule) | `sports.filter_events` |
| BR6 | If a team filter finds nothing, log a warning listing the valid team names | **D** (own rule) | `football_monitor.main` |
| BR7 | Predict a winner for each of the next 10 fixtures, with a confidence label | **D** (own rule) | `oracle.predict_match`, `oracle.predict_bout` |
| BR8 | Reports may only be written as plain `.txt` names inside the project folder (from the UI) | Safety | `main.safe_output_path` |

---

## 4. Benefits

| Benefit | Detail | Who gains |
|---|---|---|
| **Time saved** | One run (or one click) replaces manually checking several league sites. Covering five sports by hand would take far longer. | Journalists, producers |
| **Consistency** | Every report uses the same layout, rules and UTC timestamps, so there's no copy-paste drift. | Editors, downstream systems |
| **Accuracy** | Scores come straight from the data feed instead of being retyped. Postponed matches are handled correctly. | Everyone |
| **Configurable without code** | Change sport, league, team, status or date in `config.json` or the dashboard. | Non-developers |
| **Auditable** | `automation.log` shows what ran, when, with what settings, and what failed. | Support, management |
| **Shareable output** | Plain-text reports can be emailed, posted to Teams/Slack, or picked up by Power Automate. | Wider organisation |
| **Multi-sport coverage** | One tool for football, rugby, F1, cricket and MMA. | Multi-desk teams |
| **Live awareness** | Auto-refresh, live badges and match clocks for following games in progress. | Live desk |
| **Content ideas** | Sport Oracle picks and "why" comparisons feed preview articles and social posts. | Social, features |
| **Low running cost** | Python standard library plus `requests`, no API keys, runs on any laptop. | IT, budget holders |
| **Skills development** | Shows AI-assisted development, API use, testing, logging and documentation end to end. | Learners |

---

## 5. Risks and mitigations

Rating scale: **L**ikelihood and **I**mpact are each Low / Medium / High.

| # | Risk | L | I | Mitigation in place | Further mitigation (see improvements doc) |
|---|---|---|---|---|---|
| R1 | **ESPN API changes or disappears.** It's undocumented and unofficial. | M | H | All parsing uses defensive `.get()` calls. Errors are caught and logged, and the script exits with code 1. | Add a second data provider. Add contract tests that alert when ESPN's response format changes. |
| R2 | **Rate limiting / blocking** by ESPN | M | M | 10-second timeouts. The Oracle caches results for 10 minutes and caps calendar look-ups at 16 days. | Back-off and retry, a shared cache, scheduled pre-fetch. |
| R3 | **Terms of use / licensing** of ESPN data and team logos (hotlinked from ESPN's CDN) | M | H | Internal use only. Data is credited to ESPN in the footer. | Legal review before any external publication. A licensed data feed for production. |
| R4 | **Predictions misread as betting advice** | M | H | Labelled "For fun, not betting advice". The method is explained in the UI. Overconfidence is reduced by blending towards typical results. | Track and publish the model's accuracy. Add an editorial sign-off before using picks in published content. |
| R5 | **Prediction accuracy is weak**: current season only, no injuries or line-ups | H | M | Confidence labels. A "Why this pick?" breakdown. MMA is labelled as a weak signal. | A historical SQL database, a better model, backtesting (see improvements). |
| R6 | **Boxing not available** (ESPN dropped it) | H | L | Decision: show MMA as "Combat Sports" with an on-screen note. | A licensed boxing data provider. |
| R7 | **Wrong or late data from the source** | L | M | The report shows when it was generated. Logs record requests. | Cross-check against a second source. Staleness alerts. |
| R8 | **Local server exposed** | L | H | Binds to `127.0.0.1` only. POST requests need `Content-Type: application/json`, which blocks cross-site form posts. Output file names are restricted to plain `.txt` names. | Authentication if it's ever hosted, HTTPS, security headers. |
| R9 | **Log file grows forever** | H | L | Logs are appended and easy to delete. | Size- or time-based log rotation. |
| R10 | **Tests depend on live data**, so results vary | H | M | Tests check structure and behaviour, not exact scores. | Recorded fixtures/mocks, plus unit tests for parsers and the Oracle. |
| R11 | **Non-football sports and the Oracle have no automated tests** | H | M | Checked by hand against live data, with screenshots for every sport. | A pytest suite with saved JSON samples. |
| R12 | **CDN dependency** (Bootstrap, icons, fonts) | L | M | Pinned versions (Bootstrap 5.3.3, Icons 1.11.3). | Bundle assets locally for offline use. |
| R13 | **Time zone confusion** | M | L | Reports use UTC. The dashboard shows local time. | A configurable report time zone. |
| R14 | **Single point of knowledge** | M | M | README, this worksheet and code comments. | Handover session, CI pipeline, an owner rota. |
| R15 | **Port 8080 already in use** | M | L | A friendly error message instead of a crash. | A configurable port. |
| R16 | **Gambling harm**: the Bet view makes betting easier to reach | M | H | No bets are taken in the app. 18+ notice, BeGambleAware and National Gambling Helpline links. Odds shown neutrally (no "value bet" labels). | Editorial and compliance sign-off. An option to turn the Bet view off. Age-gating if hosted. |
| R17 | **Odds out of date or matched to the wrong fixture** | M | M | Retrieval time shown. "Check on the bookmaker's site" warning. Name matching with a 36-hour kick-off check and a 0.6 similarity threshold. | A team-alias table. Shorter cache before kick-off. |
| R18 | **Odds API key leaked**, or quota used up | L | M | Key kept in an env var or `odds_api.key`, not in config or the UI. 15-minute cache. Quota logged. Friendly 401/429 messages. | Secrets vault. Usage alerts. |
| R19 | **US-only bookmaker shown to UK users** (ESPN/DraftKings) | H | L | Rows tagged "US" with a note that these aren't available to UK customers. | Hide US rows once UK odds are configured. |

---

## 6. Assumptions and constraints

- The machine has internet access, plus Python 3.11+ and `requests` installed in `.venv`.
- ESPN's single-day `dates=YYYYMMDD` query is supported for every sport. Date **ranges** only work for F1 and MMA.
- Football league codes follow `country.division` (e.g. `sco.1`); UEFA competitions use names (`uefa.champions`).
- Rugby competitions use numeric ESPN IDs; NRL uses the separate `rugby-league/3` path.
- Cricket uses ESPN's "header" feed (all current matches worldwide) rather than a per-league scoreboard.
- The dashboard is single-user and local. No authentication is needed while it stays on `127.0.0.1`.

---

## 7. Feature progress log (start → finish)

Each phase lists what was built, the files touched, how it was checked, and issues found
and fixed along the way.

### Phase 0: Investigation and setup

| Step | Detail |
|---|---|
| 0.1 | Inspected the PyCharm project: empty apart from the sample `main.py` and a `.venv`. |
| 0.2 | Installed `requests` into `.venv` (it wasn't available). |
| 0.3 | **Probed the ESPN API before writing the parser.** Found: unknown league codes return **400**, `dates=YYYYMMDD` works, date **ranges fail** for `sco.1`, and **postponed matches are `state: post` but `completed: false`**, so the "completed" rule must use the `completed` flag. |

### Phase 1: Football Match Monitor (workshop Parts 1–8)

| # | Feature | Workshop part | Files | Status |
|---|---|---|---|---|
| 1.1 | Automation definition (inputs, processing, outputs, rules) | Part 1 | `README.md` → now this worksheet | ✅ Done |
| 1.2 | Call the ESPN scoreboard API with timeout and error handling | Part 2 | `football_monitor.py` → `get_scoreboard` | ✅ Done |
| 1.3 | Extract home/away team, score, status, kickoff, venue | Part 3 | `extract_matches`, `format_match` | ✅ Done |
| 1.4 | Readable output, e.g. `Hibernian 1 - 2 Celtic / Status: Full Time` | Part 3 | `format_match` | ✅ Done |
| 1.5 | Business rules: team filter (A), completed (B), scheduled (C), live (D) | Part 4 | `filter_matches` | ✅ Done |
| 1.6 | `config.json` with `league`, `team_filter`, `output_file`, plus `match_status` and `date` | Part 5 | `config.json`, `load_config` | ✅ Done |
| 1.7 | Config validation: missing file, invalid JSON, bad league, bad status, bad date | Parts 5–6 | `load_config` | ✅ Done |
| 1.8 | Test harness for 8 scenarios writing `test_results.txt` | Part 6 | `run_tests.py` | ✅ 8/8 pass |
| 1.9 | Logging: start, league, counts, file created, errors | Part 7 | `automation.log` | ✅ Done |
| 1.10 | Warning listing valid team names when a filter matches nothing | Part 7 (extra) | `main()` | ✅ Done |
| 1.11 | Exit codes (0 = success, 1 = failure) for schedulers | Part 7 (extra) | `main()` | ✅ Done |
| 1.12 | Command-line config path (`python football_monitor.py other.json`) | Extra | `__main__` | ✅ Done |
| 1.13 | Documentation: purpose, config, dependencies, inputs, outputs, limitations | Part 8 | `README.md`, `requirements.txt` | ✅ Done |

*Issue fixed:* the test results table columns overlapped, so the Expected column was widened.

### Phase 2: Web dashboard on localhost:8080

| # | Feature | Files | Status |
|---|---|---|---|
| 2.1 | Standard-library HTTP server serving `web/` plus a JSON API | `server.py` → **moved into `main.py`** at the user's request | ✅ Done |
| 2.2 | API: `/api/config` (GET/POST), `/api/matches`, `/api/report`, `/api/log` | `main.py` | ✅ Done (later expanded) |
| 2.3 | Bootstrap 5.3.3 dashboard with a custom design system (Inter font, design tokens) | `web/index.html`, `web/css/styles.css` | ✅ Done |
| 2.4 | League dropdown + custom code, date stepper, "Matchday" button, team search with suggestions, status tabs | `web/js/app.js` | ✅ Done |
| 2.5 | Stat tiles: matches, live, completed, upcoming, goals | `app.js` | ✅ Done |
| 2.6 | Match cards: crests, club-colour accent bar, score/kick-off, countdown, form dots, record, goal scorers, own goals and penalties, red cards | `app.js`, `styles.css` | ✅ Done |
| 2.7 | Report tab: generate, preview, copy, download, save as default config | `app.js`, `main.py` | ✅ Done |
| 2.8 | Activity log tab with level filters and colour coding | `app.js`, `main.py` | ✅ Done |
| 2.9 | Auto-refresh (60 s countdown), toasts, loading skeletons, empty and error states, keyboard shortcuts (`/`, `R`) | `app.js` | ✅ Done |
| 2.10 | Security: bind to 127.0.0.1, JSON-only POSTs, safe output names, path-traversal check, HTML escaping | `main.py`, `app.js` | ✅ Done |
| 2.11 | Browser opens automatically. Friendly error if port 8080 is busy. | `main.py` | ✅ Done |
| 2.12 | Log path made absolute so `automation.log` always lands in the project folder | `football_monitor.py` | ✅ Done |
| 2.13 | Config validation split out (`validate_config`) so the UI reuses the same rules | `football_monitor.py` | ✅ Done |

*Checks:* every endpoint smoke-tested, including path traversal (blocked) and cross-site
plain-text POSTs (rejected). Headless Chrome screenshots taken at desktop and phone width.
An apparent mobile overflow turned out to be headless Chrome's ~500 px minimum window
width, confirmed with a 520 px capture.

### Phase 3: Light / dark mode

| # | Feature | Status |
|---|---|---|
| 3.1 | Light, Dark and System (follows Windows) theme switch in the top bar | ✅ Done |
| 3.2 | Theme applied before the page draws (no flash). Choice remembered. Smooth colour fade. `T` shortcut. | ✅ Done |
| 3.3 | Live OS theme changes followed while in System mode | ✅ Done |

*Issue fixed:* the third option was first called "Auto", which clashed with the
auto-refresh switch beside it, so it was renamed "System".

### Phase 4: Multi-sport (rugby, F1, cricket, combat sports)

| # | Feature | Files | Status |
|---|---|---|---|
| 4.1 | Researched ESPN coverage for every sport. Rugby, F1 and cricket confirmed. **Boxing unavailable** (ESPN: "Invalid sport (boxing)"; TheSportsDB free key covers soccer and motorsport only). | — | ✅ Done |
| 4.2 | **Decision (user):** replace boxing with "Combat Sports" using ESPN MMA (UFC, PFL, Bellator), with an on-screen note | `sports.py` | ✅ Done |
| 4.3 | Multi-sport data layer normalising events into match, race and card shapes | `sports.py` (new) | ✅ Done |
| 4.4 | Football: 14 competitions, including Champions League, Europa League and FA Cup (league-code check widened for `uefa.*`) | `sports.py` | ✅ Done |
| 4.5 | Rugby: 10 union competitions + NRL, with try scorers | `sports.py` | ✅ Done |
| 4.6 | F1: race weekends → sessions → finishing order with flags | `sports.py` | ✅ Done |
| 4.7 | Cricket: all current matches worldwide, innings scores, live situation, competition filter built from the feed | `sports.py` | ✅ Done |
| 4.8 | Combat: fight cards with weight class, records, winner, round and time | `sports.py` | ✅ Done |
| 4.9 | `sport` setting in `config.json`. The command line runs any sport, and football keeps its workshop code path. | `football_monitor.py` | ✅ Done |
| 4.10 | API reworked: `/api/sports`, `/api/events`, sport-aware `/api/report` and `/api/config` | `main.py` | ✅ Done |
| 4.11 | UI: sport tabs (`1`–`5` shortcuts), per-sport competition lists, Team/Driver/Fighter search, per-sport stat tiles, F1 cards with session tabs and podium, cricket scorecards, fight cards with "show all bouts" | `index.html`, `app.js`, `styles.css` | ✅ Done |
| 4.12 | Sport-aware text reports for every sport | `sports.write_report` | ✅ Done |

*Issues fixed:* cricket scores such as "184 & 117/5" read badly in the "A 1 - 2 B"
layout (now "A score v B score"). ESPN calls the NRL just "Rugby League", so our own
names are used for rugby. Cricket overs text showed a mangled `88 ov) (f/o`, now
`88 ov · f/o`. The subtitle said "1 grands prix", now "1 Grand Prix".

### Phase 5: Sport Oracle (predictions sidebar)

| # | Feature | Files | Status |
|---|---|---|---|
| 5.1 | Research: ESPN rejects date ranges for football and rugby, but league **calendars** list match dates. Standings endpoints exist. Bookmaker odds are deliberately not used. | — | ✅ Done |
| 5.2 | "Next 10 fixtures": walks forward through calendar dates, 4 in parallel, skipping days already covered | `oracle.py` (new) | ✅ Done |
| 5.3 | Standings loader for football and rugby tables (different stat names handled) | `oracle.py` | ✅ Done |
| 5.4 | Team model: 45% win rate (shrunk towards average early in the season) + 35% form + 20% margin + home advantage, then logistic probabilities | `oracle.py` | ✅ Done |
| 5.5 | **Calibration:** first version gave 86% for one football side and a 4% draw chance. Added a 15% blend towards each sport's typical result split, a minimum draw chance, and a gentler curve for football. Now football tops out at ~63–73% with 14–27% draws. | `oracle.py` | ✅ Done |
| 5.6 | MMA model from career records (25% blend towards 50/50, labelled a weak signal) | `oracle.py` | ✅ Done |
| 5.7 | 10-minute cache, plus a `fresh=1` bypass for the refresh button | `oracle.py`, `main.py` | ✅ Done |
| 5.8 | `/api/oracle` endpoint with validation (rejects F1 and cricket with a clear message) | `main.py` | ✅ Done |
| 5.9 | Right sidebar at ≥1200 px, slide-out drawer below that (🔮 button or `O`). Independent sport/league selector, remembered per browser. | `index.html`, `app.js`, `styles.css` | ✅ Done |
| 5.10 | Prediction cards: percentages, three-part probability bar, ★ pick, Strong/Lean/Toss-up, "Why this pick?" comparison table, "How the Oracle predicts" explainer and disclaimer | `app.js`, `styles.css` | ✅ Done |
| 5.11 | Page widened to fit the sidebar (`container-wide`, filters in a 2×2 grid beside it) | `index.html`, `styles.css` | ✅ Done |

*Issues fixed:* long fight-card names pushed the combat cards wider than the sidebar
(fixed with a constrained grid column and `min-width: 0`). The card name was repeated on
every bout (now shown once as a heading). The NRL off-season returns no fixtures, so a
friendly empty state was added.

### Phase 7: Match detail modal and Bet view

| # | Feature | Files | Status |
|---|---|---|---|
| 7.1 | Research: ESPN's match `summary` endpoint has last-5 form, head-to-head, standings, broadcasts and news. Its odds are **DraftKings only (US)** for football. The "Bet 365" entry is always empty. Rugby and UFC have no odds. | — | ✅ Done |
| 7.2 | **Decision (user):** add The Odds API for UK bookmakers, with ESPN as the fallback. Key is kept in `ODDS_API_KEY` or `odds_api.key`, never in `config.json`. | `match_centre.py` | ✅ Done |
| 7.3 | **Decision (user):** the app must **not** take or place bets. It only lists where to bet, with prices and links to bookmakers' own sites. | `app.js` | ✅ Done |
| 7.4 | Match detail: venue, last 5 results (scores from the team's own side), head-to-head (football and rugby formats), league table (falls back to the standings feed for rugby), broadcasts, news | `match_centre.py` | ✅ Done |
| 7.5 | Odds: Odds API sport-key mapping (football fixed list, rugby union by title, NRL, MMA), fuzzy team-name matching with a kick-off time check, home/away swap handling, US→decimal conversion, best price per result, average implied chance with margin removed | `match_centre.py` | ✅ Done |
| 7.6 | API: `/api/match`, `/api/odds` with validation (sport, league, numeric event ID) | `main.py` | ✅ Done |
| 7.7 | Clickable Oracle cards (mouse and keyboard). Bootstrap modal with hero, prediction, form, head-to-head, table, broadcasts, news. Full-screen on phones. | `index.html`, `app.js`, `styles.css` | ✅ Done |
| 7.8 | Bet view: bookmaker table, fractional/decimal toggle (remembered), best-price highlight, Visit site links (new tab, `noopener`), Oracle vs bookmaker comparison, setup notice when no key, 18+ / BeGambleAware / National Gambling Helpline note | `app.js`, `styles.css` | ✅ Done |
| 7.9 | Caching: summaries 5 min, odds 15 min (protects the 500/month free quota), Odds API sports list 12 h. Remaining quota logged. | `match_centre.py` | ✅ Done |

*Issues fixed:*
- ESPN's last-5 scores are in home-away order, so an away defeat read "3-0 L". Scores are now shown from the team's side.
- The rugby summary has no table, so the module falls back to the standings feed.
- With one bookmaker, every price was marked "best", so the highlight now only appears with two or more.
- The MMA Bet heading said "win/draw/win" and now says "fight winner".
- The Oracle hint text wrapped awkwardly beside the legend, so it now has its own line.

### Phase 8: Game detail modal from the results list

| # | Feature | Files | Status |
|---|---|---|---|
| 8.1 | Research: ESPN summaries have stats, key events, line-ups and attendance for football. Rugby has line-ups only. Cricket has scorecards and notes. F1 and MMA return 404. | — | ✅ Done |
| 8.2 | `game_detail()`: football/rugby stats, key events, line-ups (rugby starters from shirt numbers 1–15), attendance. Cricket notes and scorecards. Substitution on/off worked out from the line-ups. | `match_centre.py` | ✅ Done |
| 8.3 | `/api/game` endpoint with validation. Cricket events now carry their competition ID (`league_id`). | `main.py`, `sports.py` | ✅ Done |
| 8.4 | Every results card is clickable (mouse and keyboard) for all 5 sports. F1 session tabs and "show all" buttons keep their own behaviour. | `app.js` | ✅ Done |
| 8.5 | Results modal views: football/rugby (timeline, stat bars, line-ups, H2H, table, news), cricket (scorecards, notes), F1 (all sessions, full order), MMA (full card) | `app.js`, `styles.css` | ✅ Done |
| 8.6 | Shared form, head-to-head, table, broadcast and news sections pulled out of the Oracle modal so both modals use one copy | `app.js` | ✅ Done |

*Checks:* `/api/game` tested (valid, F1 returns "not available", bad ID → 400, bad sport → 400).
Screenshots of the results modal for all 5 sports with live data. 8/8 regression tests still pass.

### Phase 9: Formula 1 in the Sport Oracle

| # | Feature | Files | Status |
|---|---|---|---|
| 9.1 | Research: ESPN's F1 standings list every driver's points in each race this season, and the scoreboard accepts a date range for all remaining Grands Prix in one request. Qualifying competitor IDs match the standings' driver IDs. | — | ✅ Done |
| 9.2 | `predict_race()`: rating = 55% season points per race + 45% last 5 races (40% grid once qualifying is done), 5,000 seeded Plackett-Luce simulations for win and podium chances, blended 10% towards an open race. Drivers who missed the latest round are left out. | `oracle.py` | ✅ Done |
| 9.3 | F1 added to the Oracle sports (`"oracle": True`), so `/api/oracle` accepts it | `sports.py` | ✅ Done |
| 9.4 | Oracle race cards (top 3 with win bars, pick, podium chance, "Why this pick?") and a race modal (every driver's chances, grid, points per race, last 5, championship, weekend schedule). No Bet view for F1. | `app.js`, `styles.css`, `index.html` | ✅ Done |

*Checks:* Live forecast for the 5 remaining 2026 races (about 1 s). Grid path tested with the US GP
qualifying. Football and UFC forecasts unchanged. Headless Chrome screenshots of the sidebar and race modal.

### Phase 10: Cricket in the Sport Oracle

| # | Feature | Files | Status |
|---|---|---|---|
| 10.1 | Research: the cricket header feed accepts `dates=YYYYMMDD` (one day at a time). Competition tables exist at `/apis/v2/sports/cricket/{id}/standings` for leagues but not bilateral tours. Team IDs match between the two. Each match carries its format (`class.generalClassCard`). | — | ✅ Done |
| 10.2 | Upcoming matches (14 days ahead, TBA knockout slots skipped) and team form (last 21 days, any competition). Day feeds are cached, with older days kept for 6 hours. | `oracle.py` | ✅ Done |
| 10.3 | Cricket model: 60% table win rate + 40% form (shrunk for small samples), home advantage, draws only in Tests and first-class matches | `oracle.py` | ✅ Done |
| 10.4 | Standings parser reads cricket's `matchesWon/Lost/Draw/Tied`. The team rating skips scoring margin when a table has no points for/against. | `oracle.py` | ✅ Done |
| 10.5 | Oracle UI: cricket tab, competition headings, format tag, competition list built from the feed, match modal with recent results, match notes and news. No Bet view. | `app.js`, `styles.css`, `index.html` | ✅ Done |
| 10.6 | Fixes: Oracle tabs stack icon over label so five sports fit; group headings no longer collapse to zero height; main sport tabs wrap instead of being cut off; dashboard files are versioned and sent with `Cache-Control: no-cache` | `styles.css`, `index.html`, `main.py` | ✅ Done |

*Checks:* Live forecast across 7 competitions (about 6 s cold, under 1 s cached). Single competition
(Ranji Trophy) returns 10 matches. `/api/game` for an upcoming Test returns the venue, notes and news.
Football and rugby forecasts unchanged. Headless Chrome screenshots of the sidebar and match modal.

### Phase 6: Documentation

| # | Item | Status |
|---|---|---|
| 6.1 | `README.md` rewritten for multi-sport, then extended for the Oracle | ✅ Done |
| 6.2 | `AUTOMATION_WORKSHEET.md` (this document) | ✅ Done |
| 6.3 | `POTENTIAL_IMPROVEMENTS.md` | ✅ Done |

---

## 8. Feature status summary

| Area | Features | Done | Partial | Not started |
|---|---|---|---|---|
| Core automation (CLI) | 13 | 13 | 0 | 0 |
| Web dashboard | 13 | 13 | 0 | 0 |
| Theming | 3 | 3 | 0 | 0 |
| Multi-sport | 12 | 11 | 1 (boxing → MMA substitute) | 0 |
| Sport Oracle | 11 | 11 | 0 | 0 |
| Match modal and Bet view | 9 | 8 | 1 (UK odds path needs a user key to verify live) | 0 |
| Automated tests | Football path | ✅ | Other sports and Oracle: manual only | Unit tests and mocks |
| Documentation | 3 | 3 | 0 | 0 |

### Code size (approximate lines)

| File | Lines | Role |
|---|---|---|
| `football_monitor.py` | 315 | CLI automation, config validation |
| `sports.py` | 522 | Multi-sport data layer |
| `oracle.py` | 305 | Predictions |
| `main.py` | 209 | Web server and API |
| `run_tests.py` | 161 | Test harness |
| `web/js/app.js` | 1,119 | Dashboard logic |
| `web/css/styles.css` | 1,261 | Design system and layout |
| `web/index.html` | 260 | Page structure |

---

## 9. Testing and validation record

### Automated (workshop Part 6): `python run_tests.py`

| Test | Expected | Result |
|---|---|---|
| Scottish league (sco.1) | Report lists Scottish Premiership matches | **Pass** |
| English league (eng.1) | Report lists Premier League matches | **Pass** |
| Valid team (Celtic) | Only Celtic matches in report | **Pass** |
| Invalid team | "No matches found", no crash | **Pass** |
| Completed matches only | Only completed matches | **Pass** |
| Missing configuration file | Friendly error, exit code 1 | **Pass** |
| Invalid league code | API error handled, exit code 1 | **Pass** |
| Invalid config value | Validation rejects it | **Pass** |

**Workshop checklist:** API returned data ✅ · Report created ✅ · Team filter worked ✅ · Configuration loaded ✅

Re-run after every major change (Phases 2, 4 and 5): still **8/8**.

### Manual / smoke tests

| Area | Check | Result |
|---|---|---|
| API | `/api/events` for every sport, plus invalid sport (400) and invalid league (400) | ✅ |
| API | `/api/report` for combat (writes `combat_report.txt`) | ✅ |
| API | `/api/oracle` for EPL, NRL (0 fixtures, off-season), F1 (400), bad league (400), UFC `fresh=1` | ✅ |
| Security | Path traversal `/../config.json` → 404. Plain-text POST → 400. `../evil.txt` output → 400. | ✅ |
| CLI | `sport: f1` config → exit 0, report written | ✅ |
| UI | Headless Chrome screenshots: football, rugby, F1, cricket, combat; light and dark; 1440 / 1920 / 520 px; Oracle sidebar and mobile drawer | ✅ |
| Modal | `/api/match` (valid, bad ID → 400, F1 → 400). `/api/odds` for EPL returns DraftKings 1.83 / 3.95 / 4.0. Screenshots of the football and rugby detail views and the football and MMA Bet views. | ✅ |
| Odds API logic | Fixture matching (straight, swapped, wrong day rejected), name similarity (Bournemouth, Brighton "&", Atlético accents), price parsing, all against sample data in the documented v4 format | ✅ (live UK odds need a key) |
| Oracle | Calibration check across EPL, SPFL, URC and UFC (max pick %, draw range, confidence mix) | ✅ |

---

## 10. Key decisions

| Decision | Options considered | Chosen | Why |
|---|---|---|---|
| Data source | ESPN, TheSportsDB, paid APIs | ESPN public APIs | Free, no key, broad coverage, rich data |
| "Completed" rule | `state == post` vs the `completed` flag | `completed` flag | Postponed matches are `post` but not completed |
| Web stack | Flask / FastAPI vs standard library | Standard library `http.server` | No extra dependencies, enough for local use |
| Entry point | `server.py` vs `main.py` | `main.py` | User request |
| Boxing | Paid API, skip, or MMA substitute | MMA as "Combat Sports" | User choice. Works today, clearly labelled. |
| Cricket feed | Per-league scoreboards vs the global header feed | Header feed + competition filter | One call covers all current matches, with richer summaries |
| Prediction basis | Bookmaker odds vs our own model | Own transparent model | Odds aren't available everywhere, and a gambling-based source isn't appropriate |
| Oracle sports | All five vs head-to-head only | Football, rugby, MMA | F1 has no head-to-head matches. Cricket has no form or standings. |
| Status filtering in the UI | Server-side vs browser-side | Browser-side, mirroring the server rules | Status tabs switch instantly. Reports still use the server's rules. |
| Odds source | ESPN only vs The Odds API + ESPN | The Odds API + ESPN fallback | User choice. ESPN only has one US bookmaker, for football only. |
| Betting in the app | Place bets vs link out | Link out only | User requirement. The app never handles money or accounts. |
| Odds API key storage | `config.json` vs env var / key file | Env var or `odds_api.key` | "Save as default config" rewrites `config.json`, and secrets shouldn't sit in config |

---

## 11. Known limitations

See also the README.

- Unofficial API: it can change without notice.
- Boxing not supported. MMA results don't include how the fight ended (KO, submission, decision).
- F1 has finishing order only (no times, gaps or teams).
- One day per query for football, rugby and cricket.
- The Oracle uses current-season data only, so it knows nothing about injuries, line-ups or head-to-head history.
- Automated tests cover the football path only.
- The log file is never rotated. Reports are overwritten on each run.
- Single-user local server with no authentication.

---

## 12. Future features

Priorities: **P1** next, **P2** soon, **P3** later.

| Priority | Feature | Value |
|---|---|---|
| P1 | Historical results database (SQL) to train and backtest the Oracle | Much better predictions, measurable accuracy |
| P1 | Oracle accuracy tracker: record each pick, compare with the actual result, show hit rate and Brier score | Trust and transparency |
| P1 | Unit tests with recorded JSON for every sport and the Oracle, run in CI | Fewer regressions |
| P2 | Scheduled runs with Power Automate / Task Scheduler and email or Teams delivery of reports | Hands-free daily briefings |
| P2 | Favourite teams and alerts (kick-off, goal, final score) | Personalised newsroom feed |
| P2 | League tables / standings view in the dashboard | Context for coverage |
| P2 | Export to CSV, JSON and PDF | Easier reuse |
| P2 | Head-to-head history and player-availability inputs for the Oracle | Better picks |
| P3 | Boxing via a licensed provider | Complete combat coverage |
| P3 | More sports (tennis, NFL, NBA, golf) via ESPN | Wider coverage |
| P3 | Multi-user hosting with sign-in | Team-wide access |
| P3 | Match detail pages (line-ups, stats, timeline) | Deeper coverage |
| P3 | Social card generator (image of a score or prediction) | Faster publishing |

---

## 13. Deliverables checklist

| Deliverable | File | Confirm |
|---|---|---|
| Main automation script | `football_monitor.py` | ✅ y |
| Configuration settings | `config.json` | ✅ y |
| Generated report | `football_report.txt` | ✅ y |
| Execution log | `automation.log` | ✅ y |
| Test results | `test_results.txt` | ✅ y |
| Support documentation | `README.md` | ✅ y |
| *Extra:* web dashboard | `main.py`, `web/` | ✅ y |
| *Extra:* multi-sport layer | `sports.py` | ✅ y |
| *Extra:* Sport Oracle | `oracle.py` | ✅ y |
| *Extra:* match detail and bookmaker odds | `match_centre.py` | ✅ y |
| *Extra:* automation worksheet | `AUTOMATION_WORKSHEET.md` | ✅ y |
| *Extra:* improvement plan | `POTENTIAL_IMPROVEMENTS.md` | ✅ y |

### Workshop success criteria

- ✅ Retrieves sports data from the ESPN API
- ✅ Applies at least one business rule (eight are implemented)
- ✅ Uses a configuration file
- ✅ Generates an output report
- ✅ Records activity in a log file
- ✅ Tested and validated
- ✅ Includes supporting documentation
