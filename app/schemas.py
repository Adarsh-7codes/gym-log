from datetime import date
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr

from app.models import BodyPart, Difficulty, Feeling, Role


class UserCreate(BaseModel):
    name: str
    email: EmailStr
    password: str


class UserOut(BaseModel):
    id: int
    name: str
    email: EmailStr
    role: Role

    model_config = ConfigDict(from_attributes=True)


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class ExerciseCreate(BaseModel):
    name: str
    body_part: Optional[BodyPart] = None
    difficulty: Optional[Difficulty] = None
    equipment: Optional[str] = None
    instructions: Optional[str] = None
    demo_url: Optional[str] = None


class ExerciseOut(BaseModel):
    id: int
    name: str
    body_part: Optional[BodyPart] = None
    difficulty: Optional[Difficulty] = None
    equipment: Optional[str] = None
    instructions: Optional[str] = None
    demo_url: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class LogCreate(BaseModel):
    exercise_id: int
    date: date
    weight: Optional[float] = None
    reps: Optional[int] = None
    sets: Optional[int] = None
    next_action: Optional[str] = None
    notes: Optional[str] = None
    feeling: Optional[Feeling] = None
    user_id: Optional[int] = None  # trainer-only: log a workout on behalf of a member


class LogUpdate(BaseModel):
    exercise_id: Optional[int] = None
    date: Optional[date] = None
    weight: Optional[float] = None
    reps: Optional[int] = None
    sets: Optional[int] = None
    next_action: Optional[str] = None
    notes: Optional[str] = None
    feeling: Optional[Feeling] = None


class LogOut(BaseModel):
    id: int
    user_id: int
    exercise_id: int
    date: date
    weight: Optional[float] = None
    reps: Optional[int] = None
    sets: Optional[int] = None
    next_action: Optional[str] = None
    notes: Optional[str] = None
    feeling: Optional[Feeling] = None
    logged_by: Optional[Role] = None

    model_config = ConfigDict(from_attributes=True)
