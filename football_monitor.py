"""
Football Match Monitor.

Retrieves fixtures and results from the ESPN soccer scoreboard API,
applies business rules from config.json, writes a text report and
records activity in automation.log.

Setting "sport" in the config to rugby, f1, cricket or combat runs the
same steps for that sport using sports.py.

Usage:
    python football_monitor.py                 # uses config.json
    python football_monitor.py my_config.json  # uses another config file
"""

import json
import logging
import os
import re
import sys
from datetime import datetime, timezone

import requests

import sports

API_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/scoreboard"
DEFAULT_CONFIG_FILE = "config.json"
LOG_FILE = "automation.log"
VALID_STATUSES = ("all", "completed", "scheduled", "live")

logging.basicConfig(
    filename=os.path.join(os.path.dirname(os.path.abspath(__file__)), LOG_FILE),
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)


def load_config(config_file):
    """Load and validate configuration settings. Returns None if invalid."""
    try:
        with open(config_file, encoding="utf-8") as file:
            config = json.load(file)
    except FileNotFoundError:
        message = f"Configuration file not found: {config_file}"
        print(message)
        logging.error(message)
        return None
    except json.JSONDecodeError as error:
        message = f"Configuration file contains invalid JSON: {error}"
        print(message)
        logging.error(message)
        return None

    config, errors = validate_config(config)
    if errors:
        for error in errors:
            print(f"Configuration error: {error}")
            logging.error("Configuration error: %s", error)
        return None
    return config


def validate_config(config):
    """Clean and check config values. Returns (cleaned_config, list_of_errors)."""
    sport = str(config.get("sport", "football") or "football").strip().lower()
    league = str(config.get("league", "")).strip().lower()
    output_file = str(config.get("output_file", "")).strip()
    team_filter = str(config.get("team_filter", "") or "").strip()
    match_status = str(config.get("match_status", "all")).strip().lower()
    match_date = str(config.get("date", "") or "").strip()

    errors = []
    league_error = sports.validate_league(sport, league)
    if league_error:
        errors.append(league_error)
    if not output_file:
        errors.append("'output_file' is required.")
    if match_status not in VALID_STATUSES:
        errors.append(f"'match_status' must be one of: {', '.join(VALID_STATUSES)}.")
    if match_date and not re.fullmatch(r"\d{8}", match_date):
        errors.append("'date' must be blank or in YYYYMMDD format.")

    cleaned = {
        "sport": sport,
        "league": league,
        "team_filter": team_filter,
        "output_file": output_file,
        "match_status": match_status,
        "date": match_date,
    }
    return cleaned, errors


def get_scoreboard(league, match_date=""):
    """Call the ESPN API and return the scoreboard JSON, or None on failure."""
    url = API_URL.format(league=league)
    params = {"dates": match_date} if match_date else None
    logging.info("Requesting %s (date=%s)", url, match_date or "current matchday")

    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.HTTPError as error:
        message = f"API returned an error for league '{league}': {error}"
    except requests.RequestException as error:
        message = f"Unable to connect to the ESPN API: {error}"
    except ValueError:
        message = "The ESPN API returned data that is not valid JSON."

    print(message)
    logging.error(message)
    return None


def extract_matches(scoreboard):
    """Convert the raw ESPN JSON into a simple list of match dictionaries."""
    matches = []
    for event in scoreboard.get("events", []):
        competition = event["competitions"][0]
        status = competition["status"]["type"]
        teams = {team["homeAway"]: team for team in competition["competitors"]}
        home, away = teams["home"], teams["away"]
        side_by_team_id = {home["team"]["id"]: "home", away["team"]["id"]: "away"}

        # Goals and red cards, used by the web UI (not needed for the text report)
        incidents = []
        for detail in competition.get("details", []):
            if not (detail.get("scoringPlay") or detail.get("redCard")):
                continue
            players = detail.get("athletesInvolved") or [{}]
            incidents.append({
                "type": "red_card" if detail.get("redCard") else "goal",
                "label": detail.get("type", {}).get("text", ""),
                "minute": detail.get("clock", {}).get("displayValue", ""),
                "player": players[0].get("displayName", "Unknown"),
                "side": side_by_team_id.get(detail.get("team", {}).get("id"), "home"),
            })

        matches.append({
            "id": event.get("id"),
            "home_logo": home["team"].get("logo", ""),
            "away_logo": away["team"].get("logo", ""),
            "home_abbr": home["team"].get("abbreviation", ""),
            "away_abbr": away["team"].get("abbreviation", ""),
            "home_color": home["team"].get("color", ""),
            "away_color": away["team"].get("color", ""),
            "home_form": home.get("form", ""),
            "away_form": away.get("form", ""),
            "home_record": (home.get("records") or [{}])[0].get("summary", ""),
            "away_record": (away.get("records") or [{}])[0].get("summary", ""),
            "clock": competition["status"].get("displayClock", ""),
            "short_status": status.get("shortDetail", ""),
            "incidents": incidents,
            "date": event["date"],
            "home_team": home["team"]["displayName"],
            "away_team": away["team"]["displayName"],
            "home_score": home.get("score", "0"),
            "away_score": away.get("score", "0"),
            "state": status["state"],          # pre / in / post
            "completed": status["completed"],  # False for postponed matches
            "status": status["description"],   # e.g. "Full Time", "Scheduled"
            "venue": competition.get("venue", {}).get("fullName", "Unknown venue"),
        })
    return matches


