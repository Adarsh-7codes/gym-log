from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app import crud
from app.database import get_db
from app.deps import get_current_user
from app.models import User
from app.schemas import LogCreate, LogOut, LogUpdate

router = APIRouter(prefix="/api/logs", tags=["logs"])


@router.get("", response_model=list[LogOut])
def list_logs(
    user_id: Optional[int] = None,
    exercise_id: Optional[int] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return crud.list_logs(
        db,
        current_user,
        user_id=user_id,
        exercise_id=exercise_id,
        start_date=start_date,
        end_date=end_date,
    )


@router.post("", response_model=LogOut, status_code=status.HTTP_201_CREATED)
def create_log(
    payload: LogCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return crud.create_log(
            db,
            current_user,
            exercise_id=payload.exercise_id,
            log_date=payload.date,
            weight=payload.weight,
            reps=payload.reps,
            sets=payload.sets,
            next_action=payload.next_action,
            notes=payload.notes,
            feeling=payload.feeling,
            user_id=payload.user_id,
        )
    except crud.Forbidden as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except crud.NotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.get("/{log_id}", response_model=LogOut)
def get_log(log_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        return crud.get_owned_log(db, log_id, current_user)
    except crud.NotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.put("/{log_id}", response_model=LogOut)
def update_log(
    log_id: int,
    payload: LogUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        log = crud.get_owned_log(db, log_id, current_user)
    except crud.NotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return crud.update_log(db, log, **payload.model_dump(exclude_unset=True))


@router.delete("/{log_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_log(log_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        log = crud.get_owned_log(db, log_id, current_user)
    except crud.NotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    crud.delete_log(db, log)
