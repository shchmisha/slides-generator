"""Password hashing with Argon2id (the argon2-cffi defaults follow RFC 9106).

Each hash takes 64 MiB of memory on purpose. Without a cap, a burst of sign-ins across the
API's 40 worker threads would need 2.5 GiB, so only a few run at a time and the rest wait.
"""

import threading

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

MAX_PARALLEL_HASHES = 4
_slots = threading.BoundedSemaphore(MAX_PARALLEL_HASHES)
_hasher = PasswordHasher()
# Verified when the email is unknown, so a wrong email takes as long as a wrong password and
# response time does not reveal which accounts exist.
_DUMMY_HASH = _hasher.hash("dummy-password-for-timing")


def hash_password(password: str) -> str:
    with _slots:
        return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        with _slots:
            ok = _hasher.verify(password_hash or _DUMMY_HASH, password)
    except (VerificationError, InvalidHashError):
        return False
    return ok and password_hash is not None


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)
