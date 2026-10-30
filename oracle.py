"""
Sport Oracle: the next 10 fixtures in a competition, with a predicted winner.

Predictions come from a simple, transparent statistical model, not betting odds:

Team sports (football, rugby) - each team gets a strength rating:
    45%  season win rate (wins + half of draws), shrunk towards average
         early in the season so 2 games don't dominate
    35%  recent form (last 5 results: W=1, D=0.5, L=0)
    20%  average scoring margin per game, scaled for the sport
A home-advantage bonus is added, and the rating gap is turned into
home / draw / away probabilities with a logistic curve. Draws are common
in football and rare in rugby. Results are blended 15% towards the sport's
typical home/draw/away split to avoid overconfident picks.

Combat sports - each fighter's career win rate from their record, turned
into win probabilities the same way and blended 25% towards 50/50. No draws.

Formula 1 - each driver gets a pace rating in championship points per race:
    55%  season average (shrunk towards the field average early on)
    45%  last 5 races
Once qualifying has finished, the grid counts for 40% (pole = 25 points,
2nd = 18 ... 10th = 1). The race is then simulated thousands of times,
drawing the finishing order with each driver's chance of beating the rest
in proportion to rating^2.5, to get win and podium chances. Chances are
blended 10% towards an open race where every driver is equal.

Cricket - the same team model, without scoring margin (cricket tables don't
publish runs for and against): 60% season win rate where the competition has
a table, 40% form from results in the last 3 weeks, plus home advantage.
Only multi-day matches (Tests, first-class) can be drawn; limited-overs
matches always have a winner.
"""

import logging
import math
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import sports

FIXTURE_COUNT = 10
CACHE_SECONDS = 600       # ESPN data changes slowly; avoid hammering the API
MAX_DAYS_SCANNED = 16     # calendar days to look ahead for team sports

# "prior" is the typical home/draw/away split for the sport. Every prediction is blended
# 15% towards it, so small early-season samples can't produce wildly confident picks.
TUNING = {
    "football": {"home_edge": 0.06, "margin_scale": 2.0, "draw_base": 0.27, "steepness": 4,
                 "prior": {"home": 0.45, "draw": 0.27, "away": 0.28}, "margin_label": "Goal diff / game"},
    "rugby": {"home_edge": 0.08, "margin_scale": 20.0, "draw_base": 0.03, "steepness": 5,
              "prior": {"home": 0.58, "draw": 0.03, "away": 0.39}, "margin_label": "Points diff / game"},
    # Cricket is split by format: draws only happen in multi-day matches
    # Cricket form is shrunk towards average, as it often covers only one or two matches
    "cricket_long": {"home_edge": 0.08, "draw_base": 0.28, "steepness": 3, "shrink_form": True,
                     "prior": {"home": 0.42, "draw": 0.28, "away": 0.30}, "margin_label": None},
    "cricket_short": {"home_edge": 0.05, "draw_base": 0.0, "steepness": 3, "shrink_form": True,
                      "prior": {"home": 0.53, "draw": 0.0, "away": 0.47}, "margin_label": None},
}
PRIOR_WEIGHT = 0.15
COMBAT_PRIOR_WEIGHT = 0.25  # records are a weaker signal, so lean harder on 50/50

F1_STANDINGS = "https://site.api.espn.com/apis/v2/sports/racing/f1/standings"
F1_POINTS = [25, 18, 15, 12, 10, 8, 6, 4, 2, 1]
F1_PRIOR_WEIGHT = 0.10    # blend towards every driver having the same chance
F1_GRID_WEIGHT = 0.40     # share of the rating taken from the starting grid once qualifying is done
F1_SHARPNESS = 2.5        # how strongly a higher rating turns into finishing ahead
F1_SIMULATIONS = 5000

CRICKET_DAYS_AHEAD = 14   # days scanned for upcoming matches
CRICKET_DAYS_BACK = 21    # days of results scanned to work out each team's form
CRICKET_LONG_FORMATS = ("test", "first-class")

_cache = {}
_cache_lock = threading.Lock()


def oracle_sports():
    return [key for key, sport in sports.SPORTS.items() if sport.get("oracle")]


# ---------------------------------------------------------------- data gathering

