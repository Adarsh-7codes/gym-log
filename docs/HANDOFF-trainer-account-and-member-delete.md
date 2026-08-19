# Handoff — trainer account editing & member deletion

**Status:** code written, **no tests, not merged.**
**Branch:** `wip/trainer-account-and-member-delete` (commit `d71bc86`)
**Base:** `main` at `9adaa15`

> Read `../CLAUDE.md` and `CLAUDE.md` first. The rules there apply to this work.

---

## Why this is on a branch

`main` auto-deploys to Render. This code has **no test coverage**, so merging it
would put untested behaviour — including a **destructive delete** — in front of
a real user. Finish the tests first, then merge.

The 77 existing tests pass on this branch, so nothing is regressed. The gap is
that none of them touch the new code.

## What the user asked for

1. **"How do I change the trainer?"** — the live trainer is currently a demo
   account. They want to change its name and login email to their own, keeping
   the same account and role. (Password change already exists from Phase 0.5.)
2. **"The trainer must be able to delete members."** — no deletion existed
   anywhere in the app.

## What is already built

| Piece | Where | Notes |
|---|---|---|
| `crud.update_profile(db, user, name=, email=)` | `app/crud.py` | Validates non-blank, rejects an email already used by another account. Role is deliberately **not** editable. |
| `crud.delete_member(db, member)` | `app/crud.py` | Raises `Forbidden` unless the target is a `member`. Deletes dependents **explicitly** — see the warning below. |
| `POST /account/profile` | `app/routers/web.py` | Any logged-in user edits their own name/email. |
| `POST /members/{user_id}/delete` | `app/routers/web.py` | Trainer-only via `require_trainer_web`. Requires the member's **name typed exactly** to confirm. |
| "Your details" form | `app/templates/account.html` | Name + email, with a note that the role stays trainer. |
| Delete control | `app/templates/members.html` | Only rendered for `member` rows; warns the deletion is permanent. |

### ⚠️ Why deletion is explicit, not `ON DELETE CASCADE`

SQLite does not enforce foreign keys unless `PRAGMA foreign_keys=ON`, which we
do not set. Postgres does enforce them. Relying on the database would mean
**deleting a member behaves differently locally and in production** — which
breaks the project rule that the same code must run identically on both. So
`delete_member()` removes each dependent table's rows itself.

If you add a new table with a `user_id`, **you must add it to that function** or
deleting a member will leave orphans. That is a real trap; a test should catch it.

## What is left to do

### 1. Write the tests (the actual blocker)

Suggested: extend `tests/test_account_recovery.py` or add
`tests/test_account_and_deletion.py`. Cover at minimum:

**Profile editing**
- Trainer changes name + email; can then log in with the **new** email and not
  the old one.
- Role is unchanged after editing (still `trainer`).
- Rejects an email already belonging to another account.
- Rejects blank name, blank email, and an invalid email (`ada@12`).
- A member can edit their own details but **cannot** edit anyone else's.

**Member deletion**
- Deleting a member removes the user **and** their logs, attendance,
  memberships, routines, split days, targets, body weights and password-change
  rows. Assert counts are zero for each.
- **Other members are untouched** — seed two members, delete one, assert the
  other's data still exists in full.
- The trainer account **cannot** be deleted through this route.
- A member calling the delete route gets **403**.
- A wrong or empty `confirm_name` does **not** delete.
- Deleting a member who wrote a `PasswordChange` for someone else nulls
  `changed_by_user_id` rather than deleting that audit row.

**Guard against the orphan trap**
- A test that fails if any table with a `user_id` column is missing from
  `delete_member()`. Reflect the tables and compare against the list — this is
  the test that protects future contributors.

### 2. Decide one open question

Deletion is currently **permanent**. Consider whether an "archive/deactivate"
option is wanted instead, so a member who leaves and returns keeps their
history. **Ask the user** — do not assume. Permanent deletion is what was
requested, but it is irreversible and they may not have considered the
trade-off.

### 3. Update the documentation

- `docs/CLAUDE.md`: add the two new routes, `update_profile` / `delete_member`,
  and a line in §11 about the explicit-deletion rule.
- `README.md`: mention that a trainer can edit their own login and remove
  members.
- `docs/RESUMING.md`: add "how do I change the trainer's credentials?" to the
  operational notes — it is a question the user will ask again.
- Regenerate the handbook (`python scripts/build_handbook.py`) if a feature
  section is added.

### 4. Merge

Only once `python -m pytest` is green:

```bash
git checkout main
git merge wip/trainer-account-and-member-delete
git push          # this deploys to Render
```

## How to verify by hand afterwards

1. Log in as the trainer → **☰ → Account** → change name and email → sign out →
   sign in with the **new** email on the **Trainer** tab.
2. **Members** → add a throwaway member → give them a log and an attendance mark
   → **Delete** → type their name → confirm they and their data are gone while
   other members are intact.

## Context the user will not repeat

- The live trainer account is a demo one they want to rename — that is the
  motivation for feature 1.
- They have been bitten twice by acting on the **local** database while testing
  the **live** app. When advising them, always say which database a command
  targets.
- They are learning deliberately and asked to be told *why*, not just *what*.
  Explain trade-offs and name the alternative you rejected.
