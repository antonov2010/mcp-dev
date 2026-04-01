# Debugging the MCP Server with Breakpoints

Attach the VS Code or Cursor debugger to the MCP server running in MCP Inspector for interactive debugging with breakpoints.

## Quick Start Workflow

### 0. Install `debugpy`

From the project virtual environment:

```bash
pip install debugpy
```

### 1. Start the MCP Server from MCP Inspector

Open **MCP Inspector** in your browser at **http://localhost:6274**.

Configure the server launch settings:

| Field | Value |
|-------|--------|
| **Transport** | STDIO |
| **Command** | `/full/path/to/workbench-mcp/.venv/bin/python` |
| **Arguments** | `-m debugpy --listen 127.0.0.1:5678 -m workbench_mcp.server` |
| **Working directory** | Project root (the folder containing `src/`) |

Click **Connect** to start the MCP server process.

### 2. Attach the Debugger by Process ID

In VS Code or Cursor, run the debug configuration **Attach to MCP server (process id)** from `.vscode/launch.json`. Select the active Python process running the MCP server.

### 3. Set Breakpoints and Debug

Place breakpoints in `server.py`, `tools/database.py`, or any source file. Call a tool from the Inspector's **Tools** tab to trigger breakpoints.

### Tips for Success

- **Order matters:** Start the process in Inspector first, then attach from the IDE.
- **If attach fails:** Disconnect and reconnect in Inspector, then re-attach from the IDE.
- **Process cleanup:** The process continues running after disconnect; you can re-attach at any time.

---

## Port Reference

| Port | Purpose |
|------|---------|
| **6274** | MCP Inspector web interface (HTML/JavaScript UI) |

## Standalone Mode: Run MCP Without Inspector

Use the debug configuration **Run MCP server only (no Inspector)** to start the server directly in the terminal. This is useful for quick startup verification but doesn't integrate with Inspector's tool interface. Use Inspector + process-id attach for full debugging capability.

## Why Not Debug on Port 6274?

Port 6274 hosts the Inspector's web UI. The actual MCP server runs as a child process communicating with Inspector over **stdio pipes**, not through network ports.