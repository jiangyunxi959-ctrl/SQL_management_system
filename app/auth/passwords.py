"""密码只保存 Argon2 摘要。"""
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError

_HASHER = PasswordHasher()
_DUMMY = _HASHER.hash("zhishi-dummy-password")


def hash_password(password: str) -> str:
    if not isinstance(password, str) or len(password) < 8:
        raise ValueError("密码至少 8 位")
    return _HASHER.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        _HASHER.verify(password_hash or _DUMMY, password)
        return password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
