# Authorization Handling for HTTP Tools

This feature allows each MCP caller to decide which JWT should be forwarded to the target API.

## Why this exists

Some requests must run with the caller's real permissions instead of a shared default token.
The HTTP tools now support an explicit `jwt_token` field for that purpose.

## How authorization is resolved

The HTTP tools resolve the outgoing `Authorization` header in this order:

1. `jwt_token` from the tool call
2. `API_BEARER_TOKEN` from `.env` or process environment
3. No authorization header

## Rules agents should follow

- Pass the caller JWT in `jwt_token`.
- Do not send auth via `headers.Authorization`.
- You may pass either a raw JWT or `Bearer <jwt>` in `jwt_token`.
- If `jwt_token` is empty or whitespace, the server falls back to `API_BEARER_TOKEN`.

## Tool examples

### GET with caller token

```json
{
  "url": "https://localhost:5001/api/v1/sales/my-sales",
  "jwt_token": "eyJhbGciOi..."
}
```

### POST with caller token

```json
{
  "url": "https://localhost:5001/api/v1/orders",
  "jwt_token": "eyJhbGciOi...",
  "body": {
    "customerId": 10,
    "status": "new"
  }
}
```

### Use the default server token

```json
{
  "url": "https://localhost:5001/api/v1/orders"
}
```

## Notes for local setup

- Keep `API_BEARER_TOKEN` in `.env` as the safe default token.
- Use per-call `jwt_token` only when the request must reflect the caller's own permissions.
- `API_USER_TIMEZONE` is still forwarded independently as `X-User-Timezone`.