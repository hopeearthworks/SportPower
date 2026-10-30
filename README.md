# Sports Monitor

## Purpose

A Python automation for a sports media team. It started as the workshop's Football Match
Monitor and now covers **football, rugby, Formula 1, cricket and combat sports (MMA)**.

It retrieves fixtures and results from ESPN's public APIs, applies business rules (a
team/driver/fighter search and a status filter), writes a plain-text report that other
systems can pick up, and logs every run to `automation.log`. All settings live in
`config.json`, so you can change its behaviour without editing code.

## Automation definition

| Area | Detail |
|---|---|
| **Problem** | Staff manually check sports websites to track fixtures and results. |
| **Inputs** | Sport, competition, search term, match status, optional date, output file name (all from `config.json` or the dashboard). |
| **Processing** | Call the ESPN API → extract teams/drivers/fighters, scores, status, start time, venue → apply filters → build report. |
| **Outputs** | A text report (e.g. `football_report.txt`), `automation.log` (execution log). |
| **Business rules** | Only events involving the selected team, driver or fighter (Option A); optionally only completed, scheduled, or live events (Options B, C, D). |
| **Approach** | Python script plus a local web dashboard. The script can be scheduled (Task Scheduler / Power Automate) and the report shared onwards. |

## Sports and competitions

| Sport | `sport` | `league` values | What you see |
|---|---|---|---|
| Football | `football` | Any ESPN code, e.g. `sco.1`, `eng.1`, `esp.1`, `uefa.champions` (first three letters of the country + division) | Scores, crests, goal scorers, red cards, form, season record |
| Rugby | `rugby` | `267979` Premiership, `270557` URC, `270559` Top 14, `271937` Champions Cup, `272073` Challenge Cup, `180659` Six Nations, `244293` Rugby Championship, `242041` Super Rugby, `164205` World Cup, `289234` Internationals, `nrl` NRL (league) | Scores, crests, try scorers, form |
| Formula 1 | `f1` | `f1` | Each Grand Prix with Practice, Qualifying and Race: podium and finishing order with nationality |
| Cricket | `cricket` | `all` or an ESPN competition number (the dashboard lists those currently in the feed) | Innings scores and overs, live situation ("Australia lead by 82 runs"), results |
| Combat Sports | `combat` | `ufc`, `pfl`, `bellator` | Fight cards: weight class, records, winner, round and time |

> **Boxing:** ESPN no longer publishes boxing data, and no free keyless source was
> available, so Combat Sports shows MMA instead. The dashboard says this on screen.

## Dependencies

- Python 3.11+
- `requests` (see `requirements.txt`)

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Configuration settings (`config.json`)

```json
{
    "sport": "football",
    "league": "sco.1",
    "team_filter": "Celtic",
    "output_file": "football_report.txt",
    "match_status": "all",
    "date": "20261025"
}
```

| Setting | Required | Description |
|---|---|---|
| `sport` | No | `football` (default), `rugby`, `f1`, `cricket` or `combat`. |
| `league` | Yes | The competition. See the table above for valid values per sport. |
| `team_filter` | No | Search term: a team (football, rugby, cricket), driver (F1) or fighter (combat). Case-insensitive partial match. Leave blank (`""`) for everything. |
| `output_file` | Yes | Name/path of the report file to create. |
| `match_status` | No | `all` (default), `completed`, `scheduled` (not yet started) or `live` (in progress). |
| `date` | No | A single day in `YYYYMMDD` format. Leave blank for the latest events ESPN returns by default. |

## How to run

### Web dashboard (main entry point)

```powershell
python main.py
```

This starts a local server at **http://localhost:8080** and opens it in your browser
(stop it with Ctrl+C). The dashboard lets you:

- **Pick a sport** from the tabs at the top (or press `1`–`5`), then a competition
- Step through dates, or press **Latest** for ESPN's current events
- Search by team, driver or fighter (with suggestions from the feed) and filter by status: All / Live / Final / Upcoming
- See cards designed for each sport:
  - **Football / rugby:** crests, scores, scorers and form
  - **Cricket:** innings scorecards with the match situation
  - **F1:** session tabs with a podium and finishing order
  - **Combat:** the full fight card with winners
- See summary tiles for each sport (goals, points, sessions completed, competitions, bouts)
- Turn on auto-refresh every 60 seconds to follow live events
- **Generate report**: writes a `.txt` report for the current sport and filters
- **Save as default config**: writes the current selections to `config.json`
- View, filter and refresh `automation.log` in the Activity log tab
- Switch theme with the **Light / Dark / System** control (or press `T`)

Other shortcuts: `/` focuses the search box and `R` refreshes.

### Sport Oracle (right sidebar)

