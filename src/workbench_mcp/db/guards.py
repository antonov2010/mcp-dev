"""SQL validation and security enforcement layer."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import sqlparse


class SqlGuardError(ValueError):
    """Raised when a SQL batch violates the safety policy."""


@dataclass(slots=True)
class GuardResult:
    warnings: list[str] = field(default_factory=list)


_BLOCKED_PATTERNS = [
    (re.compile(r"\balter\b", re.IGNORECASE), "ALTER statements are not permitted."),
    (
        re.compile(r"\btruncate\b", re.IGNORECASE),
        "TRUNCATE statements are not permitted.",
    ),
    (re.compile(r"\bmerge\b", re.IGNORECASE), "MERGE statements are not permitted."),
    (
        re.compile(r"\bgrant\b|\brevoke\b|\bdeny\b", re.IGNORECASE),
        "Permission modifications (GRANT/REVOKE/DENY) are not permitted.",
    ),
    (re.compile(r"\bcopy\b", re.IGNORECASE), "COPY statements are not permitted."),
    (re.compile(r"\bvacuum\b", re.IGNORECASE), "VACUUM statements are not permitted."),
    (re.compile(r"\breindex\b", re.IGNORECASE), "REINDEX statements are not permitted."),
    (re.compile(r"\banalyze\b", re.IGNORECASE), "ANALYZE statements are not permitted."),
    (
        re.compile(r"\bselect\b[\s\S]*?\binto\b", re.IGNORECASE),
        "SELECT INTO is not permitted; use CREATE TEMP TABLE AS SELECT for temporary workflows.",
    ),
]

_IDENTIFIER_PART = r'(?:"(?:[^"]|"")+"|[a-zA-Z_][\w$]*)'
_QUALIFIED_IDENTIFIER = rf'{_IDENTIFIER_PART}(?:\s*\.\s*{_IDENTIFIER_PART})?'
_CREATE_TEMP_TABLE_RE = re.compile(
    rf'^\s*create\s+(?:local\s+)?temp(?:orary)?\s+table\s+(?:if\s+not\s+exists\s+)?(?P<name>{_QUALIFIED_IDENTIFIER})\b',
    re.IGNORECASE,
)
_CREATE_ANY_RE = re.compile(r'^\s*create\b', re.IGNORECASE)
_DROP_TABLE_RE = re.compile(
    rf'^\s*drop\s+table\s+(?:if\s+exists\s+)?(?P<name>{_QUALIFIED_IDENTIFIER})\b',
    re.IGNORECASE,
)
_DROP_ANY_RE = re.compile(r'^\s*drop\b', re.IGNORECASE)
_INSERT_RE = re.compile(
    rf'^\s*insert\s+into\s+(?:only\s+)?(?P<name>{_QUALIFIED_IDENTIFIER})\b',
    re.IGNORECASE,
)
_UPDATE_RE = re.compile(
    rf'^\s*update\s+(?:only\s+)?(?P<name>{_QUALIFIED_IDENTIFIER})\b',
    re.IGNORECASE,
)
_DELETE_RE = re.compile(
    rf'^\s*delete\s+from\s+(?:only\s+)?(?P<name>{_QUALIFIED_IDENTIFIER})\b',
    re.IGNORECASE,
)
_CALL_RE = re.compile(r'^\s*call\b', re.IGNORECASE)


def _normalize_identifier(identifier: str) -> str:
    """Parse and normalize a PostgreSQL identifier (schema.table or just table)."""
    parts = [part.strip() for part in re.split(r"\s*\.\s*", identifier.strip()) if part.strip()]
    normalized_parts: list[str] = []
    for part in parts:
        if part.startswith('"') and part.endswith('"'):
            normalized_parts.append(part[1:-1].replace('""', '"'))
        else:
            normalized_parts.append(part.lower())
    return ".".join(normalized_parts)


def _is_temp_table(identifier: str, temp_tables: set[str]) -> bool:
    """Check if an identifier refers to a known temporary table in the current batch."""
    normalized = _normalize_identifier(identifier)
    if normalized in temp_tables:
        return True
    if "." not in normalized and f"pg_temp.{normalized}" in temp_tables:
        return True
    if normalized.startswith("pg_temp.") and normalized.removeprefix("pg_temp.") in temp_tables:
        return True
    return False


def _split_statements(sql: str) -> list[str]:
    """Split SQL text into individual statements using sqlparse."""
    return [statement.strip() for statement in sqlparse.split(sql) if statement.strip()]


def _validate_statement(statement: str, temp_tables: set[str]) -> set[str]:
    """Validate a single SQL statement against the safety policy.
    
    Returns the set of temporary tables created by this statement (if any).
    Raises SqlGuardError if the statement violates the policy.
    """
    for pattern, message in _BLOCKED_PATTERNS:
        if pattern.search(statement):
            raise SqlGuardError(message)

    created_temp_tables: set[str] = set()

    create_temp_match = _CREATE_TEMP_TABLE_RE.match(statement)
    if create_temp_match:
        created_temp_tables.add(_normalize_identifier(create_temp_match.group("name")))
        return created_temp_tables

    if _CREATE_ANY_RE.match(statement):
        raise SqlGuardError("Only CREATE TEMP TABLE is permitted in ad-hoc batches.")

    drop_match = _DROP_TABLE_RE.match(statement)
    if drop_match:
        if not _is_temp_table(drop_match.group("name"), temp_tables):
            raise SqlGuardError("DROP TABLE is only permitted for temporary tables created in the current batch.")
        return created_temp_tables

    if _DROP_ANY_RE.match(statement):
        raise SqlGuardError("Only DROP TABLE for temporary tables is permitted.")

    for matcher, message in (
        (_INSERT_RE, "INSERT is only permitted for temporary tables created in the current batch."),
        (_UPDATE_RE, "UPDATE is only permitted for temporary tables created in the current batch."),
        (_DELETE_RE, "DELETE is only permitted for temporary tables created in the current batch."),
    ):
        match = matcher.match(statement)
        if match and not _is_temp_table(match.group("name"), temp_tables):
            raise SqlGuardError(message)

    return created_temp_tables


def strip_sql_comments(sql: str) -> str:
    """Remove block and line comments from SQL text."""
    without_block_comments = re.sub(r"/\*[\s\S]*?\*/", " ", sql)
    without_line_comments = re.sub(r"--.*?$", " ", without_block_comments, flags=re.MULTILINE)
    return without_line_comments


def validate_readonly_sql(sql: str) -> GuardResult:
    """Validate a SQL batch against read-only and safety policies.
    
    Allows SELECT, CTEs, temporary table creation/modification, and stored procedure calls.
    Returns warnings about temp table scope and routine execution risks.
    """
    cleaned = strip_sql_comments(sql).strip()
    if not cleaned:
        raise SqlGuardError("Provided SQL batch contains no executable statements.")

    statements = _split_statements(cleaned)
    if not statements:
        raise SqlGuardError("Provided SQL batch contains no executable statements.")

    warnings: list[str] = []
    temp_tables: set[str] = set()

    for statement in statements:
        created_temp_tables = _validate_statement(statement, temp_tables)
        if created_temp_tables:
            temp_tables.update(created_temp_tables)

    if temp_tables:
        warnings.append(
            "Warning: Temporary tables created in this batch are scoped to the current session."
        )
    if any(_CALL_RE.match(statement) for statement in statements):
        warnings.append(
            "Warning: Procedure or function execution detected; safety depends on the routine's own implementation."
        )

    return GuardResult(warnings=warnings)


def validate_preview_query(sql: str) -> GuardResult:
    """Validate that SQL is a read-only SELECT or CTE query suitable for preview."""
    cleaned = strip_sql_comments(sql).strip()
    if not re.match(r"^(select|with)\b", cleaned, re.IGNORECASE):
        raise SqlGuardError(
            "preview_query accepts only SELECT statements and Common Table Expressions (CTEs)."
        )
    return validate_readonly_sql(cleaned)
