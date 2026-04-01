"""PostgreSQL database client for object inspection, queries, and routine execution."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, time
from decimal import Decimal
import re
from typing import Any, Iterable

import psycopg
import sqlparse

from workbench_mcp.config import Settings


def _normalize_value(value: Any) -> Any:
    """Convert database values to JSON-serializable types.

    Handles Decimal, datetime, date, time, and bytes conversions.
    """
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    return value


class DatabaseClient:
    """Main interface for executing queries and inspecting PostgreSQL objects."""

    def __init__(self, settings: Settings) -> None:
        """Initialize with configuration settings."""
        self._settings = settings

    @contextmanager
    def connect(self) -> Iterable[psycopg.Connection]:
        """Establish and manage a PostgreSQL connection with autocommit and timeout."""
        connection = psycopg.connect(
            **self._settings.connection_kwargs(),
            autocommit=True,
        )
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT set_config('statement_timeout', %s, false)",
                    [str(max(1, self._settings.db_query_timeout_seconds) * 1000)],
                )
            yield connection
        finally:
            connection.close()

    def _fetch_rows(
        self,
        cursor: psycopg.Cursor,
        *,
        row_limit: int,
    ) -> dict[str, Any]:
        """Fetch result rows in batches with truncation support."""
        columns = [column.name for column in cursor.description or ()]
        rows: list[dict[str, Any]] = []
        truncated = False

        while True:
            batch = cursor.fetchmany(25)
            if not batch:
                break
            for row in batch:
                if len(rows) >= row_limit:
                    truncated = True
                    break
                rows.append(
                    {
                        columns[index]: _normalize_value(value)
                        for index, value in enumerate(row)
                    }
                )
            if truncated:
                break

        return {
            "columns": columns,
            "rows": rows,
            "row_limit": row_limit,
            "truncated": truncated,
        }

    def _split_sql(self, sql: str) -> list[str]:
        """Split SQL text into individual statements."""
        return [statement.strip() for statement in sqlparse.split(sql) if statement.strip()]

    def execute_batch(
        self,
        sql: str,
        params: list[Any] | tuple[Any, ...] | None = None,
        *,
        max_rows: int | None = None,
    ) -> dict[str, Any]:
        """Execute a SQL batch and return result sets with row and set limits."""
        params = params or []
        row_limit = max_rows or self._settings.db_max_rows
        result_sets: list[dict[str, Any]] = []
        statements = self._split_sql(sql)

        if not statements:
            return {
                "result_sets": [],
                "result_set_count": 0,
                "max_result_sets": self._settings.db_max_result_sets,
            }
        if params and len(statements) != 1:
            raise ValueError("Parameterized queries are only supported for single SQL statements.")

        with self.connect() as connection:
            cursor = connection.cursor()
            for statement in statements:
                if params:
                    cursor.execute(statement, params)
                else:
                    cursor.execute(statement)

                if cursor.description:
                    result_sets.append(self._fetch_rows(cursor, row_limit=row_limit))
                    if len(result_sets) >= self._settings.db_max_result_sets:
                        break

        return {
            "result_sets": result_sets,
            "result_set_count": len(result_sets),
            "max_result_sets": self._settings.db_max_result_sets,
        }

    def _split_name_and_signature(self, object_name: str) -> tuple[str | None, str, str | None]:
        """Parse object_name into optional schema, name, and optional signature.

        Handles formats like 'table_name', 'schema.table_name', and 'routine(arg_types)'.
        """
        stripped = object_name.strip()
        if not stripped:
            raise ValueError("Object name cannot be empty.")

        signature: str | None = None
        name_only = stripped
        if "(" in stripped and stripped.endswith(")"):
            name_only, signature = stripped.split("(", 1)
            signature = signature[:-1].strip()

        parts = [part.strip() for part in name_only.split(".") if part.strip()]
        if len(parts) == 1:
            return None, parts[0].strip('"'), signature
        if len(parts) == 2:
            return parts[0].strip('"'), parts[1].strip('"'), signature
        raise ValueError(f"Unsupported object name format: {object_name}")

    def _quote_ident(self, identifier: str) -> str:
        """Quote a PostgreSQL identifier for safe use in SQL."""
        return '"' + identifier.replace('"', '""') + '"'

    def _qualified_name(self, schema_name: str | None, object_name: str) -> str:
        """Build a fully-qualified object name (schema.object or just object)."""
        if schema_name:
            return f"{self._quote_ident(schema_name)}.{self._quote_ident(object_name)}"
        return self._quote_ident(object_name)

    def _load_routine_parameters(
        self,
        connection: psycopg.Connection,
        routine_oid: int,
    ) -> list[dict[str, Any]]:
        sql = """
        WITH routine AS (
            SELECT
                p.oid,
                CASE
                    WHEN p.pronargs = 0 THEN ARRAY[]::oid[]
                    WHEN p.proallargtypes IS NOT NULL THEN p.proallargtypes
                    ELSE string_to_array(p.proargtypes::text, ' ')::oid[]
                END AS arg_types,
                COALESCE(
                    p.proargmodes,
                    array_fill(
                        'i'::"char",
                        ARRAY[
                            CASE
                                WHEN p.pronargs = 0 THEN 0
                                WHEN p.proallargtypes IS NOT NULL THEN cardinality(p.proallargtypes)
                                ELSE cardinality(string_to_array(p.proargtypes::text, ' ')::oid[])
                            END
                        ]
                    )
                ) AS arg_modes,
                COALESCE(p.proargnames, ARRAY[]::text[]) AS arg_names
            FROM pg_proc p
            WHERE p.oid = %s
        )
        SELECT
            position AS parameter_id,
            COALESCE(r.arg_names[position], format('$%s', position)) AS name,
            format_type(r.arg_types[position], NULL) AS data_type,
            CASE COALESCE(r.arg_modes[position], 'i')
                WHEN 'i' THEN 'IN'
                WHEN 'o' THEN 'OUT'
                WHEN 'b' THEN 'INOUT'
                WHEN 'v' THEN 'VARIADIC'
                WHEN 't' THEN 'TABLE'
                ELSE 'IN'
            END AS parameter_mode
        FROM routine r,
        LATERAL generate_subscripts(r.arg_types, 1) AS position
        ORDER BY position
        """

        with connection.cursor() as cursor:
            cursor.execute(sql, [routine_oid])
            return [
                {
                    "parameter_id": row[0],
                    "name": row[1],
                    "data_type": row[2],
                    "parameter_mode": row[3],
                }
                for row in cursor.fetchall()
            ]

    def _resolve_routine(
        self,
        connection: psycopg.Connection,
        object_name: str,
    ) -> dict[str, Any]:
        schema_name, routine_name, signature = self._split_name_and_signature(object_name)

        if signature is not None:
            exact_sql = """
            SELECT
                p.oid,
                n.nspname AS schema_name,
                p.proname AS object_name,
                CASE p.prokind
                    WHEN 'f' THEN 'FUNCTION'
                    WHEN 'p' THEN 'PROCEDURE'
                    WHEN 'a' THEN 'AGGREGATE'
                    WHEN 'w' THEN 'WINDOW FUNCTION'
                END AS type_desc,
                p.prokind,
                p.proretset,
                pg_get_function_result(p.oid) AS return_type,
                pg_get_function_identity_arguments(p.oid) AS identity_arguments,
                pg_get_functiondef(p.oid) AS definition
            FROM pg_proc p
            JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE p.oid = to_regprocedure(%s)
            """
            with connection.cursor() as cursor:
                cursor.execute(exact_sql, [object_name])
                row = cursor.fetchone()
            if not row:
                raise ValueError(f"Routine not found: {object_name}")
            parameters = self._load_routine_parameters(connection, row[0])
            return {
                "oid": row[0],
                "schema_name": row[1],
                "object_name": row[2],
                "type_desc": row[3],
                "prokind": row[4],
                "proretset": row[5],
                "return_type": row[6],
                "identity_arguments": row[7],
                "definition": row[8] or "",
                "parameters": parameters,
            }

        lookup_sql = """
        SELECT
            p.oid,
            n.nspname AS schema_name,
            p.proname AS object_name,
            CASE p.prokind
                WHEN 'f' THEN 'FUNCTION'
                WHEN 'p' THEN 'PROCEDURE'
                WHEN 'a' THEN 'AGGREGATE'
                WHEN 'w' THEN 'WINDOW FUNCTION'
            END AS type_desc,
            p.prokind,
            p.proretset,
            pg_get_function_result(p.oid) AS return_type,
            pg_get_function_identity_arguments(p.oid) AS identity_arguments,
            pg_get_functiondef(p.oid) AS definition
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE p.proname = %s
          AND (%s IS NULL OR n.nspname = %s)
        ORDER BY (n.nspname = 'public') DESC, n.nspname, p.pronargs, p.oid
        """
        with connection.cursor() as cursor:
            cursor.execute(lookup_sql, [routine_name, schema_name, schema_name])
            rows = cursor.fetchall()

        if not rows:
            raise ValueError(f"Routine not found: {object_name}")
        if len(rows) > 1:
            candidates = ", ".join(
                f"{row[1]}.{row[2]}({row[7]})"
                for row in rows[:5]
            )
            raise ValueError(
                "Routine name is ambiguous. Pass a signature such as "
                f"schema.name(type, ...). Candidates: {candidates}"
            )

        row = rows[0]
        parameters = self._load_routine_parameters(connection, row[0])
        return {
            "oid": row[0],
            "schema_name": row[1],
            "object_name": row[2],
            "type_desc": row[3],
            "prokind": row[4],
            "proretset": row[5],
            "return_type": row[6],
            "identity_arguments": row[7],
            "definition": row[8] or "",
            "parameters": parameters,
        }

    def describe_object(self, object_name: str) -> dict[str, Any]:
        relation_sql = """
        SELECT
            n.nspname AS schema_name,
            c.relname AS object_name,
            CASE c.relkind
                WHEN 'r' THEN 'TABLE'
                WHEN 'p' THEN 'PARTITIONED TABLE'
                WHEN 'v' THEN 'VIEW'
                WHEN 'm' THEN 'MATERIALIZED VIEW'
                WHEN 'f' THEN 'FOREIGN TABLE'
            END AS type_desc,
            CASE
                WHEN c.relkind IN ('v', 'm') THEN pg_get_viewdef(c.oid, true)
                ELSE NULL
            END AS definition,
            c.oid
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.oid = to_regclass(%s)
          AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
        """

        refs_sql = """
        SELECT DISTINCT
            ref_ns.nspname AS schema_name,
            COALESCE(ref_class.relname, ref_proc.proname) AS entity_name
        FROM pg_depend dep
        LEFT JOIN pg_class ref_class ON ref_class.oid = dep.refobjid
        LEFT JOIN pg_proc ref_proc ON ref_proc.oid = dep.refobjid
        LEFT JOIN pg_namespace ref_ns
            ON ref_ns.oid = COALESCE(ref_class.relnamespace, ref_proc.pronamespace)
        WHERE dep.objid = %s
          AND COALESCE(ref_class.relname, ref_proc.proname) IS NOT NULL
        ORDER BY ref_ns.nspname, entity_name
        """

        with self.connect() as connection:
            cursor = connection.cursor()

            cursor.execute(relation_sql, [object_name])
            details_row = cursor.fetchone()
            if details_row:
                cursor.execute(refs_sql, [details_row[4]])
                referenced_objects = [
                    {
                        "schema_name": row[0],
                        "entity_name": row[1],
                    }
                    for row in cursor.fetchall()
                ]

                definition = details_row[3] or ""
                definition_preview = definition[: self._settings.db_object_preview_chars]
                return {
                    "schema_name": details_row[0],
                    "object_name": details_row[1],
                    "type_desc": details_row[2],
                    "parameters": [],
                    "referenced_objects": referenced_objects,
                    "definition_preview": definition_preview,
                    "definition_truncated": len(definition) > len(definition_preview),
                }

            routine = self._resolve_routine(connection, object_name)
            cursor.execute(refs_sql, [routine["oid"]])
            referenced_objects = [
                {
                    "schema_name": row[0],
                    "entity_name": row[1],
                }
                for row in cursor.fetchall()
            ]

        definition = routine["definition"]
        definition_preview = definition[: self._settings.db_object_preview_chars]
        return {
            "schema_name": routine["schema_name"],
            "object_name": routine["object_name"],
            "type_desc": routine["type_desc"],
            "parameters": routine["parameters"],
            "referenced_objects": referenced_objects,
            "identity_arguments": routine["identity_arguments"],
            "return_type": routine["return_type"],
            "definition_preview": definition_preview,
            "definition_truncated": len(definition) > len(definition_preview),
        }

    def list_tables_and_columns(
        self,
        *,
        schema_name: str | None = None,
        search_term: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        row_limit = limit or self._settings.db_max_rows
        search_pattern = f"%{search_term}%" if search_term else None
        sql = """
        SELECT
            c.table_schema,
            c.table_name,
            c.column_name,
            c.data_type
        FROM information_schema.columns c
        WHERE c.table_schema NOT IN ('information_schema', 'pg_catalog')
          AND (%s IS NULL OR c.table_schema = %s)
          AND (
                %s IS NULL
                OR c.table_name ILIKE %s
                OR c.column_name ILIKE %s
              )
        ORDER BY c.table_schema, c.table_name, c.ordinal_position
        LIMIT %s
        """
        result = self.execute_batch(
            sql,
            [schema_name, schema_name, search_pattern, search_pattern, search_pattern, row_limit],
            max_rows=row_limit,
        )
        return result

    def execute_routine_preview(
        self,
        routine_name: str,
        parameters: dict[str, Any] | None = None,
        *,
        max_rows: int | None = None,
    ) -> dict[str, Any]:
        parameters = parameters or {}
        ordered_values = list(parameters.values())
        row_limit = max_rows or self._settings.db_max_rows

        with self.connect() as connection:
            routine = self._resolve_routine(connection, routine_name)
            placeholders = ", ".join(["%s"] * len(ordered_values))
            qualified_name = self._qualified_name(
                routine["schema_name"],
                routine["object_name"],
            )

            if routine["prokind"] == "p":
                sql = f"CALL {qualified_name}({placeholders})" if placeholders else f"CALL {qualified_name}()"
            elif routine["proretset"]:
                sql = f"SELECT * FROM {qualified_name}({placeholders})" if placeholders else f"SELECT * FROM {qualified_name}()"
            else:
                sql = (
                    f"SELECT {qualified_name}({placeholders}) AS result"
                    if placeholders
                    else f"SELECT {qualified_name}() AS result"
                )

            with connection.cursor() as cursor:
                if ordered_values:
                    cursor.execute(sql, ordered_values)
                else:
                    cursor.execute(sql)

                result_sets: list[dict[str, Any]] = []
                if cursor.description:
                    result_sets.append(self._fetch_rows(cursor, row_limit=row_limit))

        return {
            "result_sets": result_sets,
            "result_set_count": len(result_sets),
            "max_result_sets": self._settings.db_max_result_sets,
            "routine": {
                "schema_name": routine["schema_name"],
                "object_name": routine["object_name"],
                "type_desc": routine["type_desc"],
                "identity_arguments": routine["identity_arguments"],
                "return_type": routine["return_type"],
            },
        }
