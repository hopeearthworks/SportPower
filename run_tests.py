"""
Test harness for the Football Match Monitor.

Runs each workshop test scenario against the live ESPN API using a
temporary config file, compares expected and actual results, and
writes a summary to test_results.txt.
"""

import json
import os
from datetime import datetime

import football_monitor

RESULTS_FILE = "test_results.txt"
TEST_CONFIG = "test_config.json"
TEST_REPORT = "test_report.txt"


def run_with_config(config):
    """Write a temporary config (or none) and run the monitor. Returns exit code."""
    for path in (TEST_CONFIG, TEST_REPORT):
        if os.path.exists(path):
            os.remove(path)
    if config is not None:
        with open(TEST_CONFIG, "w", encoding="utf-8") as file:
            json.dump(config, file)
    return football_monitor.main(TEST_CONFIG)


def read_report():
    """Return the generated test report text, or an empty string."""
    if not os.path.exists(TEST_REPORT):
        return ""
    with open(TEST_REPORT, encoding="utf-8") as file:
        return file.read()


def base_config(**overrides):
    """Return a standard test config with any overridden settings."""
    config = {
        "league": "sco.1",
        "team_filter": "",
        "output_file": TEST_REPORT,
        "match_status": "all",
        "date": "20261025",
    }
    config.update(overrides)
    return config


def test_scottish_league():
    code = run_with_config(base_config())
    report = read_report()
    passed = code == 0 and "Scottish Premiership" in report and "Total matches: 0" not in report
    return passed, "Report lists Scottish Premiership matches", f"exit={code}"


def test_english_league():
    code = run_with_config(base_config(league="eng.1", date=""))
    report = read_report()
    passed = code == 0 and "Premier League" in report
    return passed, "Report lists Premier League matches", f"exit={code}"


def test_valid_team():
    code = run_with_config(base_config(team_filter="Celtic"))
    report = read_report()
    match_lines = [line for line in report.splitlines() if " - " in line or " vs " in line]
    passed = code == 0 and bool(match_lines) and all("Celtic" in line for line in match_lines)
    return passed, "Only Celtic matches in report", f"{len(match_lines)} match(es), exit={code}"


def test_invalid_team():
    code = run_with_config(base_config(team_filter="Not A Real Team FC"))
    report = read_report()
    passed = code == 0 and "No matches found" in report
    return passed, "Report says no matches found, no crash", f"exit={code}"


def test_completed_only():
    code = run_with_config(base_config(match_status="completed"))
    report = read_report()
    statuses = [line for line in report.splitlines() if line.startswith("  Status")]
    passed = code == 0 and bool(statuses) and all("Full Time" in s for s in statuses)
    return passed, "Only completed matches in report", f"{len(statuses)} match(es), exit={code}"


def test_missing_config():
    code = run_with_config(None)
    passed = code == 1 and not os.path.exists(TEST_REPORT)
    return passed, "Friendly error, exit code 1, no traceback", f"exit={code}"


def test_invalid_league():
    code = run_with_config(base_config(league="xyz.9", date=""))
    passed = code == 1
    return passed, "API error handled, exit code 1", f"exit={code}"


def test_invalid_config_value():
    code = run_with_config(base_config(match_status="finished"))
    passed = code == 1
    return passed, "Config validation rejects bad value", f"exit={code}"


TESTS = [
    ("Scottish league (sco.1)", test_scottish_league),
    ("English league (eng.1)", test_english_league),
    ("Valid team (Celtic)", test_valid_team),
    ("Invalid team", test_invalid_team),
    ("Completed matches only", test_completed_only),
    ("Missing configuration file", test_missing_config),
    ("Invalid league code", test_invalid_league),
    ("Invalid config value", test_invalid_config_value),
]


def main():
    rows = []
    for name, test in TESTS:
        try:
            passed, expected, actual = test()
        except Exception as error:  # a crash is a test failure, not a harness failure
            passed, expected, actual = False, "No crash", f"{type(error).__name__}: {error}"
        rows.append((name, expected, actual, "Pass" if passed else "Fail"))

    for path in (TEST_CONFIG, TEST_REPORT):
        if os.path.exists(path):
            os.remove(path)

    passed_count = sum(1 for row in rows if row[3] == "Pass")
    with open(RESULTS_FILE, "w", encoding="utf-8") as file:
        file.write("Football Match Monitor - Test Results\n")
        file.write(f"Run at: {datetime.now():%Y-%m-%d %H:%M}\n")
        file.write("=" * 100 + "\n")
        file.write(f"{'Test':<30}{'Expected':<44}{'Actual':<20}Result\n")
        file.write("-" * 100 + "\n")
        for name, expected, actual, result in rows:
            file.write(f"{name:<30}{expected:<44}{actual:<20}{result}\n")
        file.write("-" * 100 + "\n")
        file.write(f"{passed_count}/{len(rows)} tests passed\n\n")

        file.write("Workshop checklist\n")
        file.write("-" * 30 + "\n")
        results = {name: result for name, _, _, result in rows}
        checklist = {
            "API returned data": results["Scottish league (sco.1)"],
            "Report created": results["Scottish league (sco.1)"],
            "Team filter worked": results["Valid team (Celtic)"],
            "Configuration loaded": results["Scottish league (sco.1)"],
        }
        for item, result in checklist.items():
            file.write(f"{item:<25}{result}\n")

    print(f"{passed_count}/{len(rows)} tests passed - see {RESULTS_FILE}")
    return 0 if passed_count == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
