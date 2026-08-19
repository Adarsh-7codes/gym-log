"""Server-rendered pages (cookie auth), separate from the JSON API under /api.

Auth flow: login/register set an httponly `access_token` cookie; protected
pages depend on `get_current_user_web`, which raises WebAuthRequired when the
cookie is missing/invalid -- main.py maps that to a redirect to /login.
"""
import os
import time
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import quote_plus
from typing import Optional

from email_validator import EmailNotValidError, validate_email
from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app import crud
from app.config import settings
from app.database import get_db
from app.deps import get_current_user_web, get_current_user_web_optional, require_trainer_web
from app.models import (
    BodyPart, Difficulty, Exercise, Log, MembershipStatus, PasswordChangeMethod, Role, User,
)
from app.security import hash_password, token_for_user, verify_password

router = APIRouter(tags=["web"], include_in_schema=False)

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))

COOKIE_NAME = "access_token"
COOKIE_MAX_AGE = settings.access_token_expire_minutes * 60


def _set_auth_cookie(response: RedirectResponse, user: User) -> None:
    token = token_for_user(user)
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
    )


def _normalize_email(raw: str) -> Optional[str]:
    """Validate + normalize an email; None if it isn't a real address.

    Rejects things the browser's type=email lets through, e.g. "ada@12"
    (no proper domain). check_deliverability=False keeps it offline/fast --
    we validate the format, not whether the mailbox exists.
    """
    try:
        return validate_email(raw.strip(), check_deliverability=False).normalized
    except EmailNotValidError:
        return None


def _safe_next(next_url: Optional[str]) -> str:
    # Only allow same-site relative redirects -- never an attacker-supplied
    # absolute URL like //evil.com.
    if next_url and next_url.startswith("/") and not next_url.startswith("//"):
        return next_url
    return "/dashboard"


# --- Public pages -------------------------------------------------------