def _safe_get(url, params=None):
    try:
        return sports.get_json(url, params)
    except sports.FetchError as error:
        logging.warning("[Oracle] %s", error)
        return {}


def _calendar_days(calendar, today):
    """ESPN calendars are lists of ISO dates (or dicts of entries); return future days."""
    values = []
    for item in calendar or []:
        if isinstance(item, str):
            values.append(item)
        elif isinstance(item, dict):
            values += [e.get("startDate", "") for e in item.get("entries", [])] or [item.get("startDate", "")]
    days = set()
    for value in values:
        try:
            day = datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        except ValueError:
            continue
        if day >= today:
            days.add(day)
    return sorted(days)


def _event_day(event):
    return datetime.fromisoformat(event["date"].replace("Z", "+00:00")).date()


def _upcoming_team_events(sport, league):
    """Collect the next scheduled matches by walking forward through the league calendar."""
    url = sports.ESPN_SCOREBOARD.format(path=sports.espn_path(sport, league))
    first = sports.get_json(url)
    info = (first.get("leagues") or [{}])[0]
    name = sports.league_name(sport, league) if sport == "rugby" else info.get("name") or sports.league_name(sport, league)

    found = {e["id"]: e for e in sports.parse_team_events(first, name) if e["state"] == "pre"}
    covered = {_event_day(e) for e in sports.parse_team_events(first, name)}
    today = datetime.now(timezone.utc).date()
    pending = [d for d in _calendar_days(info.get("calendar"), today) if d not in covered][:MAX_DAYS_SCANNED]

    with ThreadPoolExecutor(max_workers=4) as pool:
        for start in range(0, len(pending), 4):
            if len(found) >= FIXTURE_COUNT:
                break
            days = pending[start:start + 4]
            for data in pool.map(lambda d: _safe_get(url, {"dates": d.strftime("%Y%m%d")}), days):
                for event in sports.parse_team_events(data, name):
                    if event["state"] == "pre":
                        found[event["id"]] = event

    events = sorted(found.values(), key=lambda e: e["date"])[:FIXTURE_COUNT]
    return name, (info.get("logos") or [{}])[0].get("href", ""), events


def _upcoming_bouts(league):
    """Next scheduled bouts from upcoming fight cards (main event first on each card)."""
    url = sports.ESPN_SCOREBOARD.format(path=sports.espn_path("combat", league))
    today = datetime.now(timezone.utc).date()
    window = f"{today:%Y%m%d}-{today + timedelta(days=120):%Y%m%d}"
    data = sports.get_json(url, {"dates": window})
    info = (data.get("leagues") or [{}])[0]
    name = info.get("name") or sports.league_name("combat", league)

    bouts = []
    for card in sorted(sports.parse_cards(data, name), key=lambda c: c["date"]):
        for index, bout in enumerate(card["bouts"]):
            if bout["state"] == "pre" and len(bout["fighters"]) == 2:
                bouts.append({"card": card, "bout": bout, "main_event": index == 0})
    return name, (info.get("logos") or [{}])[0].get("href", ""), bouts[:FIXTURE_COUNT]


def _upcoming_races():
    """Grands Prix whose race hasn't finished yet, with the grid once qualifying is done."""
    url = sports.ESPN_SCOREBOARD.format(path=sports.espn_path("f1", "f1"))
    today = datetime.now(timezone.utc).date()
    # Start a few days back so a race weekend that is already under way is included
    window = f"{today - timedelta(days=4):%Y%m%d}-{today + timedelta(days=200):%Y%m%d}"
    data = sports.get_json(url, {"dates": window})
    info = (data.get("leagues") or [{}])[0]
    name = info.get("name") or sports.league_name("f1", "f1")

    code = lambda c: (c.get("type") or {}).get("abbreviation", "")
    status = lambda c: (c.get("status") or {}).get("type") or {}
    races = []
    for event in data.get("events", []):
        sessions = event.get("competitions") or []
        race = next((c for c in sessions if code(c) == "Race"), None)
        if not race or status(race).get("completed"):
            continue
        qualifying = next((c for c in sessions if code(c) == "Qual" and status(c).get("completed")), None)
        grid = {}
        for competitor in (qualifying or {}).get("competitors", []):
            athlete = competitor.get("athlete") or {}
            grid[str(competitor.get("id"))] = {"position": competitor.get("order"),
                                               "name": athlete.get("displayName", "Unknown"),
                                               "flag": athlete.get("flag") or {}}
        races.append({
            "event": event,
            "date": race.get("date") or event.get("date"),
            "grid": grid,
            "sessions": [{"name": sports.F1_SESSIONS.get(code(c), code(c) or "Session"),
                          "date": c.get("date"), **sports._status(status(c))} for c in sessions],
        })
    races.sort(key=lambda r: r["date"] or "")
    return name, (info.get("logos") or [{}])[0].get("href", ""), races[:FIXTURE_COUNT]


