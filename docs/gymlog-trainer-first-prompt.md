# Claude Code prompt — GymLog trainer-first build

Copy everything below the line into Claude Code.

---

## Context

You are working on GymLog, a FastAPI + SQLAlchemy 2.x + Jinja2 gym tracking app. Read `CLAUDE.md` in the repo root first — it is the source of truth for architecture, schema and conventions.

**Positioning has changed.** This app is sold to the trainer/gym owner, not to members. The trainer is the only guaranteed daily user. Members visit the gym ~1.5–2 times a week and only 10–25% of them will ever type data into an app. Every feature below is therefore judged on: *does it make the trainer's day easier, or does it help him sell and retain memberships?*

Member-facing screens still matter, but only where they require **zero data entry** from the member.

## Ground rules

- **Schema changes are extend-only.** New columns go in `app/database.py::ensure_schema()` as idempotent `ADD COLUMN IF NOT EXISTS`-style operations. New tables are handled by `create_all`. There is no Alembic; do not introduce it.
- Everything must work on **both SQLite (local) and Postgres (Render)**. Avoid dialect-specific SQL.
- Server-rendered Jinja2 templates, mobile-first. Vanilla JS only where necessary. **Do not add a JS framework.**
- Keep the existing gold (trainer) / blue (member) theming.
- Work in **phases**. After each phase: run the app, verify the acceptance criteria, make one focused commit, then stop and report before starting the next phase.
- **Before writing any code, produce a short plan** listing the files you will touch per phase and any assumptions you are making. Wait for my confirmation.

---

## Phase 0 — Security (blocking; do this before anything else)

Phase 1 adds financial records to the database. Do not add them to an app with broken authorisation.

1. **Audit every route** in `app/routers/` (both web and `/api/*`). For each one, list: the route, who should be able to call it, and whether that is currently enforced **server-side** (not just hidden in the template).
2. Fix every gap found. A member must not be able to read or write another member's logs, routine, split, membership or payment data by guessing an ID or calling the JSON API directly. Trainer-only routes must reject member tokens with 403.
3. Add a single reusable dependency (e.g. `require_trainer`) rather than repeating checks inline.
4. **Remove the `/danger/reset` endpoint and the `RESET_TOKEN` env var entirely.** It wipes the database and it is not worth the risk on a system holding payment records. If a demo reset is needed later, it will be a local-only management script.
5. Confirm the repo is private and that no secrets are committed.

**Acceptance criteria:** Logged in as a member, I can call every trainer route and every other member's data endpoint directly (via curl with the member's cookie/token) and receive 403 or 404 on all of them. Write the curl commands you used into a short `docs/authz-check.md` so I can re-run them.

---

## Phase 1 — Membership and dues tracking (highest business value)

This is what makes the app a purchase rather than a nice-to-have. The owner's daily question is "who owes me money and whose membership expires this week."

**New table `Membership`:**
- `id`, `user_id` (FK to User)
- `plan_start` (date), `duration_months` (int)
- `expires_on` (date — computed on save, stored, not derived at read time)
- `amount` (numeric), `status` (`paid` | `pending`), `paid_on` (date, nullable)
- `notes` (text, nullable), `created_at`

A member can have multiple `Membership` rows over time (renewal history). The "current" membership is the one with the latest `plan_start`.

**Trainer UI:**
- On each member's page: current plan, expiry date, payment status, and full renewal history.
- "Add renewal" form: start date, duration, amount, paid/pending.
- "Mark as paid" one-click action on a pending membership.

**Roster changes (`/dashboard`):**
- Two new columns: **Expires in N days** and **Dues**.
- Expiry states: expired (red), expiring within 7 days (amber), otherwise plain.
- Make the roster sortable, and default the sort to: overdue dues → expiring soon → existing stall/inactivity flags → everyone else.
- Add a summary strip at the top: total active members, expiring in next 7 days, total pending dues (₹).

**Member UI (read-only):**
- "Your membership is valid till DD Mon YYYY" plus a simple payment history list.
- Members must never see dues-chasing language or another member's payment data.

**Explicitly out of scope:** payment processing, UPI integration, payment gateways, storing card details, GST invoicing. The trainer collects cash or UPI himself and marks it paid in the app. Do not add any of this even if it seems easy.

**Acceptance criteria:** I can add a member, record a 3-month membership starting today, see it on the roster with the correct expiry; backdate one to 4 months ago and see it flagged red and sorted to the top; mark a pending membership paid and see the dues total drop.

---

## Phase 2 — Trainer edits member routines and splits

Most of this already exists. `MemberRoutine` and `SplitDay` are exactly "which exercises, on which weekday, for which member." Currently only the member can edit their own. Change that.

- From a member's page, the trainer can edit that member's `SplitDay` (which body parts on which weekday) and their `MemberRoutine` (which exercises per body part), using the existing screens with the member as the target.
- Record who made the change. Add `assigned_by` (`member` | `trainer`) to `MemberRoutine`, defaulting to `member` for existing rows.
- On the member's log screen, show a small "Assigned by your trainer" marker on trainer-assigned exercises.
- Surface the existing `Exercise.demo_url` next to each exercise in the member's day view, as a plain link or small "How to do this" control. This is nearly free and it is the single most useful thing the app can give a nervous beginner standing in front of a machine.