@router.get("/", include_in_schema=False)
def root(user: Optional[User] = Depends(get_current_user_web_optional)):
    return RedirectResponse(url="/dashboard" if user else "/login", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/dashboard", error: Optional[str] = None, role: str = "member"):
    if role not in ("trainer", "member"):
        role = "member"
    return templates.TemplateResponse(
        "login.html", {"request": request, "next": next, "error": error, "role": role}
    )


# Simple in-process login throttle. Deliberately not Redis: one web process,
# and the goal is to blunt password guessing, not to build a security product.
LOGIN_MAX_FAILURES = 5
LOGIN_LOCKOUT_SECONDS = 120
_login_failures: dict = {}


def _login_locked_for(key: str) -> int:
    """Seconds remaining in a lockout, or 0 if not locked."""
    count, first_at = _login_failures.get(key, (0, 0.0))
    if count < LOGIN_MAX_FAILURES:
        return 0
    remaining = int(LOGIN_LOCKOUT_SECONDS - (time.monotonic() - first_at))
    if remaining <= 0:
        _login_failures.pop(key, None)
        return 0
    return remaining


def _record_login_failure(key: str) -> None:
    count, first_at = _login_failures.get(key, (0, 0.0))
    if count == 0 or (time.monotonic() - first_at) > LOGIN_LOCKOUT_SECONDS:
        _login_failures[key] = (1, time.monotonic())
    else:
        _login_failures[key] = (count + 1, first_at)


@router.post("/login")
def login_submit(
    email: str = Form(...),
    password: str = Form(...),
    role: str = Form("member"),
    next: str = Form("/dashboard"),
    db: Session = Depends(get_db),
):
    if role not in ("trainer", "member"):
        role = "member"
    normalized = _normalize_email(email)
    key = (normalized or email.strip().lower())

    def bounce(msg: str, r: str = role):
        return RedirectResponse(
            url=f"/login?error={quote_plus(msg)}&role={r}&next={_safe_next(next)}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    locked = _login_locked_for(key)
    if locked:
        return bounce(f"Too many attempts. Try again in {locked} seconds.")

    user = db.scalar(select(User).where(User.email == normalized)) if normalized else None
    if not user or not verify_password(password, user.password_hash):
        _record_login_failure(key)
        return bounce("Incorrect email or password")

    # Credentials are good but the role toggle is wrong. Previously this looked
    # identical to a bad password, which made a simple mis-tap feel like a
    # lockout. Point at the toggle without confirming the account exists.
    if user.role.value != role:
        _record_login_failure(key)
        return bounce("Login failed — check that you've selected the correct role above.", role)

    _login_failures.pop(key, None)
    response = RedirectResponse(url=_safe_next(next), status_code=status.HTTP_303_SEE_OTHER)
    _set_auth_cookie(response, user)
    return response


def _registration_open(db: Session) -> bool:
    """Self-registration is closed on a trainer-first product.

    The one exception is bootstrapping: an empty database must let the very
    first account (the trainer) be created. After that, only the trainer adds
    members -- unless ALLOW_OPEN_REGISTRATION is explicitly turned on.
    """
    if settings.allow_open_registration:
        return True
    return db.scalar(select(func.count()).select_from(User)) == 0


@router.get("/register", response_class=HTMLResponse)
def register_page(request: Request, error: Optional[str] = None, db: Session = Depends(get_db)):
    if not _registration_open(db):
        return templates.TemplateResponse(
            "registration_closed.html", {"request": request}, status_code=status.HTTP_403_FORBIDDEN
        )
    is_first_user = db.scalar(select(func.count()).select_from(User)) == 0
    return templates.TemplateResponse(
        "register.html", {"request": request, "error": error, "is_first_user": is_first_user}
    )


@router.post("/register")
def register_submit(
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    if not _registration_open(db):
        # Enforced server-side: hiding the form is not enough.
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Registration is closed")
    email = _normalize_email(email)
    if not email:
        return RedirectResponse(
            url="/register?error=Please+enter+a+valid+email+address", status_code=status.HTTP_303_SEE_OTHER
        )
    if db.scalar(select(User).where(User.email == email)):
        return RedirectResponse(
            url="/register?error=Email+already+registered", status_code=status.HTTP_303_SEE_OTHER
        )
    # Bootstrap: the very first account becomes the trainer, everyone after is a member.
    is_first_user = db.scalar(select(func.count()).select_from(User)) == 0
    user = User(
        name=name,
        email=email,
        password_hash=hash_password(password),
        role=Role.trainer if is_first_user else Role.member,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    response = RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    _set_auth_cookie(response, user)
    return response


@router.post("/logout")
def logout():
    response = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(COOKIE_NAME)
    return response


# --- Authenticated pages ------------------------------------------------


def _opt_date(value: Optional[str]) -> Optional[date]:
    value = (value or "").strip()
    return date.fromisoformat(value) if value else None


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(
    request: Request,
    user_id: Optional[int] = None,
    exercise_id: Optional[int] = None,
    start_date: str = "",
    end_date: str = "",
    sort: str = "default",
    view: str = "all",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_web),
):
    is_trainer = current_user.role == Role.trainer

    # Trainer landing = member roster (quick scan). Drilling into a member
    # (?user_id=) switches to that member's full log view.
    if is_trainer and user_id is None:
        roster = crud.member_roster(db, sort=sort)
        new_members = [r for r in roster if r["is_new"]]
        if view == "new":
            shown = new_members
        else:
            shown = roster
        return templates.TemplateResponse(
            "dashboard.html",
            {
                "request": request,
                "current_user": current_user,
                "is_trainer": True,
                "show_roster": True,
                "roster": shown,
                "summary": crud.roster_summary(roster),
                "sort": sort if sort in crud.ROSTER_SORTS else "default",
                # First-90-days cohort: where churn actually happens.
                "view": "new" if view == "new" else "all",
                "new_count": len(new_members),
                "new_attention": sum(1 for r in new_members if r["cohort"].get("needs_attention")),
                "new_member_days": crud.NEW_MEMBER_DAYS,
            },
        )

    try:
        start, end = _opt_date(start_date), _opt_date(end_date)
    except ValueError:
        start, end = None, None
    logs = crud.list_logs(
        db,
        current_user,
        user_id=user_id,
        exercise_id=exercise_id,
        start_date=start,
        end_date=end,
    )
    members = db.scalars(select(User).order_by(User.name)).all() if is_trainer else []
    exercises = db.scalars(select(Exercise).order_by(Exercise.name)).all()
    # Whose data are we viewing? (self for a member, the selected member for a trainer)
    viewed_user_id = user_id if (is_trainer and user_id) else current_user.id
    viewed_member = db.get(User, viewed_user_id) if (is_trainer and user_id) else None
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "current_user": current_user,
            "is_trainer": is_trainer,
            "show_roster": False,
            "logs": logs,
            "members": members,
            "exercises": exercises,
            "viewed_member": viewed_member,
            "routine": crud.routine_grouped(db, viewed_user_id),
            "stalled_ids": crud.stalled_exercise_ids(db, viewed_user_id),
            "selected_user_id": user_id,
            "selected_exercise_id": exercise_id,
            "start_date": start_date,
            "end_date": end_date,
            # Membership: trainer sees full history + actions for the member
            # they opened; a member sees only their own, read-only.
            "membership": crud.current_membership(db, viewed_user_id),
            "membership_history": crud.membership_history(db, viewed_user_id),
            "today": date.today(),
            "expiry_soon_days": crud.EXPIRY_SOON_DAYS,
            # Attendance is read-only everywhere except the trainer's Today screen.
            "sessions_this_month": crud.attendance_this_month(db, viewed_user_id),
            "attendance_streak": crud.attendance_streak(db, viewed_user_id),
            # Talking points are notes for the trainer to speak from -- never
            # rendered to the member as a verdict on themselves.
            "talking_points": crud.talking_points(db, viewed_user_id) if viewed_member else [],
            "cohort": crud.cohort_stats(db, viewed_member) if viewed_member else None,
            # Overload targets: trainer sets them, member sees their own.
            "targets": crud.targets_for(db, viewed_user_id),
            # Body weight: trend and rate only, never a percentage of a goal.
            "weight_trend": crud.body_weight_trend(db, viewed_user_id),
        },
    )


@router.get("/logs/new", response_class=HTMLResponse)
def new_log_page(
    request: Request,
    error: Optional[str] = None,
    saved: Optional[str] = None,
    weekday: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_web),
):
    exercises = db.scalars(select(Exercise).order_by(Exercise.name)).all()

    # Members get the fast, tap-first logging screen, driven by their weekly split.
    if current_user.role != Role.trainer:
        today_wd = date.today().weekday()  # 0 = Monday
        selected_wd = weekday if (weekday is not None and 0 <= weekday <= 6) else today_wd
        split = crud.get_split(db, current_user.id)

        if not split:
            # No split set up yet -> fall back to the whole routine (nothing breaks),
            # with a nudge to build a split for day-focused logging.
            routine = crud.routine_grouped(db, current_user.id)
            day_parts = []
            no_split = True
        else:
            day_parts = crud.body_parts_for_day(db, current_user.id, selected_wd)
            routine = crud.routine_for_parts(db, current_user.id, day_parts)
            no_split = False

        # Day-picker options (option B): every weekday, with its scheduled parts.
        day_picker = [
            {
                "index": i,
                "name": crud.WEEKDAY_NAMES[i],
                "short": crud.WEEKDAY_NAMES[i][:3],
                "parts": [bp.value for bp in split.get(i, [])],
                "is_today": i == today_wd,
                "is_selected": i == selected_wd,
            }
            for i in range(7)
        ]
        last = crud.last_sets_by_exercise(db, current_user.id)
        return templates.TemplateResponse(
            "quick_log.html",
            {
                "request": request,
                "current_user": current_user,
                "routine": routine,          # {body_part: [Exercise]} for the selected day
                "last": last,
                "today": date.today().isoformat(),
                "error": error,
                "saved": saved,
                "no_split": no_split,
                "day_parts": [bp.value for bp in day_parts],
                "day_name": crud.WEEKDAY_NAMES[selected_wd],
                "selected_weekday": selected_wd,
                "day_picker": day_picker,
                "trainer_assigned": crud.trainer_assigned_exercise_ids(db, current_user.id),
                "demo_link": crud.demo_link,
            },
        )

    # Trainers get the full form (they log on behalf of a member, all fields).
    members = db.scalars(select(User).order_by(User.name)).all()
    return templates.TemplateResponse(
        "log_form.html",
        {
            "request": request,
            "current_user": current_user,
            "exercises": exercises,
            "members": members,
            "is_trainer": True,
            "today": date.today().isoformat(),
            "error": error,
        },
    )


