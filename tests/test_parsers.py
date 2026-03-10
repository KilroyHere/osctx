"""
Tests for the ChatGPT and Gemini parsers.
Uses the exact data shapes from DATA_SAMPLE.md.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from osctx.daemon.parsers.base import Conversation, Message
from osctx.daemon.parsers.chatgpt import (
    parse_chatgpt_export,
    parse_conversation,
)
from osctx.daemon.parsers.gemini import parse_gemini_export


# ---------------------------------------------------------------------------
# Fixtures — data from DATA_SAMPLE.md
# ---------------------------------------------------------------------------

SAMPLE_CHATGPT_CONV = {
    "id": "conv_abc123",
    "title": "PostgreSQL schema for auth system",
    "create_time": 1730649600.0,
    "update_time": 1730653200.0,
    "mapping": {
        "node_root": {
            "id": "node_root",
            "message": None,
            "parent": None,
            "children": ["node_sys_1"],
        },
        "node_sys_1": {
            "id": "node_sys_1",
            "message": {
                "id": "node_sys_1",
                "author": {"role": "system"},
                "content": {"content_type": "text", "parts": ["You are a helpful assistant."]},
                "create_time": 1730649600.0,
            },
            "parent": "node_root",
            "children": ["node_user_1"],
        },
        "node_user_1": {
            "id": "node_user_1",
            "message": {
                "id": "node_user_1",
                "author": {"role": "user"},
                "content": {
                    "content_type": "text",
                    "parts": ["Should I use UUIDs or auto-increment integers for user IDs?"],
                },
                "create_time": 1730649660.0,
            },
            "parent": "node_sys_1",
            "children": ["node_asst_1"],
        },
        "node_asst_1": {
            "id": "node_asst_1",
            "message": {
                "id": "node_asst_1",
                "author": {"role": "assistant"},
                "content": {
                    "content_type": "text",
                    "parts": ["Use UUIDs for security and distributed systems compatibility."],
                },
                "create_time": 1730649720.0,
            },
            "parent": "node_user_1",
            "children": ["node_user_2"],
        },
        "node_user_2": {
            "id": "node_user_2",
            "message": {
                "id": "node_user_2",
                "author": {"role": "user"},
                "content": {"content_type": "text", "parts": ["DB sessions or JWTs?"]},
                "create_time": 1730649780.0,
            },
            "parent": "node_asst_1",
            "children": ["node_asst_2"],
        },
        "node_asst_2": {
            "id": "node_asst_2",
            "message": {
                "id": "node_asst_2",
                "author": {"role": "assistant"},
                "content": {
                    "content_type": "text",
                    "parts": ["DB sessions for instant revocation."],
                },
                "create_time": 1730649840.0,
            },
            "parent": "node_user_2",
            "children": [],
        },
    },
}


# ---------------------------------------------------------------------------
# ChatGPT parser tests
# ---------------------------------------------------------------------------


def test_parse_conversation_basic():
    conv = parse_conversation(SAMPLE_CHATGPT_CONV)

    assert conv is not None
    assert conv.id == "conv_abc123"
    assert conv.source == "chatgpt"
    assert conv.title == "PostgreSQL schema for auth system"
    assert conv.create_time == 1730649600.0


def test_parse_conversation_message_count():
    conv = parse_conversation(SAMPLE_CHATGPT_CONV)

    assert conv is not None
    # Should have 4 messages: user, asst, user, asst (system skipped)
    assert conv.message_count == 4


def test_parse_conversation_roles():
    conv = parse_conversation(SAMPLE_CHATGPT_CONV)

    assert conv is not None
    roles = [m.role for m in conv.messages]
    assert roles == ["user", "assistant", "user", "assistant"]


def test_parse_conversation_first_message():
    conv = parse_conversation(SAMPLE_CHATGPT_CONV)

    assert conv is not None
    assert "UUIDs" in conv.messages[0].content or "auto-increment" in conv.messages[0].content


def test_parse_conversation_system_messages_skipped():
    conv = parse_conversation(SAMPLE_CHATGPT_CONV)

    assert conv is not None
    for msg in conv.messages:
        assert msg.role in ("user", "assistant")


def test_parse_conversation_timestamps_preserved():
    conv = parse_conversation(SAMPLE_CHATGPT_CONV)

    assert conv is not None
    # First user message should have timestamp
    assert conv.messages[0].timestamp == 1730649660.0


def test_parse_conversation_null_message_skipped():
    raw = dict(SAMPLE_CHATGPT_CONV)
    mapping = dict(raw["mapping"])
    # Inject a node with null message
    mapping["null_node"] = {"id": "null_node", "message": None, "parent": "node_asst_2", "children": []}
    mapping["node_asst_2"] = dict(mapping["node_asst_2"])
    mapping["node_asst_2"]["children"] = ["null_node"]
    raw["mapping"] = mapping

    conv = parse_conversation(raw)
    assert conv is not None
    # null_node should be skipped, count stays at 4
    assert conv.message_count == 4


def test_parse_conversation_multimodal_parts():
    """content.parts with dicts (image refs) should be skipped, strings extracted."""
    raw = dict(SAMPLE_CHATGPT_CONV)
    mapping = dict(raw["mapping"])
    mapping["node_user_1"] = dict(mapping["node_user_1"])
    mapping["node_user_1"]["message"] = dict(mapping["node_user_1"]["message"])
    # Mix of string and dict parts
    mapping["node_user_1"]["message"]["content"] = {
        "content_type": "multimodal_text",
        "parts": [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}},
            "What does this diagram show?",
        ],
    }
    raw["mapping"] = mapping

    conv = parse_conversation(raw)
    assert conv is not None
    # Should have extracted only the string part
    user_msgs = [m for m in conv.messages if m.role == "user"]
    assert any("diagram" in m.content for m in user_msgs)


def test_parse_conversation_tool_role_skipped():
    """Messages with role='tool' should be excluded."""
    raw = dict(SAMPLE_CHATGPT_CONV)
    mapping = dict(raw["mapping"])
    mapping["tool_node"] = {
        "id": "tool_node",
        "message": {
            "id": "tool_node",
            "author": {"role": "tool"},
            "content": {"content_type": "text", "parts": ["function result"]},
            "create_time": 1730649750.0,
        },
        "parent": "node_asst_1",
        "children": ["node_user_2"],
    }
    mapping["node_asst_1"] = dict(mapping["node_asst_1"])
    mapping["node_asst_1"]["children"] = ["tool_node"]
    raw["mapping"] = mapping

    conv = parse_conversation(raw)
    assert conv is not None
    for msg in conv.messages:
        assert msg.content != "function result"


def test_parse_conversation_branching_follows_last_child():
    """When a node has multiple children, follow the last one (most recent branch)."""
    raw = dict(SAMPLE_CHATGPT_CONV)
    mapping = dict(raw["mapping"])
    # Add a sibling to node_user_2 (branched conversation)
    mapping["node_user_2_alt"] = {
        "id": "node_user_2_alt",
        "message": {
            "id": "node_user_2_alt",
            "author": {"role": "user"},
            "content": {"content_type": "text", "parts": ["Alternative question branch"]},
            "create_time": 1730649770.0,
        },
        "parent": "node_asst_1",
        "children": [],
    }
    # node_asst_1 now has two children; last child is node_user_2
    mapping["node_asst_1"] = dict(mapping["node_asst_1"])
    mapping["node_asst_1"]["children"] = ["node_user_2_alt", "node_user_2"]
    raw["mapping"] = mapping

    conv = parse_conversation(raw)
    assert conv is not None
    contents = [m.content for m in conv.messages]
    # Should follow node_user_2, not node_user_2_alt
    assert "DB sessions" in " ".join(contents)  # node_user_2 leads to node_asst_2
    assert "Alternative question branch" not in contents


def test_parse_conversation_empty_mapping():
    raw = {"id": "x", "title": None, "create_time": None, "mapping": {}}
    conv = parse_conversation(raw)
    assert conv is None


def test_parse_chatgpt_export_from_file():
    data = [SAMPLE_CHATGPT_CONV]
    with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
        json.dump(data, f)
        path = f.name

    conversations = parse_chatgpt_export(path)
    assert len(conversations) == 1
    assert conversations[0].id == "conv_abc123"

    Path(path).unlink()


def test_parse_chatgpt_export_skips_bad_conversations():
    """A malformed conversation should not abort the whole import."""
    data = [
        SAMPLE_CHATGPT_CONV,
        {"id": "bad", "mapping": None},  # invalid
    ]
    with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
        json.dump(data, f)
        path = f.name

    conversations = parse_chatgpt_export(path)
    # Only the valid one should be returned
    assert len(conversations) == 1
    assert conversations[0].id == "conv_abc123"

    Path(path).unlink()


# ---------------------------------------------------------------------------
# Base dataclass tests
# ---------------------------------------------------------------------------


def test_message_invalid_role():
    with pytest.raises(ValueError):
        Message(role="system", content="hello")


def test_message_empty_content():
    with pytest.raises(ValueError):
        Message(role="user", content="")


def test_conversation_empty_messages():
    with pytest.raises(ValueError):
        Conversation(id="x", source="chatgpt", title=None, messages=[])


def test_conversation_to_text():
    conv = Conversation(
        id="x",
        source="chatgpt",
        title=None,
        messages=[
            Message(role="user", content="Hello"),
            Message(role="assistant", content="Hi there"),
        ],
    )
    text = conv.to_text()
    assert "User: Hello" in text
    assert "Assistant: Hi there" in text


# ---------------------------------------------------------------------------
# Gemini parser tests
# ---------------------------------------------------------------------------

SAMPLE_GEMINI = {
    "gmr:chatSessionList": [
        {
            "gmr:chatSession": {
                "gmr:id": "session_abc",
                "gmr:title": "Python async patterns",
                "gmr:createTime": "2025-11-03T14:00:00Z",
                "gmr:turnList": [
                    {
                        "gmr:turn": {
                            "gmr:userContent": {
                                "gmr:parts": [{"gmr:text": "What is asyncio.gather?"}]
                            },
                            "gmr:modelContent": {
                                "gmr:parts": [{"gmr:text": "asyncio.gather runs coroutines concurrently."}]
                            },
                        }
                    }
                ],
            }
        }
    ]
}


def test_parse_gemini_basic():
    with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
        json.dump(SAMPLE_GEMINI, f)
        path = f.name

    convs = parse_gemini_export(path)
    assert len(convs) == 1
    assert convs[0].source == "gemini"
    assert convs[0].message_count == 2
    assert convs[0].messages[0].role == "user"
    assert convs[0].messages[1].role == "assistant"

    Path(path).unlink()
