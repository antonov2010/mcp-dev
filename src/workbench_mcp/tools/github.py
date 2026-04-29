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


def _branch_check(
    owner_arg: str,
    repo_arg: str,
    branch: str,
    base_url: str,
    headers: dict[str, str],
    verify_ssl: bool,
    timeout: float,
) -> dict[str, object]:
    """Check whether a branch exists and return diagnostics.

    Returns a dict with keys:
      - exists: bool
      - status_code: int | None
      - body: parsed response or text when available
      - error: error message for network errors
    """
    branch_url = f"{base_url.rstrip('/')}/repos/{owner_arg}/{repo_arg}/branches/{branch}"
    try:
        with httpx.Client(verify=verify_ssl, timeout=timeout) as client:
            resp = client.get(branch_url, headers=headers)
    except httpx.HTTPError as exc:
        return {"exists": False, "status_code": None, "body": None, "error": str(exc)}

    content_type = resp.headers.get("content-type", "")
    body: object
    if "application/json" in content_type.lower():
        try:
            body = resp.json()
        except json.JSONDecodeError:
            body = resp.text
    else:
        body = resp.text

    return {"exists": resp.status_code == 200, "status_code": resp.status_code, "body": body}


def parse_agent_branch(head: str) -> str | None:
    """Parse an agent-style branch name and return the epic-id.

    Expected format: `agents/{agent-name}/{epic-id}`. Returns the `epic-id`
    string on success or `None` when the pattern doesn't match.
    """
    if not head or not isinstance(head, str):
        return None
    parts = head.split("/")
    if len(parts) == 3 and parts[0] == "agents":
        return parts[2]
    return None


