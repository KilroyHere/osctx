"""
Tests for the LLM extraction pipeline.

All network calls are mocked — no real API keys required.
Tests cover:
  - chunk_messages(): splitting logic, topic-shift heuristic
  - extract_from_messages(): all 4 backends, confidence filter, dedup within call
  - summarize_conversation(): summary generation, truncation, error recovery
  - Rolling summary between chunks
  - Graceful degradation when a backend call fails
"""

from __future__ import annotations

import asyncio
import json
import sys
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from osctx.daemon.extraction import (
    ExtractedUnit,
    _format_messages,
    _is_topic_shift,
    chunk_messages,
    extract_from_messages,
    summarize_conversation,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_messages(n: int = 4) -> list[dict]:
    """Return n alternating user/assistant messages."""
    msgs = []
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        msgs.append({"role": role, "content": f"Message {i}: {'x' * 100}"})
    return msgs


def make_long_messages(total_chars: int) -> list[dict]:
    """Return messages whose total content is approximately total_chars."""
    content = "a" * (total_chars // 4)
    return [
        {"role": "user", "content": content},
        {"role": "assistant", "content": content},
        {"role": "user", "content": content},
        {"role": "assistant", "content": content},
    ]


def _fake_unit_dict(**overrides) -> dict:
    base = {
        "content": "Use UUIDs for distributed IDs",
        "category": "decision",
        "topic_tags": ["database"],
        "confidence": 0.9,
        "context": "Decided during system design",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# chunk_messages
# ---------------------------------------------------------------------------

class TestChunkMessages:
    def test_empty_returns_empty(self):
        assert chunk_messages([]) == []

    def test_short_conversation_is_single_chunk(self):
        msgs = make_messages(6)
        chunks = chunk_messages(msgs)
        assert len(chunks) == 1
        assert chunks[0] == msgs

    def test_long_conversation_splits(self):
        # 6000 tokens * 4 chars = 24000 chars threshold
        msgs = make_long_messages(30_000)
        chunks = chunk_messages(msgs)
        assert len(chunks) >= 2

    def test_each_chunk_within_hard_cap(self):
        msgs = make_long_messages(50_000)
        chunks = chunk_messages(msgs)
        for chunk in chunks:
            total = sum(len(m.get("content", "")) for m in chunk)
            assert total <= 6000 * 4 * 1.1  # 10% tolerance for boundary messages

    def test_all_messages_preserved_across_chunks(self):
        msgs = make_long_messages(40_000)
        chunks = chunk_messages(msgs)
        reconstructed = [m for chunk in chunks for m in chunk]
        assert reconstructed == msgs


class TestIsTopicShift:
    def test_non_user_message_is_not_shift(self):
        msg = {"role": "assistant", "content": "I will explain Python decorators"}
        recent = [{"role": "user", "content": "explain decorators"}]
        assert _is_topic_shift(msg, recent) is False

    def test_empty_recent_is_not_shift(self):
        msg = {"role": "user", "content": "Tell me about cats"}
        assert _is_topic_shift(msg, []) is False

    def test_related_topic_is_not_shift(self):
        msg = {"role": "user", "content": "What about Python decorators in classes?"}
        recent = [
            {"role": "assistant", "content": "Python decorators wrap functions to modify behavior"},
        ]
        assert _is_topic_shift(msg, recent) is False

    def test_unrelated_topic_is_shift(self):
        msg = {"role": "user", "content": "Tell me about medieval castles"}
        recent = [
            {"role": "assistant", "content": "Python decorators are syntactic sugar for higher order functions"},
        ]
        assert _is_topic_shift(msg, recent) is True


class TestFormatMessages:
    def test_basic_format(self):
        msgs = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
        ]
        text = _format_messages(msgs)
        assert "User: Hello" in text
        assert "Assistant: Hi there" in text

    def test_preamble_included(self):
        msgs = [{"role": "user", "content": "Continue"}]
        text = _format_messages(msgs, preamble="We discussed Python earlier")
        assert "We discussed Python earlier" in text
        assert "[Context from earlier" in text


# ---------------------------------------------------------------------------
# Mock factories for each backend
# ---------------------------------------------------------------------------

def _anthropic_response(units: list[dict]) -> MagicMock:
    """Build a mock Anthropic messages.create() response with tool_use block."""
    tool_block = SimpleNamespace(
        type="tool_use",
        name="extract_knowledge",
        input={"units": units},
    )
    return SimpleNamespace(content=[tool_block])


def _anthropic_summary_response(text: str) -> MagicMock:
    """Build a mock Anthropic response for summarization."""
    block = SimpleNamespace(text=text)
    return SimpleNamespace(content=[block])


def _openai_response(units: list[dict]) -> MagicMock:
    """Build a mock OpenAI chat.completions.create() response."""
    tool_call = SimpleNamespace(
        function=SimpleNamespace(arguments=json.dumps({"units": units}))
    )
    message = SimpleNamespace(tool_calls=[tool_call], content=None)
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice])


