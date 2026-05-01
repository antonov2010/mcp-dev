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
        """Retrieve structural details, parameters, and definition for a database object.

        When to use this tool:
        - You need to inspect the schema of a table, view, function, or procedure.
        - You want to know column names, data types, constraints, or routine signatures.
        - You are unsure what parameters a function/procedure expects.

        Example:
            object_name='sales."Vw_CommissionDetails"'
            object_name='sales."Fn_GetSalesChamps"'
        """
        return get_database_client().describe_object(object_name)

    @mcp.tool()
    def list_tables_and_columns(
        schema_name: str | None = None,
        search_term: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Discover tables and columns with optional filtering by schema or keyword search.

        When to use this tool:
        - You need to find what tables exist in a schema.
        - You want to search for columns by name across the database.
        - You are exploring the database structure before writing a query.

        Parameters:
        - `schema_name`: filter to a specific schema (e.g., `sales`).
        - `search_term`: keyword to match against table or column names.
        - `limit`: maximum number of results to return.

        For detailed schema of a specific object, use `describe_object` instead.
        """
        return get_database_client().list_tables_and_columns(
            schema_name=schema_name,
            search_term=search_term,
            limit=limit,
        )

    @mcp.tool()
    def preview_query(sql: str, max_rows: int | None = None) -> dict[str, Any]:
        """Execute read-only SELECT statements and CTEs with safety validation and row limits.

        When to use this tool:
        - You need to run a single SELECT query or CTE to inspect data.
        - You want the strictest safety guardrails (only SELECT / WITH allowed).
        - You do NOT need to create temporary tables or call stored procedures.

        Allowed:
        - SELECT and WITH (CTE) statements.
        - SET TIME ZONE (e.g., `SET TIME ZONE 'America/Tijuana';`) before the query.

        Not allowed:
        - CREATE TEMP TABLE, INSERT, UPDATE, DELETE, CALL, or any other DML/DDL.

        If you need temp tables or procedure calls, use `execute_readonly_sql` instead.
        """
        guard_result = validate_preview_query(sql)
        result = get_database_client().execute_batch(sql, max_rows=max_rows)
        result["warnings"] = guard_result.warnings
        return result

    @mcp.tool()
    def execute_readonly_sql(sql: str, max_rows: int | None = None) -> dict[str, Any]:
        """Execute read-only SQL batches with support for temporary tables within the session.

        When to use this tool:
        - You need to run a multi-statement batch.
        - You want to CREATE TEMP TABLE, populate it, and SELECT from it.
        - You need to CALL a stored procedure or execute a function that returns a result set.
        - You need to set the session timezone with `SET TIME ZONE` before querying.

        Allowed:
        - SELECT, WITH (CTEs).
        - CREATE TEMP TABLE (scoped to the current session).
        - INSERT / UPDATE / DELETE — but ONLY against temp tables created in the same batch.
        - DROP TABLE — but ONLY for temp tables created in the same batch.
        - CALL (stored procedures) and SET TIME ZONE.

        Not allowed:
        - Permanent table modifications (INSERT/UPDATE/DDELETE on real tables).
        - DDL such as ALTER, TRUNCATE, GRANT, REVOKE, CREATE permanent objects, etc.

        If you only need a simple SELECT without temp tables or procedures,
        prefer `preview_query` for stricter safety guarantees.
        """
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
        """Execute a PostgreSQL stored procedure by name with named parameters.

        When to use this tool:
        - You know the exact procedure name and want to call it with named arguments.
        - The procedure is read-only or returns a preview result set.

        Parameters are passed as a dictionary mapping parameter names to values.
        Example:
            proc_name="sales.sp_get_monthly_summary"
            parameters={"year": 2025, "month": 4}

        If you need to run arbitrary SQL (including CALL with complex logic or temp tables),
        use `execute_readonly_sql` instead.
        """
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

    @mcp.tool()
    def exec_function_preview(
        function_name: str,
        parameters: list[Any] | None = None,
        max_rows: int | None = None,
    ) -> dict[str, Any]:
        """Execute a PostgreSQL function by name with positional parameters and return preview rows.

        When to use this tool:
        - You know the exact function name and want to call it with positional arguments.
        - The function returns a result set or scalar values suitable for preview.

        Pass arguments in positional order using JSON-compatible values:
        - scalars: `2`, `2025`, `5`
        - arrays: `[1, 2, 5]`
        - null: `null`

        PostgreSQL array parameters should be passed as normal lists; psycopg adapts them
        to PostgreSQL arrays automatically.

        Example:
            function_name='sales."Fn_GetSalesChamps"'
            parameters=[2, 2025, [1, 2, 5], 5]

        If you need named parameters or are calling a procedure (not a function),
        use `exec_proc_preview` instead.
        If you need to run arbitrary SQL, use `execute_readonly_sql` or `preview_query`.
        """
        guard_sql = f"SELECT {function_name}()"
        try:
            guard_result = validate_readonly_sql(guard_sql)
        except SqlGuardError as exc:
            raise ValueError(str(exc)) from exc

        result = get_database_client().execute_routine_preview(
            function_name,
            parameters=parameters or [],
            max_rows=max_rows,
        )
        result["warnings"] = guard_result.warnings
        return result

    @mcp.tool()
    def insert_row(
        table_name: str,
        row: dict[str, Any],
        returning_columns: list[str] | None = None,
    ) -> dict[str, Any]:
        """Insert a single row into a PostgreSQL table.

        When to use this tool:
        - You need to insert exactly one row into a permanent table.
        - You want a simple, structured API without writing raw SQL.

        Parameters:
        - `table_name`: target table, optionally schema-qualified (e.g., `sales.orders`).
        - `row`: dictionary mapping column names to values.
        - `returning_columns`: optional list of columns to return via `RETURNING`.

        Example:
            table_name="sales.orders"
            row={"customer_id": 10, "status": "new"}
            returning_columns=["order_id"]

        For bulk inserts, use `insert_rows` instead.
        For read-only queries, use `preview_query` or `execute_readonly_sql`.
        """
        return get_database_client().insert_row(
            table_name,
            row,
            returning_columns=returning_columns,
        )

    @mcp.tool()
    def insert_rows(
        table_name: str,
        rows: list[dict[str, Any]],
        returning_columns: list[str] | None = None,
    ) -> dict[str, Any]:
        """Insert multiple rows into a PostgreSQL table in one batch.

        When to use this tool:
        - You need to insert many rows at once (bulk insert).
        - All rows share the same columns.

        Parameters:
        - `table_name`: target table, optionally schema-qualified.
        - `rows`: list of dictionaries, each mapping column names to values.
        - `returning_columns`: optional list of columns to return from inserted rows.

        Notes:
        - Every row must use the same columns in the same order.
        - Arrays can be passed as JSON lists; psycopg adapts them automatically.

        For inserting a single row, use `insert_row` instead.
        For read-only queries, use `preview_query` or `execute_readonly_sql`.
        """
        return get_database_client().insert_rows(
            table_name,
            rows,
            returning_columns=returning_columns,
        )