def _get_repo_default_branch(owner_arg: str, repo_arg: str, base_url: str, headers: dict[str, str], verify_ssl: bool, timeout: float) -> dict[str, object]:
    """Return diagnostics for the repository including `default_branch` when available.

    Returns a dict with keys: `ok` (bool), `default_branch` (str|None), `status_code`, `body`.
    """
    repo_url = f"{base_url.rstrip('/')}/repos/{owner_arg}/{repo_arg}"
    try:
        with httpx.Client(verify=verify_ssl, timeout=timeout) as client:
            resp = client.get(repo_url, headers=headers)
    except httpx.HTTPError as exc:
        return {"ok": False, "default_branch": None, "status_code": None, "body": None, "error": str(exc)}

    content_type = resp.headers.get("content-type", "")
    body: object
    if "application/json" in content_type.lower():
        try:
            body = resp.json()
        except json.JSONDecodeError:
            body = resp.text
    else:
        body = resp.text

    default_branch = None
    if isinstance(body, dict):
        default_branch = body.get("default_branch")

    return {"ok": resp.status_code == 200, "default_branch": default_branch, "status_code": resp.status_code, "body": body}


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
    base: str | None,
    title: str,
    body: str | None = None,
    draft: bool = False,
    maintainer_can_modify: bool = True,
    github_token: str | None = None,
    github_api_base: str | None = None,
    verify_ssl: bool = True,
    timeout: float = 30.0,
    derive_base_from_head: bool = False,
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
    headers["X-GitHub-Api-Version"] = "2026-03-10"
    if token:
        headers["Authorization"] = f"Bearer {token}"

    # Pre-validate branches and optionally derive base from head
    # head may be "user:branch" for forks — detect that and split accordingly.
    head_owner = owner
    head_branch = head
    if ":" in head:
        head_owner, head_branch = head.split(":", 1)

    chosen_base: str | None = base

    # Determine whether we should derive the base. Priority:
    # 1. explicit `derive_base_from_head` parameter (True/False)
    # 2. settings.github_derive_base_default when parameter is False — in this
    #    case we only derive when head/base appear to mismatch or base is missing.
    settings_default = bool(get_settings().github_derive_base_default)
    effective_derive = bool(derive_base_from_head)
    if not effective_derive and settings_default:
        # conditional derive: only when caller provided base doesn't match the epic inferred
        inferred = parse_agent_branch(head_branch)
        if inferred:
            # derive when no base provided or provided base differs from inferred epic-id
            if not base or base.strip() == "" or base.strip() != inferred:
                effective_derive = True
        else:
            # no inference possible; derive only if base is missing
            if not base or base.strip() == "":
                effective_derive = True

    if effective_derive:
        derived = parse_agent_branch(head_branch)
        if derived:
            chosen_base = derived
        else:
            # Fallback order when derivation fails: provided `base` -> configured fallback -> 'develop' -> repo default
            if base and base.strip():
                chosen_base = base
            else:
                # first check configured fallback from settings
                cfg_fallback = get_settings().github_fallback_base
                if cfg_fallback:
                    fb_check = _branch_check(owner, repo_name, cfg_fallback, base_url, headers, verify_ssl, timeout)
                    if fb_check.get("exists"):
                        chosen_base = cfg_fallback
                    elif fb_check.get("status_code") == 403:
                        return {
                            "ok": False,
                            "status_code": 403,
                            "error": (
                                f"Permission denied when checking configured fallback base '{cfg_fallback}' in {owner}/{repo_name}."
                            ),
                            "details": fb_check.get("body"),
                        }
                if not chosen_base:
                    # check whether 'develop' exists
                    dev_check = _branch_check(owner, repo_name, "develop", base_url, headers, verify_ssl, timeout)
                    if dev_check.get("exists"):
                        chosen_base = "develop"
                    else:
                        # try to get repository default branch
                        repo_diag = _get_repo_default_branch(owner, repo_name, base_url, headers, verify_ssl, timeout)
                        if repo_diag.get("default_branch"):
                            chosen_base = repo_diag.get("default_branch")  # type: ignore[assignment]
                        else:
                            # no derived base and no fallbacks available
                            return {
                                "ok": False,
                                "status_code": 400,
                                "error": (
                                    "Could not derive base from head, no `base` provided, configured fallback and 'develop' missing, "
                                    "and repository default branch could not be determined."
                                ),
                                "details": repo_diag.get("body"),
                            }

    # validate chosen_base exists before proceeding
    if not chosen_base or not isinstance(chosen_base, str) or not chosen_base.strip():
        return {"ok": False, "status_code": 400, "error": "No base branch specified."}

    base_check = _branch_check(owner, repo_name, chosen_base, base_url, headers, verify_ssl, timeout)
    if base_check.get("status_code") == 403:
        return {
            "ok": False,
            "status_code": 403,
            "error": f"Permission denied when checking base branch '{chosen_base}' in {owner}/{repo_name}.",
            "details": base_check.get("body"),
        }
    if not base_check.get("exists"):
        return {
            "ok": False,
            "error": f"Base branch '{chosen_base}' not found in {owner}/{repo_name}.",
            "status_code": 404,
            "details": base_check.get("body"),
        }

    # now validate head existence (as before)
    head_check = _branch_check(head_owner, repo_name, head_branch, base_url, headers, verify_ssl, timeout)
    if head_check.get("status_code") == 403:
        return {
            "ok": False,
            "status_code": 403,
            "error": (
                f"Permission denied when checking head branch '{head_branch}' in {head_owner}/{repo_name}."
            ),
            "details": head_check.get("body"),
        }

    if not head_check.get("exists"):
        # If head specifies a different owner (fork) but repo_name differs, try head owner/repo
        if head_owner != owner:
            alt_head_check = _branch_check(head_owner, repo_name, head_branch, base_url, headers, verify_ssl, timeout)
            if alt_head_check.get("exists"):
                head_check = alt_head_check
            else:
                return {
                    "ok": False,
                    "error": (
                        f"Head branch '{head_branch}' not found in {head_owner}/{repo_name}. "
                        "When creating a PR from a fork, pass 'owner:branch' as head."
                    ),
                    "status_code": 404,
                    "details": head_check.get("body"),
                }
        else:
            return {
                "ok": False,
                "error": (
                    f"Head branch '{head_branch}' not found in {head_owner}/{repo_name}. "
                    "When creating a PR from a fork, pass 'owner:branch' as head."
                ),
                "status_code": 404,
                "details": head_check.get("body"),
            }

    payload: dict[str, Any] = {
        "title": title,
        "head": head,
        "base": chosen_base,
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

    # Provide clearer diagnostics for validation errors (422) by surfacing the
    # 'errors' array when available.
    error_payload: Any = parsed
    if isinstance(parsed, dict) and "errors" in parsed:
        # Normalize to a concise message + full details
        details = parsed.get("errors")
        message = parsed.get("message", "Validation Failed")
        return {
            "ok": False,
            "status_code": response.status_code,
            "error": message,
            "errors": details,
            "documentation_url": parsed.get("documentation_url"),
        }

    return {"ok": False, "status_code": response.status_code, "error": error_payload}


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