On screens 1200px and wider the **Sport Oracle** sits on the right. On smaller screens
open it with the 🔮 button in the top bar (or press `O`). Choose a sport (Football, Rugby,
F1, Cricket or Combat) and a competition. For the team sports and MMA it shows the **next 10 fixtures** with
(F1 and cricket are described [below](#formula-1-in-the-sport-oracle)):

- home / draw / away win chances as percentages and a bar
- the Oracle's pick (★) and a confidence label: **Strong** (≥60%), **Lean** (≥45%) or **Toss-up**
- **Why this pick?**: a side-by-side comparison of league position, record, scoring margin, form and rating

How it predicts (see `oracle.py`):
- **Team sports:** each team's rating is 45% season win rate, 35% last-5 form and 20% average scoring margin.
- **Home advantage:** a bonus is added for the home side.
- **Probabilities:** the rating gap becomes win/draw/loss chances, blended 15% towards the sport's typical result split so early-season data can't make it overconfident.
- **MMA:** picks use career records only, blended 25% towards 50/50.

Results are cached for 10 minutes, and the ↻ button forces a refresh. Predictions are for
fun, not betting advice. The model knows nothing about injuries, line-ups or travel.

### Game details from the results list

**Click any result card** (or focus it and press Enter) for a detailed view of that game:

| Sport | What the modal shows |
|---|---|
| Football | Score and status, venue and attendance, match events timeline (goals, own goals, cards, substitutions with who came on and off), team stats with comparison bars, line-ups with formations and substitutes, head-to-head, league table, news. Upcoming games also show recent form. |
| Rugby | Score, try and red-card timeline, starting XVs and replacements, head-to-head, league table, news |
| Cricket | Innings scores and result, match notes (toss, match days, hours of play, points), batting/bowling/partnership scorecards for the latest innings, news |
| Formula 1 | Every session of the weekend with the **full** finishing order (all drivers, flags, nationality) |
| Combat Sports | The **whole** fight card: weight class, records, winners, round and time, scheduled rounds |

ESPN has no detail feed for F1 or MMA, so those two views use the full results already loaded.

### Formula 1 in the Sport Oracle

Choose **F1** in the Oracle to see every Grand Prix still to run this season. Each card shows
the three favourites with their chance to win, the Oracle's pick and its podium chance.
**Click a race** for every driver's win and podium chances, their points per race, last 5
results, championship position and the weekend schedule in your local time.

How it predicts:
- **Driver rating:** 55% season championship points per race (shrunk towards the field average
  early in the season) and 45% average points over the last 5 races.
- **Starting grid:** once qualifying has finished, the grid counts for 40% of the rating
  (pole = 25, 2nd = 18 … 10th = 1).
- **Simulation:** each race is simulated 5,000 times to get win and podium chances, blended 10%
  towards every driver having an equal chance. Confidence is **Strong** (≥40% to win),
  **Lean** (≥25%) or **Toss-up**.
- Drivers who missed the latest race are left out, as they've usually lost their seat.

Until qualifying, every remaining race gets the same prediction, because the model knows
nothing about individual circuits, car upgrades, penalties or weather. F1 has no Bet button.

### Cricket in the Sport Oracle

Choose **Cricket** in the Oracle to see the next 10 matches across every competition in ESPN's
feed (looking up to 14 days ahead), grouped by competition, with the format (Test, First-class,
List A, T20 …) next to the start time. Once the matches have loaded, the competition list also
offers each competition on its own. **Click a match** for the prediction, each team's last 5 results,
match notes and news.

How it predicts:
- **Season record (60%):** win rate from the competition's table, where ESPN publishes one.
  Bilateral tours have no table.
- **Form (40%):** results from the last 3 weeks in any competition, shrunk towards average
  because a team often has only one or two results.
- **Home advantage:** a bonus for the home side.
- **Draws:** only Tests and first-class (multi-day) matches can be drawn. Limited-overs matches
  show home and away chances only.
- When neither team has a table or recent results, the card says so and the pick is mostly
  home advantage.

ESPN's cricket feed has one request per day, so the first load takes about 6 seconds while
three weeks of results are read. After that, it's cached. Cricket has no Bet button.

### Match details and the Bet button

**Click any match in the Sport Oracle** to open a detailed view:

- kick-off time, countdown and venue
- the Oracle's prediction with the full side-by-side comparison
- each team's **last 5 results** (from the team's own point of view, e.g. "L 0-3 at Brentford")
- **head-to-head** meetings, the **league table** with both teams highlighted, where to watch, and the latest ESPN news

Press **Bet** to see **bookmaker odds** for the match:

- a table of bookmakers with home / draw / away prices, in **fractional** (e.g. 5/6) or **decimal** (1.83) format
- the best price for each result highlighted when several bookmakers are listed
- a **Visit site** link to each bookmaker
- the bookmakers' average implied chance (margin removed) next to the Oracle's prediction

**Sports Monitor never takes or places bets.** Any bet is made on the bookmaker's own
website. The view shows an 18+ notice and links to BeGambleAware.org.

#### Bookmaker odds: adding a free The Odds API key

Without a key, the Bet view shows ESPN's odds only, which come from **DraftKings, a US
bookmaker that doesn't serve UK customers**. These are only available for football. To
compare **UK bookmakers** (Bet365, William Hill, Paddy Power, Sky Bet, Betfair and others)
for football, rugby and MMA:

1. Sign up for a free key at https://the-odds-api.com (the free tier allows 500 requests a month).
2. Give the key to Sports Monitor in **one** of these ways:
   - **Environment variable:** run `setx ODDS_API_KEY "your-key-here"`, then open a new terminal, or
   - **Key file:** create a file called `odds_api.key` in the project folder containing only the key.
3. Restart `python main.py`.

Keep the key private: don't share or commit `odds_api.key`. Odds are cached for
15 minutes per competition, so one competition costs at most about 4 requests an hour
while you're using it. The number of requests left this month is written to `automation.log`.

The server only listens on `127.0.0.1` and only writes plain `.txt` file names inside
the project folder. It uses only the Python standard library. Bootstrap, Bootstrap Icons
and Google Fonts load from CDNs, so the browser needs internet access.

### Command line / scheduled runs

```powershell
python football_monitor.py                  # uses config.json
python football_monitor.py other_config.json
```

The script runs whichever `sport` the config names. Exit code is `0` on success and `1`
on failure (bad config, API error, or file error), so schedulers can detect failed runs.

## Outputs

**Report file**: a header showing the sport, competition, date, filters and generation
time, followed by each event. For example:

```
Hibernian 1 - 2 Celtic
  Status : Full Time
  Kickoff: Sun 25 Oct 2026, 12:00 UTC
  Venue  : Easter Road
```

```
UFC 333: Volkanovski vs. Evloev
  Status : Final
  Venue  : Etihad Arena, Abu Dhabi, United Arab Emirates
  [Featherweight] Alexander Volkanovski (29-4-0) vs Movsar Evloev (20-1-0) -> Alexander Volkanovski wins (R2 4:54)
```

F1 reports list every session with the top 3 (top 10 for races). Cricket shows innings
scores and the result or live situation.

**`automation.log`**: records script start, configuration loaded, sport and league
selected, API requests, number of events processed, events after filtering, output file
created, and any errors or warnings. Dashboard activity is tagged `[UI]`.

## Testing

```powershell
python run_tests.py
```

Runs the workshop scenarios against the live API and writes `test_results.txt`:
Scottish league, English league, valid team, invalid team, completed-only filter,
missing config file, invalid league code, and invalid config value.

## Project files

| File | Purpose |
|---|---|
| `main.py` | Entry point: web server and JSON API for the dashboard (port 8080) |
| `sports.py` | Multi-sport data layer: ESPN feeds, data normalisation, filters, report formatting |
| `oracle.py` | Sport Oracle: finds the next 10 fixtures or remaining Grands Prix, fetches standings, predicts winners |
| `match_centre.py` | Match detail (form, head-to-head, table, news) and bookmaker odds for the Oracle modal |
| `odds_api.key` *(optional, you create it)* | Your private The Odds API key |
| `AUTOMATION_WORKSHEET.md` | Automation definition, benefits, risks, feature progress log, future features |
| `POTENTIAL_IMPROVEMENTS.md` | Improvement ideas for every stage, including a historical SQL database for predictions |
| `football_monitor.py` | Command-line automation and config validation (football path from the workshop, other sports via `sports.py`) |
| `web/` | Dashboard front end (`index.html`, `css/styles.css`, `js/app.js`) |
| `config.json` | Default settings |
| `run_tests.py` | Test scenarios that write `test_results.txt` |
| `*_report.txt` / `automation.log` | Generated outputs |

## Known limitations

- **Unofficial API**: ESPN's endpoints are undocumented and may change or be rate-limited without notice.
- **No boxing**: Combat Sports is MMA only (see above). ESPN also doesn't say *how* a fight ended (KO, submission, decision), only the round and time.
- **F1 detail**: ESPN's scoreboard gives finishing order and nationality, not lap times, gaps or team names.
- **One day per run**: `date` accepts a single day, because ESPN rejects date ranges for some competitions.
- **Default events**: with no `date`, ESPN chooses what to return (usually the next or most recent round), not necessarily today. Cricket returns all matches currently in progress or recently finished.
- **Postponed matches** are excluded from `completed` (ESPN marks them `completed: false`) but appear under `all`.
- **Search is substring-based**: `"Hearts"` will not match ESPN's `"Heart of Midlothian"`. Use the suggestions in the dashboard for exact names.
- **Times**: reports use UTC. The dashboard shows your local time.
- **Oracle predictions** use current-season data only (no historical results), so they're rough early in a season. NRL and other finished seasons show no fixtures.
- **Bookmaker odds** can lag the bookmakers' own sites, so always check the price there. Matching fixtures between ESPN and The Odds API uses team names and kick-off times, so a fixture with very different naming (e.g. "Hearts" vs "Heart of Midlothian") may not be matched. The UK-bookmaker path can only be fully tested once a key is added. Its matching and parsing were checked against sample data in The Odds API's documented format.
- **Tests need internet access** and cover the football path. They call the live API, so results depend on what ESPN returns at the time.
- The report is overwritten on each run. The log file is appended to and is never rotated.