def _opt_float(value: str) -> Optional[float]:
    value = (value or "").strip()
    return float(value) if value else None


def _opt_int(value: str) -> Optional[int]:
    value = (value or "").strip()
    return int(value) if value else None


@router.post("/logs/new")
def create_log_submit(
    exercise_id: int = Form(...),
    log_date: str = Form(...),
    weight: str = Form(""),
    reps: str = Form(""),
    sets: str = Form(""),
    next_action: str = Form(""),
    notes: str = Form(""),
    feeling: str = Form(""),
    user_id: str = Form(""),
    weekday: str = Form(""),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_web),
):
    try:
        crud.create_log(
            db,
            current_user,
            exercise_id=exercise_id,
            log_date=date.fromisoformat(log_date),
            weight=_opt_float(weight),
            reps=_opt_int(reps),
            sets=_opt_int(sets),
            next_action=next_action.strip() or None,
            notes=notes.strip() or None,
            feeling=feeling.strip() or None,
            user_id=_opt_int(user_id),
        )
    except (crud.Forbidden, crud.NotFound, ValueError) as exc:
        return RedirectResponse(
            url=f"/logs/new?error={str(exc).replace(' ', '+')}", status_code=status.HTTP_303_SEE_OTHER
        )
    if current_user.role == Role.trainer:
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    # Members stay on the same day's fast screen to log the next set.
    wd = _opt_int(weekday)
    dest = f"/logs/new?saved=1&weekday={wd}" if wd is not None else "/logs/new?saved=1"
    return RedirectResponse(url=dest, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/logs/{log_id}/edit", response_class=HTMLResponse)
def edit_log_page(
    request: Request,
    log_id: int,
    error: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_web),
):
    try:
        log = crud.get_owned_log(db, log_id, current_user)
    except crud.NotFound:
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    exercises = db.scalars(select(Exercise).order_by(Exercise.name)).all()
    return templates.TemplateResponse(
        "log_form.html",
        {
            "request": request,
            "current_user": current_user,
            "exercises": exercises,
            "members": [],  # owner isn't reassignable on edit
            "is_trainer": current_user.role == Role.trainer,
            "log": log,
            "today": log.date.isoformat(),
            "error": error,
        },
    )


@router.post("/logs/{log_id}/edit")
def edit_log_submit(
    log_id: int,
    exercise_id: int = Form(...),
    log_date: str = Form(...),
    weight: str = Form(""),
    reps: str = Form(""),
    sets: str = Form(""),
    next_action: str = Form(""),
    notes: str = Form(""),
    feeling: str = Form(""),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_web),
):
    try:
        log = crud.get_owned_log(db, log_id, current_user)
    except crud.NotFound:
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    if not db.get(Exercise, exercise_id):
        return RedirectResponse(
            url=f"/logs/{log_id}/edit?error=Exercise+not+found", status_code=status.HTTP_303_SEE_OTHER
        )
    try:
        crud.update_log(
            db,
            log,
            exercise_id=exercise_id,
            date=date.fromisoformat(log_date),
            weight=_opt_float(weight),
            reps=_opt_int(reps),
            sets=_opt_int(sets),
            next_action=next_action.strip() or None,
            notes=notes.strip() or None,
            feeling=feeling.strip() or None,
        )
    except ValueError:
        return RedirectResponse(
            url=f"/logs/{log_id}/edit?error=Invalid+value", status_code=status.HTTP_303_SEE_OTHER
        )
    return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/logs/{log_id}/delete")
def delete_log_submit(
    log_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_web),
):
    try:
        log = crud.get_owned_log(db, log_id, current_user)
        crud.delete_log(db, log)
    except crud.NotFound:
        pass  # already gone / not theirs -- land back on the dashboard either way
    return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/exercises", response_class=HTMLResponse)
def exercises_page(
    request: Request,
    error: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_trainer_web),
):
    exercises = db.scalars(
        select(Exercise).order_by(Exercise.body_part, Exercise.name)
    ).all()
    return templates.TemplateResponse(
        "exercises.html",
        {
            "request": request,
            "current_user": current_user,
            "exercises": exercises,
            "body_parts": list(BodyPart),
            "difficulties": list(Difficulty),
            "error": error,
        },
    )


