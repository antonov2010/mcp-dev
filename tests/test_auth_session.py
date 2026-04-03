"""Unit tests for SessionTokenManager.

Covers:
- get_token() returns None when no session is active
- acquire() stores the token and returns ok=True
- get_token() returns the stored token before expiry
- get_token() returns None (and clears state) after expiry
- needs_refresh() fires when within the TTL buffer
- clear() wipes the state
- acquire() with HTTP error returns ok=False
- acquire() with non-200 status returns ok=False
- acquire() with missing token field returns ok=False
"""
from __future__ import annotations

import base64
import json
import time
import unittest
from unittest.mock import MagicMock, patch

from workbench_mcp.auth.session import SessionTokenManager, _parse_expires_in


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_jwt(exp_offset: float) -> str:
    """Build a minimal JWT with an exp claim offset from now."""
    header = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').rstrip(b"=").decode()
    payload_dict = {"sub": "test@example.com", "exp": int(time.time()) + int(exp_offset)}
    payload = (
        base64.urlsafe_b64encode(json.dumps(payload_dict).encode()).rstrip(b"=").decode()
    )
    return f"{header}.{payload}.fakesig"


def _make_response(
    status_code: int = 200,
    *,
    token: str | None = None,
    email: str = "test@example.com",
    display_name: str = "Test User",
    user_name: str = "testuser",
    store: str = "Main",
) -> MagicMock:
    """Return a mock httpx Response."""
    resp = MagicMock()
    resp.status_code = status_code
    if status_code == 200:
        body: dict = {
            "email": email,
            "displayName": display_name,
            "userName": user_name,
            "store": store,
            "token": token or _make_jwt(3600),
            "impersonated": True,
        }
        resp.json.return_value = body
    else:
        resp.text = "unauthorized"
        resp.json.side_effect = ValueError("not json")
    return resp


# ---------------------------------------------------------------------------
# _parse_expires_in tests
# ---------------------------------------------------------------------------


class ParseExpiresInTests(unittest.TestCase):
    def test_returns_remaining_seconds_from_exp_claim(self) -> None:
        offset = 3600
        token = _make_jwt(offset)
        remaining = _parse_expires_in(token)
        # Allow ±5 s for test execution time
        self.assertAlmostEqual(remaining, offset, delta=5)

    def test_returns_fallback_for_malformed_token(self) -> None:
        self.assertEqual(_parse_expires_in("not.a.token"), 14400.0)

    def test_returns_fallback_for_missing_exp(self) -> None:
        header = base64.urlsafe_b64encode(b'{"alg":"HS256"}').rstrip(b"=").decode()
        payload = base64.urlsafe_b64encode(b'{"sub":"x"}').rstrip(b"=").decode()
        token = f"{header}.{payload}.sig"
        self.assertEqual(_parse_expires_in(token), 14400.0)


# ---------------------------------------------------------------------------
# SessionTokenManager tests
# ---------------------------------------------------------------------------


class SessionTokenManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manager = SessionTokenManager(ttl_buffer_seconds=60)

    # -- initial state -------------------------------------------------------

    def test_get_token_returns_none_when_no_session(self) -> None:
        self.assertIsNone(self.manager.get_token())

    def test_status_is_inactive_when_no_session(self) -> None:
        status = self.manager.status()
        self.assertFalse(status["active"])

    def test_needs_refresh_is_false_when_no_session(self) -> None:
        self.assertFalse(self.manager.needs_refresh())

    # -- successful acquire --------------------------------------------------

    def test_acquire_stores_token_and_returns_ok(self) -> None:
        token = _make_jwt(3600)
        resp = _make_response(token=token)

        with patch("workbench_mcp.auth.session.httpx.post", return_value=resp):
            result = self.manager.acquire(
                exchange_url="https://api.test/mcp/exchange",
                shared_secret="secret",
                email="test@example.com",
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["email"], "test@example.com")
        self.assertEqual(result["display_name"], "Test User")
        self.assertTrue(result["token_acquired"])

    def test_get_token_returns_stored_token_before_expiry(self) -> None:
        token = _make_jwt(3600)
        resp = _make_response(token=token)

        with patch("workbench_mcp.auth.session.httpx.post", return_value=resp):
            self.manager.acquire(
                exchange_url="https://api.test/mcp/exchange",
                shared_secret="secret",
                email="test@example.com",
            )

        self.assertEqual(self.manager.get_token(), token)

    def test_status_is_active_after_acquire(self) -> None:
        token = _make_jwt(3600)
        resp = _make_response(token=token)

        with patch("workbench_mcp.auth.session.httpx.post", return_value=resp):
            self.manager.acquire(
                exchange_url="https://api.test/mcp/exchange",
                shared_secret="secret",
                email="test@example.com",
            )

        status = self.manager.status()
        self.assertTrue(status["active"])
        self.assertEqual(status["email"], "test@example.com")
        self.assertGreater(status["expires_in_seconds"], 0)

    # -- expiry --------------------------------------------------------------

    def test_get_token_returns_none_after_expiry(self) -> None:
        # Use a token that expired 10 seconds ago
        token = _make_jwt(-10)
        resp = _make_response(token=token)

        with patch("workbench_mcp.auth.session.httpx.post", return_value=resp):
            self.manager.acquire(
                exchange_url="https://api.test/mcp/exchange",
                shared_secret="secret",
                email="test@example.com",
            )

        # Force monotonic age to simulate expiry
        self.manager._state.acquired_at -= 100_000  # type: ignore[union-attr]
        self.assertIsNone(self.manager.get_token())
        # State should be cleared
        self.assertIsNone(self.manager._state)

    # -- TTL buffer / needs_refresh ------------------------------------------

    def test_needs_refresh_fires_within_buffer(self) -> None:
        token = _make_jwt(3600)
        resp = _make_response(token=token)

        manager = SessionTokenManager(ttl_buffer_seconds=600)
        with patch("workbench_mcp.auth.session.httpx.post", return_value=resp):
            manager.acquire(
                exchange_url="https://api.test/mcp/exchange",
                shared_secret="secret",
                email="test@example.com",
            )

        # Make the token look like it's almost expired (within 600s buffer)
        manager._state.acquired_at -= 3000 + 1  # type: ignore[union-attr]
        self.assertTrue(manager.needs_refresh())

    def test_needs_refresh_false_with_ample_time(self) -> None:
        token = _make_jwt(3600)
        resp = _make_response(token=token)

        manager = SessionTokenManager(ttl_buffer_seconds=60)
        with patch("workbench_mcp.auth.session.httpx.post", return_value=resp):
            manager.acquire(
                exchange_url="https://api.test/mcp/exchange",
                shared_secret="secret",
                email="test@example.com",
            )

        self.assertFalse(manager.needs_refresh())

    # -- clear ---------------------------------------------------------------

    def test_clear_removes_session(self) -> None:
        token = _make_jwt(3600)
        resp = _make_response(token=token)

        with patch("workbench_mcp.auth.session.httpx.post", return_value=resp):
            self.manager.acquire(
                exchange_url="https://api.test/mcp/exchange",
                shared_secret="secret",
                email="test@example.com",
            )

        self.assertIsNotNone(self.manager.get_token())
        self.manager.clear()
        self.assertIsNone(self.manager.get_token())
        self.assertFalse(self.manager.status()["active"])

    # -- failure paths -------------------------------------------------------

    def test_acquire_returns_error_on_http_exception(self) -> None:
        import httpx as _httpx

        with patch(
            "workbench_mcp.auth.session.httpx.post",
            side_effect=_httpx.ConnectError("refused"),
        ):
            result = self.manager.acquire(
                exchange_url="https://api.test/mcp/exchange",
                shared_secret="secret",
                email="test@example.com",
            )

        self.assertFalse(result["ok"])
        self.assertIn("error", result)
        self.assertIsNone(self.manager.get_token())

    def test_acquire_returns_error_on_non_200_status(self) -> None:
        resp = _make_response(status_code=401)

        with patch("workbench_mcp.auth.session.httpx.post", return_value=resp):
            result = self.manager.acquire(
                exchange_url="https://api.test/mcp/exchange",
                shared_secret="secret",
                email="test@example.com",
            )

        self.assertFalse(result["ok"])
        self.assertIn("401", result["error"])
        self.assertIsNone(self.manager.get_token())

    def test_acquire_returns_error_when_token_missing(self) -> None:
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"email": "test@example.com"}  # no token field

        with patch("workbench_mcp.auth.session.httpx.post", return_value=resp):
            result = self.manager.acquire(
                exchange_url="https://api.test/mcp/exchange",
                shared_secret="secret",
                email="test@example.com",
            )

        self.assertFalse(result["ok"])
        self.assertIn("token", result["error"])


if __name__ == "__main__":
    unittest.main()
