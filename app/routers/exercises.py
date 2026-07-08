from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user, require_trainer
from app.models import Exercise, User
from app.schemas import ExerciseCreate, ExerciseOut

router = APIRouter(prefix="/api/exercises", tags=["exercises"])


@router.get("", response_model=list[ExerciseOut])
def list_exercises(db: Session = Depends(get_db), _user: User = Depends(get_current_user)):
    return db.scalars(select(Exercise).order_by(Exercise.name)).all()


@router.post("", response_model=ExerciseOut, status_code=status.HTTP_201_CREATED)
def create_exercise(
    payload: ExerciseCreate,
    db: Session = Depends(get_db),
    _trainer: User = Depends(require_trainer),
):
    existing = db.scalar(select(Exercise).where(Exercise.name == payload.name))
    if existing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Exercise already exists")
    exercise = Exercise(
        name=payload.name,
        body_part=payload.body_part,
        difficulty=payload.difficulty,
        equipment=payload.equipment,
        instructions=payload.instructions,
        demo_url=payload.demo_url,
    )
    db.add(exercise)
    db.commit()
    db.refresh(exercise)
    return exercise


@router.delete("/{exercise_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_exercise(
    exercise_id: int,
    db: Session = Depends(get_db),
    _trainer: User = Depends(require_trainer),
):
    exercise = db.get(Exercise, exercise_id)
    if not exercise:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Exercise not found")
    db.delete(exercise)
    db.commit()
