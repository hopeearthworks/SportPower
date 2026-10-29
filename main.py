"""
Sports Monitor - web UI (entry point).

Serves the dashboard in ./web and a small JSON API. Validation and logging
come from football_monitor.py and the sport data from sports.py, so the UI
follows the same business rules as the command-line automation.

Usage:
    python main.py              # opens http://localhost:8080
"""

import json
import logging
import os
import re
import threading
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import football_monitor as monitor
import match_centre
import oracle
import sports

HOST = "127.0.0.1"
PORT = 8080
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(BASE_DIR, "web")
CONFIG_FILE = os.path.join(BASE_DIR, monitor.DEFAULT_CONFIG_FILE)
LOG_FILE = os.path.join(BASE_DIR, monitor.LOG_FILE)


def safe_output_path(output_file):
    """Only allow plain file names, written inside the project folder."""
    name = os.path.basename(output_file)
    if not name or name != output_file or not name.endswith(".txt"):
        return None
    return os.path.join(BASE_DIR, name)


class MonitorHandler(SimpleHTTPRequestHandler):
    """Serves static files from ./web and JSON from /api/*."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=WEB_DIR, **kwargs)

    def log_message(self, format, *args):
        pass  # keep the console quiet; activity goes to automation.log

    # ---------- helpers ----------

    def send_json(self, data, status=200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_json_body(self):
        """Read a JSON request body. Requiring application/json blocks
        cross-site form posts from other websites."""
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            return None
        length = int(self.headers.get("Content-Length", 0))
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return None

    def fetch(self, settings, status="all"):
        """Validate settings, fetch the sport's data and apply the business rules.

        Returns (config, result, events, error_response).
        """
        config, errors = monitor.validate_config({"output_file": "ui.txt", **settings})
        if errors:
            return None, None, None, ({"error": " ".join(errors)}, 400)
        try:
            result = sports.fetch_events(config["sport"], config["league"], config["date"])
        except sports.FetchError as error:
            logging.error("[UI] %s", error)
            return None, None, None, ({"error": str(error)}, 502)

        events = sports.filter_events(result["events"], config["team_filter"], status)
        logging.info("[UI] %s / %s: %d events processed, %d after filters",
                     config["sport"], config["league"], len(result["events"]), len(events))
        return config, result, events, None

    # ---------- routes ----------

    def do_GET(self):
        url = urlparse(self.path)
        query = {key: values[0] for key, values in parse_qs(url.query).items()}

        if url.path == "/api/sports":
            return self.send_json(sports.SPORTS)

        if url.path == "/api/config":
            config = monitor.load_config(CONFIG_FILE)
            if config is None:
                return self.send_json({"error": "config.json is missing or invalid"}, 500)
            return self.send_json(config)

        if url.path == "/api/events":
            settings = {
                "sport": query.get("sport", "football"),
                "league": query.get("league", ""),
                "team_filter": query.get("search", ""),
                "date": query.get("date", ""),
            }
            # Status is filtered in the browser so the status tabs switch instantly
            config, result, events, error = self.fetch(settings)
            if error:
                return self.send_json(*error)
            names = sorted({name for event in result["events"] for name in sports.participant_names(event)})
            return self.send_json({
                "sport": config["sport"],
                "league": config["league"],
                "name": result["name"],
                "logo": result["logo"],
                "season": result["season"],
                "total": len(result["events"]),
                "competitions": result["competitions"],
                "names": names,
                "events": events,
            })

        if url.path == "/api/oracle":
            sport = query.get("sport", "football")
            league = query.get("league", "")
            if sport not in oracle.oracle_sports():
                return self.send_json({"error": f"Sport Oracle supports: {', '.join(oracle.oracle_sports())}."}, 400)
            league_error = sports.validate_league(sport, league)
            if league_error:
                return self.send_json({"error": league_error}, 400)
            try:
                return self.send_json(oracle.forecast(sport, league, fresh=query.get("fresh") == "1"))
            except sports.FetchError as error:
                logging.error("[Oracle] %s", error)
                return self.send_json({"error": str(error)}, 502)

        if url.path == "/api/game":
            sport = query.get("sport", "")
            league = query.get("league", "")
            event_id = query.get("id", "")
            cricket_league = query.get("cleague", "")
            league_error = sports.validate_league(sport, league)
            if league_error:
                return self.send_json({"error": league_error}, 400)
            if not event_id.isdigit() or (cricket_league and not cricket_league.isdigit()):
                return self.send_json({"error": "'id' must be an ESPN event id."}, 400)
            try:
                return self.send_json(match_centre.game_detail(sport, league, event_id, cricket_league))
            except sports.FetchError as error:
                logging.error("[Game] %s", error)
                return self.send_json({"error": str(error)}, 502)

        if url.path in ("/api/match", "/api/odds"):
            sport = query.get("sport", "")
            league = query.get("league", "")
            event_id = query.get("id", "")
            if sport not in oracle.oracle_sports():
                return self.send_json({"error": f"Supported sports: {', '.join(oracle.oracle_sports())}."}, 400)
            league_error = sports.validate_league(sport, league)
            if league_error:
                return self.send_json({"error": league_error}, 400)
            if not re.fullmatch(r"\d+(-\d+)?", event_id):
                return self.send_json({"error": "'id' must be an ESPN event id."}, 400)
            try:
                if url.path == "/api/match":
                    return self.send_json(match_centre.match_detail(sport, league, event_id))
                return self.send_json(match_centre.match_odds(
                    sport, league, event_id, query.get("home", ""), query.get("away", ""), query.get("date", "")))
            except sports.FetchError as error:
                logging.error("[Match] %s", error)
                return self.send_json({"error": str(error)}, 502)

        if url.path == "/api/log":
            lines = []
            if os.path.exists(LOG_FILE):
                with open(LOG_FILE, encoding="utf-8", errors="replace") as file:
                    lines = file.read().splitlines()[-150:]
            return self.send_json({"lines": lines})

        if url.path.startswith("/api/"):
            return self.send_json({"error": "Not found"}, 404)

        return super().do_GET()

    def do_POST(self):
        url = urlparse(self.path)
        body = self.read_json_body()
        if body is None:
            return self.send_json({"error": "Expected a JSON request body"}, 400)

        if url.path == "/api/config":
            config, errors = monitor.validate_config(body)
            if not errors and safe_output_path(config["output_file"]) is None:
                errors.append("'output_file' must be a plain .txt file name.")
            if errors:
                return self.send_json({"error": " ".join(errors)}, 400)
            with open(CONFIG_FILE, "w", encoding="utf-8") as file:
                json.dump(config, file, indent=4)
            logging.info("[UI] Configuration saved: %s", config)
            return self.send_json(config)

        if url.path == "/api/report":
            output_path = safe_output_path(str(body.get("output_file", "")))
            if output_path is None:
                return self.send_json({"error": "'output_file' must be a plain .txt file name."}, 400)
            config, result, events, error = self.fetch(body, str(body.get("match_status", "all")).lower())
            if error:
                return self.send_json(*error)
            if not sports.write_report(output_path, config["sport"], result, events, config):
                return self.send_json({"error": "Unable to write the report file"}, 500)
            name = os.path.basename(output_path)
            logging.info("[UI] Output file created: %s", name)
            with open(output_path, encoding="utf-8") as file:
                return self.send_json({"file": name, "content": file.read()})

        return self.send_json({"error": "Not found"}, 404)


def main():
    try:
        server = ThreadingHTTPServer((HOST, PORT), MonitorHandler)
    except OSError as error:
        print(f"Unable to start on port {PORT} (is it already in use?): {error}")
        logging.error("[UI] Unable to start web server on port %d: %s", PORT, error)
        return
    logging.info("[UI] Web server started on http://localhost:%d", PORT)
    print(f"Sports Monitor UI running at http://localhost:{PORT}  (Ctrl+C to stop)")
    threading.Timer(0.5, webbrowser.open, args=(f"http://localhost:{PORT}",)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
    finally:
        server.server_close()
        logging.info("[UI] Web server stopped")


if __name__ == "__main__":
    main()
