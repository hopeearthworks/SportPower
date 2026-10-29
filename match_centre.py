"""
Match Centre: detail for one upcoming fixture, plus bookmaker odds.

Used by the Sport Oracle modal:
    match_detail() - form, head-to-head, league table, broadcasts and news (ESPN summary)
    match_odds()   - prices from UK bookmakers (The Odds API, needs a free key) and
                     from ESPN's odds provider (DraftKings, US only) as a fallback

This app never places or takes bets. It only shows prices and links to the
bookmakers' own websites.

The Odds API key is read from the ODDS_API_KEY environment variable, or from a
file called odds_api.key in the project folder. It is never stored in config.json.
"""

import difflib
import logging
import os
import re
import threading
import time
import unicodedata
from datetime import datetime, timezone

import requests

import sports

ODDS_API = "https://api.the-odds-api.com/v4"
ODDS_KEY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "odds_api.key")
SUMMARY_CACHE_SECONDS = 300
ODDS_CACHE_SECONDS = 900          # the free Odds API tier allows 500 requests a month
SPORTS_LIST_CACHE_SECONDS = 43200
MAX_KICKOFF_GAP_HOURS = 36        # how far apart ESPN and Odds API start times may be

# ESPN league -> The Odds API sport key
FOOTBALL_ODDS_KEYS = {
    "eng.1": "soccer_epl",
    "eng.2": "soccer_efl_champ",
    "eng.fa": "soccer_fa_cup",
    "sco.1": "soccer_spl",
    "uefa.champions": "soccer_uefa_champs_league",
    "uefa.europa": "soccer_uefa_europa_league",
    "esp.1": "soccer_spain_la_liga",
    "ger.1": "soccer_germany_bundesliga",
    "ita.1": "soccer_italy_serie_a",
    "fra.1": "soccer_france_ligue_one",
    "ned.1": "soccer_netherlands_eredivisie",
    "por.1": "soccer_portugal_primeira_liga",
    "usa.1": "soccer_usa_mls",
}
# Rugby union keys vary by season, so they are looked up by title
RUGBY_TITLE_HINTS = {
    "267979": "premiership", "270557": "united rugby", "270559": "top 14",
    "271937": "champions cup", "272073": "challenge cup", "180659": "six nations",
    "244293": "rugby championship", "242041": "super rugby", "164205": "world cup",
}
BOOKMAKER_SITES = {
    "williamhill": "https://sports.williamhill.com",
    "paddypower": "https://www.paddypower.com",
    "skybet": "https://m.skybet.com",
    "betfair_ex_uk": "https://www.betfair.com/exchange",
    "betfair_sb_uk": "https://www.betfair.com/sport",
    "ladbrokes_uk": "https://sports.ladbrokes.com",
    "coral": "https://sports.coral.co.uk",
    "betvictor": "https://www.betvictor.com",
    "unibet_uk": "https://www.unibet.co.uk",
    "sport888": "https://www.888sport.com",
    "boylesports": "https://www.boylesports.com",
    "livescorebet": "https://www.livescorebet.com",
    "virginbet": "https://www.virginbet.com",
    "matchbook": "https://www.matchbook.com",
    "smarkets": "https://smarkets.com",
    "betway": "https://betway.com",
    "leovegas": "https://www.leovegas.com",
    "draftkings": "https://sportsbook.draftkings.com",
}

_cache = {}
_cache_lock = threading.Lock()


class OddsError(Exception):
    """A problem talking to The Odds API, worded for the user."""


def _cached(key, ttl, loader):
    with _cache_lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
    value = loader()
    with _cache_lock:
        _cache[key] = (time.time(), value)
    return value


def _summary(sport, league, event_id):
    url = f"https://site.api.espn.com/apis/site/v2/sports/{sports.espn_path(sport, league)}/summary"
    return _cached(("summary", sport, league, event_id), SUMMARY_CACHE_SECONDS,
                   lambda: sports.get_json(url, {"event": event_id}))


# ---------------------------------------------------------------- match detail

