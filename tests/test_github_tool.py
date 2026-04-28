import unittest
from unittest.mock import patch

from workbench_mcp.tools.github import create_pull_request
from workbench_mcp.config import get_settings
from pydantic import SecretStr


class _DummyResponse:
    def __init__(self, status_code: int = 201) -> None:
        self.content = b'{"number": 12, "title": "My PR"}'
        self.headers = {"content-type": "application/json"}
        self.encoding = "utf-8"
        self.status_code = status_code

    def json(self):
        return {"number": 12, "title": "My PR"}


class _DummyClient:
    last_request = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None

    def post(self, url: str, headers: dict, json: dict):
        type(self).last_request = {"url": url, "headers": headers, "json": json}
        return _DummyResponse()


def _build_settings():
    return get_settings().__class__(
        db_host="localhost",
        db_name="app_dev",
        db_user="app_user",
        db_password=SecretStr("db-pass"),
        api_bearer_token=SecretStr("env-token"),
        api_user_timezone="America/Chicago",
    )


class GitHubToolTests(unittest.TestCase):
    def test_invalid_repo_format(self):
        result = create_pull_request(repo="not-a-repo", head="feature", base="main", title="x")
        self.assertFalse(result["ok"])

    def test_create_pull_request_success(self):
        with patch("workbench_mcp.tools.github.get_settings", return_value=_build_settings()):
            with patch("workbench_mcp.tools.github.httpx.Client", _DummyClient):
                result = create_pull_request(
                    repo="owner/repo",
                    head="feature-branch",
                    base="main",
                    title="Add feature",
                    body="Details",
                )

        self.assertTrue(result["ok"])
        self.assertIn("pull_request", result)


if __name__ == "__main__":
    unittest.main()
