"""OS script tools for running bash scripts discovered through PATH."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
from typing import Any

from mcp.server.fastmcp import FastMCP

_MAX_OUTPUT_CHARS = 20_000
_DEFAULT_TIMEOUT_SECONDS = 30
_MAX_TIMEOUT_SECONDS = 300


def _truncate_text(value: str) -> tuple[str, bool]:
    if len(value) <= _MAX_OUTPUT_CHARS:
        return value, False
    return value[:_MAX_OUTPUT_CHARS], True


def register_os_tools(mcp: FastMCP) -> None:
    """Register MCP tools for executing bash scripts from PATH."""

    @mcp.tool()
    def execute_path_bash_script(
        script_name: str,
        args: list[str] | None = None,
        timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        """Execute a bash script located via the PATH environment variable.

        Only script names are accepted (no absolute or relative paths).
        The script is resolved with PATH, then executed as: bash <resolved_script> <args...>.
        """
        name = (script_name or "").strip()
        if not name:
            return {"ok": False, "error": "script_name cannot be empty."}
        if "/" in name or "\\" in name:
            return {
                "ok": False,
                "error": "script_name must be a script name only, not a file path.",
            }

        resolved = shutil.which(name)
        if not resolved:
            return {
                "ok": False,
                "error": f"Script '{name}' was not found in PATH.",
                "path": os.environ.get("PATH", ""),
            }

        resolved_path = Path(resolved)
        if not resolved_path.is_file():
            return {
                "ok": False,
                "error": f"Resolved target is not a file: {resolved}",
            }

        script_args = [str(value) for value in (args or [])]

        run_timeout = timeout_seconds if timeout_seconds is not None else _DEFAULT_TIMEOUT_SECONDS
        if run_timeout <= 0:
            return {"ok": False, "error": "timeout_seconds must be greater than 0."}
        run_timeout = min(run_timeout, _MAX_TIMEOUT_SECONDS)

        command = ["bash", resolved, *script_args]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=run_timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
            stderr = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
            truncated_stdout, stdout_truncated = _truncate_text(stdout)
            truncated_stderr, stderr_truncated = _truncate_text(stderr)
            return {
                "ok": False,
                "error": f"Script timed out after {run_timeout} seconds.",
                "script_name": name,
                "resolved_path": resolved,
                "timeout_seconds": run_timeout,
                "stdout": truncated_stdout,
                "stderr": truncated_stderr,
                "stdout_truncated": stdout_truncated,
                "stderr_truncated": stderr_truncated,
            }
        except OSError as exc:
            return {
                "ok": False,
                "error": str(exc),
                "script_name": name,
                "resolved_path": resolved,
            }

        stdout, stdout_truncated = _truncate_text(completed.stdout)
        stderr, stderr_truncated = _truncate_text(completed.stderr)

        return {
            "ok": completed.returncode == 0,
            "script_name": name,
            "resolved_path": resolved,
            "command": command,
            "return_code": completed.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "stdout_truncated": stdout_truncated,
            "stderr_truncated": stderr_truncated,
            "timeout_seconds": run_timeout,
        }
