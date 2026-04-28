# GitHub Tool (workbench_mcp)

This document describes the GitHub integration added to the workbench MCP. It provides an MCP tool to create pull requests programmatically.

Configuration
- `GITHUB_API_BASE_URL` (optional): Base URL for the GitHub REST API. Defaults to `https://api.github.com`.
- `GITHUB_TOKEN` (optional): Personal access token used when making API requests. If not provided, the MCP session token is used when available.

Tool
- `github_create_pull_request(repo, head, base, title, body=None, draft=False, maintainer_can_modify=True, github_token=None, github_api_base=None)`
  - `repo`: required, must be in `owner/repo` format.
  - `head`: branch name containing changes.
  - `base`: target branch to merge into.
  - `title`: PR title.
  - `body`: optional PR body/description.
  - `draft`: boolean, create PR as draft when True.
  - `github_token`: optional call-specific token to override configured token/session.

Notes
- The tool will prefer a provided `github_token`, then the active MCP session token, then the configured `GITHUB_TOKEN`.
- For more advanced reviewer/request flows the agent can call the GitHub REST API using the `http_post`/`http_put` tools directly; this helper provides a focused convenience for creating PRs.
