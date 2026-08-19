# GymLog — Project Report (running work log)

A narrative, newest-*section*-first journal of the work done on this project:
**what** was changed, **why**, **every test** (why it exists and what a failure
would mean), **any failures** actually hit and how they were fixed, and **how to
use** each new thing.

This is deliberately verbose. It is the opposite of [`CLAUDE.md`](CLAUDE.md),
which is the terse single-source-of-truth reference. When a fact here becomes
settled (a new route, a schema column, a constant), it is *also* written into
`CLAUDE.md` in short form — this report keeps the story, `CLAUDE.md` keeps the
facts.

## How to read an entry

Each phase/work-unit uses the same shape so it stays skimmable however long it
grows:

- **What & why** — the change and the reasoning behind it.
- **Files touched** — where to look.
- **Tests** — each test named, *why it exists*, and *what it would mean if it
  went red*.
- **Failures & fixes** — anything that actually broke while building, the real
  reason, and the approach taken to fix it. ("None" is a valid entry.)
- **How to use it** — the human-facing steps.
- **Result** — test count and green/red state at the end of the unit.

---

# Context — the branch this work lives on

- **Branch:** `wip/trainer-account-and-member-delete`, forked from `main` at
  `9adaa15`.
- **Goal (two features the user asked for):**
  1. **Trainer can rename themselves** — change the live trainer's name and
     login email so the seeded *demo* account becomes the user's real one.
     Same account, same role, new identity.
  2. **Trainer can remove a member** — no deletion existed anywhere before.
