"""
Multi-sport data layer.

Fetches fixtures and results from ESPN's public APIs for football, rugby,
Formula 1, cricket and combat sports (MMA), and converts each sport's JSON
into one of three simple shapes that the web UI and reports understand:

    match - two sides with a score (football, rugby, cricket)
    race  - a race weekend with sessions and finishing orders (F1)
    card  - a fight card made up of individual bouts (MMA)
"""

import logging
import re
from datetime import datetime, timezone

import requests

ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard"
ESPN_CRICKET = "https://site.web.api.espn.com/apis/personalized/v2/scoreboard/header"
STATUSES = ("all", "live", "completed", "scheduled")

SPORTS = {
    "football": {
        "name": "Football", "emoji": "⚽", "kind": "match", "search_label": "Team", "oracle": True,
        "custom_league": True,
        "leagues": [
            {"id": "sco.1", "name": "Scottish Premiership"},
            {"id": "sco.2", "name": "Scottish Championship"},
            {"id": "eng.1", "name": "English Premier League"},
            {"id": "eng.2", "name": "English Championship"},
            {"id": "eng.fa", "name": "English FA Cup"},
            {"id": "uefa.champions", "name": "UEFA Champions League"},
            {"id": "uefa.europa", "name": "UEFA Europa League"},
            {"id": "esp.1", "name": "Spanish LaLiga"},
            {"id": "ger.1", "name": "German Bundesliga"},
            {"id": "ita.1", "name": "Italian Serie A"},
            {"id": "fra.1", "name": "French Ligue 1"},
            {"id": "ned.1", "name": "Dutch Eredivisie"},
            {"id": "por.1", "name": "Portuguese Primeira Liga"},
            {"id": "usa.1", "name": "USA Major League Soccer"},
        ],
    },
    "rugby": {
        "name": "Rugby", "emoji": "🏉", "kind": "match", "search_label": "Team", "oracle": True,
        "leagues": [
            {"id": "267979", "name": "Gallagher Premiership"},
            {"id": "270557", "name": "United Rugby Championship"},
            {"id": "270559", "name": "French Top 14"},
            {"id": "271937", "name": "European Champions Cup"},
            {"id": "272073", "name": "European Challenge Cup"},
            {"id": "180659", "name": "Six Nations"},
            {"id": "244293", "name": "The Rugby Championship"},
            {"id": "242041", "name": "Super Rugby Pacific"},
            {"id": "164205", "name": "Rugby World Cup"},
            {"id": "289234", "name": "International Tests"},
            {"id": "nrl", "name": "NRL (Rugby League)"},
        ],
    },
    "f1": {
        "name": "Formula 1", "emoji": "🏎️", "kind": "race", "search_label": "Driver", "oracle": True,
        "leagues": [{"id": "f1", "name": "Formula 1 World Championship"}],
    },
    "cricket": {
        "name": "Cricket", "emoji": "🏏", "kind": "match", "search_label": "Team",
        "dynamic_leagues": True,
        "leagues": [{"id": "all", "name": "All competitions"}],
    },
    "combat": {
        "name": "Combat Sports", "emoji": "🥊", "kind": "card", "search_label": "Fighter", "oracle": True,
        "note": "Combat Sports shows MMA (UFC, PFL, Bellator). ESPN does not publish boxing data.",
        "leagues": [
            {"id": "ufc", "name": "UFC"},
            {"id": "pfl", "name": "PFL"},
            {"id": "bellator", "name": "Bellator"},
        ],
    },
}

F1_SESSIONS = {
    "FP1": "Practice 1", "FP2": "Practice 2", "FP3": "Practice 3",
    "SS": "Sprint Shootout", "SQ": "Sprint Qualifying", "Sprint": "Sprint",
    "Qual": "Qualifying", "Race": "Race",
}


class FetchError(Exception):
    """Raised when data cannot be retrieved from ESPN."""


# ---------------------------------------------------------------- validation

def validate_league(sport, league):
    """Return an error message if the league is not valid for the sport, else None."""
    if sport not in SPORTS:
        return f"'sport' must be one of: {', '.join(SPORTS)}."
    if sport == "football":
        if not re.fullmatch(r"[a-z]{3,4}\.[a-z0-9]+", league):
            return "'league' must look like 'sco.1', 'eng.1' or 'uefa.champions'."
        return None
    if sport == "cricket":
        if league != "all" and not league.isdigit():
            return "Cricket 'league' must be 'all' or an ESPN competition number."
        return None
    if league not in {item["id"] for item in SPORTS[sport]["leagues"]}:
        names = ", ".join(item["id"] for item in SPORTS[sport]["leagues"])
        return f"'league' for {SPORTS[sport]['name']} must be one of: {names}."
    return None


