"""
osctx doctor — checks all dependencies and reports in plain English.
"""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path

OSCTX_DIR = Path.home() / ".osctx"
CONFIG_PATH = OSCTX_DIR / "config.json"


def _check(label: str, ok: bool, detail: str = "") -> bool:
    status = "✓" if ok else "✗"
    print(f"  {status} {label}", end="")
    if detail:
        print(f"  ({detail})", end="")
    print()
    return ok


def run_doctor() -> int:
    """Run all diagnostic checks. Returns exit code (0 = all OK)."""
    print("\nOSCTX Doctor\n")
    failures = 0

    # Python version
    py_ok = sys.version_info >= (3, 11)
    if not _check(f"Python {sys.version_info.major}.{sys.version_info.minor}", py_ok,
                  "need 3.11+" if not py_ok else ""):
        failures += 1

    # Required packages
    required = ["fastapi", "uvicorn", "anthropic", "sentence_transformers", "sqlite_vec", "typer", "pydantic"]
    for pkg in required:
        try:
            importlib.import_module(pkg.replace("-", "_"))
            _check(f"Package: {pkg}", True)
        except ImportError:
            _check(f"Package: {pkg}", False, "not installed — run: pip install osctx")
            failures += 1

    # Config file
    config_exists = CONFIG_PATH.exists()
    _check(f"Config file: {CONFIG_PATH}", config_exists, "run: osctx config --set anthropic_api_key=sk-..." if not config_exists else "")
    if config_exists:
        try:
            cfg = json.loads(CONFIG_PATH.read_text())
            backend = cfg.get("extraction_backend", "anthropic")
            _check(f"Extraction backend: {backend}", True)

            if backend == "anthropic":
                has_key = bool(cfg.get("anthropic_api_key"))
                if not _check("Anthropic API key", has_key, "set with: osctx config --set anthropic_api_key=..."):
                    failures += 1
            elif backend == "openai":
                has_key = bool(cfg.get("openai_api_key"))
                if not _check("OpenAI API key", has_key):
                    failures += 1
            elif backend == "ollama":
                _check("Ollama backend selected", True, "make sure: ollama serve && ollama pull llama3.2:3b")
        except Exception as exc:
            _check("Config parse", False, str(exc))
            failures += 1

    # Database
    db_path = OSCTX_DIR / "memory.db"
    if db_path.exists():
        size_mb = db_path.stat().st_size / 1024 / 1024
        _check(f"Database: {db_path}", True, f"{size_mb:.1f} MB")
    else:
        _check(f"Database: {db_path}", False, "will be created on first import")

    # Daemon running
    try:
        import httpx
        resp = httpx.get("http://localhost:8765/status", timeout=2.0)
        stats = resp.json()
        _check("Daemon running (port 8765)", True,
               f"{stats.get('knowledge_units', 0)} units, queue: {stats.get('queue_depth', 0)}")
    except Exception:
        _check("Daemon running (port 8765)", False, "run: osctx install  (or: uvicorn osctx.daemon.main:app --port 8765)")

    # macOS: launchd plist
    if sys.platform == "darwin":
        plist_path = Path.home() / "Library" / "LaunchAgents" / "com.osctx.daemon.plist"
        _check("launchd plist installed", plist_path.exists(),
               "run: osctx install" if not plist_path.exists() else "")

    print()
    if failures == 0:
        print("All checks passed. OSCTX is ready.\n")
        return 0
    else:
        print(f"{failures} issue(s) found. See above for fixes.\n")
        return 1
