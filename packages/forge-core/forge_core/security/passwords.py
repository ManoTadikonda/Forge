"""Password hashing.

Argon2id is used with the argon2-cffi defaults, which track the OWASP guidance. The
hasher exposes ``needs_rehash`` so parameters can be raised later without invalidating
existing credentials.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

#: time_cost/memory_cost/parallelism are the library defaults for Argon2id
#: (OWASP-aligned: 64 MiB, t=3, p=4 at the time of writing).
_hasher = PasswordHasher()

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 1024


class PasswordPolicyError(ValueError):
    """Raised when a candidate password does not satisfy the policy."""


def validate_password_policy(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise PasswordPolicyError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if len(password) > MAX_PASSWORD_LENGTH:
        # Bound the input so hashing cost cannot be used as a DoS vector.
        raise PasswordPolicyError(f"Password must be at most {MAX_PASSWORD_LENGTH} characters.")
    if password.strip() == "":
        raise PasswordPolicyError("Password must not be entirely whitespace.")


def hash_password(password: str) -> str:
    validate_password_policy(password)
    return _hasher.hash(password)


def verify_password(password: str, stored_hash: str) -> bool:
    """Constant-time-ish verification. Returns False rather than raising."""
    if not stored_hash or not password:
        return False
    if len(password) > MAX_PASSWORD_LENGTH:
        return False
    try:
        return _hasher.verify(stored_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(stored_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(stored_hash)
    except InvalidHashError:
        return True


__all__ = [
    "MIN_PASSWORD_LENGTH",
    "PasswordPolicyError",
    "hash_password",
    "needs_rehash",
    "validate_password_policy",
    "verify_password",
]
