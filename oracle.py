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
"""

import logging
import math
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
}
PRIOR_WEIGHT = 0.15
COMBAT_PRIOR_WEIGHT = 0.25  # records are a weaker signal, so lean harder on 50/50

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


def _standings(sport, league):
    """Map team id -> season stats. Returns {} when the competition has no table."""
    path = sports.espn_path(sport, league)
    data = _safe_get(f"https://site.api.espn.com/apis/v2/sports/{path}/standings")
    table = {}
    for group in data.get("children") or [data]:
        for entry in (group.get("standings") or {}).get("entries", []):
            stats = {s.get("name"): s.get("value") for s in entry.get("stats", [])}
            played = stats.get("gamesPlayed") or 0
            table[str(entry.get("team", {}).get("id"))] = {
                "played": played,
                "wins": stats.get("wins", stats.get("gamesWon")) or 0,
                "draws": stats.get("ties", stats.get("gamesDrawn")) or 0,
                "losses": stats.get("losses", stats.get("gamesLost")) or 0,
                "for": stats.get("pointsFor") or 0,
                "against": stats.get("pointsAgainst") or 0,
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
    form_score = sum({"W": 1, "D": 0.5, "L": 0}[c] for c in form) / len(form) if form else 0.5

    if row and row["played"]:
        played = row["played"]
        win_rate = (row["wins"] + 0.5 * row["draws"] + 1) / (played + 2)
        margin = (row["for"] - row["against"]) / played
        margin_score = 0.5 + max(-1.0, min(1.0, margin / tuning["margin_scale"])) / 2
        rating = 0.45 * win_rate + 0.35 * form_score + 0.20 * margin_score
    else:
        margin = None
        rating = 0.65 * 0.5 + 0.35 * form_score  # no table: treat season strength as average
    return rating, row, "".join(form), margin


def _confidence(probability):
    if probability >= 0.6:
        return "Strong"
    if probability >= 0.45:
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


# ---------------------------------------------------------------- public API

def _side_summary(side):
    summary = {key: side.get(key, "") for key in ("name", "abbr", "logo", "flag", "country", "form", "record")}
    summary["id"] = str(side.get("id") or "")
    return summary


def build_forecast(sport, league):
    """Return the next fixtures with predictions. Raises sports.FetchError on failure."""
    if sport == "combat":
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
