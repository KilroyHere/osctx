"""
Parser for Google Gemini Takeout export.

The Takeout produces Gemini Apps Activity.json inside:
  Takeout/Gemini Apps Activity/

Structure (both gmr:-prefixed and non-prefixed variants handled):
{
  "gmr:chatSessionList": [
    {
      "gmr:chatSession": {
        "gmr:id": "...",
        "gmr:title": "...",
        "gmr:createTime": "2025-11-03T14:00:00Z",
        "gmr:turnList": [
          {
            "gmr:turn": {
              "gmr:userContent": { "gmr:parts": [{"gmr:text": "..."}] },
              "gmr:modelContent": { "gmr:parts": [{"gmr:text": "..."}] }
            }
          }
        ]
      }
    }
  ]
}
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .base import Conversation, Message


def _key(d: dict[str, Any], *names: str) -> Any:
    """Try multiple key variants (gmr:-prefixed and plain) in order."""
    for name in names:
        if name in d:
            return d[name]
        gmr = f"gmr:{name}"
        if gmr in d:
            return d[gmr]
    return None


def _extract_text(content_block: dict[str, Any] | None) -> str:
    if not content_block:
        return ""
    parts = _key(content_block, "parts") or []
    texts: list[str] = []
    for part in parts:
        if isinstance(part, dict):
            text = _key(part, "text")
            if isinstance(text, str):
                texts.append(text)
        elif isinstance(part, str):
            texts.append(part)
    return "".join(texts).strip()


def _parse_timestamp(ts_str: str | None) -> float | None:
    if not ts_str:
        return None
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return dt.replace(tzinfo=timezone.utc).timestamp()
    except Exception:
        return None


def parse_gemini_export(path: str) -> list[Conversation]:
    """Parse Google Gemini Takeout JSON export.

    Args:
        path: Path to Gemini Apps Activity.json

    Returns:
        List of Conversation objects.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))

    session_list = _key(data, "chatSessionList") or []
    conversations: list[Conversation] = []

    for i, session_wrapper in enumerate(session_list):
        try:
            session = _key(session_wrapper, "chatSession") or session_wrapper
            conv_id = _key(session, "id") or f"gemini_{i}"
            title = _key(session, "title")
            create_time_str = _key(session, "createTime")
            create_time = _parse_timestamp(create_time_str)

            turn_list = _key(session, "turnList") or []
            messages: list[Message] = []

            for turn_wrapper in turn_list:
                turn = _key(turn_wrapper, "turn") or turn_wrapper

                user_content = _key(turn, "userContent")
                user_text = _extract_text(user_content)
                if user_text:
                    messages.append(Message(role="user", content=user_text, timestamp=create_time))

                model_content = _key(turn, "modelContent")
                model_text = _extract_text(model_content)
                if model_text:
                    messages.append(Message(role="assistant", content=model_text, timestamp=create_time))

            if messages:
                conversations.append(Conversation(
                    id=conv_id,
                    source="gemini",
                    title=title,
                    messages=messages,
                    create_time=create_time,
                ))
        except Exception as exc:
            print(f"Warning: skipping Gemini session {i}: {exc}", file=sys.stderr)

    return conversations


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m osctx.daemon.parsers.gemini <path/to/gemini-activity.json>")
        sys.exit(1)

    import json as _json
    convs = parse_gemini_export(sys.argv[1])
    print(f"Parsed {len(convs)} Gemini conversations")
    for conv in convs[:3]:
        print(_json.dumps({"id": conv.id, "title": conv.title, "messages": conv.message_count}, indent=2))
