# Coming back to GymLog

You have been away. This page gets you productive again in about fifteen
minutes, in the order that actually matters.

---

## 1. Get it running (5 minutes)

```bash
cd C:\GYM
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open http://localhost:8000.

If `gym.db` still exists you already have local accounts. If you cannot get in,
list them and reset one:

```bash
python scripts/set_password.py --list
python scripts/set_password.py --email <your-email> --password <new-password>
```

If the database is empty, the **first account you register becomes the
trainer**.

## 2. Check nothing has rotted (2 minutes)

```bash
pip install -r requirements-dev.txt
python -m pytest
```

98 tests. If they are green, the app is in the state this documentation
describes. If any are red, **read the test name before changing anything** —
several exist to protect a decision, not a behaviour.

## 3. Remind yourself what it does

Log in as the trainer and walk the loop once:

**Members** → add a member → open them → **Edit split** (Monday = chest) →
**Edit routine** (tick 4 chest exercises) → set a **target** → record a
**weigh-in** → **Today** → mark them present. Then log in as that member and
open **New Log**.

That path touches almost every feature in the app.

## 4. Where the knowledge lives

| Question | Read |
|---|---|
| "What are the rules I must not break?" | [`../CLAUDE.md`](../CLAUDE.md) (root) |
| "What is the schema / what routes exist?" | [`CLAUDE.md`](CLAUDE.md) (this folder) |
| "*Why* was it built this way?" | [`GymLog-Project-Handbook.pdf`](GymLog-Project-Handbook.pdf) |
| "How is the code structured?" | Handbook **Part 3 — The architecture** |
| "What does this test protect?" | [`../tests/README.md`](../tests/README.md) |
| "Why was this decision made?" | `git log` — commit messages carry the reasoning |

## 5. Things that will confuse you if you forget them

- **There are two databases.** Local = SQLite file `gym.db` on your laptop.
  Live = PostgreSQL on Render. Same structure, completely separate data. The
  live app never reads your local file.
- **Self-registration is closed** once a trainer exists. The trainer creates
  members. An empty database allows exactly one bootstrap registration.
- **The first-ever account becomes the trainer.** There is no way to make a
  second one through the UI, by design.
- **`/danger/reset` is 404 on Render on purpose.** It only works on local
  SQLite. To reset the live demo data use `scripts/reset_demo.py` with
  `DATABASE_URL` pointed at Render.
- **Passwords cannot be read back.** They are bcrypt hashes. To *know* a
  member's password, set it for them.
- **Free-tier Render sleeps** after ~15 minutes idle; the first request then
  takes 30–60 seconds. Open the URL a few minutes before showing anyone.
- **How do I change the trainer's name/email?** Sign in as the trainer →
  **☰ → Account → "Your details"** → edit and save. The email is your login, so
  you sign in with the new one next time (your current session stays valid). The
  role stays `trainer`. Do this on the **live** site to rename the demo account —
  local and live are separate databases. To change the *password*, use
  **Account → change password** (Phase 0.5), which signs you out everywhere.
- **Removing a member is two steps, on purpose.** **Members → Archive** first —
  that deactivates their login and hides them from the roster/attendance but
  keeps all their history, and you can **Restore** them anytime. Only an
  *archived* member can then be permanently deleted (a separate, name-typed
  confirm). Archive is reversible; delete is not.

## 6. Check the live deployment

- Render dashboard → **gymlog** (web service) and **gymlog-db** (database).
- Push to `main` deploys automatically; watch the **Logs** tab for
  `Application startup complete`.
- GitHub → **Actions** tab shows whether the tests passed on the last push.

⚠️ **Render's free PostgreSQL expires (roughly 30 days).** If real data ever
goes in, move to a paid plan with backups. This is the one item on the list
that can lose data permanently.

## 7. If you want to add a feature

The pattern that has worked, and which the handbook argues for at length:

1. Write a short spec first — what it must do, and the acceptance criteria.
   `docs/gymlog-trainer-first-prompt.md` and
   `docs/gymlog-account-recovery-prompt.md` are the two worked examples.
2. Ask for a **plan before code**: which files, what assumptions.
3. Build it, then write the tests **into `tests/`** — not as a scratch script.
   Retrofitting them later cost real time on this project.
4. One focused commit, with the reasoning in the message.

## 8. Known weaknesses, in priority order

Full detail in the handbook, **Part 10**.

1. Render free tier: database expiry and cold starts.
2. No database backups.
3. No password-reset email — recovery is in person or via CLI, by design.
4. The N+1 query pattern on the roster; fine at gym scale, slow at 500 members.
5. Single trainer only.
6. The old "Planner" feature overlaps with Weekly Split and should be retired.
