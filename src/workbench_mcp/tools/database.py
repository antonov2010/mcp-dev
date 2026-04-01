"""Database tools and MCP tool registration for object inspection and SQL execution."""
from __future__ import annotations

from functools import lru_cache
from typing import Any

from mcp.server.fastmcp import FastMCP

from workbench_mcp.config import get_settings
from workbench_mcp.db.connection import DatabaseClient
from workbench_mcp.db.guards import SqlGuardError, validate_preview_query, validate_readonly_sql


@lru_cache(maxsize=1)
def get_database_client() -> DatabaseClient:
    """Get or create a cached database client instance."""
    return DatabaseClient(get_settings())


def register_database_tools(mcp: FastMCP) -> None:
    """Register all database-related MCP tools."""

    @mcp.tool()
    def health() -> dict[str, Any]:
        """Provide system status and configuration details without exposing secrets."""
        settings = get_settings()
        return {
            "server": "workbench-mcp",
            "database": settings.db_name,
            "host": settings.db_host,
            "port": settings.db_port,
            "adapter": "psycopg",
            "sslmode": settings.db_sslmode,
            "row_limit": settings.db_max_rows,
            "result_set_limit": settings.db_max_result_sets,
            "mode": "readonly-with-temp-tables",
            "platform": "postgresql",
        }

    @mcp.tool()
    def describe_object(object_name: str) -> dict[str, Any]:
        """Retrieve structural details, parameters, and definition for a database object."""
        return get_database_client().describe_object(object_name)

    @mcp.tool()
    def list_tables_and_columns(
        schema_name: str | None = None,
        search_term: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Discover tables and columns with optional filtering by schema or keyword search."""
        return get_database_client().list_tables_and_columns(
            schema_name=schema_name,
            search_term=search_term,
            limit=limit,
        )

    @mcp.tool()
    def preview_query(sql: str, max_rows: int | None = None) -> dict[str, Any]:
        """Execute read-only SELECT statements and CTEs with safety validation and row limits."""
        guard_result = validate_preview_query(sql)
        result = get_database_client().execute_batch(sql, max_rows=max_rows)
        result["warnings"] = guard_result.warnings
        return result

    @mcp.tool()
    def execute_readonly_sql(sql: str, max_rows: int | None = None) -> dict[str, Any]:
        """Execute read-only SQL batches with support for temporary tables within the session."""
        guard_result = validate_readonly_sql(sql)
        result = get_database_client().execute_batch(sql, max_rows=max_rows)
        result["warnings"] = guard_result.warnings
        return result

    @mcp.tool()
    def exec_proc_preview(
        proc_name: str,
        parameters: dict[str, str | int | float | bool | None] | None = None,
        max_rows: int | None = None,
    ) -> dict[str, Any]:
        """Execute PostgreSQL functions or procedures with optional parameters and result limiting."""
        routine_guard_sql = f"CALL {proc_name}()"
        try:
            guard_result = validate_readonly_sql(routine_guard_sql)
        except SqlGuardError as exc:
            raise ValueError(str(exc)) from exc

        result = get_database_client().execute_routine_preview(
            proc_name,
            parameters=parameters,
            max_rows=max_rows,
        )
        result["warnings"] = guard_result.warnings
        return result