def league_name(sport, league):
    """Look up a friendly competition name."""
    for item in SPORTS.get(sport, {}).get("leagues", []):
        if item["id"] == league:
            return item["name"]
    return league


# ---------------------------------------------------------------- fetching

def get_json(url, params=None):
    logging.info("Requesting %s %s", url, params or "")
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.HTTPError as error:
        raise FetchError(f"ESPN returned an error ({error.response.status_code}). "
                         "Check the competition and date.") from error
    except requests.RequestException as error:
        raise FetchError(f"Unable to connect to ESPN: {error}") from error
    except ValueError as error:
        raise FetchError("ESPN returned data that is not valid JSON.") from error


def espn_path(sport, league):
    if sport == "football":
        return f"soccer/{league}"
    if sport == "rugby":
        return "rugby-league/3" if league == "nrl" else f"rugby/{league}"
    if sport == "f1":
        return "racing/f1"
    return f"mma/{league}"


def fetch_events(sport, league, date=""):
    """Retrieve and normalise events. Raises FetchError on failure.

    Returns a dict: name, logo, season, events, competitions.
    """
    params = {"dates": date} if date else {}

    if sport == "cricket":
        data = get_json(ESPN_CRICKET, {"sport": "cricket", **params})
        result = _parse_cricket(data, league)
    else:
        data = get_json(ESPN_SCOREBOARD.format(path=espn_path(sport, league)), params or None)
        info = (data.get("leagues") or [{}])[0]
        # ESPN's rugby names are terse ("Rugby League", "Gallagher Prem"), so prefer ours
        name = league_name(sport, league) if sport == "rugby" else info.get("name") or league_name(sport, league)
        parser = {"match": parse_team_events, "race": _parse_races, "card": parse_cards}[SPORTS[sport]["kind"]]
        result = {
            "name": name,
            "logo": (info.get("logos") or [{}])[0].get("href", ""),
            "season": (info.get("season") or {}).get("displayName", ""),
            "events": parser(data, name),
            "competitions": [],
        }

    logging.info("%s / %s: %d events retrieved", SPORTS[sport]["name"], league, len(result["events"]))
    return result


# ---------------------------------------------------------------- parsing

def _status(status_type):
    state = status_type.get("state", "pre")
    return {
        "state": state,
        # Postponed football matches are "post" but not completed
        "completed": bool(status_type.get("completed", state == "post")),
        "status": status_type.get("description", ""),
    }


def _team_side(competitor):
    team = competitor.get("team") or {}
    record = (competitor.get("records") or [{}])[0].get("summary", "")
    return {
        "id": team.get("id"),
        "name": team.get("displayName", "TBC"),
        "abbr": team.get("abbreviation", ""),
        "logo": team.get("logo", ""),
        "color": team.get("color", ""),
        "score": competitor.get("score", ""),
        "winner": bool(competitor.get("winner")),
        "form": competitor.get("form", ""),
        "record": record if "-" in record else "",  # rugby repeats the form string here
    }


def _incidents(competition, side_by_team_id):
    """Goals, tries and red cards from the match details."""
    incidents = []
    for detail in competition.get("details", []):
        label = (detail.get("type") or {}).get("text", "")
        if detail.get("redCard") or label.lower() == "red card":
            kind = "red_card"
        elif detail.get("scoringPlay") or label.lower() == "try":
            kind = "score"
        else:
            continue
        players = detail.get("athletesInvolved") or [{}]
        incidents.append({
            "type": kind,
            "label": label,
            "minute": (detail.get("clock") or {}).get("displayValue", ""),
            "player": players[0].get("displayName", "Unknown"),
            "side": side_by_team_id.get((detail.get("team") or {}).get("id"), "home"),
        })
    return incidents


def parse_team_events(data, competition_name):
    """Football and rugby share ESPN's team-sport scoreboard format."""
    events = []
    for event in data.get("events", []):
        competition = (event.get("competitions") or [{}])[0]
        teams = {c.get("homeAway"): c for c in competition.get("competitors", [])}
        if "home" not in teams or "away" not in teams:
            continue
        home, away = _team_side(teams["home"]), _team_side(teams["away"])
        status = competition.get("status") or event.get("status") or {}
        events.append({
            "kind": "match",
            "id": event.get("id"),
            "name": event.get("name", ""),
            "date": event.get("date"),
            **_status(status.get("type") or {}),
            "clock": status.get("displayClock", ""),
            "venue": (competition.get("venue") or {}).get("fullName", ""),
            "competition": competition_name,
            "summary": "",
            "home": home,
            "away": away,
            "incidents": _incidents(competition, {home["id"]: "home", away["id"]: "away"}),
        })
    return events


