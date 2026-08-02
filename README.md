# Game day page

Generates `docs/index.html` — the next home game's schedule and important links — from the
The page is published on GitHub Pages and embedded in an iframe.

## Running it locally

```bash
python3 build.py            # writes docs/index.html
python3 build.py --dry-run  # prints the HTML instead
python3 build.py --now 2026-09-01T12:00:00 --dry-run   # pretend it is another day
```

## Schedule rules

Every time below is a default; the matching **"Actual …"** column in Coda overrides it.
Derived times chain off the resolved value, so an override to the parking lot time also
moves the tailgate.

| Line | Default |
| --- | --- |
| Parking lots open | Mon–Fri: 4:00 PM · Sat–Sun: kickoff − 4 hours |
| Tailgate starts | parking lots + 30 minutes |
| Team store opens | kickoff − 2.5 hours |
| Cove set up | gates open − 1 hour |
| Gates open | kickoff − 90 minutes |
| Fan fest starts | **no default** — only shown when `Fan fest start` is set in Coda |
| Kickoff | the `Full date` column |

Lines are sorted chronologically. The rules and the hardcoded parking link live in the
config block at the top of `build.py`.

## Links

| Label | Source | Blank in Coda |
| --- | --- | --- |
| Volunteer in the Cove | `Cove link` | omitted |
| Read the latest newsletter | `Newsletter link` | omitted |
| Buy discounted parking | hardcoded `PARKING_URL` in `build.py` | always shown |
| Tailgate sign up | `Tailgate food link` | omitted |

## Which game is shown

The soonest row in Games-v2 whose kickoff is less than 3 hours ago — so the page stays
correct during the match itself. Every row in Games-v2 is treated as a home game. When
no games remain, the page shows "Check back soon for the next home game."

## Rebuilding

`.github/workflows/build.yml` runs `build.py` and commits `docs/index.html` only when it
actually changed. It fires on:

- **Mondays at 8:00 AM Pacific** (cron).
- **Manually** — Actions tab → "Build game day page" → *Run workflow*.
- **`repository_dispatch`** with event type `coda-update` — for the Coda push below.
- Pushes that touch `build.py`, `docs/styles.css`, or the workflow itself.
