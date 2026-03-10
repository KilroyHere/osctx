"""
Shared dataclasses for all conversation parsers.
These are the canonical types that flow through the entire pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class Message:
    role: Literal["user", "assistant"]
    content: str
    timestamp: float | None = None  # unix timestamp, may be None

    def __post_init__(self) -> None:
        if self.role not in ("user", "assistant"):
            raise ValueError(f"Invalid role: {self.role!r}")
        if not self.content or not self.content.strip():
            raise ValueError("Message content must not be empty")


@dataclass
class Conversation:
    id: str                          # source-specific ID (e.g. ChatGPT conv ID)
    source: str                      # 'chatgpt' | 'claude' | 'gemini' | 'manual'
    title: str | None
    messages: list[Message]
    url: str | None = None
    create_time: float | None = None  # unix timestamp

    def __post_init__(self) -> None:
        if not self.messages:
            raise ValueError("Conversation must have at least one message")

    @property
    def first_message_content(self) -> str:
        return self.messages[0].content

    @property
    def message_count(self) -> int:
        return len(self.messages)

    def to_text(self) -> str:
        """Format conversation as plain text for LLM extraction."""
        lines: list[str] = []
        for msg in self.messages:
            speaker = "User" if msg.role == "user" else "Assistant"
            lines.append(f"{speaker}: {msg.content}")
            lines.append("")
        return "\n".join(lines).strip()
