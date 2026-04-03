"""MCP tools for managing a session-scoped JWT.

These tools let an agent acquire, inspect, switch, and clear the in-process
JWT that is automatically forwarded on every subsequent HTTP tool call.

Token precedence (highest → lowest):
  1. ``jwt_token`` passed explicitly in an HTTP tool call.
  2. Session token held here (set via ``auth_start_session``).
  3. ``API_BEARER_TOKEN`` environment variable.
"""
from __future__ import annotations

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from workbench_mcp.auth.session import session_manager
from workbench_mcp.config import get_settings

LOGGER = logging.getLogger(__name__)


def _exchange_config_ok(settings: Any) -> tuple[bool, str]:
    """Return (ok, error_message) based on whether exchange config is present."""
    if not settings.mcp_exchange_url:
        return False, (
            "MCP_EXCHANGE_URL is not configured. "
            "Set it in .env or the environment before starting a session."
        )
    if not settings.mcp_shared_secret:
        return False, (
            "MCP_SHARED_SECRET is not configured. "
            "Set it in .env or the environment before starting a session."
        )
    return True, ""


def register_auth_tools(mcp: FastMCP) -> None:
    """Register all session-auth MCP tools on *mcp*."""

    @mcp.tool()
    def auth_start_session(
        email: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Acquire a session-scoped JWT for *email* from the backend broker.

        After a successful call every HTTP tool call in this session will
        automatically use the returned token (unless the tool call provides
        its own ``jwt_token``).

        Parameters
        ----------
        email:
            The user whose identity the MCP session will impersonate.
        reason:
            Optional free-text description of why this session is needed.
            Stored in the JWT claims for audit purposes.

        Returns
        -------
        dict
            ``ok=True`` with ``email``, ``display_name``, ``user_name``,
            ``store`` on success; ``ok=False`` with ``error`` on failure.
        """
        settings = get_settings()
        config_ok, error = _exchange_config_ok(settings)
        if not config_ok:
            return {"ok": False, "error": error}

        return session_manager.acquire(
            exchange_url=settings.mcp_exchange_url,  # type: ignore[arg-type]
            shared_secret=settings.mcp_shared_secret.get_secret_value(),  # type: ignore[union-attr]
            email=email,
            reason=reason or "mcp-session",
            verify_ssl=settings.api_verify_ssl,
            timeout=settings.api_timeout_seconds,
        )

    @mcp.tool()
    def auth_switch_user(
        email: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Switch the active session to a different user.

        Equivalent to calling ``auth_start_session`` — provided as a semantic
        alias when the intent is to change the active user rather than start a
        fresh session.

        Parameters
        ----------
        email:
            The new user to impersonate.
        reason:
            Optional free-text description of why the switch is needed.

        Returns
        -------
        dict
            Same shape as ``auth_start_session``.
        """
        settings = get_settings()
        config_ok, error = _exchange_config_ok(settings)
        if not config_ok:
            return {"ok": False, "error": error}

        return session_manager.acquire(
            exchange_url=settings.mcp_exchange_url,  # type: ignore[arg-type]
            shared_secret=settings.mcp_shared_secret.get_secret_value(),  # type: ignore[union-attr]
            email=email,
            reason=reason or "mcp-switch-user",
            verify_ssl=settings.api_verify_ssl,
            timeout=settings.api_timeout_seconds,
        )

    @mcp.tool()
    def auth_status() -> dict[str, Any]:
        """Return the current session status without exposing the raw token.

        Returns
        -------
        dict
            ``active=False`` when no session is set; otherwise ``active=True``
            with ``email``, ``display_name``, ``expires_in_seconds``, and
            ``needs_refresh``.
        """
        return session_manager.status()

    @mcp.tool()
    def auth_clear_session() -> dict[str, Any]:
        """Clear the active session token from memory.

        After this call HTTP tools will fall back to ``API_BEARER_TOKEN`` (if
        configured) or make unauthenticated requests.

        Returns
        -------
        dict
            ``{"ok": True, "message": "Session cleared."}``.
        """
        session_manager.clear()
        return {"ok": True, "message": "Session cleared."}