def _drivers():
    """Current drivers from the championship table, with their points in each race this season."""
    data = _safe_get(F1_STANDINGS)
    group = next((g for g in data.get("children", []) if "driver" in (g.get("name") or "").lower()), {})
    drivers, last_round = [], -1
    for entry in (group.get("standings") or {}).get("entries", []):
        athlete = entry.get("athlete") or {}
        stats = entry.get("stats") or []
        rounds = [i for i, stat in enumerate(stats) if stat.get("played")]  # same calendar order for every driver
        values = {stat.get("name"): stat.get("value") for stat in stats}
        flag = athlete.get("flag") or {}
        drivers.append({
            "id": str(athlete.get("id") or ""),
            "name": athlete.get("displayName", "Unknown"),
            "abbr": athlete.get("abbreviation", ""),
            "flag": flag.get("href", ""),
            "country": flag.get("alt", ""),
            "rank": values.get("rank"),
            "points": values.get("championshipPts") or 0,
            "results": [stats[i].get("value") or 0 for i in rounds],
            "rounds": set(rounds),
        })
        last_round = max([last_round, *rounds])
    # Drivers who sat out the latest round have usually lost their seat (reserves, replaced drivers)
    return [d for d in drivers if last_round < 0 or last_round in d["rounds"]]


def _cricket_day(day, strict=False):
    """One day of ESPN's cricket feed. Older days barely change, so they're cached for longer."""
    key = ("cricket-day", day)
    ttl = 6 * 3600 if day < datetime.now(timezone.utc).date() - timedelta(days=2) else CACHE_SECONDS
    with _cache_lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
    params = {"sport": "cricket", "dates": day.strftime("%Y%m%d")}
    data = sports.get_json(sports.ESPN_CRICKET, params) if strict else _safe_get(sports.ESPN_CRICKET, params)
    if data:
        with _cache_lock:
            _cache[key] = (time.time(), data)
    return data


def _cricket_events(data):
    """(competition, event, {"home": competitor, "away": competitor}) for every two-team match."""
    for item in ((data.get("sports") or [{}])[0]).get("leagues", []):
        for event in item.get("events", []):
            teams = {c.get("homeAway"): c for c in event.get("competitors", [])}
            if "home" in teams and "away" in teams:
                yield item, event, teams


def _cricket_state(event):
    return ((event.get("fullStatus") or {}).get("type") or {}).get("state") or event.get("status", "")


def _cricket_format(event):
    """The format label (Test, T20, ...) and whether it's a multi-day match that can be drawn."""
    card = (event.get("class") or {}).get("generalClassCard") or event.get("eventType") or ""
    days = next((n.get("text", "") for n in event.get("notes", []) if n.get("type") == "matchdays"), "")
    long = any(f in card.lower() for f in CRICKET_LONG_FORMATS) or any(f"{n}-day" in days for n in range(3, 6))
    return card, long


def _upcoming_cricket(league):
    """The next scheduled matches, walking forward a day at a time."""
    today = datetime.now(timezone.utc).date()
    days = [today + timedelta(days=i) for i in range(CRICKET_DAYS_AHEAD + 1)]
    _cricket_day(today, strict=True)  # surface a connection error instead of showing "no fixtures"
    found = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        for start in range(0, len(days), 4):
            if len(found) >= FIXTURE_COUNT:
                break
            for data in pool.map(_cricket_day, days[start:start + 4]):
                for item, event, teams in _cricket_events(data):
                    undecided = any((c.get("displayName") or "TBA").upper() in ("TBA", "TBC", "TBD")
                                    for c in teams.values())  # knockout slots not filled yet
                    if (league == "all" or item.get("id") == league) and _cricket_state(event) == "pre" and not undecided:
                        found[event.get("id")] = (item, event, teams)
    return sorted(found.values(), key=lambda m: m[1].get("date") or "")[:FIXTURE_COUNT]