- **Decision taken up front (user's call): "Both" for removal** — *archive /
  deactivate* is the default (keeps history, reversible), and *permanent delete*
  is a separate, harder-guarded option reachable only after archiving. See the
  phase entries below as each ships.
- **Baseline before any new work:** `python -m pytest` → **77 passed**, exit 0.
  The pre-existing WIP code (already on the branch) was written but had **zero
  tests**; that missing coverage is the whole reason the branch was never
  merged, because `main` auto-deploys to Render.

---

<!-- Newest phase entries are added directly below this line. -->

# Phase 2 — Archive / deactivate a member (the reversible default)

*Status: **done**, tests green. Committed on the branch.*

## What & why

The user asked to "be able to delete members." Rather than jump straight to an
irreversible delete, we agreed on **"Both"**, with **archive as the default**:

- **Archive** = a soft "they left the gym". The member disappears from the
  active screens and can no longer sign in, but **nothing is deleted** and a
  one-click **Restore** brings them fully back.
- **Permanent delete** stays available but is now a deliberate **second step**:
  a member must be archived *first* (see the guard below). The full permanent
  delete + its cascade tests are Phase 3.

**Why archive-first (the decision):** deletion here is irreversible and wipes a
member's whole history. A gym member who quits and rejoins months later is
common; archiving keeps their logs, attendance and membership record so they
resume instead of starting from zero. Making delete a second step means the
irreversible action can never be one accidental tap away from an active member.
*(The alternative — showing Archive and Delete side by side on an active member
— was rejected for exactly that reason.)*

## Files touched

- `app/models.py` — `User.archived_at` (nullable) + `User.is_archived` property.
- `app/database.py` — idempotent `ALTER TABLE users ADD COLUMN archived_at`
  (extend-only; every existing account stays active on upgrade).
- `app/crud.py` — `archive_member()` (sets the flag **and bumps
  `token_version`** to kill any live session), `restore_member()`, an
  `archived_at IS NULL` filter on `member_roster`, and a new guard in
  `delete_member()` refusing to delete a member who isn't archived.
- `app/routers/web.py` — login block for archived members; archived excluded
  from the `/today` attendance list and from **all four** trainer member-pickers
  (dashboard, log form, planner, progress); new `POST /members/{id}/archive` and
  `/restore` routes; `members_page` sorts archived to the bottom; the delete
  route now refuses a non-archived member with a clear message.
- `app/templates/members.html` — active member → **Archive**; archived member →
  **Restore** + an "archived" badge; the old one-tap **Delete** control removed.
- Tests, `docs/CLAUDE.md`, `docs/RESUMING.md`, `README.md` — below / as noted.

### The subtle part: where "a member" is listed

The real work was finding **every** place the app enumerates members, so an
archived one leaks nowhere. There were **six**: the roster (`member_roster`),
the attendance screen, and four separate "log on behalf / pick a member"
selectors. Missing any one would show a ghost. A grep for `select(User)` and
`Role.member` across `crud.py` and `web.py` was used to be sure the list was
complete. The **members page itself deliberately still lists archived members**
— that is where you restore them.

## Tests — what each one guards, and what a failure would mean

Added to `tests/test_account_and_deletion.py` (8 new, 15 total in the file):

1. **`…hides_the_member_from_active_screens_but_keeps_the_account`** — the core
   promise: gone from roster + attendance + log picker, yet the account still
   exists (archived, not deleted). *Red would mean:* archiving either failed to
   hide them or destroyed data.
2. **`…cannot_log_in_while_an_active_one_still_can`** — archived login is
   blocked with a "deactivated" message; a different active member is
   unaffected. *Red would mean:* a member who "left" can still get in, or active
   members were locked out too.
3. **`…signs_the_member_out_of_a_live_session`** — uses a second client to hold
   the member's session, archives them, and asserts their held cookie is now
   stale. *Red would mean:* the `token_version` bump was dropped and an archived
   member keeps a working session for up to a week.
4. **`…restoring…brings_them_back_and_lets_them_log_in`** — restore returns them
   to the roster and lets them sign in with the untouched password. *Red would
   mean:* archive is effectively one-way, defeating the whole point.
5. **`…the_trainer_cannot_be_archived`** — archiving the sole trainer would lock
   the app; the route is member-only. *Red would mean:* a lockout footgun.
6. **`…a_member_cannot_archive_anyone`** — 403 for members
   (`require_trainer_web`). *Red would mean:* a member could deactivate another.
7. **`…permanent_delete_requires_archiving_first`** — deleting an *active*
   member is refused. *Red would mean:* the one-tap permanent delete we removed
   has crept back.
8. **`…members_page_shows_archive_for_active_and_restore_for_archived`** — also
   guards `members.html` against a render error (a 500 on that GET fails it).

## Failures & fixes

**None.** All 8 new tests passed on the first run; the full suite went **84 →
92 passed** with no regressions. One thing that could have bitten and didn't:
the trainer member-picker in `new_log_page` uses a slightly different query
shape (no `if is_trainer else []` suffix), so the bulk find/replace that fixed
the other three pickers skipped it — it was caught by re-grepping `select(User)`
rather than trusting the replace, and fixed before running tests.

## How to use it

As the trainer, on the **Members** page:

- **Archive** a member: their **Archive** control → confirm. They're signed out,
  drop off the roster/attendance, and can't log in. Nothing is deleted.
- **Restore**: archived members sit at the bottom of the list with a **Restore**
  button — one click and they're back, history intact.
- Archived members can still be opened (**View logs / Progress**) so you can
  look before restoring or (Phase 3) deleting.

## Result

`python -m pytest` → **92 passed** (was 84), ~2m, exit 0. Verified via the test
suite rendering the real templates; the dev server was intentionally not run
against the local `gym.db` to avoid leaving throwaway accounts in real data.

---

# Phase 1 — Trainer/member profile editing (name + login email)

*Status: **done**, tests green. Committed on the branch.*

## What & why

Feature 1 of the branch. A logged-in user can change **their own** display name
and login **email** at `POST /account/profile`, rendered by the "Your details"
form on the account page. This is the mechanism that turns the seeded *demo*
trainer into the user's real account: same account, same role, new identity.

The **role is intentionally not editable** here. There is no UI to make a second
trainer (by design — the first-ever account is the trainer, forever), so letting
someone flip their own role would be an unrecoverable footgun.

The *code* for this already existed on the branch; it had **no tests**. Phase 1
is that missing coverage — nothing about the feature's behaviour was changed.

## Files touched

- `tests/test_account_and_deletion.py` — **new**, 7 tests (this phase).
- `docs/CLAUDE.md` — added the `/account/profile` route and `crud.update_profile`
  to the reference, plus a link to this report.
- `docs/RESUMING.md` — added "how do I change the trainer's credentials?".
- `README.md` — one line noting a trainer can edit their own login.
- *(The feature code — `crud.update_profile`, the route, the template — was
  already present from the branch's earlier WIP commit and was left as-is.)*

## Tests — what each one guards, and what a failure would mean

All in `tests/test_account_and_deletion.py`:

1. **`…changes_name_and_email_then_logs_in_with_the_new_email`** — the core
   promise. After editing, the **new** email logs in and the **old** one does
   not. *Red would mean:* the change didn't persist, or the old credential still
   works — a lockout risk or a lingering-credential surprise.
2. **`…role_is_unchanged_after_editing_profile`** — editing identity must never
   touch the role. *Red would mean:* a trainer could silently demote themselves
   to member, with no way back through the UI.
3. **`…rejects_an_email_already_used_by_another_account`** — two accounts can't
   share a login email (login resolves *by* email). Also asserts the trainer's
   original email still works, i.e. the rejected edit changed nothing. *Red would
   mean:* the uniqueness guard is gone and sign-in could become ambiguous.
4. **`…rejects_a_blank_name`** — no nameless accounts; also proves no partial
   mutation happened. *Red would mean:* the blank-name guard in
   `crud.update_profile` was removed.
5. **`…rejects_a_blank_email`** — a blank email is an unusable login; caught at
   the route as an invalid address. *Red would mean:* an account could be left
   with no way to sign in.
6. **`…rejects_an_invalid_email`** — the same offline validation the rest of the
   app uses (`ada@12` has no real domain) applies here too. *Red would mean:* the
   account could hold an address that's really a typo.
7. **`…a_member_can_edit_their_own_details_only`** — the route edits
   `current_user` and takes no `user_id`, so a member can only change themselves;
   asserts two other accounts are untouched. *Red would mean:* self-service
   editing started reaching across accounts — an authorisation breach.

## Failures & fixes

**None.** All 7 new tests passed on the first run; the full suite went from
**77 → 84 passed** with no regressions. (The feature code was already written
and evidently correct — these tests simply prove and lock that in.)

## How to use it

As the trainer (or any member, for their own account):

1. **☰ menu → Account.**
2. Under **"Your details"**, edit the **Name** and/or **Email** and press
   **Save details**.
3. The email is your **login**. After changing it, sign in with the **new**
   email next time. You are *not* signed out — the change keeps your current
   session (the login token is keyed to your account id, not your email).

To turn the live demo trainer into your own account, do this on the **live**
site (https://gymlog-jtd8.onrender.com), not your local copy — local and live
are separate databases.

## Result

`python -m pytest` → **84 passed** (was 77), ~2m45s, exit 0.
