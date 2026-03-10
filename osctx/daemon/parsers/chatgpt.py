"""
Parser for ChatGPT's conversations.json export format.

The export is a list of conversation objects. Each conversation has a `mapping`
field: a dict of UUID → node. Nodes form a tree. We traverse from root to leaf
to reconstruct the linear message sequence, always following the last child
(most recent branch) when branching occurs.

Edge cases handled:
- message field is null on some nodes → skip
- content.parts may contain dicts (multimodal/image refs) → extract strings only
- content.content_type may be non-"text" (tether_browsing, multimodal_text) → skip
- Branched conversations → follow last child
- author.role "tool" / "system" → skip
- create_time on message may be null → use conversation create_time as fallback
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from .base import Conversation, Message

_SKIP_ROLES = {"system", "tool"}
_TEXT_CONTENT_TYPES = {"text", "multimodal_text"}


def _extract_text_from_parts(parts: list[Any]) -> str:
    """Extract plain string content from a content.parts list.

    Parts may be strings or dicts (multimodal refs). We keep only strings.
    """
    texts: list[str] = []
    for part in parts:
        if isinstance(part, str):
            texts.append(part)
        elif isinstance(part, dict):
            # Some multimodal_text parts have a nested "text" key
            if "text" in part and isinstance(part["text"], str):
                texts.append(part["text"])
    return "".join(texts)


def _traverse(
    node_id: str,
    mapping: dict[str, Any],
    fallback_time: float | None,
) -> list[Message]:
    """Recursively traverse the conversation tree from node_id to leaf.

    Always follows the last child (most recent branch).
    Returns messages in chronological order.
    """
    node = mapping.get(node_id)
    if node is None:
        return []

    messages: list[Message] = []

    # Process current node's message
    raw_msg = node.get("message")
    if raw_msg is not None:
        author = raw_msg.get("author") or {}
        role = author.get("role", "")

        if role not in _SKIP_ROLES and role in ("user", "assistant"):
            content_block = raw_msg.get("content") or {}
            content_type = content_block.get("content_type", "text")

            if content_type in _TEXT_CONTENT_TYPES:
                parts = content_block.get("parts") or []
                text = _extract_text_from_parts(parts).strip()

                if text:
                    ts = raw_msg.get("create_time") or fallback_time
                    messages.append(Message(
                        role=role,
                        content=text,
                        timestamp=ts,
                    ))

    # Recurse into last child (most recent branch)
    children = node.get("children") or []
    if children:
        last_child = children[-1]
        messages.extend(_traverse(last_child, mapping, fallback_time))

    return messages


def _find_root(mapping: dict[str, Any]) -> str | None:
    """Find the root node: the node with no parent."""
    for node_id, node in mapping.items():
        if node.get("parent") is None:
            return node_id
    return None


def parse_conversation(raw: dict[str, Any]) -> Conversation | None:
    """Parse a single conversation object from conversations.json.

    Returns None if the conversation has no usable messages.
    """
    conv_id = raw.get("id") or ""
    title = raw.get("title")
    create_time = raw.get("create_time")

    mapping = raw.get("mapping") or {}
    if not mapping:
        return None

    root_id = _find_root(mapping)
    if root_id is None:
        return None

    messages = _traverse(root_id, mapping, fallback_time=create_time)
    if not messages:
        return None

    return Conversation(
        id=conv_id,
        source="chatgpt",
        title=title,
        messages=messages,
        url=None,  # not in export file
        create_time=create_time,
    )


def parse_chatgpt_export(path: str) -> list[Conversation]:
    """Parse ChatGPT's conversations.json export file.

    Args:
        path: Path to conversations.json

    Returns:
        List of Conversation objects, skipping any with no usable messages.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))

    if not isinstance(data, list):
        raise ValueError(
            f"Expected a JSON array at top level, got {type(data).__name__}"
        )

    conversations: list[Conversation] = []
    for i, raw in enumerate(data):
        try:
            conv = parse_conversation(raw)
            if conv is not None:
                conversations.append(conv)
        except Exception as exc:
            conv_id = raw.get("id", f"index={i}")
            # Log and continue — one bad conversation should not abort the import
            print(f"Warning: skipping conversation {conv_id}: {exc}", file=sys.stderr)

    return conversations


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m osctx.daemon.parsers.chatgpt <path/to/conversations.json>")
        sys.exit(1)

    convs = parse_chatgpt_export(sys.argv[1])
    print(f"Parsed {len(convs)} conversations")
    print()

    for conv in convs[:3]:
        print(json.dumps({
            "id": conv.id,
            "title": conv.title,
            "message_count": conv.message_count,
            "create_time": conv.create_time,
            "first_user_message": next(
                (m.content[:120] for m in conv.messages if m.role == "user"), None
            ),
        }, indent=2))
        print()
