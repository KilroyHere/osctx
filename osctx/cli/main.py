"""
OSCTX CLI — entry point for all user-facing commands.

Commands:
  osctx import <file>        — Bulk import from ChatGPT/Gemini export
  osctx search <query>       — Search the knowledge base from terminal
  osctx status               — Show daemon status and stats
  osctx status --watch       — Watch stats update live
  osctx config --set k=v     — Set a config value
  osctx install              — Install daemon as macOS launchd service
  osctx uninstall            — Remove launchd service
  osctx doctor               — Check all dependencies
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Annotated, Optional

import typer
import httpx

app = typer.Typer(
    name="osctx",
    help="Universal memory layer for AI conversations.",
    add_completion=False,
    no_args_is_help=True,
)

mcp_app = typer.Typer(help="Claude Desktop MCP integration.")
app.add_typer(mcp_app, name="mcp")

OSCTX_DIR = Path.home() / ".osctx"
CONFIG_PATH = OSCTX_DIR / "config.json"
DAEMON_URL = "http://localhost:8765"


def _daemon_ok() -> bool:
    try:
        resp = httpx.get(f"{DAEMON_URL}/status", timeout=2.0)
        return resp.status_code == 200
    except Exception:
        return False


def _load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text())
        except Exception:
            pass
    return {}


def _save_config(cfg: dict) -> None:
    OSCTX_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
    CONFIG_PATH.chmod(0o600)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.command("import")
def import_cmd(
    file: Annotated[Path, typer.Argument(help="Path to conversations.json or Gemini export")],
    source: Annotated[str, typer.Option(help="Source: chatgpt, gemini")] = "chatgpt",
) -> None:
    """Bulk import conversations from an AI platform export file."""
    if not file.exists():
        typer.echo(f"Error: file not found: {file}", err=True)
        raise typer.Exit(1)

    if _daemon_ok():
        # Post to daemon
        try:
            resp = httpx.post(
                f"{DAEMON_URL}/ingest/bulk",
                json={"file_path": str(file.expanduser().resolve()), "source": source},
                timeout=30.0,
            )
            data = resp.json()
            typer.echo(f"Queued {data.get('conversations_queued', '?')} conversations for extraction.")
            typer.echo(f"Queue depth: {data.get('queue_depth', '?')}")
            typer.echo("Run 'osctx status --watch' to monitor progress.")
        except Exception as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(1)
    else:
        # Daemon not running — import directly (blocking)
        typer.echo("Daemon not running. Running import directly (extraction disabled).")
        typer.echo("Start the daemon with 'osctx install' to enable extraction.")

        from osctx.daemon.database import DB_PATH, get_conn, init_db, upsert_conversation, conversation_id_for

        if source == "chatgpt":
            from osctx.daemon.parsers.chatgpt import parse_chatgpt_export
            conversations = parse_chatgpt_export(str(file))
        elif source == "gemini":
            from osctx.daemon.parsers.gemini import parse_gemini_export
            conversations = parse_gemini_export(str(file))
        else:
            typer.echo(f"Unknown source: {source}", err=True)
            raise typer.Exit(1)

        init_db(DB_PATH)
        stored = 0
        with get_conn(DB_PATH) as conn:
            for conv in conversations:
                msgs = [{"role": m.role, "content": m.content} for m in conv.messages]
                conv_id = conversation_id_for(conv.url or f"{conv.source}:{conv.id}", msgs[0]["content"] if msgs else "")
                inserted = upsert_conversation(
                    conn,
                    conv_id=conv_id,
                    source=conv.source,
                    url=conv.url,
                    title=conv.title,
                    captured_at=int(conv.create_time or time.time()),
                    messages=msgs,
                    status="pending",
                )
                if inserted:
                    stored += 1

        typer.echo(f"Stored {stored}/{len(conversations)} conversations (skipped {len(conversations)-stored} duplicates).")
        typer.echo("Start the daemon to run extraction: osctx install")


@app.command()
def search(
    query: Annotated[str, typer.Argument(help="Search query")],
    limit: Annotated[int, typer.Option("--limit", "-n", help="Number of results")] = 5,
) -> None:
    """Search your AI conversation knowledge base."""
    if _daemon_ok():
        try:
            resp = httpx.get(
                f"{DAEMON_URL}/search",
                params={"q": query, "limit": limit},
                timeout=10.0,
            )
            data = resp.json()
            results = data.get("results", [])
        except Exception as exc:
            typer.echo(f"Search failed: {exc}", err=True)
            raise typer.Exit(1)
    else:
        # Search directly against DB
        from osctx.daemon.search import search as direct_search
        results = [r.to_dict() for r in direct_search(query, limit=limit)]

    if not results:
        typer.echo("No results found.")
        return

    for i, r in enumerate(results, 1):
        score = f"{r.get('similarity_score', 0):.2f}"
        source = r.get("source", "?")
        date = r.get("source_date", "")
        category = r.get("category", "")
        tags = ", ".join(r.get("topic_tags", []))

        typer.echo(f"\n{i}. [{category}] {r['content'][:120]}")
        typer.echo(f"   {source} · {date} · score: {score}")
        if tags:
            typer.echo(f"   tags: {tags}")
        if r.get("context"):
            typer.echo(f"   {r['context']}")


@app.command()
def status(
    watch: Annotated[bool, typer.Option("--watch", help="Refresh every 3 seconds")] = False,
) -> None:
    """Show daemon status and knowledge base statistics."""
    def _print_status() -> None:
        try:
            resp = httpx.get(f"{DAEMON_URL}/status", timeout=3.0)
            data = resp.json()
            typer.echo(f"Status:     {data.get('status', 'unknown')}")
            typer.echo(f"Units:      {data.get('knowledge_units', 0)}")
            typer.echo(f"Convs:      {data.get('conversations', 0)}")
            typer.echo(f"Queue:      {data.get('queue_depth', 0)} pending extraction")
            by_source = data.get("by_source", {})
            if by_source:
                typer.echo(f"By source:  {', '.join(f'{k}={v}' for k,v in by_source.items())}")
        except Exception:
            typer.echo("Daemon not running. Start with: osctx install")

    if watch:
        try:
            while True:
                typer.clear()
                _print_status()
                time.sleep(3)
        except KeyboardInterrupt:
            pass
    else:
        _print_status()


@app.command()
def config(
    set_: Annotated[Optional[str], typer.Option("--set", help="Set a value: key=value")] = None,
    get: Annotated[Optional[str], typer.Option("--get", help="Get a config value by key")] = None,
    show: Annotated[bool, typer.Option("--show", help="Print full config")] = False,
) -> None:
    """View or modify OSCTX configuration."""
    cfg = _load_config()

    if set_:
        if "=" not in set_:
            typer.echo("Error: use --set key=value format", err=True)
            raise typer.Exit(1)
        key, _, value = set_.partition("=")
        key = key.strip()
        value = value.strip()
        # Try to parse as JSON (handles booleans, numbers)
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            pass  # Keep as string
        cfg[key] = value
        _save_config(cfg)
        typer.echo(f"Set {key} = {value!r}")

    elif get:
        if get in cfg:
            typer.echo(str(cfg[get]))
        else:
            typer.echo(f"Key not found: {get}")

    elif show:
        # Redact API keys
        safe = dict(cfg)
        for k in list(safe.keys()):
            if "api_key" in k and safe[k]:
                safe[k] = safe[k][:8] + "..." if len(str(safe[k])) > 8 else "***"
        typer.echo(json.dumps(safe, indent=2))

    else:
        typer.echo("Use --set, --get, or --show")


@app.command()
def install() -> None:
    """Install OSCTX daemon as a macOS launchd service (auto-starts on login)."""
    from .install import install as do_install
    do_install()


@app.command()
def uninstall() -> None:
    """Remove the OSCTX daemon launchd service."""
    from .install import uninstall as do_uninstall
    do_uninstall()


@app.command()
def doctor() -> None:
    """Check all dependencies and configuration."""
    from .doctor import run_doctor
    exit_code = run_doctor()
    raise typer.Exit(exit_code)


@mcp_app.command("install")
def mcp_install() -> None:
    """Register osctx as an MCP server in Claude Desktop."""
    from .mcp_install import install
    install()


@mcp_app.command("uninstall")
def mcp_uninstall() -> None:
    """Remove osctx from Claude Desktop MCP servers."""
    from .mcp_install import uninstall
    uninstall()


def main() -> None:
    app()


if __name__ == "__main__":
    main()
