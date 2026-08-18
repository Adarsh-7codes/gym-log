# GymLog — Project Memory / Handoff

A full-stack gym tracking web app for **one trainer and their members**. Built with FastAPI + PostgreSQL, deployed on Render. This file is the single source of truth for context — read it first.

---

## 1. What it is
- Trainer ↔ members workout tracker. Two role-based UIs sharing one database.
- **Members** log workouts fast from their phone; **trainer** scans everyone's progress and spots plateaus.
- Mobile-first (members use it on the gym floor on their phones).

## 2. Live deployment
- **Live URL:** https://gymlog-jtd8.onrender.com
- **Host:** Render (free tier). Web service `gymlog` + Postgres database `gymlog-db`.
- **Deploy method:** Render Blueprint from `render.yaml`. Auto-deploys on every `git push` to `main`.
- **Repo:** https://github.com/Adarsh-yaarrrr/gym-log
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
- `User(id, name, email, password_hash, role[trainer|member], created_at)`
- `Exercise(id, name, body_part[chest/back/legs/shoulders/arms/core], difficulty[beginner/intermediate/advanced], equipment, instructions, demo_url)` — the **Exercise Library**, seeded on startup (48 exercises, ~8/body-part across difficulties, in `app/seed.py`).
- `MemberRoutine(user_id, exercise_id, body_part, date_added)` — a member's standing list of exercises per body part. Removing a row does **not** delete logs.
- `SplitDay(user_id, weekday 0–6, body_part)` — the **Weekly Split**: multiple rows per weekday allowed (e.g. Mon = chest AND arms).
- `Log(user_id, exercise_id, date, weight, reps, sets, next_action, notes, feeling[easy/moderate/tough], logged_by[member|trainer])` — historical performance. `logged_by` = who entered it.
- `Membership(user_id, plan_start, duration_months, expires_on, amount[Numeric(10,2)], status[paid|pending], paid_on, notes, created_at)` — **Phase 1**. One row per membership term; several rows per member = renewal history. "Current" = latest `plan_start`. `expires_on` is computed on save via `crud.add_months()` and **stored**, never derived at read time. No card/gateway/invoice data by design — the trainer collects cash/UPI and records the outcome.
- `PlanDay` / `PlanItem` — older weekly planner (per-day focus + target sets/reps). Now **trainer-only** in nav; superseded for members by SplitDay + MemberRoutine.

## 8. Features
- **Exercise Library** (member): categorized by body part, sortable by difficulty, multi-select into routine.
- **Weekly Split** (member): assign body part(s) to each weekday. Recurring; editable anytime.
- **Fast day-based logging** (member, `/logs/new`): defaults to **today's** body parts with a **day-picker** (option B) to switch days; shows only that day's routine exercises. Numeric steppers for weight/reps/sets, `inputmode` numeric keypads, and **auto-carry of last session's weight**. One-tap save; stays on the same day. Falls back to the whole routine if no split is set.
- **Trainer log form** (`/logs/new` as trainer): full form with ALL exercises + member picker (logs on behalf → `logged_by=trainer`).
- **Trainer dashboard** (`/dashboard`): roster — one row per member with **expiry**, **dues**, last session and **stall/inactivity flags**; clickable into each member's logs (routine + membership shown). Summary strip: active / expiring ≤7d / expired / total pending dues (₹). Sortable via `?sort=` — `default` (overdue dues → expiring soon → flagged → rest), `expiry`, `dues`, `activity`, `name`.
- **Membership & dues** (**Phase 1**, trainer-only writes): on a member's page — current plan, expiry, payment status, full renewal history, "Add renewal" form and one-click **Mark paid**. Members see their own *"Valid till …"* + payment history **read-only**, with no dues-chasing language and no access to anyone else's. Currency is ₹ (display only).
- **Stall detection** (`app/crud.py`): per exercise, compares the last **3** sessions' top `(weight, reps)`; no new PR → stalled. Member flagged if any exercise stalled OR no session in **10** days. Constants: `SESSIONS_TO_COMPARE=3`, `INACTIVE_DAYS=10`, `EXPIRY_SOON_DAYS=7`.
- **Progress** (`/progress`): dependency-free inline-SVG charts — weight-over-time per exercise, weekly volume bars, feeling breakdown.
- JSON API under `/api/*` mirrors auth/exercises/logs/users; interactive docs at `/docs`.

## 9. Key routes (web, `app/routers/web.py`)
- `/login` `/register` `/logout` — auth (email validated via `email-validator`; rejects `x@g`, `ada@12`, etc.).
- `/dashboard` — trainer roster or member/one-member log view.
- `/library`, `/split`, `/logs/new`, `/logs/{id}/edit|delete`, `/progress`.
- `/members` (trainer): list + create member accounts.
- `/members/{user_id}/membership` POST (trainer): record a membership term.
- `/membership/{id}/paid` POST, `/membership/{id}/delete` POST (trainer).
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

## 11. Conventions / gotchas
- **Schema changes are extend-only:** new columns added idempotently in `app/database.py::ensure_schema()` (runs on startup). New tables handled by `create_all`. Exercise library re-seeded on every startup (idempotent by name).
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
- _(Phase 1)_ membership & dues: `Membership` table, roster expiry/dues columns + summary strip + sorting, trainer renewal/mark-paid, member read-only view

## 12b. Positioning (trainer-first)
The app is sold to the **trainer/gym owner**, not to members. The trainer is the only guaranteed daily user; members attend ~1.5–2×/week and few will type into an app. Features are judged on: *does it make the trainer's day easier, or help him sell/retain memberships?* Member screens are kept only where they need **near-zero data entry** — all existing member features (Library, Weekly Split, fast day-based logging, progress) are retained. Roadmap: `docs/gymlog-trainer-first-prompt.md` (Phases 1–6: memberships & dues, trainer-edits-routines, attendance, talking points, overload targets, body weight).

## 13. Open ideas / possible next steps
- Optionally give the **trainer** the member-style day-filtered log screen too (currently trainer sees the full form).
- "Reset member password" button for the trainer (no password reset flow exists yet).
- Trim trailing `.0` on displayed weights.
- Retire the old Planner entirely (now overlaps with Weekly Split).
- Per-week split history / versioning (currently a single recurring split).
