import calendar
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional
from urllib.parse import quote_plus

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.models import (
    Attendance,
    BodyPart,
    BodyWeight,
    Difficulty,
    Exercise,
    Log,
    Membership,
    MembershipStatus,
    MemberRoutine,
    PasswordChange,
    PasswordChangeMethod,
    PlanDay,
    PlanItem,
    Role,
    SplitDay,
    Target,
    User,
)
from app.security import hash_password


class Forbidden(Exception):
    pass


class NotFound(Exception):
    pass


def list_logs(
    db: Session,
    current_user: User,
    user_id: Optional[int] = None,
    exercise_id: Optional[int] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
):
    stmt = select(Log)

    if current_user.role == Role.trainer:
        if user_id is not None:
            stmt = stmt.where(Log.user_id == user_id)
    else:
        # Members can never see another member's logs, no matter what
        # user_id is requested -- this is the enforcement point, not the UI.
        stmt = stmt.where(Log.user_id == current_user.id)

    if exercise_id is not None:
        stmt = stmt.where(Log.exercise_id == exercise_id)
    if start_date is not None:
        stmt = stmt.where(Log.date >= start_date)
    if end_date is not None:
        stmt = stmt.where(Log.date <= end_date)

    stmt = stmt.order_by(Log.date.desc(), Log.id.desc())
    return db.scalars(stmt).all()