def _openai_summary_response(text: str) -> MagicMock:
    message = SimpleNamespace(content=text, tool_calls=None)
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice])


def _gemini_response(units: list[dict]) -> MagicMock:
    """Build a mock Gemini generate_content() response."""
    return SimpleNamespace(text=json.dumps(units))


def _ollama_response(units: list[dict]) -> MagicMock:
    """Build a mock httpx response for the Ollama API."""
    mock = MagicMock()
    mock.raise_for_status = MagicMock()
    mock.json.return_value = {"response": json.dumps(units)}
    return mock


# ---------------------------------------------------------------------------
# extract_from_messages — Anthropic backend
# ---------------------------------------------------------------------------

class TestExtractAnthropic:
    CONFIG = {
        "extraction_backend": "anthropic",
        "anthropic_api_key": "test-key",
    }

    @pytest.mark.asyncio
    async def test_extracts_units(self):
        units = [_fake_unit_dict(), _fake_unit_dict(content="Redis for caching sessions", category="solution")]

        mock_create = AsyncMock(return_value=_anthropic_response(units))
        mock_client = MagicMock()
        mock_client.messages.create = mock_create

        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            result = await extract_from_messages(make_messages(4), config=self.CONFIG)

        assert len(result) == 2
        assert all(isinstance(u, ExtractedUnit) for u in result)
        assert result[0].content == "Use UUIDs for distributed IDs"
        assert result[0].category == "decision"

    @pytest.mark.asyncio
    async def test_filters_low_confidence(self):
        units = [
            _fake_unit_dict(confidence=0.9),
            _fake_unit_dict(content="Low confidence fact", confidence=0.5),
            _fake_unit_dict(content="Another low one", confidence=0.3),
        ]

        mock_create = AsyncMock(return_value=_anthropic_response(units))
        mock_client = MagicMock()
        mock_client.messages.create = mock_create

        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            result = await extract_from_messages(make_messages(4), config=self.CONFIG)

        # Only confidence >= 0.7 should pass
        assert len(result) == 1
        assert result[0].confidence == 0.9

    @pytest.mark.asyncio
    async def test_deduplicates_within_call(self):
        """Same content from two chunks should appear only once."""
        duplicate_content = "Use UUIDs for distributed IDs"
        units = [_fake_unit_dict(content=duplicate_content)]

        extraction_calls = 0

        async def mock_create_fn(*args, **kwargs):
            nonlocal extraction_calls
            # Distinguish extraction (has tool_choice) from summary (no tool_choice)
            if kwargs.get("tool_choice"):
                extraction_calls += 1
                return _anthropic_response(units)
            else:
                # Rolling summary call between chunks
                return _anthropic_summary_response("Earlier we discussed UUIDs.")

        mock_client = MagicMock()
        mock_client.messages.create = mock_create_fn

        # Force two chunks by mocking chunk_messages
        two_chunks = [make_messages(2), make_messages(2)]
        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            with patch("osctx.daemon.extraction.chunk_messages", return_value=two_chunks):
                result = await extract_from_messages(make_messages(4), config=self.CONFIG)

        assert len(result) == 1, "Duplicate content across chunks should be deduplicated"
        assert extraction_calls == 2, "Both chunks should have been processed"

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_units(self):
        mock_create = AsyncMock(return_value=_anthropic_response([]))
        mock_client = MagicMock()
        mock_client.messages.create = mock_create

        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            result = await extract_from_messages(make_messages(2), config=self.CONFIG)

        assert result == []

    @pytest.mark.asyncio
    async def test_continues_on_chunk_error(self):
        """If one chunk fails, subsequent chunks should still be processed."""
        good_unit = _fake_unit_dict(content="Redis for caching")
        call_count = 0

        async def flaky_create(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("API timeout")
            return _anthropic_response([good_unit])

        mock_client = MagicMock()
        mock_client.messages.create = flaky_create

        two_chunks = [make_messages(2), make_messages(2)]
        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            with patch("osctx.daemon.extraction.chunk_messages", return_value=two_chunks):
                result = await extract_from_messages(make_messages(4), config=self.CONFIG)

        # Second chunk succeeded despite first failing
        assert len(result) == 1
        assert result[0].content == "Redis for caching"


# ---------------------------------------------------------------------------
# extract_from_messages — OpenAI backend
# ---------------------------------------------------------------------------

def _patch_openai(mock_client):
    """Context manager that injects a mock openai module into sys.modules.

    The openai package may not be installed, but we can still test the extraction
    code by providing a fake module that returns our mock client.
    """
    mock_openai_module = MagicMock()
    mock_openai_module.AsyncOpenAI = MagicMock(return_value=mock_client)
    return patch.dict(sys.modules, {"openai": mock_openai_module})


class TestExtractOpenAI:
    CONFIG = {
        "extraction_backend": "openai",
        "openai_api_key": "test-key",
    }

    @pytest.mark.asyncio
    async def test_extracts_units(self):
        units = [_fake_unit_dict()]
        mock_create = AsyncMock(return_value=_openai_response(units))
        mock_client = MagicMock()
        mock_client.chat.completions.create = mock_create

        with _patch_openai(mock_client):
            result = await extract_from_messages(make_messages(4), config=self.CONFIG)

        assert len(result) == 1
        assert isinstance(result[0], ExtractedUnit)

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_tool_calls(self):
        """If the model returns no tool_calls, return []."""
        message = SimpleNamespace(tool_calls=None, content="Sorry, I cannot extract")
        choice = SimpleNamespace(message=message)
        response = SimpleNamespace(choices=[choice])

        mock_create = AsyncMock(return_value=response)
        mock_client = MagicMock()
        mock_client.chat.completions.create = mock_create

        with _patch_openai(mock_client):
            result = await extract_from_messages(make_messages(2), config=self.CONFIG)

        assert result == []


# ---------------------------------------------------------------------------
# extract_from_messages — Gemini backend
# ---------------------------------------------------------------------------

class TestExtractGemini:
    CONFIG = {
        "extraction_backend": "gemini",
        "gemini_api_key": "test-key",
        "gemini_model": "gemini-flash-latest",
    }

    @pytest.mark.asyncio
    async def test_extracts_units(self):
        units = [_fake_unit_dict()]
        mock_generate = AsyncMock(return_value=_gemini_response(units))

        mock_aio = MagicMock()
        mock_aio.models.generate_content = mock_generate
        mock_client = MagicMock()
        mock_client.aio = mock_aio

        with patch("google.genai.Client", return_value=mock_client):
            result = await extract_from_messages(make_messages(4), config=self.CONFIG)

        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_handles_dict_response(self):
        """Gemini sometimes returns {"units": [...]} instead of a plain array."""
        units = [_fake_unit_dict()]
        wrapped = {"units": units}
        mock_response = SimpleNamespace(text=json.dumps(wrapped))
        mock_generate = AsyncMock(return_value=mock_response)

        mock_aio = MagicMock()
        mock_aio.models.generate_content = mock_generate
        mock_client = MagicMock()
        mock_client.aio = mock_aio

        with patch("google.genai.Client", return_value=mock_client):
            result = await extract_from_messages(make_messages(2), config=self.CONFIG)

        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_handles_invalid_json_gracefully(self):
        mock_response = SimpleNamespace(text="not valid json at all {{{{")
        mock_generate = AsyncMock(return_value=mock_response)

        mock_aio = MagicMock()
        mock_aio.models.generate_content = mock_generate
        mock_client = MagicMock()
        mock_client.aio = mock_aio

        with patch("google.genai.Client", return_value=mock_client):
            result = await extract_from_messages(make_messages(2), config=self.CONFIG)

        assert result == []


# ---------------------------------------------------------------------------
# extract_from_messages — Ollama backend
# ---------------------------------------------------------------------------

class TestExtractOllama:
    CONFIG = {
        "extraction_backend": "ollama",
        "ollama_model": "llama3.2:3b",
        "ollama_base_url": "http://localhost:11434",
    }

    @pytest.mark.asyncio
    async def test_extracts_units(self):
        units = [_fake_unit_dict()]
        mock_response = _ollama_response(units)
        mock_post = AsyncMock(return_value=mock_response)
        mock_http_client = AsyncMock()
        mock_http_client.post = mock_post
        mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
        mock_http_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_http_client):
            result = await extract_from_messages(make_messages(4), config=self.CONFIG)

        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_strips_markdown_fences(self):
        """Ollama may wrap JSON in ```json``` code fences despite format=json."""
        units = [_fake_unit_dict()]
        fenced = f"```json\n{json.dumps(units)}\n```"

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"response": fenced}
        mock_post = AsyncMock(return_value=mock_response)
        mock_http_client = AsyncMock()
        mock_http_client.post = mock_post
        mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
        mock_http_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_http_client):
            result = await extract_from_messages(make_messages(2), config=self.CONFIG)

        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_handles_wrapped_units_dict(self):
        """Ollama sometimes returns {"units": [...]} instead of bare array."""
        units = [_fake_unit_dict()]
        wrapped = json.dumps({"units": units})

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"response": wrapped}
        mock_post = AsyncMock(return_value=mock_response)
        mock_http_client = AsyncMock()
        mock_http_client.post = mock_post
        mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
        mock_http_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_http_client):
            result = await extract_from_messages(make_messages(2), config=self.CONFIG)

        assert len(result) == 1


