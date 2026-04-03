import unittest
from unittest.mock import MagicMock, patch

from pydantic import SecretStr

from workbench_mcp.auth.session import SessionTokenManager
from workbench_mcp.config import Settings
from workbench_mcp.tools.http_requests import _execute_http_request


class _DummyResponse:
    def __init__(self) -> None:
        self.content = b'{"ok": true}'
        self.headers = {"content-type": "application/json"}
        self.encoding = "utf-8"
        self.status_code = 200


class _DummyClient:
    last_request: dict[str, object] | None = None

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs

    def __enter__(self) -> "_DummyClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def request(self, method: str, url: str, headers: dict[str, str], **kwargs: object) -> _DummyResponse:
        type(self).last_request = {
            "method": method,
            "url": url,
            "headers": headers,
            "kwargs": kwargs,
        }
        return _DummyResponse()


def _build_settings() -> Settings:
    return Settings(
        db_host="localhost",
        db_name="app_dev",
        db_user="app_user",
        db_password=SecretStr("db-pass"),
        api_bearer_token=SecretStr("env-token"),
        api_user_timezone="America/Chicago",
    )


class HttpAuthorizationHandlingTests(unittest.TestCase):
    def setUp(self) -> None:
        _DummyClient.last_request = None

    def test_uses_environment_token_when_override_is_missing(self) -> None:
        with patch("workbench_mcp.tools.http_requests.get_settings", return_value=_build_settings()):
            with patch("workbench_mcp.tools.http_requests.httpx.Client", _DummyClient):
                result = _execute_http_request(
                    method="GET",
                    url="https://example.com/api/items",
                    headers={"Authorization": "Bearer ignored-header-token"},
                )

        self.assertTrue(result["ok"])
        headers = _DummyClient.last_request["headers"]
        self.assertEqual(headers["Authorization"], "Bearer env-token")
        self.assertEqual(headers["x-user-timezone"], "America/Chicago")

    def test_uses_per_call_token_over_environment_token(self) -> None:
        with patch("workbench_mcp.tools.http_requests.get_settings", return_value=_build_settings()):
            with patch("workbench_mcp.tools.http_requests.httpx.Client", _DummyClient):
                result = _execute_http_request(
                    method="GET",
                    url="https://example.com/api/items",
                    jwt_token="agent-specific-token",
                )

        self.assertTrue(result["ok"])
        headers = _DummyClient.last_request["headers"]
        self.assertEqual(headers["Authorization"], "Bearer agent-specific-token")

    def test_accepts_bearer_prefixed_per_call_token(self) -> None:
        with patch("workbench_mcp.tools.http_requests.get_settings", return_value=_build_settings()):
            with patch("workbench_mcp.tools.http_requests.httpx.Client", _DummyClient):
                result = _execute_http_request(
                    method="GET",
                    url="https://example.com/api/items",
                    jwt_token="Bearer caller-token",
                )

        self.assertTrue(result["ok"])
        headers = _DummyClient.last_request["headers"]
        self.assertEqual(headers["Authorization"], "Bearer caller-token")

    def test_blank_per_call_token_falls_back_to_environment_token(self) -> None:
        with patch("workbench_mcp.tools.http_requests.get_settings", return_value=_build_settings()):
            with patch("workbench_mcp.tools.http_requests.httpx.Client", _DummyClient):
                result = _execute_http_request(
                    method="GET",
                    url="https://example.com/api/items",
                    jwt_token="   ",
                )

        self.assertTrue(result["ok"])
        headers = _DummyClient.last_request["headers"]
        self.assertEqual(headers["Authorization"], "Bearer env-token")


# ---------------------------------------------------------------------------
# Session token precedence tests
# ---------------------------------------------------------------------------


def _make_active_manager(token: str = "session-jwt") -> MagicMock:
    """Return a mock SessionTokenManager that always returns *token*."""
    mgr = MagicMock(spec=SessionTokenManager)
    mgr.get_token.return_value = token
    return mgr


def _make_inactive_manager() -> MagicMock:
    """Return a mock SessionTokenManager with no active session."""
    mgr = MagicMock(spec=SessionTokenManager)
    mgr.get_token.return_value = None
    return mgr


class HttpSessionTokenPrecedenceTests(unittest.TestCase):
    def setUp(self) -> None:
        _DummyClient.last_request = None

    def test_session_token_used_when_no_per_call_token(self) -> None:
        """Session token is injected when no explicit jwt_token is provided."""
        active_mgr = _make_active_manager("session-jwt-abc")

        with patch("workbench_mcp.tools.http_requests.get_settings", return_value=_build_settings()):
            with patch("workbench_mcp.tools.http_requests.session_manager", active_mgr):
                with patch("workbench_mcp.tools.http_requests.httpx.Client", _DummyClient):
                    result = _execute_http_request(
                        method="GET",
                        url="https://example.com/api/items",
                    )

        self.assertTrue(result["ok"])
        headers = _DummyClient.last_request["headers"]
        self.assertEqual(headers["Authorization"], "Bearer session-jwt-abc")

    def test_per_call_token_overrides_session_token(self) -> None:
        """Explicit jwt_token beats the session token."""
        active_mgr = _make_active_manager("session-jwt-xyz")

        with patch("workbench_mcp.tools.http_requests.get_settings", return_value=_build_settings()):
            with patch("workbench_mcp.tools.http_requests.session_manager", active_mgr):
                with patch("workbench_mcp.tools.http_requests.httpx.Client", _DummyClient):
                    result = _execute_http_request(
                        method="GET",
                        url="https://example.com/api/items",
                        jwt_token="per-call-override",
                    )

        self.assertTrue(result["ok"])
        headers = _DummyClient.last_request["headers"]
        self.assertEqual(headers["Authorization"], "Bearer per-call-override")
        # session manager was never consulted for the token
        active_mgr.get_token.assert_not_called()

    def test_env_token_used_when_session_is_inactive(self) -> None:
        """Falls back to API_BEARER_TOKEN when session is inactive."""
        inactive_mgr = _make_inactive_manager()

        with patch("workbench_mcp.tools.http_requests.get_settings", return_value=_build_settings()):
            with patch("workbench_mcp.tools.http_requests.session_manager", inactive_mgr):
                with patch("workbench_mcp.tools.http_requests.httpx.Client", _DummyClient):
                    result = _execute_http_request(
                        method="GET",
                        url="https://example.com/api/items",
                    )

        self.assertTrue(result["ok"])
        headers = _DummyClient.last_request["headers"]
        self.assertEqual(headers["Authorization"], "Bearer env-token")


if __name__ == "__main__":
    unittest.main()