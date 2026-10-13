"""Sign-up, sign-in, sign-out.

Sign-in gives one generic error and takes the same time for a wrong password and an unknown
email. Sign-up necessarily says "already registered" (there is no email confirmation in this
version), so it is rate-limited per IP; see ASSUMPTIONS.md.
"""

import re
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from pydantic import AfterValidator, BaseModel, Field, field_validator
from redis import Redis
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.validators import encodable
from app.auth.deps import DB, CurrentUser
from app.auth.passwords import hash_password, needs_rehash, verify_password
from app.auth.sessions import COOKIE_NAME, create_session, end_all_sessions, end_session
from app.models import User
from app.redis_client import get_redis
from app.security import rate_limit

router = APIRouter(prefix="/auth", tags=["auth"])

# No spaces, control characters or lone surrogates in any part.
_EMAIL = re.compile(
    r"^[^@\s\x00-\x1f\x7f\ud800-\udfff]{1,64}@[^@\s\x00-\x1f\x7f\ud800-\udfff]{1,189}"
    r"\.[^@\s\x00-\x1f\x7f\ud800-\udfff]{2,}$"
)
Password = Annotated[str, AfterValidator(encodable)]
BAD_CREDENTIALS = "Неверный email или пароль"


class Credentials(BaseModel):
    email: str = Field(max_length=254)
    password: Password = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def _normalise(cls, email: str) -> str:
        email = email.strip().lower()
        if not _EMAIL.match(email):
            raise ValueError("Некорректный email")
        return email


class Registration(Credentials):
    password: Password = Field(min_length=10, max_length=128)


class UserOut(BaseModel):
    id: str
    email: str


def _client_ip(request: Request) -> str:
    # nginx overwrites X-Forwarded-For with the real address; uvicorn applies it.
    return request.client.host if request.client else "unknown"


def _out(user: User) -> UserOut:
    return UserOut(id=str(user.id), email=user.email)


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(
    body: Registration,
    request: Request,
    response: Response,
    db: DB,
    redis: Annotated[Redis, Depends(get_redis)],
    sid: Annotated[str | None, Cookie(alias=COOKIE_NAME)] = None,
) -> UserOut:
    rate_limit.check(redis, rate_limit.REGISTER_PER_IP, _client_ip(request))
    user = User(email=body.email, password_hash=hash_password(body.password))
    db.add(user)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="Этот email уже зарегистрирован"
        ) from None
    end_session(db, sid, response)
    create_session(db, user, response)
    db.commit()
    return _out(user)


@router.post("/login")
def login(
    body: Credentials,
    request: Request,
    response: Response,
    db: DB,
    redis: Annotated[Redis, Depends(get_redis)],
    sid: Annotated[str | None, Cookie(alias=COOKIE_NAME)] = None,
) -> UserOut:
    ip = _client_ip(request)
    pair = f"{body.email}|{ip}"
    rate_limit.check(redis, rate_limit.LOGIN_PER_IP, ip)
    # Count as a failure up front (atomic INCR), take it back if the password is right.
    rate_limit.check(redis, rate_limit.LOGIN_FAILURES_PER_EMAIL_IP, pair)
    rate_limit.check(redis, rate_limit.LOGIN_FAILURES_PER_EMAIL, body.email)
    user = db.scalar(select(User).where(User.email == body.email))
    if not verify_password(user.password_hash if user else None, body.password) or user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=BAD_CREDENTIALS)
    rate_limit.reset(redis, rate_limit.LOGIN_FAILURES_PER_EMAIL_IP, pair)
    rate_limit.undo(redis, rate_limit.LOGIN_FAILURES_PER_EMAIL, body.email)
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    # A new session id on every sign-in; the one the browser had is ended (session fixation).
    end_session(db, sid, response)
    create_session(db, user, response)
    db.commit()
    return _out(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    response: Response,
    db: DB,
    sid: Annotated[str | None, Cookie(alias=COOKIE_NAME)] = None,
) -> None:
    end_session(db, sid, response)
    db.commit()


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
def logout_everywhere(
    user: CurrentUser,
    response: Response,
    db: DB,
    sid: Annotated[str | None, Cookie(alias=COOKIE_NAME)] = None,
) -> None:
    """End every session of this user, e.g. after a lost laptop."""
    end_all_sessions(db, user)
    end_session(db, sid, response)
    db.commit()


@router.get("/me")
def me(user: CurrentUser) -> UserOut:
    return _out(user)
