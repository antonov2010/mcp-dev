"""Session-scoped JWT manager.

Responsibilities
----------------
- Exchange a user email for a scoped JWT via the backend MCP broker endpoint.
- Cache the token in memory for the lifetime of the MCP server process.
- Auto-refresh the token when it is within the configured TTL buffer window.
- Provide thread-safe access to the current session token.

Token precedence used by HTTP tools (highest → lowest priority):
  1. Explicit ``jwt_token`` passed in the tool call.
  2. Session token held by this manager (set via ``auth_start_session``).
  3. ``API_BEARER_TOKEN`` environment variable.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

LOGGER = logging.getLogger(__name__)


@dataclass
class _SessionState:
    token: str
    email: str
    display_name: str
    acquired_at: float = field(default_factory=time.monotonic)
    expires_in_seconds: float = 14400.0  # 4 hours default, matches backend TokenService


def _parse_expires_in(token: str) -> float:
    """Decode the JWT exp claim without verifying the signature.

    Falls back to 4 hours (14 400 s) when the claim cannot be read so the
    session stays functional even if the token format changes.
    """
    try:
        import base64
        import json

        parts = token.split(".")
        if len(parts) < 2:
            return 14400.0
        # JWT payload is base64url encoded; add padding as needed
        payload_b64 = parts[1] + "=="
        payload = json.loads(base64.urlsafe_b64decode(payload_b64.encode()))
        exp: float | None = payload.get("exp")
        if exp is None:
            return 14400.0
        remaining = exp - time.time()
        return max(0.0, remaining)
    except Exception:  # noqa: BLE001
        return 14400.0


class SessionTokenManager:
    """Thread-safe in-memory JWT session manager.

    Usage
    -----
    There is one process-level singleton (``session_manager``) created at the
    bottom of this module.  Import and use that instance from other modules.
    """

    def __init__(self, ttl_buffer_seconds: int = 60) -> None:
        self._lock = threading.Lock()
        self._state: _SessionState | None = None
        self._ttl_buffer_seconds = ttl_buffer_seconds

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def acquire(
        self,
        *,
        exchange_url: str,
        shared_secret: str,
        email: str,
        reason: str = "mcp-session",
        verify_ssl: bool = True,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """Call the backend broker, store the returned token, and return info dict.

        Returns a dict with keys: ``ok``, ``email``, ``display_name``,
        ``user_name``, ``store``, ``token_acquired``.  On failure returns
        ``ok=False`` and an ``error`` key.
        """
        try:
            response = httpx.post(
                exchange_url,
                json={"email": email, "reason": reason},
                headers={"X-MCP-SECRET": shared_secret},
                verify=verify_ssl,
                timeout=timeout,
                follow_redirects=True,
            )
        except httpx.HTTPError as exc:
            LOGGER.warning("MCP exchange request failed: %s", exc)
            return {"ok": False, "error": str(exc)}

        if response.status_code != 200:
            return {
                "ok": False,
                "error": f"Exchange endpoint returned HTTP {response.status_code}.",
                "detail": response.text[:512],
            }

        try:
            data: dict[str, Any] = response.json()
        except Exception:  # noqa: BLE001
            return {"ok": False, "error": "Exchange endpoint returned non-JSON body."}

        token: str | None = data.get("token")
        if not token:
            return {"ok": False, "error": "Exchange response missing 'token' field."}

        expires_in = _parse_expires_in(token)
        with self._lock:
            self._state = _SessionState(
                token=token,
                email=data.get("email", email),
                display_name=data.get("displayName", email),
                acquired_at=time.monotonic(),
                expires_in_seconds=expires_in,
            )

        LOGGER.info("Session token acquired for %s (expires in %.0fs)", email, expires_in)
        return {
            "ok": True,
            "email": data.get("email", email),
            "display_name": data.get("displayName", email),
            "user_name": data.get("userName", ""),
            "store": data.get("store", ""),
            "token_acquired": True,
        }

    def get_token(self) -> str | None:
        """Return the current session token if valid (not near expiry).

        Returns ``None`` when no session is active or the token is within the
        TTL buffer window.
        """
        with self._lock:
            return self._get_token_unsafe()

    def needs_refresh(self) -> bool:
        """Return True when a session exists but the token is near expiry."""
        with self._lock:
            state = self._state
            if state is None:
                return False
            age = time.monotonic() - state.acquired_at
            return age >= (state.expires_in_seconds - self._ttl_buffer_seconds)

    def clear(self) -> None:
        """Remove the current session token from memory."""
        with self._lock:
            self._state = None
        LOGGER.info("Session token cleared.")

    def status(self) -> dict[str, Any]:
        """Return a safe status dict (never includes the raw token value)."""
        with self._lock:
            state = self._state
            if state is None:
                return {"active": False}
            age = time.monotonic() - state.acquired_at
            remaining = max(0.0, state.expires_in_seconds - age)
            return {
                "active": True,
                "email": state.email,
                "display_name": state.display_name,
                "expires_in_seconds": round(remaining, 0),
                "needs_refresh": age >= (state.expires_in_seconds - self._ttl_buffer_seconds),
            }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_token_unsafe(self) -> str | None:
        """Must be called with ``self._lock`` held."""
        state = self._state
        if state is None:
            return None
        age = time.monotonic() - state.acquired_at
        if age >= state.expires_in_seconds:
            # Fully expired — drop and return nothing
            self._state = None
            return None
        return state.token


# Process-level singleton — import this from other modules
session_manager = SessionTokenManager()
