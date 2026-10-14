"""User secrets at rest: encrypted, bound to their user, readable after a key rotation."""

from __future__ import annotations

import threading
import uuid
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from app.config import get_settings
from app.security import vault
from app.security.vault import KEY_FILE, VaultError, seal, unseal

ANNA, BORIS = uuid.uuid4(), uuid.uuid4()


def test_a_sealed_secret_opens_only_for_its_user(data_dir: Path) -> None:
    token = seal(ANNA, "sk_secret_value")
    assert b"sk_secret_value" not in token
    assert unseal(ANNA, token) == "sk_secret_value"
    with pytest.raises(VaultError):
        unseal(BORIS, token)  # a row copied onto another account
    with pytest.raises(VaultError):
        unseal(ANNA, token[:-4] + b"AAAA")


def test_without_secrets_key_one_key_is_generated_on_the_data_volume(data_dir: Path) -> None:
    keys: list[bytes] = []

    def first_use() -> None:
        keys.append(vault._local_key(data_dir))

    threads = [threading.Thread(target=first_use) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(set(keys)) == 1  # racing workers agree on one key
    path = data_dir / KEY_FILE
    assert path.read_bytes().strip() == keys[0] and path.stat().st_mode & 0o777 == 0o600
    assert not [p for p in path.parent.iterdir() if p.name.endswith(".part")]


def test_rotation_keeps_old_secrets_readable(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    monkeypatch.setattr(get_settings(), "secrets_key", SecretStr(old))
    vault.reset_cache()
    token = seal(ANNA, "sk_one")
    monkeypatch.setattr(get_settings(), "secrets_key", SecretStr(f"{new}, {old}"))
    vault.reset_cache()
    assert unseal(ANNA, token) == "sk_one"
    assert unseal(ANNA, seal(ANNA, "sk_two")) == "sk_two"
    monkeypatch.setattr(get_settings(), "secrets_key", SecretStr(new))
    vault.reset_cache()
    with pytest.raises(VaultError):
        unseal(ANNA, token)  # the old key was dropped: the user enters the key again