def create_log(
    db: Session,
    current_user: User,
    *,
    exercise_id: int,
    log_date: date,
    weight: Optional[float] = None,
    reps: Optional[int] = None,
    sets: Optional[int] = None,
    next_action: Optional[str] = None,
    notes: Optional[str] = None,
    feeling: Optional[str] = None,
    user_id: Optional[int] = None,
) -> Log:
    target_user_id = current_user.id
    if user_id is not None and user_id != current_user.id:
        if current_user.role != Role.trainer:
            raise Forbidden("Only a trainer can log workouts for another user")
        target_user_id = user_id

    if not db.get(User, target_user_id):
        raise NotFound("User not found")
    if not db.get(Exercise, exercise_id):
        raise NotFound("Exercise not found")

    log = Log(
        user_id=target_user_id,
        exercise_id=exercise_id,
        date=log_date,
        weight=weight,
        reps=reps,
        sets=sets,
        next_action=next_action,
        notes=notes,
        feeling=feeling,
        # Whoever is creating the entry: a member logs their own; a trainer
        # logging for a member records logged_by=trainer.
        logged_by=current_user.role,
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


def get_owned_log(db: Session, log_id: int, current_user: User) -> Log:
    log = db.get(Log, log_id)
    # A member gets the same 404 for "doesn't exist" and "exists but isn't
    # theirs" -- never confirm another member's log id is valid.
    if not log or (current_user.role != Role.trainer and log.user_id != current_user.id):
        raise NotFound("Log not found")
    return log


def update_log(db: Session, log: Log, **fields) -> Log:
    # Callers pass only the fields that should change (API: exclude_unset:
    # web form: everything on the form) so an explicit None means "clear it".
    for field, value in fields.items():
        setattr(log, field, value)
    db.commit()
    db.refresh(log)
    return log


def delete_log(db: Session, log: Log) -> None:
    db.delete(log)
    db.commit()


# --- Weekly planner -----------------------------------------------------


def resolve_target_user(current_user: User, user_id: Optional[int]) -> int:
    """Whose data is being edited (planner, routine, split).

    Only a trainer may act on someone else. A member passing a forged
    ?user_id= silently falls back to their own id -- enforced here, not in
    the template.
    """
    if user_id is not None and user_id != current_user.id and current_user.role == Role.trainer:
        return user_id
    return current_user.id


def get_week(db: Session, user_id: int) -> dict:
    """weekday (0..6) -> PlanDay for the user (only days that exist)."""
    days = db.scalars(select(PlanDay).where(PlanDay.user_id == user_id)).all()
    return {d.weekday: d for d in days}


def _get_or_create_day(db: Session, user_id: int, weekday: int) -> PlanDay:
    day = db.scalar(select(PlanDay).where(PlanDay.user_id == user_id, PlanDay.weekday == weekday))
    if day is None:
        day = PlanDay(user_id=user_id, weekday=weekday)
        db.add(day)
        db.flush()
    return day


def set_focus(db: Session, user_id: int, weekday: int, focus: Optional[str]) -> None:
    day = _get_or_create_day(db, user_id, weekday)
    day.focus = focus or None
    db.commit()


def add_plan_item(
    db: Session,
    user_id: int,
    weekday: int,
    *,
    exercise_id: int,
    target_sets: Optional[int] = None,
    target_reps: Optional[int] = None,
    target_weight: Optional[float] = None,
) -> PlanItem:
    if not db.get(Exercise, exercise_id):
        raise NotFound("Exercise not found")
    day = _get_or_create_day(db, user_id, weekday)
    item = PlanItem(
        plan_day_id=day.id,
        exercise_id=exercise_id,
        target_sets=target_sets,
        target_reps=target_reps,
        target_weight=target_weight,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def delete_plan_item(db: Session, current_user: User, item_id: int, target_user_id: int) -> None:
    item = db.get(PlanItem, item_id)
    # Only delete if it belongs to the planner the caller is allowed to edit.
    if item and item.day.user_id == target_user_id:
        db.delete(item)
        db.commit()


# --- Membership & dues --------------------------------------------------

EXPIRY_SOON_DAYS = 7   # membership expiring within this many days -> amber


def add_months(d: date, months: int) -> date:
    """Add whole months to a date, clamping to the end of the target month.

    Hand-rolled on purpose: dateutil is not a declared dependency, and this
    is the only date arithmetic the app needs. 31 Jan + 1 month -> 28/29 Feb.
    """
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(d.day, last_day))


def create_membership(
    db: Session,
    user_id: int,
    *,
    plan_start: date,
    duration_months: int,
    amount: Decimal,
    status: MembershipStatus,
    paid_on: Optional[date] = None,
    notes: Optional[str] = None,
) -> Membership:
    """Record a membership term. expires_on is computed once and stored."""
    if not db.get(User, user_id):
        raise NotFound("Member not found")
    if duration_months < 1:
        raise ValueError("Duration must be at least 1 month")
    membership = Membership(
        user_id=user_id,
        plan_start=plan_start,
        duration_months=duration_months,
        expires_on=add_months(plan_start, duration_months),
        amount=amount,
        status=status,
        # Marking it paid at creation time defaults the payment date to the start.
        paid_on=(paid_on or plan_start) if status == MembershipStatus.paid else None,
        notes=notes,
    )
    db.add(membership)
    db.commit()
    db.refresh(membership)
    return membership


def mark_membership_paid(db: Session, membership_id: int, on: Optional[date] = None) -> Optional[Membership]:
    membership = db.get(Membership, membership_id)
    if membership is None:
        return None
    membership.status = MembershipStatus.paid
    membership.paid_on = on or date.today()
    db.commit()
    db.refresh(membership)
    return membership


def delete_membership(db: Session, membership_id: int) -> Optional[int]:
    """Remove a wrongly-entered term. Returns the owner's user_id."""
    membership = db.get(Membership, membership_id)
    if membership is None:
        return None
    user_id = membership.user_id
    db.delete(membership)
    db.commit()
    return user_id


def current_membership(db: Session, user_id: int) -> Optional[Membership]:
    """The term with the latest plan_start -- what the member is on now."""
    return db.scalar(
        select(Membership)
        .where(Membership.user_id == user_id)
        .order_by(Membership.plan_start.desc(), Membership.id.desc())
        .limit(1)
    )


def membership_history(db: Session, user_id: int) -> list:
    """Every term for a member, newest first (the renewal record)."""
    return list(
        db.scalars(
            select(Membership)
            .where(Membership.user_id == user_id)
            .order_by(Membership.plan_start.desc(), Membership.id.desc())
        ).all()
    )


def pending_dues_for(db: Session, user_id: int) -> Decimal:
    """Total unpaid amount across all of this member's terms."""
    total = db.scalar(
        select(func.coalesce(func.sum(Membership.amount), 0)).where(
            Membership.user_id == user_id, Membership.status == MembershipStatus.pending
        )
    )
    return Decimal(str(total or 0))


# --- Attendance ---------------------------------------------------------


def mark_attendance(db: Session, user_id: int, on: date, marked_by: Role) -> bool:
    """Record that a member showed up. Idempotent: a repeat tap is a no-op.

    Returns True if a new row was created, False if it already existed.
    """
    existing = db.scalar(
        select(Attendance).where(Attendance.user_id == user_id, Attendance.date == on)
    )
    if existing is not None:
        return False
    db.add(Attendance(user_id=user_id, date=on, marked_by=marked_by))
    db.commit()
    return True


def unmark_attendance(db: Session, user_id: int, on: date) -> bool:
    """Undo a mis-tap. Returns True if a row was removed."""
    existing = db.scalar(
        select(Attendance).where(Attendance.user_id == user_id, Attendance.date == on)
    )
    if existing is None:
        return False
    db.delete(existing)
    db.commit()
    return True


def attended_on(db: Session, on: date) -> set:
    """Set of user_ids marked present on a given day."""
    return set(db.scalars(select(Attendance.user_id).where(Attendance.date == on)).all())


def attendance_this_month(db: Session, user_id: int, today: Optional[date] = None) -> int:
    today = today or date.today()
    first = today.replace(day=1)
    return db.scalar(
        select(func.count(Attendance.id)).where(
            Attendance.user_id == user_id, Attendance.date >= first, Attendance.date <= today
        )
    ) or 0


def attendance_counts_this_month(db: Session, today: Optional[date] = None) -> dict:
    """user_id -> sessions this month, in one grouped query."""
    today = today or date.today()
    first = today.replace(day=1)
    rows = db.execute(
        select(Attendance.user_id, func.count(Attendance.id))
        .where(Attendance.date >= first, Attendance.date <= today)
        .group_by(Attendance.user_id)
    ).all()
    return {uid: count for uid, count in rows}


def attendance_streak(db: Session, user_id: int, today: Optional[date] = None) -> int:
    """Consecutive *weeks* with at least one session, counting back from this week.

    Deliberately weekly, not daily: nobody trains 7 days a week, so a daily
    streak would read as a constant failure. A week counts if the member
    attended at least once in it.
    """
    today = today or date.today()
    days = db.scalars(
        select(Attendance.date).where(Attendance.user_id == user_id).order_by(Attendance.date.desc())
    ).all()
    if not days:
        return 0
    weeks_with_session = {d.isocalendar()[:2] for d in days}
    streak = 0
    cursor = today
    while cursor.isocalendar()[:2] in weeks_with_session:
        streak += 1
        cursor -= timedelta(days=7)
    return streak


def last_attendance(db: Session, user_id: int) -> Optional[date]:
    return db.scalar(
        select(func.max(Attendance.date)).where(Attendance.user_id == user_id)
    )


# --- Account details ----------------------------------------------------


def update_profile(db: Session, user: User, *, name: str, email: str) -> None:
    """Change a user's own display name and login email.

    This is how the seeded demo trainer becomes a real one: same account, new
    identity. The role is deliberately not editable here.
    """
    name = (name or "").strip()
    email = (email or "").strip().lower()
    if not name:
        raise ValueError("Name cannot be blank")
    if not email:
        raise ValueError("Email cannot be blank")
    clash = db.scalar(select(User).where(User.email == email, User.id != user.id))
    if clash is not None:
        raise ValueError("That email is already used by another account")
    user.name = name
    user.email = email
    db.commit()


def archive_member(db: Session, member: User) -> None:
    """Deactivate a member who has left the gym, reversibly.

    Sets ``archived_at`` (which hides them from active listings and blocks
    login) and bumps ``token_version`` so any session they still have open is
    invalidated at once -- an archived member should not stay signed in. Every
    row of their history is left untouched; ``restore_member`` undoes this.
    """
    if member.role != Role.member:
        raise Forbidden("Only member accounts can be archived")
    if member.is_archived:
        return  # idempotent: archiving an archived member is a no-op
    member.archived_at = datetime.now(timezone.utc)
    member.token_version = int(member.token_version or 0) + 1
    db.commit()


def restore_member(db: Session, member: User) -> None:
    """Reactivate an archived member -- they reappear and can log in again."""
    if member.role != Role.member:
        raise Forbidden("Only member accounts can be restored")
    member.archived_at = None
    db.commit()


# Every table with a `user_id` column, i.e. every table whose rows are owned by
# one user and must be removed when that user is deleted. PlanItem is
# deliberately absent: it has no user_id and is cleared via its PlanDay parent
# inside delete_member(). **If you add a table with a user_id, add it here** or
# deleting a member will orphan its rows -- the reflection test in
# tests/test_account_and_deletion.py fails loudly if this list falls behind.
USER_OWNED_MODELS = (
    Log,
    Attendance,
    Membership,
    MemberRoutine,
    SplitDay,
    Target,
    BodyWeight,
    PlanDay,
    PasswordChange,
)


def delete_member(db: Session, member: User) -> None:
    """Permanently remove a member and everything belonging to them.

    Dependent rows are deleted explicitly rather than leaning on ON DELETE
    CASCADE: SQLite does not enforce foreign keys unless the pragma is on, so
    relying on the database would quietly behave differently locally and in
    production. Explicit is identical on both.

    Two-step by design: a member must be **archived** before they can be
    permanently deleted, so the irreversible action is always a deliberate
    second step rather than one tap away from an active member.
    """
    if member.role != Role.member:
        raise Forbidden("Only member accounts can be deleted")
    if not member.is_archived:
        raise Forbidden("Archive the member before deleting them permanently")

    uid = member.id
    # PlanItem hangs off PlanDay, not the user -- clear it before its parent
    # (Postgres enforces the FK; SQLite doesn't, but we behave the same on both).
    db.execute(
        delete(PlanItem).where(
            PlanItem.plan_day_id.in_(select(PlanDay.id).where(PlanDay.user_id == uid))
        )
    )
    for model in USER_OWNED_MODELS:
        db.execute(delete(model).where(model.user_id == uid))
    # Audit rows this member wrote about *someone else* lose their author, not
    # the record that a change happened -- that evidence belongs to the other
    # account's history. (Their own PasswordChange rows were deleted just above.)
    db.execute(
        update(PasswordChange)
        .where(PasswordChange.changed_by_user_id == uid)
        .values(changed_by_user_id=None)
    )
    db.delete(member)
    db.commit()


# --- Password changes ---------------------------------------------------

MIN_PASSWORD_LENGTH = 8


def validate_new_password(raw: str) -> str:
    """Return the password if acceptable, else raise ValueError."""
    if raw is None or not raw.strip():
        raise ValueError("Password cannot be blank")
    if len(raw) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
    return raw


def set_password(
    db: Session,
    user: User,
    new_password: str,
    *,
    method: PasswordChangeMethod,
    changed_by: Optional[User] = None,
) -> None:
    """The single place a password is ever changed.

    Always does three things together, so no caller can do one and forget the
    others: rehash, bump token_version (killing existing sessions), and record
    that a change happened. The audit row never contains the password or hash.
    """
    validate_new_password(new_password)
    user.password_hash = hash_password(new_password)
    user.token_version = int(user.token_version or 0) + 1
    db.add(
        PasswordChange(
            user_id=user.id,
            changed_by_user_id=changed_by.id if changed_by else None,
            method=method,
        )
    )
    db.commit()


def last_password_change(db: Session, user_id: int) -> Optional[PasswordChange]:
    return db.scalar(
        select(PasswordChange)
        .where(PasswordChange.user_id == user_id)
        .order_by(PasswordChange.created_at.desc(), PasswordChange.id.desc())
        .limit(1)
    )


# --- Body weight (trend and rate only) ----------------------------------
#
# Forbidden here by design, do not add later without re-reading the brief:
#   * No progress bar or "X% of goal complete" -- body weight is not
#     monotonic, and a bar going backwards punishes a member who did nothing
#     wrong.
#   * No hard-coded target rate (e.g. "10 kg in 2 months" is ~1.25 kg/week,
#     well above what is normally considered sustainable). The UI encodes no
#     rate at all.
#   * No attribution of a result to diet, junk food, effort or discipline.
#     The app cannot observe those and must not claim to.

ROLLING_WINDOW_DAYS = 7   # smoothing window, so water-weight noise is not "failure"


def record_body_weight(
    db: Session, user_id: int, *, on: date, weight_kg: float, recorded_by: Role
) -> BodyWeight:
    """Add or update a weigh-in. One reading per member per day."""
    if not db.get(User, user_id):
        raise NotFound("Member not found")
    if weight_kg <= 0:
        raise ValueError("Weight must be greater than zero")
    existing = db.scalar(
        select(BodyWeight).where(BodyWeight.user_id == user_id, BodyWeight.date == on)
    )
    if existing is not None:
        existing.weight_kg = weight_kg
        existing.recorded_by = recorded_by
        db.commit()
        db.refresh(existing)
        return existing
    entry = BodyWeight(user_id=user_id, date=on, weight_kg=weight_kg, recorded_by=recorded_by)
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def delete_body_weight(db: Session, entry_id: int) -> Optional[int]:
    entry = db.get(BodyWeight, entry_id)
    if entry is None:
        return None
    user_id = entry.user_id
    db.delete(entry)
    db.commit()
    return user_id


def body_weights(db: Session, user_id: int) -> list:
    """All weigh-ins, oldest first."""
    return list(
        db.scalars(
            select(BodyWeight).where(BodyWeight.user_id == user_id).order_by(BodyWeight.date)
        ).all()
    )


def _rolling_average(entries: list) -> list:
    """(date, smoothed_kg) using a trailing ROLLING_WINDOW_DAYS mean.

    Only meaningful when entries are dense; with the expected weekly cadence
    each window usually holds one reading and this is a no-op.
    """
    out = []
    for i, entry in enumerate(entries):
        window_start = entry.date - timedelta(days=ROLLING_WINDOW_DAYS - 1)
        window = [e.weight_kg for e in entries[: i + 1] if e.date >= window_start]
        out.append((entry.date, sum(window) / len(window)))
    return out


def body_weight_trend(db: Session, user_id: int) -> Optional[dict]:
    """Direction and rate of change. None when there is not enough to say.

    Returns a factual summary only -- never a percentage of any goal.
    """
    entries = body_weights(db, user_id)
    if len(entries) < 2:
        return {"entries": entries, "enough": False} if entries else None

    first, last = entries[0], entries[-1]
    change = round(last.weight_kg - first.weight_kg, 1)
    days = max(1, (last.date - first.date).days)
    weeks = days / 7
    per_week = round(change / weeks, 2) if weeks >= 1 else None

    if change < -0.1:
        direction = "down"
    elif change > 0.1:
        direction = "up"
    else:
        direction = "steady"

    return {
        "entries": entries,
        "enough": True,
        "latest": last,
        "first": first,
        "change": change,
        "abs_change": abs(change),
        "direction": direction,
        "weeks": round(weeks, 1),
        "per_week": abs(per_week) if per_week is not None else None,
        "smoothed": _rolling_average(entries),
    }


# --- Progressive-overload targets ---------------------------------------


def exercise_best(db: Session, user_id: int, exercise_id: int) -> Optional[dict]:
    """Heaviest logged set for one exercise, and how long it has stood.

    `since` is when that best was FIRST reached, so "unchanged for N weeks"
    measures the plateau rather than the time since the last session.
    """
    logs = db.scalars(
        select(Log)
        .where(Log.user_id == user_id, Log.exercise_id == exercise_id, Log.weight.is_not(None))
        .order_by(Log.date)
    ).all()
    if not logs:
        return None
    best_weight = max(log.weight for log in logs)
    at_best = [log for log in logs if log.weight == best_weight]
    return {
        "weight": best_weight,
        "reps": max((log.reps or 0) for log in at_best),
        "since": at_best[0].date,
        "days_at_best": (date.today() - at_best[0].date).days,
        "sessions": len(logs),
    }


def create_target(
    db: Session,
    user_id: int,
    *,
    exercise_id: int,
    target_weight: float,
    target_reps: Optional[int],
    target_date: date,
) -> Target:
    if not db.get(User, user_id):
        raise NotFound("Member not found")
    if not db.get(Exercise, exercise_id):
        raise NotFound("Exercise not found")
    if target_weight <= 0:
        raise ValueError("Target weight must be greater than zero")
    target = Target(
        user_id=user_id,
        exercise_id=exercise_id,
        target_weight=target_weight,
        target_reps=target_reps,
        target_date=target_date,
    )
    db.add(target)
    db.commit()
    db.refresh(target)
    return target


def delete_target(db: Session, target_id: int) -> Optional[int]:
    target = db.get(Target, target_id)
    if target is None:
        return None
    user_id = target.user_id
    db.delete(target)
    db.commit()
    return user_id


def target_progress(db: Session, target: Target, today: Optional[date] = None) -> dict:
    """Where a member stands against one target -- all of it checkable from logs."""
    today = today or date.today()
    best = exercise_best(db, target.user_id, target.exercise_id)
    days_left = (target.target_date - today).days

    reached = False
    gap = None
    if best is not None:
        meets_weight = best["weight"] >= target.target_weight
        meets_reps = target.target_reps is None or best["reps"] >= target.target_reps
        reached = meets_weight and meets_reps
        gap = round(target.target_weight - best["weight"], 2)

    return {
        "target": target,
        "best": best,
        "reached": reached,
        "gap": gap,
        "days_left": days_left,
        "overdue": (not reached) and days_left < 0,
        # Only call it a plateau once the best has stood a while.
        "weeks_at_best": (best["days_at_best"] // 7) if best else None,
    }


def targets_for(db: Session, user_id: int, today: Optional[date] = None) -> list:
    """All of a member's targets with progress, unmet and soonest-due first."""
    targets = db.scalars(
        select(Target).where(Target.user_id == user_id).order_by(Target.target_date)
    ).all()
    rows = [target_progress(db, t, today) for t in targets]
    rows.sort(key=lambda r: (r["reached"], r["target"].target_date))
    return rows


# --- New-member cohort (first 90 days) ----------------------------------

NEW_MEMBER_DAYS = 90        # churn window: treat as "new" for this long
FIRST_WINDOW_DAYS = 14      # onboarding window we report sessions for
RECENT_WINDOW_DAYS = 7      # "last 7 days" attention window
MIN_SESSIONS_RECENT = 2     # fewer than this in the recent window -> attention


def joined_on(db: Session, user: User) -> Optional[date]:
    """Best available join date.

    Prefers the earliest membership plan_start -- that is how a gym actually
    thinks about when someone joined -- and falls back to the account's
    created_at. Self-corrects as real memberships are recorded.
    """
    first_plan = db.scalar(
        select(func.min(Membership.plan_start)).where(Membership.user_id == user.id)
    )
    if first_plan:
        return first_plan
    if user.created_at:
        return user.created_at.date()
    return None


def _sessions_between(db: Session, user_id: int, start: date, end: date) -> int:
    """Attendance rows in [start, end]."""
    return db.scalar(
        select(func.count(Attendance.id)).where(
            Attendance.user_id == user_id, Attendance.date >= start, Attendance.date <= end
        )
    ) or 0


def cohort_stats(db: Session, user: User, today: Optional[date] = None) -> dict:
    """First-90-days view for one member. Empty-ish dict when not applicable."""
    today = today or date.today()
    joined = joined_on(db, user)
    if joined is None:
        return {"is_new": False, "joined_on": None, "days_since_joining": None}

    days_since = (today - joined).days
    is_new = 0 <= days_since <= NEW_MEMBER_DAYS
    first_14 = _sessions_between(db, user.id, joined, joined + timedelta(days=FIRST_WINDOW_DAYS))
    last_7 = _sessions_between(db, user.id, today - timedelta(days=RECENT_WINDOW_DAYS), today)

    # Only judge attendance once there is at least one recorded session --
    # otherwise every new member flags before the trainer has started marking.
    has_any = (
        db.scalar(select(func.count(Attendance.id)).where(Attendance.user_id == user.id)) or 0
    ) > 0
    needs_attention = is_new and has_any and last_7 < MIN_SESSIONS_RECENT

    return {
        "is_new": is_new,
        "joined_on": joined,
        "days_since_joining": days_since,
        "sessions_first_14": first_14,
        "sessions_last_7": last_7,
        "needs_attention": needs_attention,
    }


# --- Talking points -----------------------------------------------------
#
# Strict rules, enforced here rather than left to the template:
#   * Facts only, derived from logged or attendance data.
#   * Never infer WHY something happened (diet, effort, motivation, lifestyle).
#   * Never accusatory or shaming phrasing -- these are notes for the trainer
#     to speak from, not a verdict handed to the member.
#   * If there is not enough data for a true statement, produce nothing.

TALKING_POINT_LIMIT = 3
_IMPROVEMENT_MIN_SESSIONS = 3   # need a real trend, not two data points


def _fmt_weight(value: float) -> str:
    """60.0 -> '60', 47.5 -> '47.5'."""
    return f"{value:g}"


def _improvement_point(db: Session, user_id: int) -> Optional[str]:
    """Biggest verified weight gain on one exercise, e.g. 'Bench 40 -> 47.5 kg over 6 weeks'."""
    logs = db.scalars(
        select(Log)
        .where(Log.user_id == user_id, Log.weight.is_not(None))
        .order_by(Log.date)
    ).all()
    by_exercise: dict = {}
    for log in logs:
        by_exercise.setdefault(log.exercise_id, []).append(log)

    best = None
    for ex_logs in by_exercise.values():
        if len(ex_logs) < _IMPROVEMENT_MIN_SESSIONS:
            continue
        first, last = ex_logs[0], ex_logs[-1]
        gain = (last.weight or 0) - (first.weight or 0)
        if gain <= 0:
            continue
        weeks = max(1, round((last.date - first.date).days / 7))
        candidate = (
            gain,
            f"{first.exercise.name} {_fmt_weight(first.weight)} → {_fmt_weight(last.weight)} kg "
            f"over {weeks} week{'' if weeks == 1 else 's'}",
        )
        if best is None or candidate[0] > best[0]:
            best = candidate
    return best[1] if best else None


def _attendance_point(db: Session, user_id: int, today: Optional[date] = None) -> Optional[str]:
    """This month's sessions vs last month's -- only when both are known."""
    today = today or date.today()
    this_first = today.replace(day=1)
    last_last = this_first - timedelta(days=1)
    last_first = last_last.replace(day=1)

    this_month = _sessions_between(db, user_id, this_first, today)
    last_month = _sessions_between(db, user_id, last_first, last_last)
    if this_month == 0 and last_month == 0:
        return None
    if last_month == 0:
        return f"{this_month} session{'' if this_month == 1 else 's'} this month"
    if this_month > last_month:
        return f"{this_month} sessions this month, up from {last_month}"
    if this_month < last_month:
        return f"{this_month} this month vs {last_month} last month"
    return f"{this_month} sessions this month, same as last month"


def _neglected_body_part_point(db: Session, user_id: int, today: Optional[date] = None) -> Optional[str]:
    """A body part in their split they used to train but haven't lately."""
    today = today or date.today()
    planned = {bp for parts in get_split(db, user_id).values() for bp in parts}
    if not planned:
        return None
    worst = None
    for bp in planned:
        last = db.scalar(
            select(func.max(Log.date))
            .join(Exercise, Log.exercise_id == Exercise.id)
            .where(Log.user_id == user_id, Exercise.body_part == bp)
        )
        if last is None:
            continue  # never trained it -> no factual "hasn't since" claim
        days = (today - last).days
        if days >= INACTIVE_DAYS and (worst is None or days > worst[0]):
            worst = (days, f"Hasn't trained {bp.value} in {days} days")
    return worst[1] if worst else None


def _stall_point(db: Session, user_id: int) -> Optional[str]:
    """An exercise sitting at the same top weight, stated neutrally."""
    logs = db.scalars(
        select(Log).where(Log.user_id == user_id).order_by(Log.date.desc(), Log.id.desc())
    ).all()
    stalled = _stalled_exercises_from_logs(logs)
    if not stalled:
        return None
    ex = stalled[0]
    ex_logs = [x for x in logs if x.exercise_id == ex.id][:SESSIONS_TO_COMPARE]
    weight = ex_logs[0].weight if ex_logs else None
    if weight is None:
        return f"{ex.name} unchanged for {len(ex_logs)} sessions"
    return f"{ex.name} unchanged at {_fmt_weight(weight)} kg for {len(ex_logs)} sessions"


def talking_points(db: Session, user_id: int, today: Optional[date] = None) -> list:
    """Up to TALKING_POINT_LIMIT true, non-judgemental lines. May be empty."""
    candidates = [
        _improvement_point(db, user_id),
        _attendance_point(db, user_id, today),
        _neglected_body_part_point(db, user_id, today),
        _stall_point(db, user_id),
    ]
    return [c for c in candidates if c][:TALKING_POINT_LIMIT]


# --- Progress / stall detection ----------------------------------------

SESSIONS_TO_COMPARE = 3   # look at the last N logged sessions per exercise
INACTIVE_DAYS = 10        # no session in this many days -> flag


def _top_set_value(log: Log) -> tuple:
    """A session's 'top set' as a comparable (weight, reps) tuple.

    Compared lexicographically: heavier weight always wins; at equal weight,
    more reps wins. A good proxy for 'did this session beat the last one'.
    """
    return (log.weight or 0.0, log.reps or 0)


def _stalled_exercises_from_logs(logs) -> list:
    """logs: one user's Log rows, newest-first.

    Returns the Exercise objects that are stalled: exercises with at least
    SESSIONS_TO_COMPARE logged sessions where the most recent session did NOT
    beat the best of the preceding ones (no new personal best -> plateau).
    """
    by_exercise: dict = {}
    for log in logs:
        by_exercise.setdefault(log.exercise_id, []).append(log)

    stalled = []
    for ex_logs in by_exercise.values():
        recent = ex_logs[:SESSIONS_TO_COMPARE]  # newest first
        if len(recent) < SESSIONS_TO_COMPARE:
            continue  # not enough history to call it a stall
        newest = _top_set_value(recent[0])
        prev_best = max(_top_set_value(log) for log in recent[1:])
        if newest <= prev_best:
            stalled.append(recent[0].exercise)
    return stalled


def member_status(db: Session, user: User) -> dict:
    """Quick-glance status for one member: last session + stall/inactivity flags."""
    logs = db.scalars(
        select(Log).where(Log.user_id == user.id).order_by(Log.date.desc(), Log.id.desc())
    ).all()
    last_logged = logs[0].date if logs else None

    # Activity = did they turn up. Attendance is the primary signal; a logged
    # workout also counts as evidence of presence (you cannot log a session you
    # did not do). This keeps a member who trains but never logs -- and one who
    # logs on a day the trainer forgot to mark -- from being falsely flagged.
    attended = last_attendance(db, user.id)
    last_date = max([d for d in (attended, last_logged) if d is not None], default=None)

    days_since = (date.today() - last_date).days if last_date else None
    inactive = last_date is None or days_since >= INACTIVE_DAYS
    stalled = _stalled_exercises_from_logs(logs)
    sessions_this_month = attendance_this_month(db, user.id)

    cohort = cohort_stats(db, user)

    reasons = []
    if last_date is None:
        reasons.append("No sessions yet")
    elif inactive:
        reasons.append(f"No session in {days_since} days")
    if cohort.get("needs_attention"):
        reasons.append(
            f"New member, {cohort['sessions_last_7']} session"
            f"{'' if cohort['sessions_last_7'] == 1 else 's'} in last 7 days"
        )
    if stalled:
        reasons.append("Stalled: " + ", ".join(e.name for e in stalled))

    membership = current_membership(db, user.id)
    expires_on = membership.expires_on if membership else None
    days_to_expiry = (expires_on - date.today()).days if expires_on else None
    dues = pending_dues_for(db, user.id)

    return {
        "user": user,
        "last_date": last_date,
        "last_logged": last_logged,
        "last_attended": attended,
        "sessions_this_month": sessions_this_month,
        "cohort": cohort,
        "is_new": cohort.get("is_new", False),
        "days_since": days_since,
        "inactive": inactive,
        "stalled": stalled,
        "flagged": bool(reasons),
        "reasons": reasons,
        # --- membership / dues (Phase 1) ---
        "membership": membership,
        "expires_on": expires_on,
        "days_to_expiry": days_to_expiry,
        "expired": days_to_expiry is not None and days_to_expiry < 0,
        "expiring_soon": days_to_expiry is not None and 0 <= days_to_expiry <= EXPIRY_SOON_DAYS,
        "dues": dues,
        "has_dues": dues > 0,
    }


# Roster sort keys. Default surfaces money and expiry before training flags,
# because that is the trainer's daily question.
def _roster_sort_key(s: dict, sort: str):
    name = s["user"].name.lower()
    # None expiry sorts last within its group.
    exp = s["days_to_expiry"] if s["days_to_expiry"] is not None else 10**6
    if sort == "name":
        return (name,)
    if sort == "expiry":
        return (exp, name)
    if sort == "dues":
        return (-float(s["dues"]), name)
    if sort == "activity":
        return (-(s["days_since"] if s["days_since"] is not None else 10**6), name)
    # default: overdue dues -> expiring soon -> stall/inactivity -> everyone else
    return (
        not s["has_dues"],
        not (s["expired"] or s["expiring_soon"]),
        not s["flagged"],
        exp,
        name,
    )


ROSTER_SORTS = ("default", "name", "expiry", "dues", "activity")


def member_roster(db: Session, sort: str = "default") -> list:
    """All members with status; dues/expiry/flags surfaced first by default."""
    if sort not in ROSTER_SORTS:
        sort = "default"
    members = db.scalars(
        select(User)
        .where(User.role == Role.member, User.archived_at.is_(None))
        .order_by(User.name)
    ).all()
    statuses = [member_status(db, m) for m in members]
    statuses.sort(key=lambda s: _roster_sort_key(s, sort))
    return statuses


def roster_summary(rows: list) -> dict:
    """Headline numbers for the top of the roster."""
    return {
        "total": len(rows),
        "active": sum(1 for r in rows if r["days_to_expiry"] is not None and r["days_to_expiry"] >= 0),
        "expiring_soon": sum(1 for r in rows if r["expiring_soon"]),
        "expired": sum(1 for r in rows if r["expired"]),
        "no_membership": sum(1 for r in rows if r["membership"] is None),
        "pending_dues": sum((r["dues"] for r in rows), Decimal("0")),
    }


def stalled_exercise_ids(db: Session, user_id: int) -> set:
    """Set of exercise ids currently flagged as stalled for this user."""
    logs = db.scalars(
        select(Log).where(Log.user_id == user_id).order_by(Log.date.desc(), Log.id.desc())
    ).all()
    return {e.id for e in _stalled_exercises_from_logs(logs)}


def last_sets_by_exercise(db: Session, user_id: int) -> dict:
    """exercise_id -> most recent Log for that exercise, for prefill defaults."""
    logs = db.scalars(
        select(Log).where(Log.user_id == user_id).order_by(Log.date.desc(), Log.id.desc())
    ).all()
    out: dict = {}
    for log in logs:
        out.setdefault(log.exercise_id, log)
    return out


# --- Exercise Library + Member Routine ---------------------------------

# Order body parts / difficulties consistently in the UI.
BODY_PARTS = list(BodyPart)
DIFFICULTY_ORDER = {Difficulty.beginner: 0, Difficulty.intermediate: 1, Difficulty.advanced: 2}


def _exercise_sort_key(ex: Exercise):
    # Beginner first so a newcomer starts easy and progresses.
    return (DIFFICULTY_ORDER.get(ex.difficulty, 99), ex.name.lower())


def library_grouped(db: Session) -> dict:
    """body_part -> [Exercise] sorted by difficulty then name (categorized library)."""
    exercises = db.scalars(select(Exercise).where(Exercise.body_part.is_not(None))).all()
    groups: dict = {}
    for ex in exercises:
        groups.setdefault(ex.body_part, []).append(ex)
    for part in groups:
        groups[part].sort(key=_exercise_sort_key)
    return groups


def routine_exercise_ids(db: Session, user_id: int) -> set:
    """Set of exercise ids currently in a member's routine (for checkbox state)."""
    return set(
        db.scalars(select(MemberRoutine.exercise_id).where(MemberRoutine.user_id == user_id)).all()
    )


def routine_grouped(db: Session, user_id: int) -> dict:
    """body_part -> [Exercise] for the member's active routine (drives the log page)."""
    entries = db.scalars(select(MemberRoutine).where(MemberRoutine.user_id == user_id)).all()
    groups: dict = {}
    for entry in entries:
        ex = entry.exercise
        if ex is not None and ex.body_part is not None:
            groups.setdefault(ex.body_part, []).append(ex)
    for part in groups:
        groups[part].sort(key=_exercise_sort_key)
    return groups


def set_routine_for_body_part(
    db: Session,
    user_id: int,
    body_part: BodyPart,
    exercise_ids,
    assigned_by: Role = Role.member,
) -> None:
    """Make the member's routine for one body_part exactly `exercise_ids`.

    Adds newly selected, removes unchecked. Removal deletes ONLY the
    MemberRoutine rows -- historical Log entries are a separate table and are
    never touched here. `assigned_by` records who prescribed the new rows.
    """
    valid = set(
        db.scalars(select(Exercise.id).where(Exercise.body_part == body_part)).all()
    )
    want = {int(i) for i in exercise_ids if int(i) in valid}

    current = db.scalars(
        select(MemberRoutine).where(
            MemberRoutine.user_id == user_id, MemberRoutine.body_part == body_part
        )
    ).all()
    have = {row.exercise_id: row for row in current}

    for ex_id, row in have.items():
        if ex_id not in want:
            db.delete(row)  # remove from routine only; logs stay intact
    for ex_id in want:
        if ex_id not in have:
            db.add(
                MemberRoutine(
                    user_id=user_id,
                    exercise_id=ex_id,
                    body_part=body_part,
                    date_added=date.today(),
                    assigned_by=assigned_by,
                )
            )
    db.commit()


def trainer_assigned_exercise_ids(db: Session, user_id: int) -> set:
    """Exercise ids in this member's routine that the trainer prescribed."""
    return set(
        db.scalars(
            select(MemberRoutine.exercise_id).where(
                MemberRoutine.user_id == user_id, MemberRoutine.assigned_by == Role.trainer
            )
        ).all()
    )


def demo_link(exercise: Exercise) -> str:
    """A always-resolvable 'how to do this' link for an exercise.

    Uses the trainer-set demo_url when present. Otherwise falls back to a
    YouTube search for the exercise name -- deliberately a search rather than
    a hardcoded video id, so links can never rot or point at the wrong lift.
    """
    if exercise.demo_url:
        return exercise.demo_url
    query = quote_plus(f"{exercise.name} proper form")
    return f"https://www.youtube.com/results?search_query={query}"


def remove_from_routine(db: Session, user_id: int, exercise_id: int) -> None:
    """Remove a single exercise from the member's routine. Logs are kept."""
    row = db.scalar(
        select(MemberRoutine).where(
            MemberRoutine.user_id == user_id, MemberRoutine.exercise_id == exercise_id
        )
    )
    if row:
        db.delete(row)
        db.commit()


# --- Weekly split (which body parts on which weekday) ------------------

WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_BODY_PART_ORDER = {bp: i for i, bp in enumerate(BODY_PARTS)}


def _sorted_parts(parts) -> list:
    return sorted(parts, key=lambda b: _BODY_PART_ORDER.get(b, 99))


def get_split(db: Session, user_id: int) -> dict:
    """weekday (0..6) -> [BodyPart] for the member's recurring weekly split."""
    rows = db.scalars(select(SplitDay).where(SplitDay.user_id == user_id)).all()
    out: dict = {}
    for row in rows:
        out.setdefault(row.weekday, []).append(row.body_part)
    for wd in out:
        out[wd] = _sorted_parts(out[wd])
    return out


def body_parts_for_day(db: Session, user_id: int, weekday: int) -> list:
    """Body parts scheduled for one weekday (empty = rest day / not set)."""
    rows = db.scalars(
        select(SplitDay).where(SplitDay.user_id == user_id, SplitDay.weekday == weekday)
    ).all()
    return _sorted_parts(row.body_part for row in rows)


def set_split_for_day(db: Session, user_id: int, weekday: int, body_parts) -> None:
    """Make the member's split for one weekday exactly `body_parts`."""
    want = set(body_parts)
    current = db.scalars(
        select(SplitDay).where(SplitDay.user_id == user_id, SplitDay.weekday == weekday)
    ).all()
    have = {row.body_part: row for row in current}
    for bp, row in have.items():
        if bp not in want:
            db.delete(row)
    for bp in want:
        if bp not in have:
            db.add(SplitDay(user_id=user_id, weekday=weekday, body_part=bp))
    db.commit()


def routine_for_parts(db: Session, user_id: int, parts) -> dict:
    """Routine exercises limited to `parts`, kept in the split's body-part order."""
    groups = routine_grouped(db, user_id)
    ordered: dict = {}
    for bp in parts:
        if bp in groups:
            ordered[bp] = groups[bp]
    return ordered
