"""Passkey hashing and verification (argon2id only).

The passkey is typed through a global keyboard hook (Windows) or an evdev grab
(Linux) that translates key codes to characters itself, so we restrict it to
printable ASCII: that survives keyboard-layout translation reliably.
"""

from __future__ import annotations

from typing import Callable

from argon2 import PasswordHasher, Type
from argon2 import exceptions as argon2_exc

MIN_LENGTH = 4
MAX_LENGTH = 128
_PREFIX = "$argon2id$"

# argon2-cffi >= 21.2 defaults follow RFC 9106's low-memory profile
# (t=3, m=64 MiB, p=4): a few hundred ms per verify on a laptop.
_hasher = PasswordHasher(type=Type.ID)

_VERIFY_ERRORS: tuple[type[BaseException], ...] = (
    argon2_exc.VerificationError,  # includes VerifyMismatchError
    getattr(argon2_exc, "InvalidHashError", argon2_exc.InvalidHash),
)


class PasskeyError(ValueError):
    pass


def validate_passkey(passkey: str) -> None:
    if len(passkey) < MIN_LENGTH:
        raise PasskeyError(f"passkey must be at least {MIN_LENGTH} characters")
    if len(passkey) > MAX_LENGTH:
        raise PasskeyError(f"passkey must be at most {MAX_LENGTH} characters")
    if any(not (32 <= ord(ch) <= 126) for ch in passkey):
        raise PasskeyError("passkey must use printable ASCII characters only (letters, digits, symbols, space)")
    if passkey != passkey.strip():
        raise PasskeyError("passkey must not start or end with a space")


def hash_passkey(passkey: str) -> str:
    validate_passkey(passkey)
    return _hasher.hash(passkey)


def verify_passkey(stored_hash: str, candidate: str) -> bool:
    if not stored_hash.startswith(_PREFIX):
        return False  # never accept non-argon2id (or empty) hashes
    try:
        return _hasher.verify(stored_hash, candidate)
    except _VERIFY_ERRORS:
        return False


def needs_rehash(stored_hash: str) -> bool:
    return _hasher.check_needs_rehash(stored_hash)


def make_verifier(stored_hash: str) -> Callable[[str], bool]:
    def verify(candidate: str) -> bool:
        return verify_passkey(stored_hash, candidate)

    return verify
