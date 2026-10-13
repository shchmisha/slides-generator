"""Request dependencies: the signed-in user and CSRF protection."""

from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.auth.sessions import COOKIE_NAME, user_for_token
from app.db import get_db
from app.models import User

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
CSRF_HEADER = "X-Requested-With"


def require_csrf_header(request: Request) -> None:
    """State-changing requests must carry a custom header. A cross-site form or link cannot
    set one, and a cross-site script cannot without a CORS preflight, which the API never
    allows. Together with SameSite=Lax cookies this closes CSRF without tokens in forms."""
    if request.method in UNSAFE_METHODS and request.headers.get(CSRF_HEADER) != "fetch":
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Запрос отклонён")


def current_user(
    db: Annotated[Session, Depends(get_db)],
    sid: Annotated[str | None, Cookie(alias=COOKIE_NAME)] = None,
) -> User:
    user = user_for_token(db, sid)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Войдите в систему")
    return user


CurrentUser = Annotated[User, Depends(current_user)]
DB = Annotated[Session, Depends(get_db)]