@router.post("/exercises")
def create_exercise_submit(
    name: str = Form(...),
    body_part: str = Form(""),
    difficulty: str = Form(""),
    equipment: str = Form(""),
    instructions: str = Form(""),
    demo_url: str = Form(""),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_trainer_web),
):
    name = name.strip()
    if not name:
        return RedirectResponse(url="/exercises?error=Name+required", status_code=status.HTTP_303_SEE_OTHER)
    if db.scalar(select(Exercise).where(Exercise.name == name)):
        return RedirectResponse(
            url="/exercises?error=Exercise+already+exists", status_code=status.HTTP_303_SEE_OTHER
        )
    bp = next((b for b in BodyPart if b.value == body_part), None)
    diff = next((d for d in Difficulty if d.value == difficulty), None)
    db.add(
        Exercise(
            name=name,
            body_part=bp,
            difficulty=diff,
            equipment=equipment.strip() or None,
            instructions=instructions.strip() or None,
            # Optional: a specific demo video. Left empty, the member's log
            # screen falls back to a YouTube search for the exercise name.
            demo_url=demo_url.strip() or None,
        )
    )
    db.commit()
    return RedirectResponse(url="/exercises", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/exercises/{exercise_id}/delete")
def delete_exercise_submit(
    exercise_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_trainer_web),
):
    exercise = db.get(Exercise, exercise_id)
    if exercise:
        db.delete(exercise)
        db.commit()
    return RedirectResponse(url="/exercises", status_code=status.HTTP_303_SEE_OTHER)


# --- Exercise Library + Member Routine ----------------------------------


def _target_suffix(target_id: int, current_user: User, sep: str = "&") -> str:
    """Keep ?user_id= in redirects while a trainer edits someone else's data."""
    return f"{sep}user_id={target_id}" if target_id != current_user.id else ""


@router.get("/library", response_class=HTMLResponse)
def library_page(
    request: Request,
    part: Optional[str] = None,
    user_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_web),
):
    # Only a trainer may target another user; a member falls back to themselves.
    target_id = crud.resolve_target_user(current_user, user_id)
    target_user = db.get(User, target_id) or current_user
    library = crud.library_grouped(db)  # {BodyPart: [Exercise]}
    body_parts = [bp for bp in crud.BODY_PARTS if bp in library]
    # active tab: requested body_part if valid, else the first with exercises
    active = None
    if part:
        active = next((bp for bp in body_parts if bp.value == part), None)
    if active is None and body_parts:
        active = body_parts[0]
    return templates.TemplateResponse(
        "library.html",
        {
            "request": request,
            "current_user": current_user,
            "library": library,
            "body_parts": body_parts,
            "active": active,
            "routine_ids": crud.routine_exercise_ids(db, target_id),
            "target_user": target_user,
            "editing_other": target_id != current_user.id,
            "demo_link": crud.demo_link,
        },
    )


@router.post("/library/{body_part}")
def library_set_routine(
    body_part: str,
    exercise_ids: list[str] = Form(default=[]),
    user_id: str = Form(""),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_web),
):
    target_id = crud.resolve_target_user(current_user, _opt_int(user_id))
    bp = next((b for b in BodyPart if b.value == body_part), None)
    if bp is None:
        return RedirectResponse(url="/library", status_code=status.HTTP_303_SEE_OTHER)
    # The checked boxes become this member's routine for that body_part
    # (adds new, removes unchecked -- logs are untouched).
    ids = [int(x) for x in exercise_ids if x.strip().isdigit()]
    crud.set_routine_for_body_part(db, target_id, bp, ids, assigned_by=current_user.role)
    dest = f"/library?part={bp.value}{_target_suffix(target_id, current_user)}"
    return RedirectResponse(url=dest, status_code=status.HTTP_303_SEE_OTHER)


@router.post("/library/remove/{exercise_id}")
def library_remove(
    exercise_id: int,
    part: str = Form(""),
    user_id: str = Form(""),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_web),
):
    target_id = crud.resolve_target_user(current_user, _opt_int(user_id))
    crud.remove_from_routine(db, target_id, exercise_id)  # routine only, keeps logs
    dest = f"/library?part={part}" if part else "/library"
    dest += _target_suffix(target_id, current_user, "&" if "?" in dest else "?")
    return RedirectResponse(url=dest, status_code=status.HTTP_303_SEE_OTHER)


# --- Weekly split (which body parts on which weekday) ------------------


@router.get("/split", response_class=HTMLResponse)
def split_page(
    request: Request,
    saved: Optional[str] = None,
    user_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_web),
):
    target_id = crud.resolve_target_user(current_user, user_id)
    target_user = db.get(User, target_id) or current_user
    split = crud.get_split(db, target_id)
    days = [
        {"index": i, "name": crud.WEEKDAY_NAMES[i], "parts": {bp.value for bp in split.get(i, [])}}
        for i in range(7)
    ]
    return templates.TemplateResponse(
        "split.html",
        {
            "request": request,
            "current_user": current_user,
            "days": days,
            "body_parts": [bp.value for bp in BodyPart],
            "saved": saved,
            "target_user": target_user,
            "editing_other": target_id != current_user.id,
        },
    )


@router.post("/split")
def split_save(
    d0: list[str] = Form(default=[]),
    d1: list[str] = Form(default=[]),
    d2: list[str] = Form(default=[]),
    d3: list[str] = Form(default=[]),
    d4: list[str] = Form(default=[]),
    d5: list[str] = Form(default=[]),
    d6: list[str] = Form(default=[]),
    user_id: str = Form(""),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_web),
):
    target_id = crud.resolve_target_user(current_user, _opt_int(user_id))
    # Each weekday posts a multi-checkbox field d0..d6 (list of body_part values).
    for weekday, values in enumerate([d0, d1, d2, d3, d4, d5, d6]):
        parts = [b for b in BodyPart if b.value in set(values)]
        crud.set_split_for_day(db, target_id, weekday, parts)
    dest = f"/split?saved=1{_target_suffix(target_id, current_user)}"
    return RedirectResponse(url=dest, status_code=status.HTTP_303_SEE_OTHER)


