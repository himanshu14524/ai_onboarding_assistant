"""Password hashing + token-based auth. Two roles: 'hr' and 'employee'.
An employee user is locked to their own employee_id; HR can access everything."""
import hashlib
import hmac
import os
import secrets

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from .db import AuthToken, User, get_db

DEFAULT_HR_EMAIL = "hr@company.com"
DEFAULT_HR_PASSWORD = "hr123"  # change this after first login in a real deployment


def hash_password(pw: str) -> str:
    salt = os.urandom(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 100_000)
    return salt.hex() + ":" + h.hex()


def verify_password(pw: str, stored: str) -> bool:
    try:
        salt_hex, h_hex = stored.split(":")
    except ValueError:
        return False
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt_hex), 100_000)
    return hmac.compare_digest(h.hex(), h_hex)


def new_token() -> str:
    return secrets.token_hex(32)


def current_user(authorization: str = Header(default=""), db: Session = Depends(get_db)) -> User:
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(401, "Not logged in")
    t = db.get(AuthToken, token)
    if not t:
        raise HTTPException(401, "Session expired, please log in again")
    user = db.get(User, t.user_id)
    if not user:
        raise HTTPException(401, "Account no longer exists")
    return user


def require_hr(user: User = Depends(current_user)) -> User:
    if user.role != "hr":
        raise HTTPException(403, "This action is restricted to HR")
    return user


def require_self_or_hr(eid: int, user: User = Depends(current_user)) -> User:
    if user.role == "hr" or user.employee_id == eid:
        return user
    raise HTTPException(403, "You can only access your own onboarding data")
