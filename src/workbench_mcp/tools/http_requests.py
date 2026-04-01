"""HTTP request tools with separate MCP tools per HTTP method."""

from __future__ import annotations

import json
import logging
from typing import Any, cast

import httpx
from mcp.server.fastmcp import FastMCP

from workbench_mcp.config import get_settings

LOGGER = logging.getLogger(__name__)
_ALLOWED_BODY_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _validate_url(url: str) -> str | None:
    stripped_url = url.strip() if url else ""
    if not stripped_url:
        return None
    lower = stripped_url.lower()
    if not (lower.startswith("http://") or lower.startswith("https://")):
        return None
    return stripped_url


def _execute_http_request(
    *,
    method: str,
    url: str,
    headers: dict[str, str] | None = None,
    body: dict[str, Any] | list[Any] | str | None = None,
    content_type: str | None = None,
) -> dict[str, Any]:
    settings = get_settings()

    validated_url = _validate_url(url)
    if not validated_url:
        return {
            "ok": False,
            "error": "url must be non-empty and start with http:// or https://.",
        }

    request_headers: dict[str, str] = dict(headers or {})
    request_headers.pop("Authorization", None)
    if settings.api_bearer_token:
        request_headers["Authorization"] = (
            f"Bearer {settings.api_bearer_token.get_secret_value()}"
        )

    request_kwargs: dict[str, Any] = {}
    method_upper = method.upper()

    if body is not None:
        if method_upper not in _ALLOWED_BODY_METHODS:
            return {
                "ok": False,
                "error": f"{method_upper} is the only supported method set for request bodies: {sorted(_ALLOWED_BODY_METHODS)}.",
            }
        raw_body = cast(object, body)
        if isinstance(raw_body, (dict, list)):
            request_kwargs["json"] = raw_body
            if content_type:
                request_headers["Content-Type"] = content_type
        elif isinstance(raw_body, str):
            request_kwargs["content"] = raw_body.encode("utf-8")
            request_headers["Content-Type"] = content_type or "application/json; charset=utf-8"
        else:
            return {
                "ok": False,
                "error": "body must be a JSON object, array, or UTF-8 string.",
            }

    max_bytes = max(1024, settings.api_max_response_bytes)
    timeout = max(1.0, float(settings.api_timeout_seconds))

    try:
        with httpx.Client(
            verify=settings.api_verify_ssl,
            timeout=timeout,
            follow_redirects=True,
        ) as client:
            response = client.request(
                method_upper,
                validated_url,
                headers=request_headers,
                **request_kwargs,
            )
    except httpx.HTTPError as exc:
        LOGGER.warning("HTTP %s request failed: %s", method_upper, exc)
        return {
            "ok": False,
            "error": str(exc),
            "method": method_upper,
            "url": validated_url,
        }

    body_bytes = response.content
    truncated = len(body_bytes) > max_bytes
    if truncated:
        body_bytes = body_bytes[:max_bytes]

    response_content_type = response.headers.get("content-type", "")
    parsed_body: Any
    if "application/json" in response_content_type.lower():
        text = body_bytes.decode(response.encoding or "utf-8", errors="replace")
        try:
            parsed_body = json.loads(text)
        except json.JSONDecodeError:
            parsed_body = text
    else:
        parsed_body = body_bytes.decode(response.encoding or "utf-8", errors="replace")

    result: dict[str, Any] = {
        "ok": True,
        "method": method_upper,
        "url": validated_url,
        "status_code": response.status_code,
        "body": parsed_body,
        "response_truncated": truncated,
    }
    if truncated:
        result["warning"] = (
            f"Response exceeded the configured limit ({max_bytes} bytes); body was truncated."
        )
    return result


def register_http_tools(mcp: FastMCP) -> None:
    """Register method-specific HTTP MCP tools for explicit agent usage."""

    @mcp.tool()
    def http_get(
        url: str,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Send an HTTP GET request.

        Use for read-only resource retrieval. Provide a full URL.
        Authorization is sourced from `API_BEARER_TOKEN` when configured.
        Optional `headers` allows extra request headers.
        """
        return _execute_http_request(
            method="GET",
            url=url,
            headers=headers,
        )

    @mcp.tool()
    def http_head(
        url: str,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Send an HTTP HEAD request.

        Use for metadata/status checks without retrieving a full body.
        Provide a full URL and optional authentication/header values.
        """
        return _execute_http_request(
            method="HEAD",
            url=url,
            headers=headers,
        )

    @mcp.tool()
    def http_post(
        url: str,
        body: dict[str, Any] | list[Any] | str | None = None,
        content_type: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Send an HTTP POST request.

        Use for create/actions. Provide a full URL.
        `body` accepts JSON object/array or UTF-8 text.
        `content_type` optionally overrides the `Content-Type` header.
        """
        return _execute_http_request(
            method="POST",
            url=url,
            body=body,
            content_type=content_type,
            headers=headers,
        )

    @mcp.tool()
    def http_put(
        url: str,
        body: dict[str, Any] | list[Any] | str | None = None,
        content_type: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Send an HTTP PUT request.

        Use for full updates/replacements.
        `body` accepts JSON object/array or UTF-8 text.
        """
        return _execute_http_request(
            method="PUT",
            url=url,
            body=body,
            content_type=content_type,
            headers=headers,
        )

    @mcp.tool()
    def http_patch(
        url: str,
        body: dict[str, Any] | list[Any] | str | None = None,
        content_type: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Send an HTTP PATCH request.

        Use for partial updates.
        `body` accepts JSON object/array or UTF-8 text.
        """
        return _execute_http_request(
            method="PATCH",
            url=url,
            body=body,
            content_type=content_type,
            headers=headers,
        )

    @mcp.tool()
    def http_delete(
        url: str,
        body: dict[str, Any] | list[Any] | str | None = None,
        content_type: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Send an HTTP DELETE request.

        Use for delete operations. Some APIs allow delete payloads; if needed,
        provide `body` as JSON object/array or UTF-8 text.
        """
        return _execute_http_request(
            method="DELETE",
            url=url,
            body=body,
            content_type=content_type,
            headers=headers,
        )