# --- Danger zone: LOCAL-ONLY demo reset --------------------------------
# Two independent guards, both required:
#   1. Local only -- refuses to run unless the DB is the local SQLite file, so
#      it is permanently dead on Render/Postgres no matter what env vars say.
#   2. Token -- RESET_TOKEN must be set and match.
# Wipes accounts/logs/routines/splits but keeps the exercise library.

_RESET_TABLES = ("plan_items", "plan_days", "split_days", "member_routines", "logs", "users")


def _reset_enabled() -> bool:
    """Never enabled against a non-SQLite (i.e. deployed) database."""
    return settings.is_sqlite and bool(os.environ.get("RESET_TOKEN", "").strip())


def _reset_token() -> str:
    return os.environ.get("RESET_TOKEN", "").strip()


@router.get("/danger/reset", response_class=HTMLResponse)
def reset_page(request: Request, token: str = ""):
    if not _reset_enabled():
        raise HTTPException(status_code=404)  # off on deployed/Postgres, or no token set
    expected = _reset_token()
    if token != expected:
        return HTMLResponse("<h3>Invalid or missing token.</h3>", status_code=403)
    return templates.TemplateResponse("reset.html", {"request": request, "token": token})


@router.post("/danger/reset", response_class=HTMLResponse)
def reset_do(
    token: str = Form(""),
    confirm: str = Form(""),
    db: Session = Depends(get_db),
):
    if not _reset_enabled():
        raise HTTPException(status_code=404)
    if token != _reset_token():
        return HTMLResponse("<h3>Invalid or missing token.</h3>", status_code=403)
    if confirm.strip().upper() != "RESET":
        return RedirectResponse(url=f"/danger/reset?token={token}", status_code=status.HTTP_303_SEE_OTHER)
    # Delete children-first so it works on both Postgres and SQLite. Exercises
    # (the library) are intentionally left intact.
    for table in _RESET_TABLES:
        db.execute(text(f"DELETE FROM {table}"))
    db.commit()
    return HTMLResponse(
        "<div style='font-family:sans-serif;max-width:520px;margin:60px auto;text-align:center'>"
        "<h2>✅ Database reset</h2>"
        "<p>All accounts, logs, routines and splits were cleared. The exercise library is kept.</p>"
        "<p><a href='/register'>Register now →</a> The first account becomes the trainer.</p>"
        "</div>"
    )


# --- Account & password recovery ----------------------------------------


@router.post("/members/{user_id}/password")
def member_password_reset(
    user_id: int,
    new_password: str = Form(...),
    db: Session = Depends(get_db),
    trainer: User = Depends(require_trainer_web),
):
    """Trainer resets a member's password. Covers ~95% of real lockouts."""
    dest = f"/members?reset_error="
    target = db.get(User, user_id)
    # A trainer resets members, not themselves and not another trainer.
    if target is None or target.role != Role.member:
        return RedirectResponse(url="/members?reset_error=Member+not+found",
                                status_code=status.HTTP_303_SEE_OTHER)
    try:
        crud.set_password(
            db, target, new_password,
            method=PasswordChangeMethod.trainer, changed_by=trainer,
        )
    except ValueError as exc:
        return RedirectResponse(url=dest + str(exc).replace(" ", "+"),
                                status_code=status.HTTP_303_SEE_OTHER)
    # Name the member back, never the password.
    return RedirectResponse(
        url=f"/members?reset_ok={quote_plus(target.name)}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("/account/password", response_class=HTMLResponse)
def account_password_page(
    request: Request,
    error: Optional[str] = None,
    current_user: User = Depends(get_current_user_web),
):
    return templates.TemplateResponse(
        "account_password.html",
        {"request": request, "current_user": current_user, "error": error},
    )


@router.post("/account/password")
def account_password_submit(
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_web),
):
    """Change your own password. Requires the current one; this is not recovery."""
    def fail(msg: str):
        return RedirectResponse(
            url=f"/account/password?error={quote_plus(msg)}", status_code=status.HTTP_303_SEE_OTHER
        )

    # Deliberately generic: never reveal which part was wrong.
    if not verify_password(current_password, current_user.password_hash):
        return fail("Could not change password. Check your current password and try again.")
    if new_password != confirm_password:
        return fail("The new passwords did not match.")
    try:
        crud.set_password(
            db, current_user, new_password,
            method=PasswordChangeMethod.self_service, changed_by=current_user,
        )
    except ValueError as exc:
        return fail(str(exc))

    # The version bump already invalidated this session's token; clear the
    # cookie too so the browser isn't left holding a dead one.
    response = RedirectResponse(
        url="/login?error=" + quote_plus("Password changed. Please sign in again."),
        status_code=status.HTTP_303_SEE_OTHER,
    )
    response.delete_cookie(COOKIE_NAME)
    return response


@router.get("/account", response_class=HTMLResponse)
def account_page(
    request: Request,
    saved: Optional[str] = None,
    current_user: User = Depends(get_current_user_web),
):
    return templates.TemplateResponse(
        "account.html",
        {"request": request, "current_user": current_user, "saved": saved},
    )


