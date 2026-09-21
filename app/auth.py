"""Authentication and user management for the support dashboard.

Roles: employee, it_agent, admin.

- Passwords are hashed with bcrypt; hashes never leave the server.
- Sessions are JWT HS256 (PyJWT), delivered in an HttpOnly cookie named
  ``veridian_access`` and also returned as ``access_token`` for API/manual
  tests. Requests may present the token either via the cookie or via
  ``Authorization: Bearer <token>``.
- Users persist in Mongo when configured, with an in-memory fallback
  (see app/db.py), so tests run with MONGODB_URI empty.
- Demo accounts are seeded idempotently on startup only when ENV=dev.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from . import db
from .llm import get_settings

ROLES = ("employee", "it_agent", "admin")
SUPPORT_ROLES = ("it_agent", "admin")

router = APIRouter(prefix="/auth", tags=["auth"])


# --- password hashing (bcrypt) ----------------------------------------------


def hash_password(password: str) -> str:
    try:
        import bcrypt  # type: ignore[import-not-found]
    except Exception as exc:
        raise RuntimeError("bcrypt is required for password hashing") from exc
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        import bcrypt  # type: ignore[import-not-found]
    except Exception:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except Exception:
        return False


# --- user shapes -------------------------------------------------------------


def public_user(doc: dict) -> dict:
    return {
        "id": str(doc.get("id", "")),
        "email": str(doc.get("email", "")),
        "name": str(doc.get("name", "")),
        "role": str(doc.get("role", "employee")),
        "is_active": bool(doc.get("is_active", True)),
    }


def _new_user_id() -> str:
    return f"U-{uuid.uuid4().hex[:12].upper()}"


def create_user_record(
    *, email: str, name: str, role: str, password: str, is_active: bool = True
) -> dict:
    email_norm = email.strip().lower()
    if "@" not in email_norm:
        raise ValueError("invalid email")
    if role not in ROLES:
        raise ValueError(f"unknown role: {role}")
    if len(password) < 8:
        raise ValueError("password must be at least 8 characters")
    doc = {
        "id": _new_user_id(),
        "email": email_norm,
        "name": name.strip() or email_norm.split("@")[0],
        "role": role,
        "is_active": is_active,
        "password_hash": hash_password(password),
        "created_at": datetime.now(UTC).isoformat(),
    }
    return db.save_user(doc)


# --- demo seeding ------------------------------------------------------------


def ensure_seed_users() -> list[dict]:
    """Create local demo accounts if missing. Only with dev defaults.

    Idempotent: existing emails are left untouched (passwords are never
    overwritten). Only runs when ENV=dev and SEED_DEMO_USERS is true.
    Never prints passwords.
    """
    s = get_settings()
    if str(getattr(s, "env", "dev")).lower() != "dev":
        return []
    if not bool(getattr(s, "seed_demo_users", True)):
        return []
    specs = [
        (s.demo_employee_email, "Demo Employee", "employee", s.demo_employee_password),
        (s.demo_agent_email, "Demo Agent", "it_agent", s.demo_agent_password),
        (s.demo_admin_email, "Demo Admin", "admin", s.demo_admin_password),
    ]
    seeded: list[dict] = []
    for email, name, role, password in specs:
        try:
            existing = db.get_user_by_email(email)
        except Exception:
            existing = None
        if existing is not None:
            continue
        if not password:
            continue
        try:
            doc = create_user_record(email=email, name=name, role=role, password=password)
        except Exception:
            continue
        seeded.append(public_user(doc))
    return seeded


# --- JWT ---------------------------------------------------------------------


def create_access_token(user: dict) -> str:
    s = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(user.get("id", "")),
        "email": str(user.get("email", "")),
        "role": str(user.get("role", "")),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=int(s.jwt_expires_minutes))).timestamp()),
    }
    return jwt.encode(payload, s.jwt_secret, algorithm=s.jwt_algorithm)


def decode_token(token: str) -> dict:
    s = get_settings()
    return jwt.decode(token, s.jwt_secret, algorithms=[s.jwt_algorithm])


def token_from_request(request: Request) -> str | None:
    # Explicit Bearer credentials win over the ambient cookie, so API/manual
    # tests with a token header are never shadowed by a stored cookie.
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        candidate = auth[7:].strip()
        if candidate:
            return candidate
    s = get_settings()
    cookie_name = str(getattr(s, "auth_cookie_name", "veridian_access"))
    raw = request.cookies.get(cookie_name)
    if raw:
        return raw
    return None


def get_current_user(request: Request) -> dict:
    token = token_from_request(request)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    try:
        payload = decode_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired") from None
    except Exception:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid session") from None
    uid = str(payload.get("sub", ""))
    try:
        doc = db.get_user_by_id(uid)
    except Exception:
        doc = None
    if doc is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unknown user")
    if not doc.get("is_active", True):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account disabled")
    return doc


def require_user(request: Request) -> dict:
    return get_current_user(request)


def require_roles(*allowed: str):
    def _dep(request: Request) -> dict:
        user = get_current_user(request)
        if str(user.get("role", "")) not in allowed:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden")
        return user

    return _dep


require_support = require_roles(*SUPPORT_ROLES)
require_admin = require_roles("admin")


def current_user_public(request: Request) -> dict:
    return public_user(get_current_user(request))


# --- request models ----------------------------------------------------------


class LoginBody(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class CreateUserBody(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    name: str = Field(min_length=1, max_length=80)
    role: str = Field(pattern="^(employee|it_agent|admin)$")
    password: str = Field(min_length=8, max_length=256)


class UpdateUserBody(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    role: str | None = Field(default=None, pattern="^(employee|it_agent|admin)$")
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=8, max_length=256)


def _set_auth_cookie(response: Response, token: str) -> None:
    s = get_settings()
    response.set_cookie(
        key=str(getattr(s, "auth_cookie_name", "veridian_access")),
        value=token,
        httponly=True,
        samesite="lax",
        path="/",
        max_age=int(s.jwt_expires_minutes) * 60,
    )


def _clear_auth_cookie(response: Response) -> None:
    s = get_settings()
    response.delete_cookie(
        key=str(getattr(s, "auth_cookie_name", "veridian_access")), path="/"
    )


# --- routes ------------------------------------------------------------------


@router.post("/login")
def login(body: LoginBody, response: Response) -> dict:
    try:
        doc = db.get_user_by_email(str(body.email))
    except Exception:
        doc = None
    if doc is None or not verify_password(body.password, str(doc.get("password_hash", ""))):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    if not doc.get("is_active", True):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account disabled")
    token = create_access_token(doc)
    _set_auth_cookie(response, token)
    return {"user": public_user(doc), "access_token": token, "token_type": "bearer"}


@router.get("/me")
def me(request: Request) -> dict:
    return current_user_public(request)


@router.post("/logout")
def logout(response: Response) -> dict:
    _clear_auth_cookie(response)
    return {"ok": True}


@router.get("/users")
def list_all_users(request: Request) -> list[dict]:
    require_admin(request)
    try:
        docs = db.list_users()
    except Exception:
        docs = []
    return [public_user(d) for d in docs]


@router.post("/users")
def create_managed_user(body: CreateUserBody, request: Request) -> dict:
    require_admin(request)
    try:
        existing = db.get_user_by_email(str(body.email))
    except Exception:
        existing = None
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already exists")
    try:
        doc = create_user_record(
            email=str(body.email),
            name=body.name,
            role=body.role,
            password=body.password,
        )
    except ValueError as exc:
        if "already exists" in str(exc).lower():
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return public_user(doc)


@router.patch("/users/{user_id}")
def update_managed_user(user_id: str, body: UpdateUserBody, request: Request) -> dict:
    requester = require_admin(request)
    try:
        doc = db.get_user_by_id(user_id)
    except Exception:
        doc = None
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such user")
    if str(user_id) == str(requester.get("id", "")):
        # An admin who demotes or disables themselves could lock the desk out
        # of its last admin account. Refuse; another admin must do it.
        if body.role is not None and body.role != str(requester.get("role", "")):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "Admins cannot change their own role"
            )
        if body.is_active is False:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "Admins cannot disable their own account"
            )
    patch: dict[str, Any] = {}
    if body.name is not None:
        cleaned = body.name.strip()
        if not cleaned:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, "Name must not be blank"
            )
        patch["name"] = cleaned
    if body.role is not None:
        if body.role not in ROLES:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unknown role")
        patch["role"] = body.role
    if body.is_active is not None:
        patch["is_active"] = bool(body.is_active)
    if body.password is not None:
        patch["password_hash"] = hash_password(body.password)
    if not patch:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Nothing to update")
    updated = {**doc, **patch}
    try:
        db.save_user(updated)
    except ValueError as exc:
        # save_user enforces email uniqueness across the memory + mongo
        # stores; surface a conflicting email as 409, anything else as 422.
        if "already exists" in str(exc).lower():
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Could not save user") from exc
    return public_user(updated)
