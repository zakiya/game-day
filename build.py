#!/usr/bin/env python3
"""Build the game day page from the Coda "Games-v2" table.

Reads the next home game out of Coda, works out the game day schedule, and writes
a static index.html that is published on GitHub Pages and embedded in an iframe.

Usage:
    python build.py                       # write docs/index.html
    python build.py --dry-run             # print the HTML instead
    python build.py --now 2026-09-01T12:00:00   # pretend it is another day
"""

import argparse
import html
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# --- Config ------------------------------------------------------------------

DOC_ID = "pQDDPbM3ln"
TABLE_ID = "grid-F2y2HWKh39"  # Games-v2
API_BASE = "https://coda.io/apis/v1"

TIMEZONE = ZoneInfo("America/Los_Angeles")

PARKING_URL = "https://am.ticketmaster.com/sdwave/stmparkingdiscount"

# Schedule rules. Any of these can be overridden per game by the matching
# "Actual ..." column in Coda.
WEEKDAY_LOTS_OPEN = time(16, 0)  # Mon-Fri: parking lots open at a fixed time
WEEKEND_LOTS_BEFORE_KICKOFF = timedelta(hours=4)
TAILGATE_AFTER_LOTS = timedelta(minutes=30)
TEAM_STORE_BEFORE_KICKOFF = timedelta(hours=2, minutes=30)
GATES_BEFORE_KICKOFF = timedelta(minutes=90)
COVE_SETUP_BEFORE_GATES = timedelta(hours=1)

# How long a game stays on the page after kickoff.
GAME_VISIBLE_AFTER_KICKOFF = timedelta(hours=3)

NO_GAME_MESSAGE = "Check back soon for the next home game."

ROOT = Path(__file__).resolve().parent


# --- Coda ---------------------------------------------------------------------


def api_token():
    token = os.environ.get("CODA")
    if token:
        return token.strip()
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() == "CODA":
                return value.strip().strip("\"'")
    sys.exit("No Coda API token: set the CODA environment variable or add it to .env")


def fetch_rows(token):
    """Every row of Games-v2, in table order."""
    url = (
        f"{API_BASE}/docs/{DOC_ID}/tables/{TABLE_ID}/rows"
        "?useColumnNames=true&valueFormat=rich&sortBy=natural&limit=100"
    )
    rows = []
    while url:
        request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as error:
            sys.exit(f"Coda API error {error.code}: {error.read().decode('utf-8', 'replace')[:300]}")
        rows.extend(payload.get("items", []))
        url = payload.get("nextPageLink")
    return rows


# --- Cell parsing -------------------------------------------------------------


def link_url(value):
    """URL out of a link column, or None when the cell is blank."""
    if isinstance(value, dict):
        url = value.get("url")
        return url or None
    if isinstance(value, str) and value.startswith("http"):
        return value
    return None


def plain_text(value):
    """Coda's rich text comes back wrapped in markdown fences and bold markers."""
    if not isinstance(value, str):
        return ""
    return value.strip().strip("`").replace("**", "").strip()


