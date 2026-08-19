# Claude Code prompt — GymLog account recovery

Slots in as **Phase 0.5** of `docs/trainer-first-plan.md`, immediately after the security audit and before membership tracking. Task 1 is standalone and can be done today.

Copy everything below the line into Claude Code.

---

## Context

Read `CLAUDE.md` first. Same ground rules apply: extend-only schema via `ensure_schema()`, must work on both SQLite (local) and Postgres (Render), server-rendered Jinja2, no JS framework, no new paid dependencies.

**The problem.** There is no password reset flow anywhere in this app. Passwords are bcrypt-hashed and unrecoverable. The trainer account is created by being the first registration ever, and there is no self-service way to create a second trainer — so if the trainer's password is lost, the gym is permanently locked out of its own data with no path back in except direct database access.

This is not a hypothetical. It has already happened once.

**Why it matters commercially, not just operationally.** The trainer sets member passwords himself via Members → Add member. With 150 members, lockouts will happen weekly. If every one of those requires the developer to run a database query, the developer becomes permanent unpaid support and the product cannot scale past one gym. **The success criterion for this phase is that the trainer never needs to contact the developer to resolve a lockout.**

## Design decisions already made — do not redesign these

- **No email or SMS service in v1.** Adding SMTP, SendGrid, Twilio or an OTP provider means a new external dependency, a paid account, deliverability problems, and DLT registration for Indian SMS. It is also the wrong fit: in a value gym the trainer is physically standing next to the member. Solve it in person.
- **Three recovery paths, in this order of frequency:**
  1. Member forgets password → trainer resets it from the members list. Covers ~95% of real cases.
  2. Member wants to change their own password → self-service, requires their current password.
  3. Trainer forgets password → break-glass CLI script run by the developer against the live database.
- **Password reset must invalidate existing sessions.** JWTs here are stateless with a 7-day expiry (`ACCESS_TOKEN_EXPIRE_MINUTES=10080`), so a stolen or stale token stays valid for a week after a password change unless this is handled explicitly.

---

## Task 1 — Break-glass CLI script (do this first, standalone)

Create `scripts/set_password.py`.

- Usage: `python scripts/set_password.py --email <email> --password <new_password>`
- Add `--list` to print all accounts as `id | name | email | role` with no password data.
- Reads `DATABASE_URL` from the environment exactly the way `config.py` does, including the `postgres://` → `postgresql://` rewrite. With no `DATABASE_URL` set it must operate on local SQLite.
- Must hash using **the application's own hashing function**, imported from the app — do not reimplement bcrypt calls in the script, or the two will drift apart.
- Print a clear confirmation naming the account and role affected. Never print or log the password itself.
- Refuse to run if either argument is missing. Do not add an interactive prompt that echoes the password.

Document the usage in `CLAUDE.md`, including how to run it against the live Render database via the Render shell.

**Acceptance criteria:** With no `DATABASE_URL` set, the script lists local SQLite accounts and successfully changes a local password. With `DATABASE_URL` pointed at Postgres, the same commands work against the live database.

## Task 2 — Trainer resets a member's password

On the trainer's members list and on each member's page, add a **Reset password** action.

- Trainer types the new password directly (this matches the existing model where the trainer already knows member passwords by setting them at creation — do not change that model here).
- Minimum length 8 characters. Reject blanks and whitespace-only.
- Confirmation step before applying, showing the member's name so the wrong person can't be reset by a mis-tap.
- Success message confirms which member was reset. Never redisplay the password on screen after submission.
- **Trainer-only**, enforced server-side via the `require_trainer` dependency from Phase 0. A member must not be able to reset anyone's password, including their own via this route.

## Task 3 — Member changes their own password

A simple `/account/password` page available to both roles.

- Requires current password, new password, confirm new password.
- Verify the current password server-side before applying. Generic error on failure — do not reveal whether the account exists or which field was wrong.
- On success, log the user out and send them to the login page with a clear message.

## Task 4 — Invalidate sessions on password change

- Add `token_version` (integer, default 0) to `User` via `ensure_schema()`.
- Include `token_version` as a claim when issuing JWTs.
- On every token validation, compare the claim against the current database value and reject on mismatch.
- Increment `token_version` on every password change, from any of the three paths above.

This means a password reset immediately kills all existing sessions for that account, which is the behaviour anyone expects from a reset.

## Task 5 — Audit trail

Add a `PasswordChange` table: `id`, `user_id`, `changed_by_user_id` (nullable — null means the CLI script), `method` (`self` | `trainer` | `cli`), `created_at`.

**Never store passwords or hashes in this table.** It records that a change happened, who did it and when — nothing else.

Show the last change date on the member's page in the trainer view, so the trainer can answer "when did we last reset this."

## Task 6 — Recovery contact on the trainer account

Add `recovery_email` and `recovery_phone` (both nullable) to `User`, editable on an account settings page.

These are not used by any automated flow in v1. They exist so that (a) there is a known contact if manual recovery is ever needed, and (b) an emailed reset link can be added later without another schema change. Prompt the trainer to fill them in if they are empty.

## Task 7 — Login page fixes

Two small things that make lockouts feel worse than they are:

- The **Trainer | Member toggle** must match the account's role or login fails. When credentials are correct but the toggle is wrong, the current error is indistinguishable from a wrong password. Return a message that points at the toggle without confirming that the account exists, e.g. "Login failed — check that you've selected the correct role above."
- Add basic rate limiting on the login endpoint: lock further attempts for that email for a short window after repeated failures. Keep it simple and in-process; do not add Redis or any new service.

---

## Security requirements

- Passwords never appear in logs, error messages, URLs, query strings, template context or the audit table.
- Every route added here is protected server-side. Verify with curl using a member's token, the same way as the Phase 0 authz check, and append the new cases to `docs/authz-check.md`.
- No plaintext password is ever committed to the repo, including in test fixtures or documentation examples.

## Out of scope

Do not implement: email or SMS password reset, magic links, OTP, security questions, OAuth or social login, self-service "forgot password" for any role, or a second trainer/admin account. Each of these is a real feature; none of them is needed to stop lockouts from reaching the developer.

## Finally

Update `CLAUDE.md`: the new table, the new routes, the `token_version` mechanism, the CLI script usage, and remove "reset member password button for the trainer" from §13 Open ideas since it will now be built.