@router.post("/account")
def account_save(
    recovery_email: str = Form(""),
    recovery_phone: str = Form(""),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_web),
):
    """Recovery contacts. Not used by any automated flow in v1 (see Phase 0.5)."""
    email = recovery_email.strip()
    if email:
        normalized = _normalize_email(email)
        if not normalized:
            return RedirectResponse(
                url="/account?saved=Please+enter+a+valid+recovery+email",
                status_code=status.HTTP_303_SEE_OTHER,
            )
        current_user.recovery_email = normalized
    else:
        current_user.recovery_email = None
    current_user.recovery_phone = recovery_phone.strip() or None
    db.commit()
    return RedirectResponse(url="/account?saved=1", status_code=status.HTTP_303_SEE_OTHER)


# --- Body weight (trainer records at the gym scale) ---------------------


@router.post("/members/{user_id}/weight")
def body_weight_add(
    user_id: int,
    weight_kg: str = Form(...),
    on: str = Form(""),
    db: Session = Depends(get_db),
    _trainer: User = Depends(require_trainer_web),
):
    dest = f"/dashboard?user_id={user_id}"
    try:
        crud.record_body_weight(
            db,
            user_id,
            on=_opt_date(on) or date.today(),
            weight_kg=float(weight_kg),
            recorded_by=Role.trainer,
        )
    except (crud.NotFound, ValueError) as exc:
        msg = str(exc).replace(" ", "+") or "Invalid+values"
        return RedirectResponse(url=f"{dest}&error={msg}", status_code=status.HTTP_303_SEE_OTHER)
    return RedirectResponse(url=dest, status_code=status.HTTP_303_SEE_OTHER)


@router.post("/weight/{entry_id}/delete")
def body_weight_delete(
    entry_id: int,
    db: Session = Depends(get_db),
    _trainer: User = Depends(require_trainer_web),
):
    user_id = crud.delete_body_weight(db, entry_id)
    dest = f"/dashboard?user_id={user_id}" if user_id else "/dashboard"
    return RedirectResponse(url=dest, status_code=status.HTTP_303_SEE_OTHER)


# --- Progressive-overload targets (trainer sets; members read only) -----


@router.post("/members/{user_id}/targets")
def target_add(
    user_id: int,
    exercise_id: int = Form(...),
    target_weight: str = Form(...),
    target_reps: str = Form(""),
    target_date: str = Form(...),
    db: Session = Depends(get_db),
    _trainer: User = Depends(require_trainer_web),
):
    dest = f"/dashboard?user_id={user_id}"
    try:
        crud.create_target(
            db,
            user_id,
            exercise_id=exercise_id,
            target_weight=float(target_weight),
            target_reps=_opt_int(target_reps),
            target_date=date.fromisoformat(target_date),
        )
    except (crud.NotFound, ValueError) as exc:
        msg = str(exc).replace(" ", "+") or "Invalid+values"
        return RedirectResponse(url=f"{dest}&error={msg}", status_code=status.HTTP_303_SEE_OTHER)
    return RedirectResponse(url=dest, status_code=status.HTTP_303_SEE_OTHER)


@router.post("/targets/{target_id}/delete")
def target_delete(
    target_id: int,
    db: Session = Depends(get_db),
    _trainer: User = Depends(require_trainer_web),
):
    user_id = crud.delete_target(db, target_id)
    dest = f"/dashboard?user_id={user_id}" if user_id else "/dashboard"
    return RedirectResponse(url=dest, status_code=status.HTTP_303_SEE_OTHER)


# --- Attendance (trainer marks; members read only) ----------------------


@router.get("/today", response_class=HTMLResponse)
def today_page(
    request: Request,
    on: str = "",
    db: Session = Depends(get_db),
    current_user: User = Depends(require_trainer_web),
):
    try:
        day = _opt_date(on) or date.today()
    except ValueError:
        day = date.today()
    members = db.scalars(select(User).where(User.role == Role.member).order_by(User.name)).all()
    present = crud.attended_on(db, day)
    counts = crud.attendance_counts_this_month(db)
    rows = [
        {
            "user": m,
            "present": m.id in present,
            "sessions_this_month": counts.get(m.id, 0),
        }
        for m in members
    ]
    # Present members drop to the bottom so the "still to mark" list stays short.
    rows.sort(key=lambda r: (r["present"], r["user"].name.lower()))
    return templates.TemplateResponse(
        "today.html",
        {
            "request": request,
            "current_user": current_user,
            "rows": rows,
            "day": day,
            "is_today": day == date.today(),
            "present_count": len(present),
            "today_date": date.today(),
            "one_day": timedelta(days=1),
        },
    )


@router.post("/attendance/{user_id}/toggle")
def attendance_toggle(
    user_id: int,
    on: str = Form(""),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_trainer_web),
):
    try:
        day = _opt_date(on) or date.today()
    except ValueError:
        day = date.today()
    target = db.get(User, user_id)
    if target is not None and target.role == Role.member:
        # Idempotent by (user_id, date): a repeat tap toggles rather than duplicating.
        if not crud.mark_attendance(db, user_id, day, current_user.role):
            crud.unmark_attendance(db, user_id, day)
    suffix = "" if day == date.today() else f"?on={day.isoformat()}"
    return RedirectResponse(url=f"/today{suffix}", status_code=status.HTTP_303_SEE_OTHER)


# --- Membership & dues (trainer only) -----------------------------------