def parse_datetime(value):
    """A "Full date" cell, e.g. 2026-08-14T19:00:00.000-07:00."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def wall_time(value):
    """A time-only cell, e.g. 1899-12-30T14:45:00.000-08:00 -> time(14, 45).

    Coda stamps these with a fixed 1899 date and PST offset, so only the
    wall-clock part is meaningful - converting the timezone would shift DST
    games by an hour.
    """
    if not isinstance(value, str) or not value:
        return None
    match = re.search(r"T(\d{2}):(\d{2})", value)
    if not match:
        return None
    return time(int(match.group(1)), int(match.group(2)))


# --- Schedule -----------------------------------------------------------------


def next_home_game(rows, now):
    """The soonest game that has not finished yet, or None."""
    upcoming = []
    for row in rows:
        kickoff = parse_datetime(row.get("values", {}).get("Full date"))
        if kickoff and kickoff + GAME_VISIBLE_AFTER_KICKOFF >= now:
            upcoming.append((kickoff, row))
    if not upcoming:
        return None, None
    upcoming.sort(key=lambda pair: pair[0])
    return upcoming[0]


def build_schedule(values, kickoff):
    """[(label, datetime)] for the game, sorted chronologically."""

    def override(column):
        clock = wall_time(values.get(column))
        if clock is None:
            return None
        return kickoff.replace(hour=clock.hour, minute=clock.minute, second=0, microsecond=0)

    is_weekday = kickoff.weekday() < 5  # Monday-Friday
    if is_weekday:
        default_lots = kickoff.replace(
            hour=WEEKDAY_LOTS_OPEN.hour, minute=WEEKDAY_LOTS_OPEN.minute, second=0, microsecond=0
        )
    else:
        default_lots = kickoff - WEEKEND_LOTS_BEFORE_KICKOFF

    lots = override("Actual Parking lot open") or default_lots
    tailgate = override("Actual tailgate start") or lots + TAILGATE_AFTER_LOTS
    team_store = override("Actual team store start") or kickoff - TEAM_STORE_BEFORE_KICKOFF
    gates = override("Actual gates open") or kickoff - GATES_BEFORE_KICKOFF
    cove = override("Actual cove set up") or gates - COVE_SETUP_BEFORE_GATES
    fan_fest = override("Fan fest start")  # no default - only shown when set in Coda

    schedule = [
        ("Parking lots open", lots),
        ("Tailgate starts", tailgate),
        ("Team store opens", team_store),
        ("Cove set up", cove),
        ("Gates open", gates),
        ("Kickoff", kickoff),
    ]
    if fan_fest:
        schedule.append(("Fan fest starts", fan_fest))
    schedule.sort(key=lambda item: item[1])
    return schedule


def build_links(values):
    """[(label, url)] for the important links, skipping the blank ones."""
    candidates = [
        ("Volunteer in the Cove", link_url(values.get("Cove link"))),
        ("Read the latest newsletter", link_url(values.get("Newsletter link"))),
        ("Buy discounted parking", PARKING_URL),
        ("Tailgate sign up", link_url(values.get("Tailgate food link"))),
    ]
    return [(label, url) for label, url in candidates if url]


# --- Rendering ----------------------------------------------------------------


def format_time(moment):
    """4:00 PM - no leading zero, on every platform."""
    hour = moment.hour % 12 or 12
    meridiem = "AM" if moment.hour < 12 else "PM"
    return f"{hour}:{moment.minute:02d} {meridiem}"


def game_heading(kickoff, team):
    """Friday, August 14 vs. Denver Summit"""
    date = f"{kickoff:%A, %B} {kickoff.day}"
    return f"{date} vs. {team}" if team else date


def render(schedule, links, theme="", heading=""):
    if not schedule:
        body = f'    <p class="no-game">{html.escape(NO_GAME_MESSAGE)}</p>'
    else:
        heading_block = f"    <h2>{html.escape(heading)}</h2>\n\n" if heading else ""
        theme_block = f'    <p class="theme">{html.escape(theme)}</p>\n\n' if theme else ""
        schedule_items = "\n".join(
            f"        <li><strong>{html.escape(label)}</strong> {format_time(moment)}</li>"
            for label, moment in schedule
        )
        link_items = "\n".join(
            f'        <li><a href="{html.escape(url, quote=True)}" target="_blank">'
            f"{html.escape(label)}</a></li>"
            for label, url in links
        )
        body = f"""{heading_block}{theme_block}    <section class="schedule">
      <h4>Schedule</h4>
      <ul>
{schedule_items}
      </ul>
    </section>

    <section class="links">
      <h4>Important links</h4>
      <ul>
{link_items}
      </ul>
    </section>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Game day</title>
  <link rel="stylesheet" href="styles.css">
</head>
<body>
  <main class="game-day">
{body}
  </main>
</body>
</html>
"""


# --- Entry point --------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--now", help="ISO timestamp to treat as the current time (for testing)")
    parser.add_argument("--out", default="docs", help="output directory (default: docs)")
    parser.add_argument("--dry-run", action="store_true", help="print the HTML instead of writing")
    args = parser.parse_args()

    if args.now:
        now = datetime.fromisoformat(args.now)
        if now.tzinfo is None:
            now = now.replace(tzinfo=TIMEZONE)
    else:
        now = datetime.now(TIMEZONE)

    rows = fetch_rows(api_token())
    kickoff, row = next_home_game(rows, now)

    if row is None:
        print(f"No upcoming home game as of {now:%Y-%m-%d %H:%M %Z}")
        schedule, links, theme, heading = [], [], "", ""
    else:
        values = row.get("values", {})
        schedule = build_schedule(values, kickoff)
        links = build_links(values)
        theme = plain_text(values.get("Theme"))
        heading = game_heading(kickoff, plain_text(values.get("Team")))
        print(f"Next home game: {heading} ({kickoff.year})")
        if theme:
            print(f"Theme: {theme}")
        for label, moment in schedule:
            print(f"  {label:<20} {format_time(moment)}")
        for label, url in links:
            print(f"  link: {label} -> {url}")

    document = render(schedule, links, theme, heading)

    if args.dry_run:
        print()
        print(document)
        return

    out_dir = ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "index.html").write_text(document)
    print(f"Wrote {out_dir / 'index.html'}")


if __name__ == "__main__":
    main()