def _form_block(entry):
    team = entry.get("team") or {}
    games = []
    for event in entry.get("events", []):
        opponent = event.get("opponent") or {}
        venue = "H" if event.get("atVs") == "vs" else "A"
        # ESPN gives "home-away"; show it from this team's side, e.g. a 3-0 away defeat as 0-3
        score = event.get("score", "")
        parts = score.split("-")
        if venue == "A" and len(parts) == 2:
            score = f"{parts[1]}-{parts[0]}"
        games.append({
            "date": event.get("gameDate"),
            "opponent": opponent.get("displayName", ""),
            "opponent_logo": opponent.get("logo", ""),
            "venue": venue,
            "score": score,
            "result": event.get("gameResult", ""),
            "competition": event.get("competitionName", ""),
        })
    return {"team": team.get("displayName", ""), "team_id": str(team.get("id", "")),
            "logo": team.get("logo", ""), "games": games}


def _head_to_head(data):
    meetings = []
    series = (data.get("seasonseries") or [{}])[0]
    for event in series.get("events", []):            # football format
        sides = {c.get("homeAway"): c for c in event.get("competitors", [])}
        if "home" in sides and "away" in sides:
            meetings.append({
                "date": event.get("date"),
                "home": (sides["home"].get("team") or {}).get("displayName", ""),
                "away": (sides["away"].get("team") or {}).get("displayName", ""),
                "home_score": sides["home"].get("score", ""),
                "away_score": sides["away"].get("score", ""),
            })
    for block in (data.get("headToHeadGames") or [])[:1]:  # rugby format, seen from one team
        team = block.get("team") or {}
        for event in block.get("events", []):
            opponent = (event.get("opponent") or {}).get("displayName", "")
            team_is_home = str(event.get("homeTeamId")) == str(team.get("id"))
            meetings.append({
                "date": event.get("gameDate"),
                "home": team.get("displayName", "") if team_is_home else opponent,
                "away": opponent if team_is_home else team.get("displayName", ""),
                "home_score": event.get("homeTeamScore", ""),
                "away_score": event.get("awayTeamScore", ""),
            })
    meetings.sort(key=lambda m: m["date"] or "", reverse=True)
    return {"summary": series.get("summary", ""), "meetings": meetings[:6]}


def _table(groups):
    """League table rows from ESPN standings groups (summary or standings endpoint)."""
    rows = []
    for group in groups:
        for index, entry in enumerate((group.get("standings") or {}).get("entries", []), start=1):
            stats = {s.get("name"): s.get("value") for s in entry.get("stats", [])}
            pick = lambda *names: next((stats[n] for n in names if stats.get(n) is not None), 0)
            team = entry.get("team")  # a name in the summary feed, a dict in the standings feed
            rows.append({
                "rank": int(pick("rank") or index),
                "team": team.get("displayName", "") if isinstance(team, dict) else team or "",
                "team_id": str(team.get("id", "")) if isinstance(team, dict) else str(entry.get("id", "")),
                "played": int(pick("gamesPlayed")),
                "won": int(pick("wins", "gamesWon")),
                "drawn": int(pick("ties", "gamesDrawn")),
                "lost": int(pick("losses", "gamesLost")),
                "diff": int(pick("pointDifferential", "pointsDifference")),
                "points": int(pick("points")),
            })
    return sorted(rows, key=lambda r: r["rank"])


