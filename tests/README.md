# Test suite

76 automated tests covering every phase of the build. These replace the
throwaway scripts used earlier in the project — anyone can now re-run the whole
suite after a change and know within two minutes whether something broke.

## Running them

```bash
pip install -r requirements-dev.txt
python -m pytest                      # everything (~2 minutes)
python -m pytest tests/test_membership.py    # one file
python -m pytest -k "password"        # anything matching a name
python -m pytest -v                   # list every test as it runs
```

Every test gets its own brand-new SQLite database in a temp folder, so tests
never interfere with each other and **never touch your real `gym.db`**.

## What is in each file

| File | Tests | Covers |
|---|---|---|
| `test_auth_and_roles.py` | 9 | First-user-becomes-trainer, the role toggle, closed self-registration, email validation, password hashing |
| `test_authorization.py` | 7 | The IDOR defences — a member reaching another member's data or any trainer route. The highest-stakes file here |
| `test_membership.py` | 6 | Phase 1: month arithmetic, expiry states, roster ordering, dues totals |
| `test_routine_split_and_logging.py` | 8 | Library seeding, multi-body-part days, day filtering, trainer-assigned routines, `logged_by`, and the rule that routine edits never delete history |
| `test_attendance.py` | 7 | Phase 3: idempotent marking, un-marking, attendance-driven inactivity |
| `test_progress_features.py` | 12 | Phases 4–6: talking points, overload targets, body-weight trend |
| `test_account_recovery.py` | 19 | Phase 0.5: the three recovery paths, session invalidation, audit trail, login throttling |
| `test_cli_set_password.py` | 8 | The break-glass CLI script, including that it never prints a password or a database credential |

## The tests that exist to stop something coming back

A few tests do not check that a feature works — they check that a tempting
**bad** idea stays out:

- `test_body_weight_never_shows_a_progress_bar_or_goal_percentage` — body
  weight is not monotonic, so a bar that moves backwards punishes a member who
  did nothing wrong.
- `test_talking_points_never_moralise` — fails if the app ever mentions diet,
  effort or discipline, none of which it can observe.
- `test_thin_data_produces_no_talking_points_rather_than_filler` — silence is
  correct when there is nothing true to say.
- `test_removing_an_exercise_from_a_routine_keeps_its_log_history` — protects
  the most important data rule in the app.
- `test_new_member_with_no_attendance_is_not_flagged` — stops the roster turning
  red for everyone the day a feature ships.

## Conventions

- Fixtures live in `conftest.py`: `client` (fresh app + database) and helpers
  `register_trainer`, `add_member`, `login`, `logout`, `user_id_by_email`.
- The `client` fixture is used as a context manager so FastAPI's lifespan runs.
  That is what creates the tables and seeds the exercise library — forgetting it
  was the very first test failure in this project.
- Test names read as sentences describing the guarantee, so a failure line is
  self-explanatory without opening the file.
