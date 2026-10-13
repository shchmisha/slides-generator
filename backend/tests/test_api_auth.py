"""Sign-up, sign-in, sessions, CSRF and rate limits."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.models import AuthSession, User
from app.redis_client import get_redis

from .api_support import PASSWORD, FakeCounter, new_client, signed_up

ANNA = {"email": "anna@example.com", "password": PASSWORD}
WRONG = {"email": "anna@example.com", "password": "wrong password!"}


def sessions(session_factory: sessionmaker[Session]) -> int:
    with session_factory() as db:
        return db.scalar(select(func.count()).select_from(AuthSession)) or 0


def test_register_signs_in_with_a_safe_cookie(app: Any) -> None:
    client = new_client(app)
    resp = client.post(
        "/api/auth/register", json={"email": " Anna@Example.com ", "password": PASSWORD}
    )
    assert resp.status_code == 201
    assert resp.json()["email"] == "anna@example.com"
    cookie = resp.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "path=/" in cookie
    assert client.get("/api/auth/me").json()["email"] == "anna@example.com"


def test_duplicate_email_is_rejected_case_insensitively(app: Any) -> None:
    signed_up(app, "anna@example.com")
    resp = new_client(app).post(
        "/api/auth/register", json={"email": "ANNA@example.com", "password": PASSWORD}
    )
    assert resp.status_code == 409


def test_weak_password_and_bad_email_are_rejected(app: Any) -> None:
    client = new_client(app)
    assert (
        client.post("/api/auth/register", json={"email": "a@b.co", "password": "short"}).status_code
        == 422
    )
    assert (
        client.post(
            "/api/auth/register", json={"email": "not-an-email", "password": PASSWORD}
        ).status_code
        == 422
    )


def test_password_is_stored_as_argon2(app: Any, session_factory: sessionmaker[Session]) -> None:
    signed_up(app)
    with session_factory() as db:
        user = db.scalar(select(User))
        assert user is not None
        assert user.password_hash.startswith("$argon2id$")
        assert PASSWORD not in user.password_hash


def test_wrong_password_and_unknown_email_get_the_same_answer(app: Any) -> None:
    signed_up(app)
    client = new_client(app)
    wrong = client.post(
        "/api/auth/login", json={"email": "anna@example.com", "password": "wrong password!"}
    )
    unknown = client.post(
        "/api/auth/login", json={"email": "nobody@example.com", "password": PASSWORD}
    )
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json() == {"detail": "Неверный email или пароль"}


def test_login_and_logout(app: Any) -> None:
    signed_up(app)
    client = new_client(app)
    assert (
        client.post(
            "/api/auth/login", json={"email": "anna@example.com", "password": PASSWORD}
        ).status_code
        == 200
    )
    token = client.cookies.get("sid")
    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/auth/me").status_code == 401
    # The old token is dead on the server too, not only removed from the browser.
    stale = new_client(app)
    stale.cookies.set("sid", token or "")
    assert stale.get("/api/auth/me").status_code == 401


def test_only_a_hash_of_the_token_is_stored(
    app: Any, session_factory: sessionmaker[Session]
) -> None:
    client = signed_up(app)
    token = client.cookies.get("sid")
    with session_factory() as db:
        stored = db.scalar(select(AuthSession))
        assert stored is not None
        assert token and stored.token_hash != token and len(stored.token_hash) == 64


def test_expired_session_is_refused(app: Any, session_factory: sessionmaker[Session]) -> None:
    client = signed_up(app)
    with session_factory() as db:
        db.execute(update(AuthSession).values(expires_at=datetime.now(UTC) - timedelta(minutes=1)))
        db.commit()
    assert client.get("/api/auth/me").status_code == 401


def test_state_changing_requests_need_the_csrf_header(app: Any) -> None:
    client = signed_up(app)
    resp = client.post(
        "/api/projects", json={"title": "x", "kind": "deck"}, headers={"X-Requested-With": ""}
    )
    assert resp.status_code == 403
    assert client.post("/api/projects", json={"title": "x", "kind": "deck"}).status_code == 201


def test_login_is_rate_limited(app: Any) -> None:
    signed_up(app)
    client = new_client(app)
    codes = [
        client.post(
            "/api/auth/login", json={"email": "anna@example.com", "password": "wrong password!"}
        ).status_code
        for _ in range(9)
    ]
    assert codes[:8] == [401] * 8
    assert codes[8] == 429


def test_rate_limit_answer_says_when_to_retry(app: Any) -> None:
    client = new_client(app)
    for _ in range(10):
        client.post("/api/auth/register", json={"email": f"u{_}@example.com", "password": PASSWORD})
    resp = client.post(
        "/api/auth/register", json={"email": "late@example.com", "password": PASSWORD}
    )
    assert resp.status_code == 429
    assert int(resp.headers["retry-after"]) > 0


def test_failed_sign_ins_from_one_place_do_not_lock_out_the_owner(app: Any) -> None:
    signed_up(app)
    attacker = new_client(app, ip="198.51.100.66")
    codes = [attacker.post("/api/auth/login", json=WRONG).status_code for _ in range(12)]
    assert codes[-1] == 429
    owner = new_client(app, ip="203.0.113.20")
    assert owner.post("/api/auth/login", json=ANNA).status_code == 200


def test_successful_sign_ins_are_not_counted_as_failures(app: Any) -> None:
    signed_up(app)
    client = new_client(app)
    for _ in range(7):
        client.post("/api/auth/login", json=WRONG)
    assert client.post("/api/auth/login", json=ANNA).status_code == 200  # resets the count
    codes = [client.post("/api/auth/login", json=WRONG).status_code for _ in range(8)]
    assert codes == [401] * 8


def test_sign_in_replaces_the_session_the_browser_had(
    app: Any, session_factory: sessionmaker[Session]
) -> None:
    client = signed_up(app)
    before = client.cookies.get("sid")
    assert client.post("/api/auth/login", json=ANNA).status_code == 200
    after = client.cookies.get("sid")
    assert before and after and before != after
    assert sessions(session_factory) == 1
    stale = new_client(app)
    stale.cookies.set("sid", before)
    assert stale.get("/api/auth/me").status_code == 401


def test_sign_up_ends_the_previous_session(
    app: Any, session_factory: sessionmaker[Session]
) -> None:
    client = signed_up(app, "anna@example.com")
    resp = client.post(
        "/api/auth/register", json={"email": "boris@example.com", "password": PASSWORD}
    )
    assert resp.status_code == 201
    assert sessions(session_factory) == 1
    assert client.get("/api/auth/me").json()["email"] == "boris@example.com"


def test_logout_everywhere_ends_every_session(
    app: Any, session_factory: sessionmaker[Session]
) -> None:
    laptop = signed_up(app)
    phone = new_client(app)
    phone.post("/api/auth/login", json=ANNA)
    assert sessions(session_factory) == 2
    assert laptop.post("/api/auth/logout-all").status_code == 204
    assert sessions(session_factory) == 0
    assert phone.get("/api/auth/me").status_code == 401


def test_rate_limiter_outage_refuses_sign_in_with_503(app: Any) -> None:
    signed_up(app)

    class Down(FakeCounter):
        def incr(self, name: str) -> int:
            raise RedisConnectionError("down")

    app.dependency_overrides[get_redis] = lambda: Down()
    resp = new_client(app).post("/api/auth/login", json=ANNA)
    assert resp.status_code == 503
    assert "retry-after" in resp.headers


def test_validation_errors_do_not_echo_the_input(app: Any) -> None:
    resp = new_client(app).post(
        "/api/auth/register", json={"email": "anna@example.com", "password": "tiny-pass"}
    )
    assert resp.status_code == 422
    assert "tiny-pass" not in resp.text
    assert resp.json() == {"detail": "password: слишком короткое значение"}


def test_api_docs_are_off_by_default(app: Any) -> None:
    client = new_client(app)
    assert client.get("/api/docs").status_code == 404
    assert client.get("/api/openapi.json").status_code == 404


def test_attempt_is_counted_before_the_password_is_checked(
    app: Any, counter: FakeCounter, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Parallel guesses must not all read "under the limit" while their checks are still running.
    signed_up(app)
    seen: list[int] = []

    def verify(password_hash: str | None, password: str) -> bool:
        seen.append(max((v for k, v in counter.values.items() if "login-fail" in k), default=0))
        return password == PASSWORD

    monkeypatch.setattr("app.api.auth.verify_password", verify)
    client = new_client(app)
    client.post("/api/auth/login", json=WRONG)
    client.post("/api/auth/login", json=ANNA)
    assert seen == [1, 2]
    assert not any(v > 0 for k, v in counter.values.items() if "login-fail-email-ip" in k)
    assert [v for k, v in counter.values.items() if "login-fail-email:" in k] == [1]


def test_unencodable_passwords_and_emails_are_a_422(app: Any) -> None:
    client = new_client(app)
    for body in (
        '{"email": "anna@example.com", "password": "long enough \\ud800 pass"}',
        '{"email": "an\\u0000na@example.com", "password": "correct horse battery"}',
    ):
        resp = client.post(
            "/api/auth/login", content=body, headers={"Content-Type": "application/json"}
        )
        assert resp.status_code == 422