# ---------------------------------------------------------------------------
# summarize_conversation
# ---------------------------------------------------------------------------

class TestSummarizeConversation:
    CONFIG = {
        "extraction_backend": "anthropic",
        "anthropic_api_key": "test-key",
    }

    @pytest.mark.asyncio
    async def test_returns_summary_string(self):
        expected = "This conversation established UUID as the preferred ID strategy."
        mock_create = AsyncMock(return_value=_anthropic_summary_response(expected))
        mock_client = MagicMock()
        mock_client.messages.create = mock_create

        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            result = await summarize_conversation(make_messages(4), config=self.CONFIG)

        assert result == expected

    @pytest.mark.asyncio
    async def test_empty_messages_returns_empty_string(self):
        result = await summarize_conversation([], config=self.CONFIG)
        assert result == ""

    @pytest.mark.asyncio
    async def test_never_raises_on_error(self):
        """summarize_conversation should return '' not raise on backend error."""
        mock_create = AsyncMock(side_effect=RuntimeError("Connection refused"))
        mock_client = MagicMock()
        mock_client.messages.create = mock_create

        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            result = await summarize_conversation(make_messages(4), config=self.CONFIG)

        assert result == ""

    @pytest.mark.asyncio
    async def test_truncates_long_conversations(self):
        """Conversations over 12000 chars should be truncated before LLM call."""
        # Create a conversation with 20000 chars
        long_msgs = make_long_messages(20_000)
        captured_prompt: list[str] = []

        async def capture_create(*args, **kwargs):
            msg = kwargs.get("messages", args[1] if len(args) > 1 else [])
            if msg:
                captured_prompt.append(msg[-1]["content"])
            return _anthropic_summary_response("Summary text")

        mock_client = MagicMock()
        mock_client.messages.create = capture_create

        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            await summarize_conversation(long_msgs, config=self.CONFIG)

        assert len(captured_prompt) > 0
        # The prompt text passed to the LLM should have been truncated
        passed_text = captured_prompt[0]
        assert "[...middle truncated...]" in passed_text

    @pytest.mark.asyncio
    async def test_short_conversations_not_truncated(self):
        """Short conversations should not get the truncation marker."""
        short_msgs = make_messages(4)
        captured_prompt: list[str] = []

        async def capture_create(*args, **kwargs):
            msg = kwargs.get("messages", args[1] if len(args) > 1 else [])
            if msg:
                captured_prompt.append(msg[-1]["content"])
            return _anthropic_summary_response("Short summary")

        mock_client = MagicMock()
        mock_client.messages.create = capture_create

        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            await summarize_conversation(short_msgs, config=self.CONFIG)

        assert len(captured_prompt) > 0
        assert "[...middle truncated...]" not in captured_prompt[0]