def _parse_cricket(data, league):
    sport = (data.get("sports") or [{}])[0]
    leagues = sport.get("leagues", [])
    events = []
    for item in leagues:
        if league != "all" and item.get("id") != league:
            continue
        for event in item.get("events", []):
            teams = {c.get("homeAway"): c for c in event.get("competitors", [])}
            if "home" not in teams or "away" not in teams:
                continue
            full_status = event.get("fullStatus") or {}
            sides = {}
            for key, competitor in teams.items():
                sides[key] = {
                    "id": competitor.get("id"),
                    "name": competitor.get("displayName", "TBC"),
                    "abbr": competitor.get("abbreviation", ""),
                    "logo": competitor.get("logo", ""),
                    "color": competitor.get("color", ""),
                    "score": competitor.get("score", ""),
                    "winner": bool(competitor.get("winner")),
                    "form": "",
                    "record": "",
                }
            events.append({
                "kind": "match",
                "id": event.get("id"),
                "name": event.get("name", ""),
                "date": event.get("date"),
                **_status(full_status.get("type") or {"state": event.get("status", "pre")}),
                "clock": "",
                "venue": event.get("location", ""),
                "competition": item.get("name", "Cricket"),
                "league_id": item.get("id", ""),  # needed to fetch the match's scorecard
                "description": event.get("description", ""),
                "summary": full_status.get("summary") or event.get("summary", ""),
                "home": sides["home"],
                "away": sides["away"],
                "incidents": [],
            })

    name = "Cricket" if league == "all" else next(
        (item.get("name") for item in leagues if item.get("id") == league), "Cricket")
    return {
        "name": name,
        "logo": (sport.get("logos") or [{}])[0].get("href", ""),
        "season": "All current competitions" if league == "all" else "",
        "events": events,
        "competitions": [{"id": item.get("id"), "name": item.get("name")} for item in leagues],
    }


def _place(address):
    address = address or {}
    return ", ".join(part for part in (address.get("city"), address.get("country")) if part)


def _parse_races(data, competition_name):
    events = []
    for event in data.get("events", []):
        circuit = event.get("circuit") or {}
        sessions = []
        for competition in event.get("competitions", []):
            code = (competition.get("type") or {}).get("abbreviation", "")
            status_type = (competition.get("status") or {}).get("type") or {}
            results = []
            for competitor in sorted(competition.get("competitors", []), key=lambda c: c.get("order", 99)):
                athlete = competitor.get("athlete") or {}
                flag = athlete.get("flag") or {}
                results.append({
                    "position": competitor.get("order"),
                    "name": athlete.get("displayName", "Unknown"),
                    "country": flag.get("alt", ""),
                    "flag": flag.get("href", ""),
                    "winner": bool(competitor.get("winner")),
                })
            sessions.append({
                "code": code,
                "name": F1_SESSIONS.get(code, code or "Session"),
                "date": competition.get("date"),
                **_status(status_type),
                "results": results,
            })

        status_type = (event.get("status") or {}).get("type") or {}
        events.append({
            "kind": "race",
            "id": event.get("id"),
            "name": event.get("name", ""),
            "date": event.get("date"),
            "end_date": event.get("endDate"),
            **_status(status_type),
            "clock": "",
            "venue": circuit.get("fullName", ""),
            "location": _place(circuit.get("address")),
            "competition": competition_name,
            "sessions": sessions,
        })
    return events


def parse_cards(data, competition_name):
    events = []
    for event in data.get("events", []):
        venue = (event.get("venues") or [{}])[0]
        bouts = []
        for competition in reversed(event.get("competitions", [])):  # ESPN lists the main event last
            status = competition.get("status") or {}
            fighters = []
            for competitor in sorted(competition.get("competitors", []), key=lambda c: c.get("order", 0)):
                athlete = competitor.get("athlete") or {}
                flag = athlete.get("flag") or {}
                fighters.append({
                    "name": athlete.get("displayName", "TBC"),
                    "country": flag.get("alt", ""),
                    "flag": flag.get("href", ""),
                    "record": (competitor.get("records") or [{}])[0].get("summary", ""),
                    "winner": bool(competitor.get("winner")),
                })
            regulation = (competition.get("format") or {}).get("regulation") or {}
            bouts.append({
                "weight_class": (competition.get("type") or {}).get("abbreviation", ""),
                "date": competition.get("date"),
                **_status(status.get("type") or {}),
                "round": status.get("period"),
                "clock": status.get("displayClock", ""),
                "scheduled_rounds": regulation.get("periods"),
                "fighters": fighters,
            })

        status_type = (event.get("status") or {}).get("type") or {}
        events.append({
            "kind": "card",
            "id": event.get("id"),
            "name": event.get("name", ""),
            "date": event.get("date"),
            **_status(status_type),
            "clock": "",
            "venue": venue.get("fullName", ""),
            "location": _place(venue.get("address")),
            "competition": competition_name,
            "bouts": bouts,
        })
    return events


# ---------------------------------------------------------------- business rules

