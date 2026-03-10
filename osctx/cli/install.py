"""
macOS launchd install/uninstall for OSCTX daemon.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PLIST_LABEL = "com.osctx.daemon"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{PLIST_LABEL}.plist"
OSCTX_DIR = Path.home() / ".osctx"
LOG_PATH = OSCTX_DIR / "daemon.log"


def _plist_content(python_path: str, osctx_module: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{PLIST_LABEL}</string>

    <key>ProgramArguments</key>
    <array>
        <string>{python_path}</string>
        <string>-m</string>
        <string>uvicorn</string>
        <string>{osctx_module}</string>
        <string>--host</string>
        <string>127.0.0.1</string>
        <string>--port</string>
        <string>8765</string>
        <string>--log-level</string>
        <string>warning</string>
    </array>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <true/>

    <key>StandardOutPath</key>
    <string>{LOG_PATH}</string>

    <key>StandardErrorPath</key>
    <string>{LOG_PATH}</string>

    <key>WorkingDirectory</key>
    <string>{Path.home()}</string>
</dict>
</plist>
"""


def install() -> None:
    """Install the OSCTX daemon as a launchd service."""
    if sys.platform != "darwin":
        print("Error: launchd install is macOS-only. On Linux, use systemd.")
        raise SystemExit(1)

    python_path = sys.executable
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    OSCTX_DIR.mkdir(parents=True, exist_ok=True)

    plist = _plist_content(python_path, "osctx.daemon.main:app")
    PLIST_PATH.write_text(plist)
    PLIST_PATH.chmod(0o644)

    # Unload first in case it's already loaded
    subprocess.run(
        ["launchctl", "unload", str(PLIST_PATH)],
        capture_output=True,
    )

    result = subprocess.run(
        ["launchctl", "load", str(PLIST_PATH)],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        print(f"Error loading plist: {result.stderr}")
        raise SystemExit(1)

    print(f"OSCTX daemon installed and started.")
    print(f"  Plist: {PLIST_PATH}")
    print(f"  Logs:  {LOG_PATH}")
    print(f"  API:   http://localhost:8765/status")


def uninstall() -> None:
    """Remove the OSCTX daemon launchd service."""
    if sys.platform != "darwin":
        print("Error: launchd uninstall is macOS-only.")
        raise SystemExit(1)

    if not PLIST_PATH.exists():
        print("OSCTX daemon is not installed.")
        return

    subprocess.run(
        ["launchctl", "unload", str(PLIST_PATH)],
        capture_output=True,
    )
    PLIST_PATH.unlink()
    print("OSCTX daemon uninstalled.")
    print(f"Data preserved at: {OSCTX_DIR}")
    print("To remove data: rm -rf ~/.osctx")
