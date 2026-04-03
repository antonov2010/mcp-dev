# Session Auth — Agent Reference

This document describes how the `workbench-mcp` session authentication system works and when to use each tool.

---

## Overview

Session auth lets an agent acquire a JWT once at the start of a task and have every subsequent HTTP tool call use it automatically — without having to pass `jwt_token` on every call.

The MCP server exchanges a shared secret + target email for a scoped JWT from the backend broker. The token is held in process memory and automatically used by the HTTP tools until it expires or is cleared.

---

## Token Precedence (highest → lowest)

| Priority | Source | When to use |
|----------|--------|-------------|
| 1 | `jwt_token` passed in the tool call | One-off override, debugging, cross-account calls |
| 2 | Session token (set via `auth_start_session`) | Standard agent workflow |
| 3 | `API_BEARER_TOKEN` env var | Shared / background processes |

---

## Required Configuration

| Env Variable | Description |
|---|---|
| `MCP_EXCHANGE_URL` | Full URL of the broker endpoint, e.g. `https://host/api/v1/mcp/exchange` |
| `MCP_SHARED_SECRET` | Shared secret sent in `X-MCP-SECRET` header |
| `MCP_TOKEN_TTL_BUFFER_SECONDS` | Seconds before expiry to consider the token stale (default `60`) |

Set these in `.env` or inject them via `.vscode/mcp.json` → `env`.

---

## Tools

### `auth_start_session`

Acquire a session-scoped JWT for a specific user.

```json
{
  "email": "jdoe@example.com",
  "reason": "Investigating low sales volume for store 7"
}
```

**Returns:**
```json
{
  "ok": true,
  "email": "jdoe@example.com",
  "display_name": "John Doe",
  "user_name": "jdoe",
  "store": "Main",
  "token_acquired": true
}
```

**Common error cases:**
- `MCP_EXCHANGE_URL` or `MCP_SHARED_SECRET` not configured → `ok: false` with a descriptive `error`.
- Backend returns non-200 → `ok: false` with the HTTP status code.
- Network error → `ok: false` with the exception message.

---

### `auth_switch_user`

Switch the active session to a different user. Semantically equivalent to `auth_start_session` — use it when the intent is a user change rather than a fresh start.

```json
{
  "email": "asmith@example.com",
  "reason": "Switching to store manager for region 3 review"
}
```

Returns the same shape as `auth_start_session`.

---

### `auth_status`

Inspect the current session without exposing the raw token.

```json
{}
```

**Returns (active session):**
```json
{
  "active": true,
  "email": "jdoe@example.com",
  "display_name": "John Doe",
  "expires_in_seconds": 3540,
  "needs_refresh": false
}
```

**Returns (no session):**
```json
{
  "active": false
}
```

When `needs_refresh` is `true` the token is still usable but will expire within the TTL buffer window. Call `auth_start_session` again to refresh.

---

### `auth_clear_session`

Remove the session token from memory.

```json
{}
```

**Returns:**
```json
{
  "ok": true,
  "message": "Session cleared."
}
```

After clearing, HTTP tools fall back to `API_BEARER_TOKEN` (or make unauthenticated requests if that is also absent).

---

## Recommended Agent Workflow

```
1. auth_start_session(email="user@domain.com", reason="describe task")
2. — perform HTTP tool calls — (no jwt_token needed)
3. auth_status()          ← optional: check if refresh is needed
4. auth_clear_session()   ← optional: good hygiene at end of task
```

---

## Security Notes

- The session token is stored **in process memory only** — it is never written to disk.
- The token is dropped automatically when it expires (`get_token()` returns `None`).
- The `MCP_SHARED_SECRET` must be treated as a sensitive credential; never commit it or log it.
- The backend broker records the `email` and `reason` in JWT claims for audit purposes.
- The raw token is never returned by any MCP tool — use per-call `jwt_token` if you need raw token access in a specific call.

---

## Related

- [AUTHORIZATION_HANDLING.md](AUTHORIZATION_HANDLING.md) — per-call `jwt_token` reference
- Backend broker: `POST /api/v1/mcp/exchange` in `salestracker/API/Controllers/McpController.cs`
