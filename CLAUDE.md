# GymLog — start here

Claude Code loads this file automatically at the start of every session. It is
deliberately short; it points at the real documents.

## Read first, before changing anything

1. **`docs/CLAUDE.md`** — the full technical state: schema, every route,
   conventions, constants, git history, positioning. **This is the source of
   truth.** Read it before writing code.
2. **`docs/GymLog-Project-Handbook.pdf`** — 39 pages on *why* every decision was
   made. Read the architecture chapter (Part 3) if you are touching structure.
3. **`docs/RESUMING.md`** — if you have been away a while, start there instead.

## The ten-second version

Trainer-first gym management web app. FastAPI + SQLAlchemy + Jinja2, no JS
framework. SQLite locally, PostgreSQL on Render. One trainer, many members, two
role-based UIs over one database.

- **Live:** https://gymlog-jtd8.onrender.com
- **Repo:** https://github.com/Adarsh-7codes/gym-log
- Push to `main` deploys automatically.

## Non-negotiable rules

These are load-bearing. Breaking one is how this codebase would rot.

- **Authorisation is enforced in `app/crud.py`, never in a template.** Hiding a
  button is not a control — anyone can send the request by hand.
- **Never trust a `user_id` from the browser.** Use `crud.resolve_target_user()`;
  a member passing someone else's id must silently fall back to their own.
- **Trainer-only routes use the shared `require_trainer_web` dependency.** Never
  copy-paste an inline role check.
- **Schema changes extend, never rewrite.** New columns go in
  `app/database.py::ensure_schema()` idempotently; new tables via `create_all`.
  The live database holds real data.
- **The same code must run on SQLite and PostgreSQL.** No dialect-specific SQL.
- **Passwords change only through `crud.set_password()`**, which rehashes, bumps
  `token_version` (killing live sessions) and writes the audit row together.
- **No JS framework, no new paid dependency.**

## Before you commit

```bash
python -m pytest          # 92 tests, ~2–3 minutes. Must be green.
```

Tests live in `tests/`; see `tests/README.md`. Some of them exist to keep a
*decision* from being undone (no body-weight progress bar, no moralising
talking points, routine edits never deleting log history). If one of those
fails, do not "fix" it without reading why it exists.

## Working style that has worked here

Build in phases. Before writing code, produce a short plan of the files you will
touch and the assumptions you are making, and wait for confirmation. After each
phase: run the tests, verify the acceptance criteria, make one focused commit,
then stop and report.
