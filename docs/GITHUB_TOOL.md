# GitHub Tool (workbench_mcp)

This document describes the GitHub integration added to the workbench MCP. It provides an MCP tool to create pull requests programmatically.

Configuration
- `GITHUB_API_BASE_URL` (optional): Base URL for the GitHub REST API. Defaults to `https://api.github.com`.
- `GITHUB_TOKEN` (optional): Personal access token used when making API requests. If not provided, the MCP session token is used when available.

Tool
- `github_create_pull_request(repo, head, base, title, body=None, draft=False, maintainer_can_modify=True, github_token=None, github_api_base=None)`
 - `github_create_pull_request(repo, head, base, title, body=None, draft=False, maintainer_can_modify=True, github_token=None, github_api_base=None, derive_base_from_head=False)`
  - `repo`: required, must be in `owner/repo` format.
  - `head`: branch name containing changes.
  - `base`: target branch to merge into (optional when `derive_base_from_head=True`).
  - `title`: PR title.
  - `body`: optional PR body/description.
  - `draft`: boolean, create PR as draft when True.
  - `github_token`: optional call-specific token to override configured token/session.

Behavior when `derive_base_from_head=True`:
- The tool will attempt to parse `head` as an agent-style branch in the form `agents/{agent-name}/{epic-id}` and use the `epic-id` as the derived base branch.
- Fallback order when derivation fails: use the provided `base` (if non-empty) → use configured `GITHUB_FALLBACK_BASE` (if set and exists) → use `develop` if it exists in the repo → use the repository's `default_branch` discovered via the GitHub API. If none can be determined, the tool returns a 400 with diagnostics.
- If the derived or chosen base branch cannot be found or is inaccessible (403), the tool returns a clear error with `status_code` and `details` so the calling agent can react (create the branch, request permissions, or choose a different base).

Behavior when `GITHUB_DERIVE_BASE_FROM_HEAD_DEFAULT=true` (settings-driven default):
- The MCP will not unconditionally override caller-provided `base`. Instead, it will only attempt to derive the base when the call's provided information does not match the head:
  - If `base` is missing, the MCP will attempt derivation.
  - If `base` is present and equals the epic-id parsed from `head`, no derivation occurs.
  - If `base` is present and differs from the epic-id parsed from `head`, the MCP will derive (override) the base to the epic-id so agents with mismatched inputs are corrected automatically.
- All derivation flows still follow the same fallback order described above when derivation itself fails.

Notes
- The tool will prefer a provided `github_token`, then the active MCP session token, then the configured `GITHUB_TOKEN`.
- For more advanced reviewer/request flows the agent can call the GitHub REST API using the `http_post`/`http_put` tools directly; this helper provides a focused convenience for creating PRs.