def match_detail(sport, league, event_id):
    """Extra detail for a fixture. Combat sports have no ESPN summary."""
    if sport == "combat":
        return {"available": False}
    data = _summary(sport, league, event_id)
    venue = (data.get("gameInfo") or {}).get("venue") or {}
    address = venue.get("address") or {}
    broadcasts = []
    for item in data.get("broadcasts") or []:
        name = (item.get("media") or {}).get("name") or (item.get("media") or {}).get("shortName")
        label = f"{name} ({(item.get('region') or '').upper()})" if item.get("region") else name
        if name and label not in broadcasts:
            broadcasts.append(label)
    news = [{
        "headline": article.get("headline", ""),
        "url": ((article.get("links") or {}).get("web") or {}).get("href", ""),
        "published": article.get("published", ""),
    } for article in ((data.get("news") or {}).get("articles") or [])[:4] if article.get("headline")]

    table = _table((data.get("standings") or {}).get("groups", []))
    if not table:  # rugby summaries have no table, so use the standings feed
        standings_url = f"https://site.api.espn.com/apis/v2/sports/{sports.espn_path(sport, league)}/standings"
        try:
            standings = _cached(("standings", sport, league), SUMMARY_CACHE_SECONDS,
                                lambda: sports.get_json(standings_url))
            table = _table(standings.get("children") or [standings])
        except sports.FetchError as error:
            logging.warning("[Match] %s", error)

    return {
        "available": True,
        "venue": {"name": venue.get("fullName", ""), "city": address.get("city", ""), "country": address.get("country", "")},
        "form": [_form_block(entry) for entry in data.get("lastFiveGames", [])],
        "head_to_head": _head_to_head(data),
        "table": table,
        "broadcasts": broadcasts,
        "news": news,
    }


# ---------------------------------------------------------------- game detail (results cards)

# (ESPN stat name, label, format) - "pct" values are 0-100, "ratio" values are 0-1
FOOTBALL_STATS = [
    ("possessionPct", "Possession", "pct"),
    ("totalShots", "Shots", None),
    ("shotsOnTarget", "Shots on target", None),
    ("wonCorners", "Corners", None),
    ("foulsCommitted", "Fouls", None),
    ("yellowCards", "Yellow cards", None),
    ("redCards", "Red cards", None),
    ("offsides", "Offsides", None),
    ("saves", "Saves", None),
    ("totalPasses", "Passes", None),
    ("passPct", "Pass accuracy", "ratio"),
    ("totalTackles", "Tackles", None),
    ("interceptions", "Interceptions", None),
]
CRICKET_NOTE_LABELS = {
    "toss": "Toss", "matchdays": "Match days", "matchnumber": "Match", "playerofthematch": "Player of the match",
    "umpires": "Umpires", "tvumpire": "TV umpire", "matchreferee": "Match referee", "hoursofplay": "Hours of play",
    "points": "Points", "reserveumpire": "Reserve umpire",
}


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _team_stats(data):
    teams = {t.get("homeAway"): {s.get("name"): s.get("displayValue") for s in t.get("statistics", [])}
             for t in (data.get("boxscore") or {}).get("teams", [])}
    if "home" not in teams or "away" not in teams:
        return []
    rows = []
    for name, label, kind in FOOTBALL_STATS:
        home, away = teams["home"].get(name), teams["away"].get(name)
        if home is None or away is None:
            continue
        home_value, away_value = _number(home), _number(away)
        if kind == "ratio" and home_value is not None and away_value is not None:
            home_value, away_value = home_value * 100, away_value * 100
        show = lambda value, raw: f"{value:.0f}%" if kind and value is not None else raw
        rows.append({"label": label, "home": show(home_value, home), "away": show(away_value, away),
                     "home_value": home_value or 0, "away_value": away_value or 0})
    return rows


def _key_events(data, home_id):
    events = []
    for item in data.get("keyEvents") or []:
        team = item.get("team") or {}
        names = [(p.get("athlete") or {}).get("displayName", "") for p in item.get("participants") or []]
        events.append({
            "minute": (item.get("clock") or {}).get("displayValue", ""),
            "type": (item.get("type") or {}).get("text", ""),
            "side": "home" if str(team.get("id")) == home_id else "away",
            "player": names[0] if names else item.get("shortText", ""),
            "other": names[1] if len(names) > 1 else "",
            "text": item.get("text", ""),
        })
    return events


