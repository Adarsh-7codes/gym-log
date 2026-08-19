# GymLog — Project Memory / Handoff

A full-stack gym tracking web app for **one trainer and their members**. Built with FastAPI + PostgreSQL, deployed on Render. This file is the single source of truth for context — read it first.

> For the *narrative* of how recent work was built — every change, why it was
> made, what each test guards, and any failures hit along the way — see
> [`PROJECT-REPORT.md`](PROJECT-REPORT.md). This file stays terse; that one keeps
> the story.

---

## 1. What it is
- Trainer ↔ members workout tracker. Two role-based UIs sharing one database.
- **Members** log workouts fast from their phone; **trainer** scans everyone's progress and spots plateaus.
- Mobile-first (members use it on the gym floor on their phones).

## 2. Live deployment
- **Live URL:** https://gymlog-jtd8.onrender.com
- **Host:** Render (free tier). Web service `gymlog` + Postgres database `gymlog-db`.
- **Deploy method:** Render Blueprint from `render.yaml`. Auto-deploys on every `git push` to `main`.
- **Repo:** https://github.com/Adarsh-7codes/gym-log
- Free tier sleeps after ~15 min idle → first load takes ~30–60s (cold start). Open the URL a couple minutes before showing it live.

## 3. Tech stack
- Python 3.12, **FastAPI**, **SQLAlchemy 2.x**, **Jinja2** server-rendered templates (no JS framework; a little vanilla JS for steppers/day-picker).
- **PostgreSQL** in production, **SQLite** (`gym.db`) locally.
- Auth: JWT in an httponly cookie (web) + bearer token (JSON API). Passwords hashed with **bcrypt**.
- Packaged with a **Dockerfile**; Render builds it.

## 4. Two databases (kept fully separate)
| | Local / Dev | Live / Demo |
|---|---|---|
| Engine | SQLite file `C:\GYM\gym.db` | Render Postgres `gymlog-db` |
| Used when | running locally (no `DATABASE_URL`) | deployed app (Render sets `DATABASE_URL`) |
They share schema but never share data. `config.py` auto-rewrites Render's `postgres://` → `postgresql://`.

## 5. Environment variables (set in Render)
- `DATABASE_URL` — Postgres connection (auto-wired from `gymlog-db`).
- `JWT_SECRET` — token signing key (Render-generated).
- `COOKIE_SECURE=true`, `JWT_ALGORITHM=HS256`, `ACCESS_TOKEN_EXPIRE_MINUTES=10080`.
- `RESET_TOKEN` — **local dev only.** Has no effect on Render (see §10). Safe to delete from the Render dashboard.
- `ALLOW_OPEN_REGISTRATION` — default **false**. Members are created by the trainer; self-registration only bootstraps the first account.
- No secrets are committed. `.env` and `*.db` are gitignored; passwords are stored only as bcrypt hashes.

## 6. Roles (important rule)
- **The first account ever registered becomes the `trainer`. Every account after is a `member`.** No self-service way to make a second trainer.
- Login page has a **Trainer | Member toggle** that must match the account's role.
- Trainer = gold theme; member = blue theme. Badge shown under the name; "Trainer view / Member view" banner on the dashboard.

