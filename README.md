# GymLog

A gym management and workout-tracking web app for **one trainer and their
members**, built to be used on a phone on the gym floor.

**Live:** https://gymlog-jtd8.onrender.com

---

## What it does

The trainer and the member need opposite things, so the app gives them two
different interfaces over one database.

| | Member | Trainer |
|---|---|---|
| Opens it | 1–2 times a week, between sets | every day |
| Needs | **speed** — log a set with almost no typing | **scanning** — spot the 3 people who need attention out of 30 |

**Member:** picks exercises from a categorised library, sets a weekly split
(Monday = chest + arms), and logs sets from a screen showing only *today's*
exercises — with the last session's weight pre-filled and +/− steppers, so the
common case needs zero typing.

**Trainer:** a roster showing who owes money, whose membership expires this
week, who has stopped attending and who has stopped progressing — plus one-tap
attendance, membership and dues tracking, strength targets, and factual talking
points for each member.

## Feature summary

- **Exercise library** — 48 seeded exercises across 6 body parts and 3
  difficulty levels, editable by the trainer
- **Weekly split** — multiple body parts per weekday; the log screen follows it
- **Fast logging** — numeric keypads, steppers, auto-carry of last weight
- **Attendance** — one-tap "Today" screen; drives the inactivity warnings
- **Membership & dues** — plans, expiry, paid/pending, renewal history
- **Stall detection** — flags lifts that have plateaued over 3 sessions
- **Talking points** — up to 3 true, non-judgemental lines per member
- **Overload targets** — trainer-set goals, checkable against the logs
- **Body weight** — trend and rate only, deliberately never a progress bar
- **Account recovery** — trainer resets member passwords; break-glass CLI for
  total lockout
- **Edit your own account** — any user can change their own name and login email
  from **Account → Your details** (turns the seeded demo trainer into a real one)
- **Archive / restore members** — a member who leaves can be archived
  (deactivated, hidden, but history kept and reversible); permanent deletion is a
  deliberate second step available only after archiving

## Running it locally

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open http://localhost:8000. With no `DATABASE_URL` set it uses a local SQLite
file (`gym.db`) — no database setup required. **The first account you register
becomes the trainer**; everyone after is a member.

To test from your phone on the same wifi:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Running the tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```

98 tests, about two–three minutes. Each gets its own throwaway database and
never touches your `gym.db`. They also run automatically on every push via
GitHub Actions. See [`tests/README.md`](tests/README.md).

## Documentation

| Document | What it is |
|---|---|
| [`CLAUDE.md`](CLAUDE.md) | Start here — the rules and where everything lives |
| [`docs/CLAUDE.md`](docs/CLAUDE.md) | Full technical state: schema, routes, conventions |
| [`docs/GymLog-Project-Handbook.pdf`](docs/GymLog-Project-Handbook.pdf) | 39 pages on *why* — every technology, test and decision |
| [`docs/RESUMING.md`](docs/RESUMING.md) | Coming back after a break? Read this first |
| [`tests/README.md`](tests/README.md) | What each test file protects |
| [`docs/authz-check.md`](docs/authz-check.md) | Re-runnable authorisation proof |

## Tech

Python 3.12 · FastAPI · SQLAlchemy 2.x · Jinja2 (server-rendered, no JS
framework) · SQLite locally / PostgreSQL in production · bcrypt · JWT in an
httponly cookie · Docker · deployed on Render.

Charts are hand-generated inline SVG — no charting library, nothing extra for a
member to download on gym wifi.

## Deploying

Push to `main`. Render rebuilds from the `Dockerfile` and redeploys
automatically; `render.yaml` defines the web service and its PostgreSQL
database. New tables and columns are created on startup, so there is no manual
migration step.

## Utility scripts

```bash
python scripts/set_password.py --list        # accounts (no password data)
python scripts/set_password.py --email <e> --password <p>   # break-glass reset
python scripts/reset_demo.py                 # wipe demo data, keep the library
python scripts/build_handbook.py             # regenerate the PDF handbook
```