def _lineups(data, sport):
    lineups = []
    for block in data.get("rosters") or []:
        players = []
        for entry in block.get("roster", []):
            jersey = str(entry.get("jersey") or "")
            starter = bool(entry.get("starter"))
            if sport == "rugby":  # ESPN doesn't flag rugby starters; shirts 1-15 start
                starter = jersey.isdigit() and int(jersey) <= 15
            position = entry.get("position") or {}
            players.append({
                "name": (entry.get("athlete") or {}).get("displayName", ""),
                "jersey": jersey,
                "position": position.get("abbreviation") or position.get("displayName", ""),
                "starter": starter,
                "subbed_in": bool(entry.get("subbedIn")),
                "subbed_out": bool(entry.get("subbedOut")),
                "captain": bool(entry.get("captain")),
            })
        if sport == "rugby":
            players.sort(key=lambda p: int(p["jersey"]) if p["jersey"].isdigit() else 99)
        lineups.append({
            "side": block.get("homeAway", ""),
            "team": (block.get("team") or {}).get("displayName", ""),
            "formation": block.get("formation") or "",
            "starters": [p for p in players if p["starter"]],
            "bench": [p for p in players if not p["starter"]],
        })
    return lineups


def _cricket_detail(league_id, event_id):
    url = f"https://site.api.espn.com/apis/site/v2/sports/cricket/{league_id}/summary"
    data = _cached(("cricket", league_id, event_id), SUMMARY_CACHE_SECONDS,
                   lambda: sports.get_json(url, {"event": event_id}))
    venue = (data.get("gameInfo") or {}).get("venue") or {}
    notes = [{"label": CRICKET_NOTE_LABELS[n["type"]], "text": n.get("text", "")}
             for n in data.get("notes") or [] if n.get("type") in CRICKET_NOTE_LABELS and n.get("text")]
    cards = [{
        "type": card.get("headline", ""),          # Batting / Bowling / Partnerships
        "team": card.get("teamName", ""),
        "innings": card.get("inningsNumber", ""),
        "total": card.get("total") or "",
        "runs": card.get("runs") or "",
        "extras": card.get("extras") or "",
        "rows": card.get("playerDetails") or [],
    } for card in data.get("matchcards") or []]
    news = [{"headline": a.get("headline", ""), "url": ((a.get("links") or {}).get("web") or {}).get("href", "")}
            for a in ((data.get("news") or {}).get("articles") or [])[:4] if a.get("headline")]
    return {
        "available": True,
        "venue": {"name": venue.get("fullName", ""), "city": (venue.get("address") or {}).get("city", ""),
                  "country": (venue.get("address") or {}).get("country", ""), "capacity": venue.get("capacity")},
        "notes": notes,
        "scorecards": cards,
        "news": news,
    }


def game_detail(sport, league, event_id, cricket_league=""):
    """Detail for any results card. F1 and MMA have no ESPN summary, so the
    browser builds those views from the results it already has."""
    if sport in ("f1", "combat"):
        return {"available": False}
    if sport == "cricket":
        return _cricket_detail(cricket_league or league, event_id)

    detail = match_detail(sport, league, event_id)  # venue, form, head-to-head, table, broadcasts, news
    data = _summary(sport, league, event_id)
    competitors = ((data.get("header") or {}).get("competitions") or [{}])[0].get("competitors", [])
    home_id = next((str(c.get("id") or (c.get("team") or {}).get("id"))
                    for c in competitors if c.get("homeAway") == "home"), "")
    lineups = _lineups(data, sport)
    key_events = _key_events(data, home_id)
    # ESPN doesn't say which substitution participant came on; the line-ups do
    came_on = {p["name"] for team in lineups for p in team["bench"] if p["subbed_in"]}
    for event in key_events:
        if event["type"] == "Substitution":
            if event["player"] not in came_on and event["other"] in came_on:
                event["player"], event["other"] = event["other"], event["player"]
            event["on"], event["off"] = event["player"], event["other"]
    detail.update({
        "attendance": (data.get("gameInfo") or {}).get("attendance") or 0,
        "key_events": key_events,
        "stats": _team_stats(data),
        "lineups": lineups,
    })
    return detail


# ---------------------------------------------------------------- odds