def filter_matches(matches, team_filter="", match_status="all"):
    """Apply the business rules: team filter and match status filter."""
    results = []
    for match in matches:
        if team_filter:
            team_names = f"{match['home_team']} {match['away_team']}".lower()
            if team_filter.lower() not in team_names:
                continue

        if match_status == "completed" and not match["completed"]:
            continue
        if match_status == "scheduled" and match["state"] != "pre":
            continue
        if match_status == "live" and match["state"] != "in":
            continue

        results.append(match)
    return results


def format_match(match):
    """Return a readable description of one match."""
    kickoff = datetime.strptime(match["date"], "%Y-%m-%dT%H:%MZ")
    kickoff_text = kickoff.strftime("%a %d %b %Y, %H:%M UTC")

    if match["state"] == "pre":
        line = f"{match['home_team']} vs {match['away_team']}"
    else:
        line = (f"{match['home_team']} {match['home_score']} - "
                f"{match['away_score']} {match['away_team']}")

    return (f"{line}\n"
            f"  Status : {match['status']}\n"
            f"  Kickoff: {kickoff_text}\n"
            f"  Venue  : {match['venue']}\n")


def save_report(matches, config, league_name, output_file):
    """Write the match report to a text file. Returns True on success."""
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    try:
        with open(output_file, "w", encoding="utf-8") as file:
            file.write("Football Match Report\n")
            file.write("=" * 50 + "\n")
            file.write(f"League      : {league_name} ({config['league']})\n")
            file.write(f"Date        : {config['date'] or 'Current matchday'}\n")
            file.write(f"Team filter : {config['team_filter'] or 'None'}\n")
            file.write(f"Status      : {config['match_status']}\n")
            file.write(f"Generated   : {generated}\n")
            file.write("=" * 50 + "\n\n")

            if not matches:
                file.write("No matches found for the selected filters.\n")
            for match in matches:
                file.write(format_match(match))
                file.write("-" * 50 + "\n")

            file.write(f"\nTotal matches: {len(matches)}\n")
    except OSError as error:
        message = f"Unable to create output file '{output_file}': {error}"
        print(message)
        logging.error(message)
        return False
    return True


def run_other_sport(config):
    """Run the automation for rugby, F1, cricket or combat sports via sports.py."""
    try:
        result = sports.fetch_events(config["sport"], config["league"], config["date"])
    except sports.FetchError as error:
        print(error)
        logging.error("Script stopped: %s", error)
        return 1

    all_events = result["events"]
    logging.info("Number of events processed: %d", len(all_events))
    events = sports.filter_events(all_events, config["team_filter"], config["match_status"])
    logging.info("Events after filters (search=%s, status=%s): %d",
                 config["team_filter"] or "None", config["match_status"], len(events))

    if not sports.write_report(config["output_file"], config["sport"], result, events, config):
        print(f"Unable to create output file '{config['output_file']}'")
        logging.error("Script stopped: report could not be written.")
        return 1
    logging.info("Output file created: %s", config["output_file"])

    print(f"{result['name']}: {len(events)} of {len(all_events)} events "
          f"written to {config['output_file']}")
    for event in events:
        print(sports.format_event(event))
    logging.info("Script completed successfully.")
    return 0


def main(config_file=DEFAULT_CONFIG_FILE):
    """Run the automation. Returns 0 on success, 1 on failure."""
    logging.info("Script started (config=%s)", config_file)

    config = load_config(config_file)
    if config is None:
        logging.error("Script stopped: configuration could not be loaded.")
        return 1
    logging.info("Configuration loaded")
    logging.info("Sport selected: %s", config["sport"])
    logging.info("League selected: %s", config["league"])

    if config["sport"] != "football":
        return run_other_sport(config)

    scoreboard = get_scoreboard(config["league"], config["date"])
    if scoreboard is None:
        logging.error("Script stopped: no data retrieved from API.")
        return 1

    leagues = scoreboard.get("leagues") or [{}]
    league_name = leagues[0].get("name", config["league"])

    all_matches = extract_matches(scoreboard)
    logging.info("Number of matches processed: %d", len(all_matches))

    matches = filter_matches(all_matches, config["team_filter"], config["match_status"])
    logging.info("Matches after filters (team=%s, status=%s): %d",
                 config["team_filter"] or "None", config["match_status"], len(matches))

    if config["team_filter"] and not matches:
        teams = sorted({m["home_team"] for m in all_matches} |
                       {m["away_team"] for m in all_matches})
        logging.warning("No matches found for team '%s'. Teams in this data: %s",
                        config["team_filter"], ", ".join(teams) or "none")

    if not save_report(matches, config, league_name, config["output_file"]):
        logging.error("Script stopped: report could not be written.")
        return 1
    logging.info("Output file created: %s", config["output_file"])

    print(f"{league_name}: {len(matches)} of {len(all_matches)} matches "
          f"written to {config['output_file']}")
    for match in matches:
        print(format_match(match))

    logging.info("Script completed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CONFIG_FILE))
