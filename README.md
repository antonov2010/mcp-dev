# workbench-mcp

A local Python MCP server for interactive PostgreSQL data exploration, API integration, and automation on Fedora/Linux systems.

## Overview

Version 1 includes:

- Python virtual environment setup for Fedora/Linux systems
- PostgreSQL 18 connectivity configured via `.env` file
- MCP tools for:
  - Discovering tables, columns, and schema structure
  - Running read-only query previews
  - Executing guarded SQL batches with temporary table support
  - Calling PostgreSQL stored functions and procedures
  - Accessing external APIs via full URL requests
  - Executing bash scripts available in `PATH`
- Enforced safety: persistent schema and data modifications are blocked
- Session-scoped temporary table workflows supported within SQL batches

## Fedora / Linux Setup

Start by installing required system packages:

```bash
sudo dnf install -y python3 python3-pip nodejs npm
```

Python 3.12 or later is required. Use `pyenv` or similar if managing multiple versions.

## Virtual Environment Setup

From the project root, create and activate a Python virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e .
```

## Environment Variables

Copy the example configuration and populate PostgreSQL connection details:

```bash
cp .env.example .env
```

**Required:**

- `DB_HOST` — PostgreSQL server hostname
- `DB_NAME` — Database name
- `DB_USER` — Database username
- `DB_PASSWORD` — Database password

**Optional (tuning):**

- `DB_PORT` — Connection port (default: 5432)
- `DB_SSLMODE` — SSL mode (default: prefer)
- `DB_APPLICATION_NAME` — Application identifier
- `DB_QUERY_TIMEOUT_SECONDS` — Query timeout (default: 30)
- `DB_MAX_ROWS` — Maximum rows per result set (default: 100)
- `DB_MAX_RESULT_SETS` — Maximum result sets per batch (default: 5)
- `DB_OBJECT_PREVIEW_CHARS` — Max definition preview length (default: 4000)

**Example local development:**

```dotenv
DB_HOST=localhost
DB_PORT=5432
DB_NAME=app_dev
DB_USER=app_user
DB_PASSWORD=your-secure-password
DB_SSLMODE=prefer
```

### Optional: HTTP Request Tuning

The HTTP tool takes a full URL per call and does not require API profile configuration.

Supported environment settings:

| Variable | Purpose |
|----------|---------|
| `API_TIMEOUT_SECONDS` | HTTP request timeout |
| `API_MAX_RESPONSE_BYTES` | Max response bytes returned by HTTP tools |
| `API_VERIFY_SSL` | `true` / `false` SSL verification (local dev certs) |
| `API_BEARER_TOKEN` | Default JWT used when a tool call does not pass `jwt_token` |
| `API_USER_TIMEZONE` | Timezone header forwarded as `X-User-Timezone` |

Example call shape:

```text
url: https://localhost:44331/api/breakouts/filter/1871161/dd-table?ParameterSetId=231022
method: GET
```

For authenticated calls, set `API_BEARER_TOKEN` in `.env` (or process env). HTTP tools automatically use it unless the caller passes its own `jwt_token`.

## Authorization Handling

HTTP tools support two authorization sources:

1. `jwt_token` passed in the tool call
2. `API_BEARER_TOKEN` from `.env` or process environment

### Precedence

- If `jwt_token` is provided, that token is forwarded as `Authorization: Bearer <jwt_token>`.
- If `jwt_token` is omitted or blank, the server falls back to `API_BEARER_TOKEN`.
- If neither value is present, the request is sent without an `Authorization` header.

### Important rule for agents

Do **not** place the bearer token inside `headers.Authorization`.
The MCP server strips `Authorization` from `headers` and only accepts auth through the dedicated `jwt_token` field.

This prevents accidental header collisions and makes token precedence explicit.

### Example: use the default server token

```json
{
  "url": "https://localhost:5001/api/v1/sales/my-sales"
}
```

### Example: forward the caller's own token

```json
{
  "url": "https://localhost:5001/api/v1/sales/my-sales",
  "jwt_token": "eyJhbGciOi..."
}
```

### Example: forward caller token with extra headers

```json
{
  "url": "https://localhost:5001/api/v1/sales/my-sales",
  "jwt_token": "eyJhbGciOi...",
  "headers": {
    "Accept": "application/json"
  }
}
```

The same `jwt_token` field is available on `http_get`, `http_head`, `http_post`, `http_put`, `http_patch`, and `http_delete`.

## Session Auth

Instead of passing a per-call `jwt_token`, agents can acquire a session-scoped JWT once and have every HTTP tool call use it automatically for the rest of the session.

### How it works

1. An agent calls `auth_start_session` with the target user's email.
2. The MCP server exchanges the shared secret + email for a scoped JWT from the backend broker (`POST /api/v1/mcp/exchange`).
3. The token is cached in process memory.
4. Every subsequent HTTP tool call that omits `jwt_token` uses the session token automatically.
5. The agent can inspect the session with `auth_status`, switch users with `auth_switch_user`, or clear it with `auth_clear_session`.

### Token precedence (highest → lowest)

| Priority | Source |
|----------|--------|
| 1 | `jwt_token` passed in the tool call |
| 2 | Session token set by `auth_start_session` |
| 3 | `API_BEARER_TOKEN` environment variable |

### Required environment variables

| Variable | Purpose |
|----------|---------|
| `MCP_EXCHANGE_URL` | Full URL of the backend broker endpoint |
| `MCP_SHARED_SECRET` | Shared secret sent in `X-MCP-SECRET` header |
| `MCP_TOKEN_TTL_BUFFER_SECONDS` | Refresh when fewer than N seconds remain (default: 60) |

### Session auth tools

| Tool | Description |
|------|-------------|
| `auth_start_session` | Acquire a session token for the given email |
| `auth_switch_user` | Switch the active session to a different user (same as start) |
| `auth_status` | Inspect the current session (email, expiry, needs_refresh) |
| `auth_clear_session` | Clear the cached session token from memory |

See **[docs/SESSION_AUTH.md](docs/SESSION_AUTH.md)** for the full agent-facing reference.


## Run Locally

After activating the virtual environment and installing dependencies, start the MCP server with either command:

```bash
workbench-mcp
```

```bash
python -m workbench_mcp.server
```

## MCP Inspector

For local MCP development and debugging, the MCP Inspector provides a fast manual test loop:

```bash
npx @modelcontextprotocol/inspector .venv/bin/python -m workbench_mcp.server
```

To launch the MCP server under `debugpy` for breakpoint debugging in the Inspector:

```bash
npx @modelcontextprotocol/inspector .venv/bin/python -m debugpy --listen 127.0.0.1:5678 -m workbench_mcp.server
```

After launch, open the Inspector UI, connect over `STDIO`, and test tools such as `health`, `describe_object`, and `exec_proc_preview`.

**Breakpoints (debugpy):** Use port **5678** for the debugger, not 6274 (6274 is only the Inspector web UI). Step-by-step workflow and “what was wrong before” are in **[docs/DEBUG_MCP.md](docs/DEBUG_MCP.md)**.

## VS Code Setup

To register the local MCP server in VS Code, add an entry to the workspace MCP configuration file:

- Workspace file: `.vscode/mcp.json`

Example configuration:

```json
{
  "servers": {
    "workbench-mcp": {
      "type": "stdio",
      "command": "/absolute/path/to/workbench-mcp/.venv/bin/python",
      "args": ["-m", "workbench_mcp.server"]
    }
  }
}
```

Replace the command path with the local repository path to your virtual environment Python.

### Secrets and Environment Values

You can supply environment values in either place:

1. **`workbench-mcp/.env`**
2. **`env` in `.vscode/mcp.json`** — VS Code injects these into the MCP server process.

**Precedence:** process environment (including `.vscode/mcp.json` → `env`) overrides values from `.env` for the same key.

Example with HTTP tuning in VS Code:

```json
{
  "servers": {
    "workbench-mcp": {
      "type": "stdio",
      "command": "/absolute/path/to/workbench-mcp/.venv/bin/python",
      "args": ["-m", "workbench_mcp.server"],
      "env": {
        "API_TIMEOUT_SECONDS": "30",
        "API_MAX_RESPONSE_BYTES": "2097152",
        "API_VERIFY_SSL": "false"
      }
    }
  }
}
```

Do **not** commit real tokens. Prefer a local-only workspace configuration or omit `env` and use `.env` (which should stay out of git).

If other MCP servers are already configured, add `workbench-mcp` inside the existing `servers` object instead of replacing the entire file.

After saving `.vscode/mcp.json`, reload VS Code or refresh MCP servers so the new server is discovered. After the server loads, run the `health` tool before testing database procedures.

## Initial Tools

- `health`
- `describe_object`
- `list_tables_and_columns`
- `preview_query`
- `execute_readonly_sql`
- `exec_proc_preview`
- `exec_function_preview`
- `insert_row`
- `insert_rows`
- `http_get`
- `http_head`
- `http_post`
- `http_put`
- `http_patch`
- `http_delete`
- `auth_start_session`
- `auth_switch_user`
- `auth_status`
- `auth_clear_session`
- `execute_path_bash_script` (script name resolved via `PATH`)

## Safety Model

- Persistent DDL and DML are blocked in ad-hoc PostgreSQL batches
- Only temp-table writes are allowed, and only for temp tables created in the current batch
- `preview_query` allows only `SELECT` statements and CTE-based reads
- `exec_proc_preview` can execute PostgreSQL procedures and functions; overloaded routines should be passed with a signature such as `public.my_func(integer, text)`
- `execute_path_bash_script` only accepts script names (not paths), resolves them via `PATH`, and executes through `bash`

## Suggested First Checks

After `.env` is configured, a typical validation flow is:

1. Describe the function, procedure, table, or view to inspect.
2. Preview the supporting configuration or reference data needed to understand that object.
3. Run `exec_proc_preview`, `preview_query`, or `execute_readonly_sql` with known inputs.
4. Compare the returned shape with the feature, investigation, or debugging scenario being evaluated.

## Function Execution Example

For positional PostgreSQL function calls, use `exec_function_preview`.
Pass PostgreSQL arrays as normal JSON lists.

Example SQL target:

```sql
select * from sales."Fn_GetSalesChamps"(2, 2025, array[1,2,5,6,7,8,9,10,11,12,15,16,18,19], 5);
```

Equivalent MCP tool input:

```json
{
  "function_name": "sales.\"Fn_GetSalesChamps\"",
  "parameters": [2, 2025, [1, 2, 5, 6, 7, 8, 9, 10, 11, 12, 15, 16, 18, 19], 5]
}
```

## Insert Examples

Single row insert:

```json
{
  "table_name": "sales.orders",
  "row": {
    "customer_id": 10,
    "status": "new"
  },
  "returning_columns": ["order_id"]
}
```

Batch insert:

```json
{
  "table_name": "sales.orders",
  "rows": [
    {"customer_id": 10, "status": "new"},
    {"customer_id": 11, "status": "pending"}
  ]
}
```