def odds_api_key():
    key = os.environ.get("ODDS_API_KEY", "").strip()
    if not key and os.path.exists(ODDS_KEY_FILE):
        with open(ODDS_KEY_FILE, encoding="utf-8") as file:
            key = file.read().strip()
    return key


def american_to_decimal(moneyline):
    moneyline = float(moneyline)
    return round(1 + (moneyline / 100 if moneyline > 0 else 100 / abs(moneyline)), 2)


def _odds_get(path, key, params):
    try:
        response = requests.get(f"{ODDS_API}{path}", params={"apiKey": key, **params}, timeout=10)
    except requests.RequestException as error:
        raise OddsError(f"Couldn't reach The Odds API: {error}") from error
    remaining = response.headers.get("x-requests-remaining")
    if remaining is not None:
        logging.info("[Odds] The Odds API requests remaining this month: %s", remaining)
    if response.status_code == 401:
        raise OddsError("The Odds API rejected the key. Check ODDS_API_KEY / odds_api.key.")
    if response.status_code == 429:
        raise OddsError("The Odds API monthly quota is used up.")
    if response.status_code in (404, 422):
        raise OddsError("The Odds API doesn't currently list this competition.")
    if not response.ok:
        raise OddsError(f"The Odds API returned an error ({response.status_code}).")
    return response.json()


def _odds_sport_key(sport, league, key):
    if sport == "football":
        return FOOTBALL_ODDS_KEYS.get(league)
    if sport == "combat":
        return "mma_mixed_martial_arts"
    if league == "nrl":
        return "rugbyleague_nrl"
    hint = RUGBY_TITLE_HINTS.get(league)
    if not hint:
        return None
    available = _cached(("odds-sports",), SPORTS_LIST_CACHE_SECONDS, lambda: _odds_get("/sports", key, {}))
    for item in available:
        text = f"{item.get('title', '')} {item.get('description', '')}".lower()
        if item.get("group", "").lower().startswith("rugby union") and hint in text:
            return item.get("key")
    return None


_NOISE = {"fc", "afc", "cf", "sc", "ac", "the", "club", "rugby", "de", "ssc"}


def _normalise(name):
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower().replace("&", " and ")
    words = [w for w in re.sub(r"[^a-z0-9 ]", " ", name).split() if w not in _NOISE]
    return " ".join(words)