**Acceptance criteria:** As trainer, I can open a member, set their Monday to chest + arms, add four specific exercises to their chest routine, and log in as that member to see exactly those four exercises on Monday's log screen, each with a working demo link.

---

## Phase 3 — Attendance (prerequisite for everything about retention)

Right now `INACTIVE_DAYS=10` fires off the `Log` table, so a member who trained six times and logged nothing looks identical to one who quit. That makes the flag close to useless and any reminder built on it would be actively harmful.

**New table `Attendance`:**
- `id`, `user_id`, `date`, `marked_by` (`trainer` | `member`), `created_at`
- Unique constraint on `(user_id, date)` — one attendance record per member per day, idempotent on repeat taps.

**Trainer UI:**
- A "Today" screen listing all members with a single large tap target per member to mark present. Optimised for one-handed phone use at the desk. Already-marked members show a clear state and can be un-marked.
- Show attendance count for the current month next to each name.

**Roster changes:**
- Replace log-derived inactivity with attendance-derived inactivity. Keep `INACTIVE_DAYS` as a constant but source it from `Attendance`.
- New column: **sessions this month**.

**Member UI (read-only):** their own attendance count and current streak. No input.

**Acceptance criteria:** Marking a member present twice on the same day creates one row. The roster inactivity flag reflects attendance, not logging — verify by having a member attend without logging and confirming they are not flagged.

---

## Phase 4 — Trainer talking points and first-90-days view

The member's value arrives through the trainer's mouth, not through a chart. The job of this phase is to give the trainer a specific, true thing to say to each member on a floor with 30 people.

**New member cohort view on the roster:**
- Filter/section for members who joined under 90 days ago — this is where nearly all churn happens, and the existing stall detection (`SESSIONS_TO_COMPARE=3`) cannot help them because they have no training history to compare.
- For each: days since joining, sessions in first 14 days, sessions in last 7 days.
- Flag anyone in their first 90 days with fewer than 2 sessions in the last 7 days.

**Talking points on each member's page:**
Generate 1–3 short factual lines from data that already exists, for example:
- "Bench press 40 → 47.5 kg over 6 weeks"
- "12 sessions this month, up from 8"
- "Hasn't trained legs in 18 days"
- "Squat unchanged at 60 kg for 4 sessions"

Rules for this feature:
- **Facts only, derived from logged or attendance data.** No inference about diet, effort, motivation or lifestyle. The app must never assert why something happened.
- Never generate accusatory or shaming phrasing. These lines are for the trainer to read, not for the member to receive as a verdict.
- If there is not enough data for a true statement, show nothing rather than something vague.

**Acceptance criteria:** A member with 6 weeks of bench press progress produces a correct improvement line. A member with two logs and no pattern produces zero lines, not filler.

---

## Phase 5 — Provable progressive-overload targets

- Let the trainer set a target `(exercise, target_weight, target_reps, target_date)` per member.
- Show the gap on both the trainer's member page and the member's own view: "Target: 60 kg by 15 Oct. Current best: 45 kg. Unchanged for 3 weeks."
- This is objective and verifiable from `Log` data, which is exactly why it is worth building.

---

## Phase 6 — Body weight (weekly cadence only)

**New table `BodyWeight`:** `id`, `user_id`, `date`, `weight_kg`, `recorded_by` (`trainer` | `member`).

- Expect weekly entry, typically by the trainer at the gym scale. Most members do not own a scale.
- Display as **trend and rate**, never as percentage-complete: "Down 2.1 kg over 6 weeks — about 0.35 kg/week."
- Chart the trend line with a 7-day rolling average if daily entries exist, so normal water-weight fluctuation does not read as failure.

**Explicitly forbidden in this phase:**
- Any progress bar or "X% of goal complete" display for body weight. Body weight is not monotonic; a bar showing backwards movement punishes a member who did everything right.
- Any hard-coded goal like "10 kg in 2 months" (≈1.25 kg/week, well above the 0.5–1 kg/week that is normally considered sustainable). Do not encode a target rate into the UI at all.
- Any inference or display attributing a stalled result to diet, junk food, effort or discipline. The app cannot observe those things and must not claim to.

If a trainer wants to set a weight goal, store it as a plain note with no progress computation attached.

---

## Not in scope for this build

Do not implement any of these, even partially, even if they seem quick:

- Payment processing, UPI, gateways, invoicing
- Diet plans beyond a single free-text/file field on the member record (no food database, no calorie or macro targets)
- Reminders, push notifications, WhatsApp or SMS integration — these require Phase 3 attendance data to exist and be trusted first
- The `WorkoutSet` per-set child table and live session logging — correct long-term, but not trainer-first
- Any JS framework, any new external service, any new paid dependency

---

## Finally

Update `CLAUDE.md` to reflect everything you changed: new tables, new routes, new constants, the removal of `/danger/reset`, and the revised positioning. Keep the existing section structure. Add the new commits to the git history section.
