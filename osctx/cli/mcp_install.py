"""
osctx mcp install / uninstall — manages the Claude Desktop MCP server entry.

Writes/removes the osctx entry from:
  ~/Library/Application Support/Claude/claude_desktop_config.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

CLAUDE_CONFIG = (
    Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
)


def install() -> None:
    config = json.loads(CLAUDE_CONFIG.read_text()) if CLAUDE_CONFIG.exists() else {}
    config.setdefault("mcpServers", {})
    config["mcpServers"]["osctx"] = {
        "command": sys.executable,
        "args": ["-m", "osctx.mcp_server.server"],
        "env": {},
    }
    CLAUDE_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CLAUDE_CONFIG.write_text(json.dumps(config, indent=2))
    print("✓ osctx MCP server registered in Claude Desktop config")
    print(f"  Python : {sys.executable}")
    print(f"  Config : {CLAUDE_CONFIG}")
    print("  Restart Claude Desktop to activate.")


def uninstall() -> None:
    if not CLAUDE_CONFIG.exists():
        print("No Claude Desktop config found — nothing to remove.")
        return
    config = json.loads(CLAUDE_CONFIG.read_text())
    removed = config.get("mcpServers", {}).pop("osctx", None)
    if removed is None:
        print("osctx not found in Claude Desktop config.")
        return
    CLAUDE_CONFIG.write_text(json.dumps(config, indent=2))
    print("✓ osctx removed from Claude Desktop MCP servers.")
    print("  Restart Claude Desktop to apply.")