def participant_names(event):
    """Teams, drivers or fighters involved in an event."""
    if event["kind"] == "match":
        return [event["home"]["name"], event["away"]["name"]]
    if event["kind"] == "race":
        return [result["name"] for session in event["sessions"] for result in session["results"]]
    return [fighter["name"] for bout in event["bouts"] for fighter in bout["fighters"]]


def matches_status(event, status):
    if status == "completed":
        return event["completed"]
    if status == "scheduled":
        return event["state"] == "pre"
    if status == "live":
        return event["state"] == "in"
    return True


def filter_events(events, search="", status="all"):
    """Apply the business rules: participant search and status filter."""
    needle = search.strip().lower()
    results = []
    for event in events:
        if not matches_status(event, status):
            continue
        if needle:
            if not any(needle in name.lower() for name in participant_names(event)):
                continue
            if event["kind"] == "card":  # keep only the bouts involving the fighter
                event = {**event, "bouts": [
                    bout for bout in event["bouts"]
                    if any(needle in f["name"].lower() for f in bout["fighters"])
                ]}
        results.append(event)
    return results


# ---------------------------------------------------------------- reporting

def _format_date(value):
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return moment.strftime("%a %d %b %Y, %H:%M UTC")
    except (AttributeError, ValueError):
        return value or "TBC"


def format_event(event):
    """Return a readable text block for one event."""
    lines = []
    if event["kind"] == "match":
        home, away = event["home"], event["away"]
        if event["state"] == "pre" or not (home["score"] or away["score"]):
            lines.append(f"{home['name']} vs {away['name']}")
        elif not (home["score"].isdigit() and away["score"].isdigit()):  # cricket, e.g. "184 & 117/5"
            lines.append(f"{home['name']} {home['score'] or '-'}  v  {away['name']} {away['score'] or '-'}")
        else:
            lines.append(f"{home['name']} {home['score']} - {away['score']} {away['name']}")
        status = event["status"] + (f" - {event['summary']}" if event.get("summary") else "")
        lines += [f"  Status : {status}",
                  f"  Start  : {_format_date(event['date'])}",
                  f"  Venue  : {event['venue'] or 'Unknown venue'}"]
        if event.get("description"):
            lines.append(f"  Info   : {event['description']}")

    elif event["kind"] == "race":
        lines += [event["name"],
                  f"  Status : {event['status']}",
                  f"  Start  : {_format_date(event['date'])}",
                  f"  Circuit: {event['venue']}{', ' + event['location'] if event['location'] else ''}"]
        for session in event["sessions"]:
            if not session["results"]:
                lines.append(f"  {session['name']}: {session['status']} ({_format_date(session['date'])})")
                continue
            lines.append(f"  {session['name']}: {session['status']}")
            top = session["results"][:10 if session["code"] in ("Race", "Sprint") else 3]
            for result in top:
                country = f" ({result['country']})" if result["country"] else ""
                lines.append(f"    {result['position']:>2}. {result['name']}{country}")

    else:
        lines += [event["name"],
                  f"  Status : {event['status']}",
                  f"  Start  : {_format_date(event['date'])}",
                  f"  Venue  : {event['venue']}{', ' + event['location'] if event['location'] else ''}"]
        for bout in event["bouts"]:
            names = " vs ".join(
                f"{f['name']}{' (' + f['record'] + ')' if f['record'] else ''}" for f in bout["fighters"])
            line = f"  [{bout['weight_class'] or 'Bout'}] {names}"
            winner = next((f["name"] for f in bout["fighters"] if f["winner"]), None)
            if bout["completed"] and winner:
                line += f" -> {winner} wins (R{bout['round']} {bout['clock']})"
            elif bout["completed"]:
                line += f" -> {bout['status']}"
            lines.append(line)

    return "\n".join(lines) + "\n"


def write_report(path, sport, result, events, settings):
    """Write a text report. Returns True on success."""
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    search_label = SPORTS[sport]["search_label"].lower()
    try:
        with open(path, "w", encoding="utf-8") as file:
            file.write(f"{SPORTS[sport]['name']} Report\n")
            file.write("=" * 60 + "\n")
            file.write(f"Competition : {result['name']} ({settings['league']})\n")
            file.write(f"Date        : {settings['date'] or 'Current'}\n")
            file.write(f"Search      : {settings['team_filter'] or 'None'} ({search_label})\n")
            file.write(f"Status      : {settings['match_status']}\n")
            file.write(f"Generated   : {generated}\n")
            file.write("=" * 60 + "\n\n")
            if not events:
                file.write("No events found for the selected filters.\n")
            for event in events:
                file.write(format_event(event))
                file.write("-" * 60 + "\n")
            file.write(f"\nTotal events: {len(events)}\n")
    except OSError as error:
        logging.error("Unable to create output file '%s': %s", path, error)
        return False
    return True
