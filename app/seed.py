"""Seed demo accounts: ``py -m app.seed``. Idempotent; dev only (ENV=dev)."""

from __future__ import annotations

from .auth import ensure_seed_users
from .db import ensure_indexes


def main() -> int:
    try:
        ensure_indexes()
    except Exception:
        pass
    seeded = ensure_seed_users()
    for user in seeded:
        print(f"seeded {user['email']} ({user['role']})")
    if not seeded:
        print("nothing to seed (already present or ENV != dev)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
