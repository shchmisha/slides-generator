"""User secrets at rest (the ElevenLabs key): Fernet (AES-128-CBC + HMAC-SHA256).

The encryption key is not in the database: SECRETS_KEY in the environment or, for a local
`docker compose up` without it, a key generated once on the data volume. A database dump alone
reveals no user key. Each token is bound to its user (the plaintext starts with the user id),
so a row copied onto another account does not decrypt there.
"""

from __future__ import annotations

import os
import uuid
from functools import lru_cache
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.config import get_settings

KEY_FILE = Path("secrets") / "fernet.key"


class VaultError(Exception):
    """A token that cannot be opened: another key, another user, or damaged."""


def _local_key(data_dir: Path) -> bytes:
    path = data_dir / KEY_FILE
    if path.is_file():
        return path.read_bytes().strip()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Written under a temporary name and linked into place: link() fails if another process
    # got there first, and nobody ever reads a half-written key.
    partial = path.with_name(f".{uuid.uuid4().hex}.part")
    fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as out:
        out.write(Fernet.generate_key())
    try:
        os.link(partial, path)
    except FileExistsError:
        pass
    finally:
        partial.unlink(missing_ok=True)
    return path.read_bytes().strip()


@lru_cache
def _fernet() -> MultiFernet:
    settings = get_settings()
    configured = [k.strip() for k in settings.secrets_key.get_secret_value().split(",")]
    keys = [k.encode("ascii") for k in configured if k] or [_local_key(settings.data_dir)]
    return MultiFernet([Fernet(key) for key in keys])


def seal(user_id: uuid.UUID, secret: str) -> bytes:
    return _fernet().encrypt(f"{user_id}\n{secret}".encode())


def unseal(user_id: uuid.UUID, token: bytes) -> str:
    try:
        owner, _, secret = _fernet().decrypt(token).decode().partition("\n")
    except (InvalidToken, UnicodeDecodeError) as exc:
        raise VaultError("cannot decrypt") from exc
    if owner != str(user_id) or not secret:
        raise VaultError("token belongs to another user")
    return secret


def reset_cache() -> None:
    """For tests that change SECRETS_KEY or DATA_DIR."""
    _fernet.cache_clear()
