import enum
from datetime import date

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import relationship

from app.database import Base


class Role(str, enum.Enum):
    trainer = "trainer"
    member = "member"


class Feeling(str, enum.Enum):
    easy = "easy"
    moderate = "moderate"
    tough = "tough"


class BodyPart(str, enum.Enum):
    chest = "chest"
    back = "back"
    legs = "legs"
    shoulders = "shoulders"
    arms = "arms"
    core = "core"


class MembershipStatus(str, enum.Enum):
    paid = "paid"
    pending = "pending"


class Difficulty(str, enum.Enum):
    beginner = "beginner"
    intermediate = "intermediate"
    advanced = "advanced"


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    name = Column(String(120), nullable=False)
    email = Column(String(255), nullable=False, unique=True, index=True)
    password_hash = Column(String(255), nullable=False)
    # native_enum=False -> stored as a plain VARCHAR (+ CHECK) instead of a Postgres
    # ENUM type, so adding a role later is a normal migration, not an ALTER TYPE.
    role = Column(SAEnum(Role, native_enum=False, length=20), nullable=False, default=Role.member)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    logs = relationship("Log", back_populates="user", cascade="all, delete-orphan")
    memberships = relationship(
        "Membership", back_populates="user", cascade="all, delete-orphan", order_by="Membership.plan_start.desc()"
    )


class Membership(Base):
    """One membership term for a member (plan start + duration + dues).

    A member accumulates several rows over time -- that history is the renewal
    record. The "current" membership is the row with the latest plan_start.
    `expires_on` is computed once on save and stored, so roster queries and
    sorting never have to derive it at read time.

    Deliberately NOT here: card details, gateway ids, invoices. The trainer
    collects cash/UPI himself and only records the outcome.
    """

    __tablename__ = "memberships"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    plan_start = Column(Date, nullable=False)
    duration_months = Column(Integer, nullable=False)
    expires_on = Column(Date, nullable=False, index=True)
    amount = Column(Numeric(10, 2), nullable=False, default=0)
    status = Column(
        SAEnum(MembershipStatus, native_enum=False, length=20),
        nullable=False,
        default=MembershipStatus.pending,
        index=True,
    )
    paid_on = Column(Date, nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="memberships")


class Exercise(Base):
    __tablename__ = "exercises"

    id = Column(Integer, primary_key=True)
    name = Column(String(120), nullable=False, unique=True)
    body_part = Column(SAEnum(BodyPart, native_enum=False, length=20), nullable=True, index=True)
    difficulty = Column(SAEnum(Difficulty, native_enum=False, length=20), nullable=True, index=True)
    equipment = Column(String(120), nullable=True)          # optional: what's needed
    instructions = Column(Text, nullable=True)              # optional: short how-to
    demo_url = Column(String(300), nullable=True)           # optional: image/video link

    logs = relationship("Log", back_populates="exercise")


class SplitDay(Base):
    """One (weekday, body_part) pairing in a member's recurring weekly split.

    A day can have several rows -- e.g. Monday -> chest AND arms -- so members
    who train two body parts a day are supported. The log screen reads the
    current weekday's body parts and shows only those exercises.
    """

    __tablename__ = "split_days"
    __table_args__ = (UniqueConstraint("user_id", "weekday", "body_part", name="uq_split_user_day_part"),)

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    weekday = Column(Integer, nullable=False)  # 0 = Monday ... 6 = Sunday
    body_part = Column(SAEnum(BodyPart, native_enum=False, length=20), nullable=False)


class MemberRoutine(Base):
    """A member's standing selection of exercises per body_part.

    Persistent (not per-session): the member's permanent 'Chest Day' set, etc.
    Removing a row here only stops the exercise appearing on future log days --
    historical Log rows are a separate table and are never touched.
    """

    __tablename__ = "member_routines"
    __table_args__ = (UniqueConstraint("user_id", "exercise_id", name="uq_routine_user_exercise"),)

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    exercise_id = Column(Integer, ForeignKey("exercises.id", ondelete="CASCADE"), nullable=False, index=True)
    # denormalized for fast per-body_part queries (matches exercise.body_part at add time)
    body_part = Column(SAEnum(BodyPart, native_enum=False, length=20), nullable=True, index=True)
    date_added = Column(Date, nullable=False, default=date.today)
    # Who put this exercise in the routine: the member themselves, or their
    # trainer prescribing it. Existing rows backfill to `member`.
    assigned_by = Column(
        SAEnum(Role, native_enum=False, length=20), nullable=False, default=Role.member
    )

    exercise = relationship("Exercise")


class Log(Base):
    __tablename__ = "logs"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    exercise_id = Column(Integer, ForeignKey("exercises.id"), nullable=False, index=True)
    date = Column(Date, nullable=False)
    weight = Column(Float, nullable=True)
    reps = Column(Integer, nullable=True)
    sets = Column(Integer, nullable=True)
    next_action = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)
    # How the set felt from the member's perspective (easy/moderate/tough).
    feeling = Column(SAEnum(Feeling, native_enum=False, length=20), nullable=True)
    # Which role entered this log: the member themselves, or a trainer on their
    # behalf. Distinct from user_id (whose log it is).
    logged_by = Column(SAEnum(Role, native_enum=False, length=20), nullable=True)

    user = relationship("User", back_populates="logs")
    exercise = relationship("Exercise", back_populates="logs")


class PlanDay(Base):
    """One weekday of a member's recurring weekly plan (0=Mon .. 6=Sun)."""

    __tablename__ = "plan_days"
    __table_args__ = (UniqueConstraint("user_id", "weekday", name="uq_planday_user_weekday"),)

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    weekday = Column(Integer, nullable=False)  # 0 = Monday ... 6 = Sunday
    focus = Column(String(120), nullable=True)  # e.g. "Chest & Back"

    items = relationship("PlanItem", back_populates="day", cascade="all, delete-orphan", order_by="PlanItem.id")


class PlanItem(Base):
    """A planned exercise on a weekday, with target sets/reps/weight."""

    __tablename__ = "plan_items"

    id = Column(Integer, primary_key=True)
    plan_day_id = Column(Integer, ForeignKey("plan_days.id", ondelete="CASCADE"), nullable=False, index=True)
    exercise_id = Column(Integer, ForeignKey("exercises.id"), nullable=False)
    target_sets = Column(Integer, nullable=True)
    target_reps = Column(Integer, nullable=True)
    target_weight = Column(Float, nullable=True)

    day = relationship("PlanDay", back_populates="items")
    exercise = relationship("Exercise")
