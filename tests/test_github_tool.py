import unittest
from unittest.mock import patch

from workbench_mcp.tools.github import (
    create_pull_request,
    list_pr_comments,
    create_pr_comment,
    update_pr_comment,
)
from workbench_mcp.config import get_settings
from pydantic import SecretStr


class _DummyResponse:
    def __init__(self, status_code: int = 201, json_data=None, headers=None) -> None:
        self.content = b"{}"
        self.headers = headers or {"content-type": "application/json"}
        self.encoding = "utf-8"
        self.status_code = status_code
        self._json_data = json_data

    def json(self):
        return self._json_data or {}


class _DummyClient:
    last_request = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None

    def post(self, url: str, headers: dict, json: dict):
        type(self).last_request = {"url": url, "headers": headers, "json": json, "method": "POST"}
        return _DummyResponse()

    def get(self, url: str, headers: dict, params: dict = None):
        type(self).last_request = {"url": url, "headers": headers, "params": params, "method": "GET"}
        return _DummyResponse(status_code=200, json_data=[{"id": 1, "body": "Nice!"}])

    def patch(self, url: str, headers: dict, json: dict):
        type(self).last_request = {"url": url, "headers": headers, "json": json, "method": "PATCH"}
        return _DummyResponse(status_code=200, json_data={"id": 1, "body": json.get("body")})


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

    def test_list_pr_comments_invalid_repo(self):
        result = list_pr_comments(repo="bad-repo", pull_number=1)
        self.assertFalse(result["ok"])

    def test_list_pr_comments_success(self):
        with patch("workbench_mcp.tools.github.get_settings", return_value=_build_settings()):
            with patch("workbench_mcp.tools.github.httpx.Client", _DummyClient):
                result = list_pr_comments(repo="owner/repo", pull_number=42)
        self.assertTrue(result["ok"])
        self.assertEqual(result["status_code"], 200)
        self.assertIn("comments", result)
        req = _DummyClient.last_request
        self.assertEqual(req["method"], "GET")
        self.assertIn("pulls/42/comments", req["url"])

    def test_create_pr_comment_invalid_repo(self):
        result = create_pr_comment(
            repo="bad-repo", pull_number=1, body="ok", commit_id="abc", path="f.txt", line=1
        )
        self.assertFalse(result["ok"])

    def test_create_pr_comment_success(self):
        with patch("workbench_mcp.tools.github.get_settings", return_value=_build_settings()):
            with patch("workbench_mcp.tools.github.httpx.Client", _DummyClient):
                result = create_pr_comment(
                    repo="owner/repo",
                    pull_number=42,
                    body="Looks good",
                    commit_id="abc123",
                    path="src/main.py",
                    line=10,
                    side="RIGHT",
                )
        self.assertTrue(result["ok"])
        self.assertEqual(result["status_code"], 201)
        req = _DummyClient.last_request
        self.assertEqual(req["method"], "POST")
        self.assertEqual(req["json"]["body"], "Looks good")
        self.assertEqual(req["json"]["line"], 10)

    def test_update_pr_comment_invalid_repo(self):
        result = update_pr_comment(repo="bad-repo", comment_id=1, body="fixed")
        self.assertFalse(result["ok"])

    def test_update_pr_comment_success(self):
        with patch("workbench_mcp.tools.github.get_settings", return_value=_build_settings()):
            with patch("workbench_mcp.tools.github.httpx.Client", _DummyClient):
                result = update_pr_comment(repo="owner/repo", comment_id=99, body="Resolved")
        self.assertTrue(result["ok"])
        self.assertEqual(result["status_code"], 200)
        req = _DummyClient.last_request
        self.assertEqual(req["method"], "PATCH")
        self.assertEqual(req["json"]["body"], "Resolved")
        self.assertIn("pulls/comments/99", req["url"])


if __name__ == "__main__":
    unittest.main()
