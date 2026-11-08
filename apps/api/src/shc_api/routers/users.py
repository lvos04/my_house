"""User management (verbeterlijst item 18).

To be implemented here: listing, disabling and deleting users, changing roles, changing
your own password (revoking that user's web sessions and phone tokens), lockout state
(``User.failed_logins`` / ``locked_until``) and a read-only view of the audit log.
Creating users currently lives in ``routers/auth.py`` (``POST /auth/users``).
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["users"])
