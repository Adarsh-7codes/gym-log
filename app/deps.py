from typing import Optional

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Role, User
from app.security import decode_access_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


class WebAuthRequired(Exception):
    """Raised by web-page dependencies; main.py maps this to a redirect to /login."""


def _user_from_token(token: Optional[str], db: Session) -> Optional[User]:
    if not token:
        return None
    try:
        payload = decode_access_token(token)
        user_id = payload.get("sub")
    except jwt.PyJWTError:
        return None
    if user_id is None:
        return None
    user = db.get(User, int(user_id))
    if user is None:
        return None
    # A password change bumps token_version, so tokens issued before it stop
    # validating immediately -- otherwise a stale JWT stays usable for its full
    # 7-day life after a reset. Tokens predating this field carry no claim, so
    # they are treated as version 0.
    if int(payload.get("tv", 0)) != int(user.token_version or 0):
        return None
    return user


# --- JSON API: Authorization header, 401 on failure ---------------------


def get_current_user(
    token: Optional[str] = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    user = _user_from_token(token, db)
    if user is None:
        raise credentials_exception
    return user


def require_trainer(user: User = Depends(get_current_user)) -> User:
    if user.role != Role.trainer:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Trainer access required")
    return user


# --- Server-rendered pages: cookie, redirect to /login on failure -------


def get_current_user_web(request: Request, db: Session = Depends(get_db)) -> User:
    user = _user_from_token(request.cookies.get("access_token"), db)
    if user is None:
        raise WebAuthRequired()
    return user


def get_current_user_web_optional(request: Request, db: Session = Depends(get_db)) -> Optional[User]:
    return _user_from_token(request.cookies.get("access_token"), db)


def require_trainer_web(user: User = Depends(get_current_user_web)) -> User:
    if user.role != Role.trainer:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Trainer access required")
    return user