@router.post("/members/{user_id}/membership")
def membership_add(
    user_id: int,
    plan_start: str = Form(...),
    duration_months: str = Form(...),
    amount: str = Form(""),
    # aliased so the form field can be "status" without shadowing fastapi.status
    pay_status: str = Form("pending", alias="status"),
    notes: str = Form(""),
    db: Session = Depends(get_db),
    _trainer: User = Depends(require_trainer_web),
):
    dest = f"/dashboard?user_id={user_id}"
    target = db.get(User, user_id)
    if target is None or target.role != Role.member:
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    try:
        crud.create_membership(
            db,
            user_id,
            plan_start=date.fromisoformat(plan_start),
            duration_months=int(duration_months),
            amount=Decimal(amount.strip() or "0"),
            status=(
                MembershipStatus.paid if pay_status == "paid" else MembershipStatus.pending
            ),
            notes=notes.strip() or None,
        )
    except (crud.NotFound, ValueError, InvalidOperation) as exc:
        msg = str(exc).replace(" ", "+") or "Invalid+values"
        return RedirectResponse(url=f"{dest}&error={msg}", status_code=status.HTTP_303_SEE_OTHER)
    return RedirectResponse(url=dest, status_code=status.HTTP_303_SEE_OTHER)


@router.post("/membership/{membership_id}/paid")
def membership_mark_paid(
    membership_id: int,
    db: Session = Depends(get_db),
    _trainer: User = Depends(require_trainer_web),
):
    membership = crud.mark_membership_paid(db, membership_id)
    dest = f"/dashboard?user_id={membership.user_id}" if membership else "/dashboard"
    return RedirectResponse(url=dest, status_code=status.HTTP_303_SEE_OTHER)


@router.post("/membership/{membership_id}/delete")
def membership_delete(
    membership_id: int,
    db: Session = Depends(get_db),
    _trainer: User = Depends(require_trainer_web),
):
    user_id = crud.delete_membership(db, membership_id)
    dest = f"/dashboard?user_id={user_id}" if user_id else "/dashboard"
    return RedirectResponse(url=dest, status_code=status.HTTP_303_SEE_OTHER)


# --- Members (trainer only) ---------------------------------------------


@router.get("/members", response_class=HTMLResponse)
def members_page(
    request: Request,
    error: Optional[str] = None,
    reset_ok: Optional[str] = None,
    reset_error: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_trainer_web),
):
    # log count + most recent log date per user, in one grouped query
    counts = dict(
        db.execute(select(Log.user_id, func.count(Log.id)).group_by(Log.user_id)).all()
    )
    last_dates = dict(
        db.execute(select(Log.user_id, func.max(Log.date)).group_by(Log.user_id)).all()
    )
    users = db.scalars(select(User).order_by(User.name)).all()
    rows = [
        {
            "user": u,
            "log_count": counts.get(u.id, 0),
            "last_date": last_dates.get(u.id),
        }
        for u in users
    ]
    return templates.TemplateResponse(
        "members.html",
        {"request": request, "current_user": current_user, "rows": rows, "error": error, "reset_ok": reset_ok, "reset_error": reset_error},
    )


@router.post("/members")
def create_member_submit(
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_trainer_web),
):
    email = _normalize_email(email)
    if not email:
        return RedirectResponse(
            url="/members?error=Please+enter+a+valid+email+address", status_code=status.HTTP_303_SEE_OTHER
        )
    if db.scalar(select(User).where(User.email == email)):
        return RedirectResponse(
            url="/members?error=Email+already+registered", status_code=status.HTTP_303_SEE_OTHER
        )
    db.add(
        User(name=name, email=email, password_hash=hash_password(password), role=Role.member)
    )
    db.commit()
    return RedirectResponse(url="/members", status_code=status.HTTP_303_SEE_OTHER)


# --- Weekly planner -----------------------------------------------------

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _planner_redirect(target_user_id: int, current_user: User) -> str:
    # Trainers editing a member's planner keep the ?user_id in the URL.
    if current_user.role == Role.trainer and target_user_id != current_user.id:
        return f"/planner?user_id={target_user_id}"
    return "/planner"


@router.get("/planner", response_class=HTMLResponse)
def planner_page(
    request: Request,
    user_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_web),
):
    is_trainer = current_user.role == Role.trainer
    target_user_id = crud.resolve_target_user(current_user, user_id)
    target_user = db.get(User, target_user_id) or current_user
    week = crud.get_week(db, target_user_id)
    days = [
        {"weekday": i, "name": WEEKDAYS[i], "day": week.get(i)}
        for i in range(7)
    ]
    exercises = db.scalars(select(Exercise).order_by(Exercise.name)).all()
    members = db.scalars(select(User).order_by(User.name)).all() if is_trainer else []
    return templates.TemplateResponse(
        "planner.html",
        {
            "request": request,
            "current_user": current_user,
            "is_trainer": is_trainer,
            "target_user": target_user,
            "editing_other": target_user_id != current_user.id,
            "days": days,
            "exercises": exercises,
            "members": members,
            "selected_user_id": target_user_id if is_trainer else None,
        },
    )


@router.post("/planner/{weekday}/focus")
def planner_set_focus(
    weekday: int,
    focus: str = Form(""),
    user_id: str = Form(""),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_web),
):
    target = crud.resolve_target_user(current_user, _opt_int(user_id))
    if 0 <= weekday <= 6:
        crud.set_focus(db, target, weekday, focus.strip() or None)
    return RedirectResponse(url=_planner_redirect(target, current_user), status_code=status.HTTP_303_SEE_OTHER)


@router.post("/planner/{weekday}/item")
def planner_add_item(
    weekday: int,
    exercise_id: int = Form(...),
    target_sets: str = Form(""),
    target_reps: str = Form(""),
    target_weight: str = Form(""),
    user_id: str = Form(""),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_web),
):
    target = crud.resolve_target_user(current_user, _opt_int(user_id))
    if 0 <= weekday <= 6:
        try:
            crud.add_plan_item(
                db,
                target,
                weekday,
                exercise_id=exercise_id,
                target_sets=_opt_int(target_sets),
                target_reps=_opt_int(target_reps),
                target_weight=_opt_float(target_weight),
            )
        except (crud.NotFound, ValueError):
            pass
    return RedirectResponse(url=_planner_redirect(target, current_user), status_code=status.HTTP_303_SEE_OTHER)