# ---------------------------------------------------------------------------
# Rolling summary between chunks
# ---------------------------------------------------------------------------

class TestRollingSummary:
    CONFIG = {
        "extraction_backend": "anthropic",
        "anthropic_api_key": "test-key",
    }

    @pytest.mark.asyncio
    async def test_rolling_summary_passed_to_next_chunk(self):
        """After chunk N, the summary should be prepended as preamble to chunk N+1."""
        units = [_fake_unit_dict()]
        summary_text = "Earlier we decided to use PostgreSQL."
        extraction_response = _anthropic_response(units)
        summary_response = _anthropic_summary_response(summary_text)

        # Track what's passed to each call
        call_index = 0
        preamble_seen: list[str | None] = []

        async def mock_create(*args, **kwargs):
            nonlocal call_index
            msgs = kwargs.get("messages", [])
            content = msgs[-1]["content"] if msgs else ""

            if call_index == 0:
                # First extraction call — no preamble yet
                preamble_seen.append(None if "[Context from earlier" not in content else content)
                call_index += 1
                return extraction_response
            elif call_index == 1:
                # Rolling summary call
                call_index += 1
                return summary_response
            else:
                # Second extraction call — should contain preamble
                preamble_seen.append(content if "[Context from earlier" in content else None)
                return extraction_response

        mock_client = MagicMock()
        mock_client.messages.create = mock_create

        two_chunks = [make_messages(2), make_messages(2)]
        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            with patch("osctx.daemon.extraction.chunk_messages", return_value=two_chunks):
                result = await extract_from_messages(make_messages(4), config=self.CONFIG)

        # The second extraction call should have had a preamble
        assert preamble_seen[1] is not None, "Second chunk should have preamble with rolling summary"
        assert summary_text in preamble_seen[1]


# ---------------------------------------------------------------------------
# Unknown backend
# ---------------------------------------------------------------------------

class TestUnknownBackend:
    @pytest.mark.asyncio
    async def test_raises_on_unknown_backend(self):
        with pytest.raises(ValueError, match="Unknown extraction backend"):
            await extract_from_messages(
                make_messages(2),
                config={"extraction_backend": "not_a_real_backend"},
            )