def _cricket_form():
    """Each team's results over the last few weeks, oldest first, keyed by team id."""
    today = datetime.now(timezone.utc).date()
    days = [today - timedelta(days=i) for i in range(CRICKET_DAYS_BACK, -1, -1)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        feeds = list(pool.map(_cricket_day, days))

    seen, games = set(), {}
    for data in feeds:
        for item, event, teams in _cricket_events(data):
            if _cricket_state(event) != "post" or event.get("id") in seen:
                continue
            seen.add(event.get("id"))
            winner = next((side for side, c in teams.items() if c.get("winner")), None)
            if not winner and not _cricket_format(event)[1]:
                continue  # a limited-overs match with no winner was abandoned: no result
            for side, other in (("home", "away"), ("away", "home")):
                me, opponent = teams[side], teams[other]
                games.setdefault(str(me.get("id")), []).append({
                    "date": event.get("date"),
                    "opponent": opponent.get("displayName", ""),
                    "opponent_logo": opponent.get("logo", ""),
                    "venue": "H" if side == "home" else "A",
                    "score": me.get("score", ""),
                    "result": "D" if not winner else "W" if winner == side else "L",
                    "competition": item.get("name", ""),
                })
    for results in games.values():
        results.sort(key=lambda g: g["date"] or "")
    return games


def _standings(sport, league):
    """Map team id -> season stats. Returns {} when the competition has no table."""
    path = sports.espn_path(sport, league)
    data = _safe_get(f"https://site.api.espn.com/apis/v2/sports/{path}/standings")
    table = {}
    for group in data.get("children") or [data]:
        for entry in (group.get("standings") or {}).get("entries", []):
            stats = {s.get("name"): s.get("value") for s in entry.get("stats", [])}
            first = lambda *names: next((stats[n] for n in names if stats.get(n) is not None), 0) or 0
            wins = first("wins", "gamesWon", "matchesWon")
            draws = first("ties", "gamesDrawn", "matchesDraw") + first("matchesTied")
            losses = first("losses", "gamesLost", "matchesLost")
            table[str(entry.get("team", {}).get("id"))] = {
                # Cricket's matchesPlayed counts no-results, so it's rebuilt from the results
                "played": stats.get("gamesPlayed") or wins + draws + losses,
                "wins": wins,
                "draws": draws,
                "losses": losses,
                "for": stats.get("pointsFor"),      # None for cricket, which has no runs for/against
                "against": stats.get("pointsAgainst"),
                "rank": stats.get("rank"),
            }
    return table


# ---------------------------------------------------------------- model

def _logistic(x, k):
    return 1 / (1 + math.exp(-k * x))


def _ordinal(n):
    n = int(n)
    suffix = "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _team_rating(side, table, tuning):
    row = table.get(str(side.get("id")))
    form = [c for c in (side.get("form") or "") if c in "WDL"][-5:]
    points = sum({"W": 1, "D": 0.5, "L": 0}[c] for c in form)
    if tuning.get("shrink_form"):
        form_score = (points + 1) / (len(form) + 2)  # one or two results shouldn't count as a full run of form
    else:
        form_score = points / len(form) if form else 0.5

    if row and row["played"]:
        played = row["played"]
        win_rate = (row["wins"] + 0.5 * row["draws"] + 1) / (played + 2)
        if row["for"] is None or row["against"] is None:  # no scoring totals in the table (cricket)
            margin = None
            rating = 0.6 * win_rate + 0.4 * form_score
        else:
            margin = (row["for"] - row["against"]) / played
            margin_score = 0.5 + max(-1.0, min(1.0, margin / tuning["margin_scale"])) / 2
            rating = 0.45 * win_rate + 0.35 * form_score + 0.20 * margin_score
    else:
        margin = None
        rating = 0.65 * 0.5 + 0.35 * form_score  # no table: treat season strength as average
    return rating, row, "".join(form), margin


def _confidence(probability, strong=0.6, lean=0.45):
    if probability >= strong:
        return "Strong"
    if probability >= lean:
        return "Lean"
    return "Toss-up"


def predict_match(event, table, sport):
    tuning = TUNING[sport]
    home, away = event["home"], event["away"]
    r_home, row_h, form_h, margin_h = _team_rating(home, table, tuning)
    r_away, row_a, form_a, margin_a = _team_rating(away, table, tuning)

    diff = r_home + tuning["home_edge"] - r_away
    draw = tuning["draw_base"] * max(0.45, 1 - abs(diff) * 2)
    home_win = (1 - draw) * _logistic(diff, tuning["steepness"])
    model = {"home": home_win, "draw": draw, "away": 1 - draw - home_win}
    probabilities = {k: (1 - PRIOR_WEIGHT) * model[k] + PRIOR_WEIGHT * tuning["prior"][k] for k in model}

    pick = max(probabilities, key=probabilities.get)
    comparison = []
    if row_h or row_a:
        comparison.append({"label": "League position",
                           "home": _ordinal(row_h["rank"]) if row_h and row_h["rank"] else "–",
                           "away": _ordinal(row_a["rank"]) if row_a and row_a["rank"] else "–"})
        record = lambda r: f"{int(r['wins'])}-{int(r['draws'])}-{int(r['losses'])}" if r else "–"
        comparison.append({"label": "Record (W-D-L)", "home": record(row_h), "away": record(row_a)})
        if tuning["margin_label"]:
            signed = lambda m: "–" if m is None else f"{m:+.1f}"
            comparison.append({"label": tuning["margin_label"], "home": signed(margin_h), "away": signed(margin_a)})
    comparison.append({"label": "Form (last 5)", "home": form_h or "–", "away": form_a or "–"})
    comparison.append({"label": "Oracle rating", "home": f"{r_home * 100:.0f}", "away": f"{r_away * 100:.0f}"})

    return {
        "pick": pick,
        "pick_name": {"home": home["name"], "away": away["name"], "draw": "Draw"}[pick],
        "probabilities": {k: round(v, 3) for k, v in probabilities.items()},
        "confidence": _confidence(probabilities[pick]),
        "comparison": comparison,
        "note": f"Includes home advantage for {home['name']}." if not (row_h or row_a)
                else "Based on season table, form and home advantage.",
    }


def _record_rate(record):
    try:
        wins, losses, draws = (int(x) for x in (record or "").split("-")[:3])
    except ValueError:
        return None, 0.5
    return (wins, losses, draws), (wins + 0.5 * draws + 1) / (wins + losses + draws + 2)


def predict_bout(bout):
    a, b = bout["fighters"]
    rec_a, rate_a = _record_rate(a.get("record"))
    rec_b, rate_b = _record_rate(b.get("record"))
    a_win = (1 - COMBAT_PRIOR_WEIGHT) * _logistic(rate_a - rate_b, 6) + COMBAT_PRIOR_WEIGHT * 0.5
    probabilities = {"home": a_win, "draw": 0.0, "away": 1 - a_win}
    pick = "home" if a_win >= 0.5 else "away"
    fights = lambda r: str(sum(r)) if r else "–"
    return {
        "pick": pick,
        "pick_name": a["name"] if pick == "home" else b["name"],
        "probabilities": {k: round(v, 3) for k, v in probabilities.items()},
        "confidence": _confidence(probabilities[pick]),
        "comparison": [
            {"label": "Record (W-L-D)", "home": a.get("record") or "–", "away": b.get("record") or "–"},
            {"label": "Pro fights", "home": fights(rec_a), "away": fights(rec_b)},
            {"label": "Win rate", "home": f"{rate_a * 100:.0f}%", "away": f"{rate_b * 100:.0f}%"},
        ],
        "note": "Based on career records only, a weak signal for MMA.",
    }


def _driver_rating(driver, field_average, grid):
    results = driver["results"]
    season = (sum(results) + 2 * field_average) / (len(results) + 2)  # two average races so small samples can't dominate
    recent = results[-5:]
    form = (sum(recent) + field_average) / (len(recent) + 1)
    rating = 0.55 * season + 0.45 * form
    if grid:
        slot = (grid.get(driver["id"]) or {}).get("position") or 99
        grid_points = F1_POINTS[slot - 1] if slot <= len(F1_POINTS) else 0.5
        rating = (1 - F1_GRID_WEIGHT) * rating + F1_GRID_WEIGHT * grid_points
    return rating, season, recent


def _simulate(ratings, seed):
    """Monte Carlo races: each driver's share of wins and podiums."""
    rng = random.Random(seed)  # seeded so the same data always gives the same numbers
    strength = [F1_SHARPNESS * math.log(max(r, 0.1)) for r in ratings]
    wins, podiums = [0] * len(ratings), [0] * len(ratings)
    for _ in range(F1_SIMULATIONS):
        # Adding Gumbel noise and sorting draws a finishing order in which each driver
        # beats the rest of the field in proportion to rating^sharpness (Plackett-Luce)
        noisy = [s - math.log(-math.log(rng.random() or 1e-12)) for s in strength]
        order = sorted(range(len(noisy)), key=noisy.__getitem__, reverse=True)
        wins[order[0]] += 1
        for i in order[:3]:
            podiums[i] += 1
    return [w / F1_SIMULATIONS for w in wins], [p / F1_SIMULATIONS for p in podiums]


def predict_race(race, drivers):
    """Win and podium chances for every driver. Returns None before any race has been run."""
    grid = race["grid"]
    field = list(drivers)
    if grid:
        field = [d for d in drivers if d["id"] in grid]
        known = {d["id"] for d in field}
        for driver_id, slot in grid.items():  # stand-in drivers who aren't in the table yet
            if driver_id not in known:
                field.append({"id": driver_id, "name": slot["name"], "abbr": "", "flag": slot["flag"].get("href", ""),
                              "country": slot["flag"].get("alt", ""), "rank": None, "points": 0, "results": []})
    races_run = sum(len(d["results"]) for d in field)
    if not races_run:
        return None

    field_average = sum(sum(d["results"]) for d in field) / races_run
    rated = [(d, *_driver_rating(d, field_average, grid)) for d in field]
    wins, podiums = _simulate([r[1] for r in rated], race["event"].get("id") or "f1")
    size = len(rated)

    table = [{
        "id": driver["id"], "name": driver["name"], "abbr": driver["abbr"],
        "flag": driver["flag"], "country": driver["country"],
        "rank": int(driver["rank"]) if driver["rank"] else None,
        "points": int(driver["points"]),
        "season_avg": round(season, 1),
        "last5": [int(v) for v in recent],
        "grid": (grid.get(driver["id"]) or {}).get("position"),
        "rating": round(rating, 1),
        "win": round((1 - F1_PRIOR_WEIGHT) * win + F1_PRIOR_WEIGHT / size, 3),
        "podium": round((1 - F1_PRIOR_WEIGHT) * podium + F1_PRIOR_WEIGHT * min(3, size) / size, 3),
    } for (driver, rating, season, recent), win, podium in zip(rated, wins, podiums)]
    table.sort(key=lambda d: (-d["win"], -d["podium"]))

    return {
        "pick": table[0]["id"],
        "pick_name": table[0]["name"],
        "confidence": _confidence(table[0]["win"], strong=0.40, lean=0.25),
        "drivers": table,
        "note": "Based on the starting grid, season points and recent form." if grid
                else "Based on season points per race and the last 5 races. Updates once qualifying is done.",
    }


# ---------------------------------------------------------------- public API

def _side_summary(side):
    summary = {key: side.get(key, "") for key in ("name", "abbr", "logo", "flag", "country", "form", "record")}
    summary["id"] = str(side.get("id") or "")
    return summary


def _cricket_forecast(league):
    """Upcoming cricket matches across competitions, each predicted with its own competition's table."""
    upcoming = _upcoming_cricket(league)
    form = _cricket_form() if upcoming else {}
    league_ids = sorted({item.get("id") for item, _, _ in upcoming})
    with ThreadPoolExecutor(max_workers=4) as pool:
        tables = dict(zip(league_ids, pool.map(lambda lid: _standings("cricket", lid), league_ids)))

    fixtures = []
    for item, event, teams in upcoming:
        card, long = _cricket_format(event)
        sides = {}
        for key, competitor in teams.items():
            team_id = str(competitor.get("id") or "")
            sides[key] = {"id": team_id, "name": competitor.get("displayName", "TBC"),
                          "abbr": competitor.get("abbreviation", ""), "logo": competitor.get("logo", ""),
                          "form": "".join(g["result"] for g in form.get(team_id, []))[-5:]}
        table = tables.get(item.get("id")) or {}
        prediction = predict_match(sides, table, "cricket_long" if long else "cricket_short")
        has_table = any(sides[k]["id"] in table for k in sides)
        has_form = any(sides[k]["form"] for k in sides)
        prediction["note"] = (
            "Based on the competition table, results in the last 3 weeks and home advantage." if has_table
            else "Based on results in the last 3 weeks and home advantage." if has_form
            else "Neither team has a table or recent results in ESPN's feed, so this pick is mostly home advantage.")
        if long:
            prediction["note"] += " Multi-day matches can end in a draw."
        fixtures.append({
            "id": event.get("id"),
            "date": event.get("date"),
            "venue": event.get("location", ""),
            "context": f"{item.get('name', 'Cricket')} · {event.get('title') or card or 'Match'}",
            "competition": item.get("name", "Cricket"),
            "league_id": item.get("id", ""),
            "format": card,
            "description": event.get("description", ""),
            "home": _side_summary(sides["home"]),
            "away": _side_summary(sides["away"]),
            # Same shape as the football form block, so the match modal can reuse its layout
            "recent": [{"team": s["name"], "team_id": s["id"], "logo": s["logo"],
                        "games": list(reversed(form.get(s["id"], [])[-5:]))} for s in (sides["home"], sides["away"])],
            "prediction": prediction,
        })

    names = {item.get("id"): item.get("name") for item, _, _ in upcoming}
    name = "All cricket competitions" if league == "all" else names.get(league) or "Cricket"
    return name, "", fixtures


def build_forecast(sport, league):
    """Return the next fixtures with predictions. Raises sports.FetchError on failure."""
    if sport == "f1":
        name, logo, races = _upcoming_races()
        drivers = _drivers() if races else []
        fixtures = [{
            "id": race["event"].get("id"),
            "kind": "race",
            "name": race["event"].get("name", ""),
            "date": race["date"],
            "venue": (race["event"].get("circuit") or {}).get("fullName", ""),
            "location": sports._place((race["event"].get("circuit") or {}).get("address")),
            "context": name,
            "sessions": race["sessions"],
            "prediction": predict_race(race, drivers),
        } for race in races]
    elif sport == "combat":
        name, logo, bouts = _upcoming_bouts(league)
        fixtures = [{
            "id": f"{item['card']['id']}-{i}",
            "date": item["bout"]["date"] or item["card"]["date"],
            "venue": item["card"]["venue"],
            "context": f"{item['card']['name']} · {'Main event' if item['main_event'] else item['bout']['weight_class'] or 'Bout'}",
            "home": _side_summary(item["bout"]["fighters"][0]),
            "away": _side_summary(item["bout"]["fighters"][1]),
            "prediction": predict_bout(item["bout"]),
        } for i, item in enumerate(bouts)]
    elif sport == "cricket":
        name, logo, fixtures = _cricket_forecast(league)
    else:
        name, logo, events = _upcoming_team_events(sport, league)
        table = _standings(sport, league)
        fixtures = [{
            "id": event["id"],
            "date": event["date"],
            "venue": event["venue"],
            "context": name,
            "home": _side_summary(event["home"]),
            "away": _side_summary(event["away"]),
            "prediction": predict_match(event, table, sport),
        } for event in events]

    logging.info("[Oracle] %s / %s: %d fixtures predicted", sport, league, len(fixtures))
    return {
        "sport": sport,
        "league": league,
        "name": name,
        "logo": logo,
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fixtures": fixtures,
    }


def forecast(sport, league, fresh=False):
    """Cached wrapper around build_forecast. fresh=True skips the cache."""
    key = (sport, league)
    with _cache_lock:
        hit = _cache.get(key)
        if hit and not fresh and time.time() - hit[0] < CACHE_SECONDS:
            return hit[1]
    result = build_forecast(sport, league)
    with _cache_lock:
        _cache[key] = (time.time(), result)
    return result