## 7. Data model (SQLAlchemy — `app/models.py`)
- `User(id, name, email, password_hash, role[trainer|member], created_at, token_version, recovery_email, recovery_phone, archived_at)` — **Phase 0.5** added token_version + the two recovery fields. `token_version` is bumped on every password change (and on archive) and carried as the `tv` JWT claim, so a reset invalidates live sessions instead of leaving a stale token valid for its full 7 days. `archived_at` (nullable) is the **archive/deactivate** flag: NULL = active, a timestamp = archived — hidden from active listings and blocked from login, but never deleted and reversible via restore. `User.is_archived` is the derived boolean.
- `Exercise(id, name, body_part[chest/back/legs/shoulders/arms/core], difficulty[beginner/intermediate/advanced], equipment, instructions, demo_url)` — the **Exercise Library**, seeded on startup (48 exercises, ~8/body-part across difficulties, in `app/seed.py`).
- `MemberRoutine(user_id, exercise_id, body_part, date_added, assigned_by[member|trainer])` — a member's standing list of exercises per body part. Removing a row does **not** delete logs. `assigned_by` (**Phase 2**) records who prescribed it; pre-existing rows backfilled to `member`.
- `SplitDay(user_id, weekday 0–6, body_part)` — the **Weekly Split**: multiple rows per weekday allowed (e.g. Mon = chest AND arms).
- `Log(user_id, exercise_id, date, weight, reps, sets, next_action, notes, feeling[easy/moderate/tough], logged_by[member|trainer])` — historical performance. `logged_by` = who entered it.
- `BodyWeight(user_id, date, weight_kg, recorded_by[trainer|member], created_at)` — **Phase 6**. Unique `(user_id, date)`; re-entering a day updates rather than duplicating.
- `Target(user_id, exercise_id, target_weight, target_reps, target_date, created_at)` — **Phase 5**. Trainer-set progressive-overload goal; every part is checkable against `Log` rows.
- `PasswordChange(user_id, changed_by_user_id, method[self|trainer|cli], created_at)` — **Phase 0.5**. Audit that a password changed. **Never stores the password or hash.** `changed_by_user_id` is null when the CLI script did it.
- `Attendance(user_id, date, marked_by[trainer|member], created_at)` — **Phase 3**. Ground truth for "did they come", independent of logging. **Unique `(user_id, date)`** makes repeat taps idempotent.
- `Membership(user_id, plan_start, duration_months, expires_on, amount[Numeric(10,2)], status[paid|pending], paid_on, notes, created_at)` — **Phase 1**. One row per membership term; several rows per member = renewal history. "Current" = latest `plan_start`. `expires_on` is computed on save via `crud.add_months()` and **stored**, never derived at read time. No card/gateway/invoice data by design — the trainer collects cash/UPI and records the outcome.
- `PlanDay` / `PlanItem` — older weekly planner (per-day focus + target sets/reps). Now **trainer-only** in nav; superseded for members by SplitDay + MemberRoutine.

