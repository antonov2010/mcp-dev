"""GitHub integration tools for creating pull requests and related actions.

This module provides a small, testable helper to create pull requests via the
GitHub REST API and registers an MCP tool wrapper for agent usage.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

from workbench_mcp.config import get_settings
from workbench_mcp.auth.session import session_manager

LOGGER = logging.getLogger(__name__)


def _normalize_token(token: str | None) -> str | None:
    if token is None:
        return None
    normalized = token.strip()
    if not normalized:
        return None
    if normalized[:7].lower() == "bearer ":
        normalized = normalized[7:].strip()
    return normalized or None


def create_pull_request(
    repo: str,
    head: str,
    base: str,
    title: str,
    body: str | None = None,
    draft: bool = False,
    maintainer_can_modify: bool = True,
    github_token: str | None = None,
    github_api_base: str | None = None,
    verify_ssl: bool = True,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Create a GitHub pull request.

    Parameters mirror the GitHub REST API. `repo` must be "owner/repo".
    The `github_token` parameter may be provided to override environment
    configuration; otherwise session token or configured token is used.
    """
    settings = get_settings()

    if not repo or "/" not in repo:
        return {"ok": False, "error": "repo must be in 'owner/repo' format."}

    base_url = (github_api_base or settings.github_api_base_url or "").strip()
    if not base_url:
        return {"ok": False, "error": "GitHub API base URL is not configured."}

    owner, repo_name = repo.split("/", 1)
    url = f"{base_url.rstrip('/')}/repos/{owner}/{repo_name}/pulls"

    token = _normalize_token(github_token)
    if token is None:
        token = _normalize_token(session_manager.get_token())
    if token is None and settings.github_token:
        token = settings.github_token.get_secret_value()  # type: ignore[union-attr]

    headers: dict[str, str] = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    payload: dict[str, Any] = {
        "title": title,
        "head": head,
        "base": base,
        "draft": draft,
        "maintainer_can_modify": maintainer_can_modify,
    }
    if body is not None:
        payload["body"] = body

    try:
        with httpx.Client(verify=verify_ssl, timeout=timeout) as client:
            response = client.post(url, headers=headers, json=payload)
    except httpx.HTTPError as exc:
        LOGGER.warning("GitHub PR request failed: %s", exc)
        return {"ok": False, "error": str(exc), "url": url}

    content_type = response.headers.get("content-type", "")
    parsed: Any
    if "application/json" in content_type.lower():
        try:
            parsed = response.json()
        except json.JSONDecodeError:
            parsed = response.text
    else:
        parsed = response.text

    if 200 <= response.status_code < 300:
        return {"ok": True, "status_code": response.status_code, "pull_request": parsed}
    return {"ok": False, "status_code": response.status_code, "error": parsed}


def register_github_tools(mcp: FastMCP) -> None:
    """Register GitHub-related MCP tools."""

    @mcp.tool()
    def github_create_pull_request(
        repo: str,
        head: str,
        base: str,
        title: str,
        body: str | None = None,
        draft: bool = False,
        maintainer_can_modify: bool = True,
        github_token: str | None = None,
        github_api_base: str | None = None,
    ) -> dict[str, Any]:
        """Create a pull request on GitHub.

        Uses configured `GITHUB_TOKEN` when available. Prefer passing a token
        explicitly for transient agent-based requests.
        """
        settings = get_settings()
        return create_pull_request(
            repo=repo,
            head=head,
            base=base,
            title=title,
            body=body,
            draft=draft,
            maintainer_can_modify=maintainer_can_modify,
            github_token=github_token,
            github_api_base=(github_api_base or settings.github_api_base_url),
            verify_ssl=settings.api_verify_ssl,
            timeout=settings.api_timeout_seconds,
        )
