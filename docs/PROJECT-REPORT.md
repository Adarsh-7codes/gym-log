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