## 8. Features
- **Exercise Library** (member): categorized by body part, sortable by difficulty, multi-select into routine.
- **Weekly Split** (member): assign body part(s) to each weekday. Recurring; editable anytime.
- **Fast day-based logging** (member, `/logs/new`): defaults to **today's** body parts with a **day-picker** (option B) to switch days; shows only that day's routine exercises. Numeric steppers for weight/reps/sets, `inputmode` numeric keypads, and **auto-carry of last session's weight**. One-tap save; stays on the same day. Falls back to the whole routine if no split is set.
- **Trainer log form** (`/logs/new` as trainer): full form with ALL exercises + member picker (logs on behalf → `logged_by=trainer`).
- **Trainer dashboard** (`/dashboard`): roster — one row per member with **expiry**, **dues**, last session and **stall/inactivity flags**; clickable into each member's logs (routine + membership shown). Summary strip: active / expiring ≤7d / expired / total pending dues (₹). Sortable via `?sort=` — `default` (overdue dues → expiring soon → flagged → rest), `expiry`, `dues`, `activity`, `name`.
- **Trainer edits member routine/split** (**Phase 2**): `/library?user_id=N` and `/split?user_id=N` operate on that member (trainer only — `crud.resolve_target_user()` makes a member's forged `user_id` fall back to self). Entry points are **Edit split / Edit routine** on the member's page; both screens show an "Editing X's …" banner. Trainer-assigned exercises show an **"assigned by your trainer"** badge on the member's log screen.
- **Demo links**: every exercise on the member's day view has a **How?** link via `crud.demo_link()` — uses `Exercise.demo_url` when the trainer sets one (new optional field on the `/exercises` form), otherwise falls back to a **YouTube search** for the exercise name. Deliberately a search, not a hardcoded video id, so links can't rot or point at the wrong lift.
- **Membership & dues** (**Phase 1**, trainer-only writes): on a member's page — current plan, expiry, payment status, full renewal history, "Add renewal" form and one-click **Mark paid**. Members see their own *"Valid till …"* + payment history **read-only**, with no dues-chasing language and no access to anyone else's. Currency is ₹ (display only).
- **Account recovery** (**Phase 0.5**): three paths, no email/SMS dependency. (1) Trainer resets a member's password from the Members list — covers ~95% of lockouts. (2) Anyone changes their own password at `/account/password`, requiring the current one; success signs them out everywhere. (3) Trainer lockout → `scripts/set_password.py`, run against local SQLite or the live database from the Render shell. All three go through `crud.set_password()`, which rehashes, bumps `token_version` and writes the audit row together so no caller can do one and forget the others. Login now throttles after 5 failures for 120s, and a wrong role-toggle says so instead of looking like a bad password.
- **Attendance** (**Phase 3**): trainer-only **`/today`** screen — one big tap target per member (one-handed phone use), tap again to un-mark, shows sessions-this-month, unmarked members sorted first, and `?on=` to catch up a previous day. Roster gains a **sessions this month** column. Members see their own count + **week streak** read-only (no input; 403 on `/today` and the toggle).
- **Activity signal**: inactivity is sourced from **attendance**, with a logged workout also counting as evidence of presence (you can't log a session you didn't do). This is a deliberate softening of "replace logs with attendance": a strict swap would flag every member the day attendance ships (empty table), and would also flag someone who logs on a day the trainer forgot to mark. `last_date = max(last_attendance, last_log)`.
- **Body weight** (**Phase 6**): trainer records weigh-ins (weekly cadence expected — most members don't own a scale). Shown as **trend and rate only**: *"Down 2.1 kg over 6 weeks — about 0.35 kg/week"*, plus an inline-SVG line on a `ROLLING_WINDOW_DAYS=7` rolling average so water-weight noise doesn't read as failure. Member sees their own read-only.
  **Forbidden here by design** (asserted in tests, documented in `crud.py`): no progress bar or %-of-goal (body weight is not monotonic — a bar moving backwards punishes a member who did nothing wrong); no hard-coded target rate; no attribution of a result to diet, effort or discipline. A weight goal, if wanted, is a plain note with no progress computation.
- **Overload targets** (**Phase 5**): the trainer sets `(exercise, weight, optional reps, by date)` per member. Both the trainer's member page and the member's own dashboard show the gap — *"60 kg by 15 Oct · Current best 45 kg · 15 kg to go · Unchanged for 3 weeks"* — with reached/overdue states. `crud.exercise_best()` derives the current best and dates the plateau from when that best was **first** reached. Trainer-only writes (403 for members); members see their own read-only.
- **New-member cohort** (**Phase 4**): roster tab for members who joined within `NEW_MEMBER_DAYS=90` — the window where nearly all churn happens and where stall detection cannot help (no history to compare). Columns: joined N days ago, sessions in first 14 days, sessions in last 7. Flags anyone new with `< MIN_SESSIONS_RECENT=2` sessions in the last 7 days. **Join date** = earliest `Membership.plan_start`, falling back to `User.created_at`. The flag is **suppressed until the member has at least one recorded session**, so a fresh install doesn't flag everyone.
- **Talking points** (**Phase 4**, trainer's member page only): up to `TALKING_POINT_LIMIT=3` short factual lines generated in `crud.talking_points()` — biggest verified lift improvement, attendance this month vs last, a planned body part not trained recently, and a stalled lift. **Rules enforced in code, not the template:** facts only from logged/attendance data; **never** infer diet, effort, motivation or lifestyle; never accusatory phrasing; and when there is not enough data it renders *"Not enough data yet"* rather than filler. Members never see talking points about themselves.
- **Stall detection** (`app/crud.py`): per exercise, compares the last **3** sessions' top `(weight, reps)`; no new PR → stalled. Member flagged if any exercise stalled OR no session in **10** days. Constants: `SESSIONS_TO_COMPARE=3`, `INACTIVE_DAYS=10`, `EXPIRY_SOON_DAYS=7`, `NEW_MEMBER_DAYS=90`, `FIRST_WINDOW_DAYS=14`, `RECENT_WINDOW_DAYS=7`, `MIN_SESSIONS_RECENT=2`, `TALKING_POINT_LIMIT=3`.
- **Progress** (`/progress`): dependency-free inline-SVG charts — weight-over-time per exercise, weekly volume bars, feeling breakdown.
- JSON API under `/api/*` mirrors auth/exercises/logs/users; interactive docs at `/docs`.

## 9. Key routes (web, `app/routers/web.py`)
- `/login` `/register` `/logout` — auth (email validated via `email-validator`; rejects `x@g`, `ada@12`, etc.).
- `/dashboard` — trainer roster or member/one-member log view. `?view=new` filters to the first-90-days cohort.
- `/library` (GET) and `/library/{body_part}` (POST) — pick the exercises in a routine; `/library/remove/{exercise_id}` (POST) drops one. All accept `?user_id=`/`user_id=` for a trainer editing a member.
- `/split` (GET/POST), `/logs/new`, `/logs/{id}/edit|delete`, `/progress`.
- `/planner` (GET), `/planner/{weekday}/focus` (POST), `/planner/{weekday}/item` (POST), `/planner/item/{item_id}/delete` (POST) — **the legacy weekly planner, trainer-only in nav.** Superseded for members by Weekly Split + Exercise Library; kept only so existing `PlanDay`/`PlanItem` rows stay reachable. **Scheduled for removal** (see §13).
- `/members` (trainer): list + create member accounts.
- `/members/{user_id}/membership` POST (trainer): record a membership term.
- `/membership/{id}/paid` POST, `/membership/{id}/delete` POST (trainer).
- `/today` (trainer): attendance screen. `/attendance/{user_id}/toggle` POST (trainer).
- `/members/{user_id}/weight` POST, `/weight/{id}/delete` POST (trainer).
- `/members/{user_id}/targets` POST, `/targets/{id}/delete` POST (trainer).
- `/members/{user_id}/password` POST (trainer): reset a member's password.
- `/members/{user_id}/archive` POST, `/members/{user_id}/restore` POST (trainer): **archive/deactivate** a member (reversible) via `crud.archive_member()` / `crud.restore_member()`. Archive sets `archived_at`, bumps `token_version` (kills any live session), and hides them from the roster, attendance and all member-pickers; restore clears it. Members-only (never the trainer). Archived members remain listed on `/members` so they can be restored or deleted.
- `/members/{user_id}/delete` POST (trainer): **permanent** delete via `crud.delete_member()` — **two-step by design**: refuses unless the member is already **archived**, then requires the member's name typed exactly. Removes every dependent row explicitly (not via CASCADE — SQLite doesn't enforce FKs), and nulls (not deletes) any `PasswordChange` the member authored for someone else. The tables to clear are the single list `crud.USER_OWNED_MODELS` (every table with a `user_id` column); a reflection test fails the build if that list ever falls behind the schema.
- `/account`, `/account/password` (any role): recovery contacts and self-service change.
- `/account/profile` POST (any role): change your own **name + login email** via `crud.update_profile()` — validates non-blank, reuses the app's email validation, rejects an email already used by another account, and **does not touch the role**. This is how the seeded demo trainer becomes a real account. Session survives the change (the JWT subject is the user id, not the email).
- `/exercises` (trainer): manage the raw exercise list.
- `/danger/reset` — demo reset, **local only** (see §10).
- `/register` — 403 once a trainer exists (renders `registration_closed.html`).

## 10. Demo reset (LOCAL ONLY)
- `GET/POST /danger/reset` requires **both** guards: the DB must be **SQLite** (local) **and** `RESET_TOKEN` must be set and match. On Render/Postgres it is permanently **404** regardless of env vars.
- Local use: `RESET_TOKEN=x uvicorn app.main:app --reload`, open `http://localhost:8000/danger/reset?token=x`, type `RESET`. Wipes accounts/logs/routines/splits, keeps the exercise library.

## 10b. Authorization model (Phase 0)
- Trainer-only web routes use the shared **`require_trainer_web`** dependency (403 for members) — no inline copy-paste checks.
- Trainer-only API routes use **`require_trainer`** (403).
- Member data scoping is enforced in **`crud.py`**, not templates: forged `?user_id=` is ignored; another member's log returns **404**.
- **Self-registration is closed** once a trainer exists (403 on GET and POST); the trainer creates members at `/members`. Bootstrap exception: an empty DB allows the first account (becomes trainer). Override locally with `ALLOW_OPEN_REGISTRATION=true`.
- Re-runnable proof: **`docs/authz-check.md`**.

## 10c. Break-glass password reset (`scripts/set_password.py`)
```
python scripts/set_password.py --list
python scripts/set_password.py --email <email> --password <new_password>
```
Reads `DATABASE_URL` exactly like the app (including the `postgres://` rewrite); with none set it operates on local SQLite. For the **live** database use Render → gymlog → **Shell**, where `DATABASE_URL` is already set — nothing to paste and no credential to leak. Uses the app's own hashing so it can never drift, never prints the password, and masks remote credentials in its output.

## 11. Conventions / gotchas
- **Tests live in `tests/` and are run with `pytest`** (98 tests, ~2–3 min). `pip install -r requirements-dev.txt` then `python -m pytest`. Each test gets a throwaway SQLite database and never touches `gym.db`. See `tests/README.md`.
- **Schema changes are extend-only:** new columns added idempotently in `app/database.py::ensure_schema()` (runs on startup). Each block guards its own table independently — a missing table must never skip the others' migrations. New tables handled by `create_all`. Exercise library re-seeded on every startup (idempotent by name).
- **Deleting a member is explicit, and any new user-owned table must join it.** `crud.delete_member()` removes rows table-by-table (see `crud.USER_OWNED_MODELS`) rather than relying on `ON DELETE CASCADE`, because SQLite doesn't enforce FKs without a pragma and the same code must behave identically on SQLite and Postgres. **If you add a table with a `user_id` column, add its model to `USER_OWNED_MODELS`** or a deleted member will orphan its rows — the reflection test `test_delete_member_handles_every_table_that_references_a_user` fails the build if you forget.
- Fresh Postgres on Render auto-creates all tables + seeds on first boot — no manual migration.
- Passwords never retrievable (bcrypt one-way). To *know* a member's password, the trainer sets it via Members → Add member.
- Weights display as floats (e.g. `100.0`).
- Local dev: `pip install -r requirements.txt` then `uvicorn app.main:app --reload`. Phone testing over LAN: `--host 0.0.0.0`, open `http://<PC-LAN-IP>:PORT`.

## 12. Git history (main)
- `dfc7cc6` initial app + Render deploy config
- `e2cdb54` weekly split: day-based exercise filtering
- `1b0ee77` token-guarded demo reset endpoint
- `d4f3aaa` add this project-memory doc
- `987a79c` _(Phase 0)_ security hardening: local-only reset, shared `require_trainer_web`, closed self-registration, `docs/authz-check.md`
- `4555fef` _(Phase 1)_ membership & dues: `Membership` table, roster expiry/dues columns + summary strip + sorting, trainer renewal/mark-paid, member read-only view
- `d2cea67` _(Phase 2)_ trainer edits member routine/split: `assigned_by` on `MemberRoutine`, `?user_id=` targeting on `/library` + `/split`, demo links, `resolve_planner_target` → `resolve_target_user`
- `ace6753` _(Phase 3)_ attendance: `Attendance` table, trainer `/today` screen, attendance-sourced inactivity, sessions-this-month column, member read-only count + streak
- `41aa590` _(Phase 4)_ talking points + first-90-days cohort: `crud.talking_points()`, `crud.cohort_stats()`, roster cohort tab, no schema change
- `4ea2868` _(Phase 5)_ progressive-overload targets: `Target` table, `crud.exercise_best()`/`target_progress()`, gap shown to trainer and member
- _(Phase 6)_ body weight: `BodyWeight` table, trend/rate + rolling-average SVG, no progress bars or goal percentages

## 12b. Positioning (trainer-first)
The app is sold to the **trainer/gym owner**, not to members. The trainer is the only guaranteed daily user; members attend ~1.5–2×/week and few will type into an app. Features are judged on: *does it make the trainer's day easier, or help him sell/retain memberships?* Member screens are kept only where they need **near-zero data entry** — all existing member features (Library, Weekly Split, fast day-based logging, progress) are retained. Roadmap: `docs/gymlog-trainer-first-prompt.md` (Phases 1–6: memberships & dues, trainer-edits-routines, attendance, talking points, overload targets, body weight).

## 13. Open ideas / possible next steps
- Optionally give the **trainer** the member-style day-filtered log screen too (currently trainer sees the full form).
- Trim trailing `.0` on displayed weights.
- Retire the old Planner entirely (now overlaps with Weekly Split).
- Per-week split history / versioning (currently a single recurring split).
