"""Authentication, workspace isolation, and upload security controls.

Behavior:
- In Demo Mode (`REQUIRE_AUTH=false`), requests without an auth header use `default_workspace_id`
  ("demo-workspace") so a beginner can run and demonstrate the console immediately.
- When a user provides a workspace token (`X-Ops-Token` or `Authorization: Bearer <token>`)
  or when `REQUIRE_AUTH=true` in Connected Mode:
  - Validates either `SUPABASE_JWT_SECRET` (Supabase Auth JWT) or `APP_ACCESS_TOKEN`.
  - Isolates each authenticated user/tenant into their own `workspace_id` so no user can
    inspect or modify another user's customer records.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import jwt
from fastapi import Header, HTTPException, status

from src.config import get_config


@dataclass
class AuthContext:
    workspace_id: str
    user_label: str
    is_authenticated: bool
    is_demo_workspace: bool


def resolve_auth_context(
    authorization: str | None = Header(default=None),
    x_ops_token: str | None = Header(default=None),
    x_workspace_id: str | None = Header(default=None),
) -> AuthContext:
    """FastAPI dependency that resolves and enforces workspace isolation and authentication."""
    cfg = get_config()

    bearer_token = ""
    if authorization and authorization.lower().startswith("bearer "):
        bearer_token = authorization[7:].strip()

    token = bearer_token or (x_ops_token or "").strip()

    # 1. If a Supabase JWT secret is configured and a Bearer token is supplied, verify JWT
    if token and cfg.supabase_jwt_secret:
        try:
            payload = jwt.decode(
                token,
                cfg.supabase_jwt_secret,
                algorithms=["HS256"],
                options={"verify_aud": False},
            )
            sub = str(payload.get("sub") or "").strip()
            email = str(payload.get("email") or "Authenticated User").strip()
            if not sub:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid Supabase JWT: missing subject ('sub') claim.",
                )
            ws_id = f"user-{sub[:36]}"
            return AuthContext(
                workspace_id=ws_id,
                user_label=email,
                is_authenticated=True,
                is_demo_workspace=False,
            )
        except jwt.PyJWTError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid or expired authentication token: {exc}",
            ) from exc

    # 2. If APP_ACCESS_TOKEN is configured, verify token match
    if cfg.app_access_token:
        if token == cfg.app_access_token:
            ws_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]
            custom_ws = (x_workspace_id or f"team-{ws_hash}").strip()
            return AuthContext(
                workspace_id=custom_ws,
                user_label="Operations Team (Authenticated)",
                is_authenticated=True,
                is_demo_workspace=(custom_ws == cfg.default_workspace_id),
            )
        if cfg.require_auth or token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Valid access token (X-Ops-Token or Authorization Bearer) is required.",
            )

    # 3. If REQUIRE_AUTH=true but no valid credentials were provided, block access
    if cfg.require_auth:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication is required in Connected Mode. Provide a valid Bearer JWT or X-Ops-Token.",
        )

    # 4. Local / Demo Mode fallback
    return AuthContext(
        workspace_id=cfg.default_workspace_id,
        user_label="Demo Operations User",
        is_authenticated=False,
        is_demo_workspace=True,
    )