def name_similarity(a, b):
    """0..1 similarity between team/fighter names from two providers."""
    a, b = _normalise(a), _normalise(b)
    if not a or not b:
        return 0.0
    if a == b or a in b or b in a:
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def find_odds_event(events, home, away, kickoff):
    """Match an ESPN fixture to an Odds API event. Returns (event, swapped) or (None, False)."""
    try:
        start = datetime.fromisoformat(kickoff.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        start = None
    best, best_score, swapped = None, 0.0, False
    for event in events:
        if start and event.get("commence_time"):
            other = datetime.fromisoformat(event["commence_time"].replace("Z", "+00:00"))
            if abs((other - start).total_seconds()) > MAX_KICKOFF_GAP_HOURS * 3600:
                continue
        straight = min(name_similarity(home, event.get("home_team", "")), name_similarity(away, event.get("away_team", "")))
        flipped = min(name_similarity(home, event.get("away_team", "")), name_similarity(away, event.get("home_team", "")))
        score, is_flipped = max((straight, False), (flipped, True))
        if score > best_score:
            best, best_score, swapped = event, score, is_flipped
    return (best, swapped) if best_score >= 0.6 else (None, False)


def bookmakers_from_odds_event(event, swapped):
    """Turn one Odds API event into rows of home / draw / away decimal prices."""
    home_name, away_name = event.get("home_team"), event.get("away_team")
    if swapped:
        home_name, away_name = away_name, home_name
    rows = []
    for bookmaker in event.get("bookmakers", []):
        market = next((m for m in bookmaker.get("markets", []) if m.get("key") == "h2h"), None)
        if not market:
            continue
        prices = {}
        for outcome in market.get("outcomes", []):
            if outcome.get("name") == "Draw":
                prices["draw"] = outcome.get("price")
            elif outcome.get("name") == home_name:
                prices["home"] = outcome.get("price")
            elif outcome.get("name") == away_name:
                prices["away"] = outcome.get("price")
        if "home" in prices and "away" in prices:
            rows.append({
                "name": bookmaker.get("title", bookmaker.get("key", "")),
                "url": BOOKMAKER_SITES.get(bookmaker.get("key"), ""),
                "region": "UK",
                "source": "The Odds API",
                "updated": bookmaker.get("last_update", ""),
                "odds": prices,
            })
    return rows


def _espn_bookmakers(sport, league, event_id):
    try:
        data = _summary(sport, league, event_id)
    except sports.FetchError as error:
        logging.warning("[Odds] %s", error)
        return []
    rows = []
    for item in data.get("pickcenter") or data.get("odds") or []:
        home = (item.get("homeTeamOdds") or {}).get("moneyLine")
        away = (item.get("awayTeamOdds") or {}).get("moneyLine")
        if home is None or away is None:
            continue  # ESPN lists some providers with no prices
        prices = {"home": american_to_decimal(home), "away": american_to_decimal(away)}
        draw = (item.get("drawOdds") or {}).get("moneyLine")
        if draw is not None:
            prices["draw"] = american_to_decimal(draw)
        name = (item.get("provider") or {}).get("name", "ESPN provider")
        rows.append({
            "name": name,
            "url": BOOKMAKER_SITES.get(re.sub(r"[^a-z]", "", name.lower()), ""),
            "region": "US",
            "source": "ESPN",
            "updated": "",
            "odds": prices,
        })
    return rows


def match_odds(sport, league, event_id, home, away, kickoff):
    """Collect bookmaker prices for one fixture. Never raises for odds problems."""
    outcomes = ["home", "away"] if sport == "combat" else ["home", "draw", "away"]
    key = odds_api_key()
    bookmakers, notes, sources = [], [], []

    if key:
        try:
            sport_key = _odds_sport_key(sport, league, key)
            if not sport_key:
                notes.append("The Odds API doesn't cover this competition.")
            else:
                events = _cached(("odds", sport_key), ODDS_CACHE_SECONDS, lambda: _odds_get(
                    f"/sports/{sport_key}/odds", key, {"regions": "uk", "markets": "h2h", "oddsFormat": "decimal"}))
                event, swapped = find_odds_event(events, home, away, kickoff)
                if event:
                    bookmakers += bookmakers_from_odds_event(event, swapped)
                    sources.append("The Odds API (UK bookmakers)")
                else:
                    notes.append("UK bookmakers haven't priced this fixture yet.")
        except OddsError as error:
            logging.warning("[Odds] %s", error)
            notes.append(str(error))

    if sport != "combat" and str(event_id).isdigit():
        espn_rows = _espn_bookmakers(sport, league, event_id)
        if espn_rows:
            bookmakers += espn_rows
            sources.append("ESPN (DraftKings, US only)")

    best = {}
    for outcome in outcomes:
        prices = [b["odds"].get(outcome) for b in bookmakers if b["odds"].get(outcome)]
        best[outcome] = max(prices) if prices else None

    # Average implied chance with the bookmaker margin removed, for comparison with the Oracle
    implied = None
    complete = [b for b in bookmakers if all(b["odds"].get(o) for o in outcomes)]
    if complete:
        totals = {o: 0.0 for o in outcomes}
        for row in complete:
            raw = {o: 1 / row["odds"][o] for o in outcomes}
            overround = sum(raw.values())
            for o in outcomes:
                totals[o] += raw[o] / overround
        implied = {o: round(totals[o] / len(complete), 3) for o in outcomes}

    logging.info("[Odds] %s v %s: %d bookmaker prices", home, away, len(bookmakers))
    return {
        "outcomes": outcomes,
        "bookmakers": bookmakers,
        "best": best,
        "implied": implied,
        "sources": sources,
        "notes": notes,
        "odds_api_configured": bool(key),
        "retrieved": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