@router.post("/planner/item/{item_id}/delete")
def planner_delete_item(
    item_id: int,
    user_id: str = Form(""),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_web),
):
    target = crud.resolve_target_user(current_user, _opt_int(user_id))
    crud.delete_plan_item(db, current_user, item_id, target)
    return RedirectResponse(url=_planner_redirect(target, current_user), status_code=status.HTTP_303_SEE_OTHER)


# --- Progress chart -----------------------------------------------------


def _build_chart(series: list[tuple[date, float]], width: int = 640, height: int = 260, pad: int = 36) -> dict:
    """Turn (date, weight) points into SVG geometry the template can draw."""
    if not series:
        return {"points": [], "polyline": "", "y_ticks": [], "width": width, "height": height}
    weights = [w for _, w in series]
    wmin, wmax = min(weights), max(weights)
    if wmax == wmin:  # flat line -> pad the range so it sits mid-chart
        wmax, wmin = wmax + 1, wmin - 1
    span = wmax - wmin
    inner_w, inner_h = width - 2 * pad, height - 2 * pad
    n = len(series)

    def x(i: int) -> float:
        return pad if n == 1 else pad + inner_w * i / (n - 1)

    def y(w: float) -> float:
        return pad + inner_h * (1 - (w - wmin) / span)

    points = [
        {"x": round(x(i), 1), "y": round(y(w), 1), "date": d.isoformat(), "weight": w}
        for i, (d, w) in enumerate(series)
    ]
    polyline = " ".join(f"{p['x']},{p['y']}" for p in points)
    y_ticks = [
        {"y": round(y(wmin + span * frac), 1), "label": round(wmin + span * frac, 1)}
        for frac in (0, 0.5, 1)
    ]
    return {"points": points, "polyline": polyline, "y_ticks": y_ticks, "width": width, "height": height}


def _build_bars(bars: list[tuple[str, float]], width: int = 640, height: int = 220, pad: int = 36) -> dict:
    """Turn (label, value) pairs into SVG bar geometry for the template."""
    if not bars:
        return {"bars": [], "width": width, "height": height, "baseline": height - pad}
    vmax = max((v for _, v in bars), default=0) or 1
    inner_w, inner_h = width - 2 * pad, height - 2 * pad
    n = len(bars)
    gap = 10
    bar_w = max(6, (inner_w - gap * (n - 1)) / n)
    baseline = height - pad
    out = []
    for i, (label, v) in enumerate(bars):
        h = inner_h * (v / vmax)
        out.append(
            {
                "x": round(pad + i * (bar_w + gap), 1),
                "y": round(baseline - h, 1),
                "w": round(bar_w, 1),
                "h": round(h, 1),
                "label": label,
                "value": round(v, 1),
            }
        )
    return {"bars": out, "width": width, "height": height, "baseline": baseline}


def _weekly_volume(logs: list[Log], weeks: int = 8) -> list[tuple[str, float]]:
    """Sum of weight*reps*sets per ISO week, most recent `weeks` buckets."""
    from collections import OrderedDict

    buckets: "OrderedDict[str, float]" = OrderedDict()
    for log in sorted(logs, key=lambda x: x.date):
        if log.weight and log.reps and log.sets:
            iso = log.date.isocalendar()
            key = f"{iso[0]}-W{iso[1]:02d}"
            buckets[key] = buckets.get(key, 0.0) + log.weight * log.reps * log.sets
    items = list(buckets.items())[-weeks:]
    # Show just the week number as the label to keep the axis readable.
    return [(k.split("-W")[1], v) for k, v in items]


@router.get("/progress", response_class=HTMLResponse)
def progress_page(
    request: Request,
    exercise_id: Optional[int] = None,
    user_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_web),
):
    is_trainer = current_user.role == Role.trainer
    # Members only ever chart their own data, whatever user_id is requested.
    target_user_id = user_id if (is_trainer and user_id) else current_user.id

    exercises = db.scalars(select(Exercise).order_by(Exercise.name)).all()
    members = db.scalars(select(User).order_by(User.name)).all() if is_trainer else []

    series: list[tuple[date, float]] = []
    if exercise_id:
        stmt = (
            select(Log.date, Log.weight)
            .where(Log.user_id == target_user_id, Log.exercise_id == exercise_id, Log.weight.is_not(None))
            .order_by(Log.date)
        )
        series = [(d, w) for d, w in db.execute(stmt).all()]

    # Overview charts across all of the member's logs.
    all_logs = db.scalars(select(Log).where(Log.user_id == target_user_id)).all()
    volume_bars = _build_bars(_weekly_volume(all_logs))
    feeling_order = [("easy", "Easy"), ("moderate", "Moderate"), ("tough", "Tough")]
    feeling_counts = {k: 0 for k, _ in feeling_order}
    for log in all_logs:
        if log.feeling is not None:
            feeling_counts[log.feeling.value] = feeling_counts.get(log.feeling.value, 0) + 1
    feeling_bars = _build_bars([(label, feeling_counts[k]) for k, label in feeling_order])

    return templates.TemplateResponse(
        "progress.html",
        {
            "request": request,
            "current_user": current_user,
            "is_trainer": is_trainer,
            "exercises": exercises,
            "members": members,
            "selected_exercise_id": exercise_id,
            "selected_user_id": target_user_id if is_trainer else None,
            "chart": _build_chart(series),
            "has_selection": bool(exercise_id),
            "volume_chart": volume_bars,
            "feeling_chart": feeling_bars,
            "has_logs": bool(all_logs),
            "has_feelings": any(feeling_counts.values()),
        },
    )
