"""
LLM extraction pipeline.

Takes a raw conversation and extracts structured Knowledge Units.

Backends:
  - anthropic (default): Claude Haiku 3.5 via tool_use for guaranteed JSON structure
  - openai: GPT-4o-mini via tool calling
  - gemini: Gemini 2.0 Flash via google-genai SDK with JSON response schema
  - ollama: llama3.2:3b via local API (no structured output guarantee, falls back to JSON parse)

Chunking:
  - Hard cap: 6000 tokens per chunk (estimated at 4 chars/token)
  - Rolling summary: after each chunk, ask LLM for 2-3 sentence summary
  - Summary prepended to next chunk as context (NOT raw overlap)
  - Topic-shift detection for natural chunk boundaries
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG: dict[str, Any] = {
    "extraction_backend": "anthropic",
    "anthropic_api_key": "",
    "openai_api_key": "",
    "gemini_api_key": "",
    "gemini_model": "gemini-flash-latest",
    "ollama_model": "llama3.2:3b",
    "ollama_base_url": "http://localhost:11434",
}

# ---------------------------------------------------------------------------
# Extraction prompt
# ---------------------------------------------------------------------------

_EXTRACTION_SYSTEM = """You are a knowledge extraction engine. Your only job is to identify durable, reusable knowledge from an AI conversation.

EXTRACT these types of knowledge units:
- Decisions: Choices made that will affect future work
- Facts: Technical truths established in the conversation
- Solutions: Specific problems that were solved, with the solution
- Code patterns: Reusable code structures, not one-off snippets
- Preferences: User's stated preferences or constraints
- References: Books, tools, services the user should look up again

DO NOT EXTRACT:
- Small talk or meta-conversation about the AI
- Hypotheticals that were not adopted
- Information the user already clearly knew (they stated it as fact, not asked)
- Anything with confidence below 0.7

CONTENT QUALITY RULES:
- Write content as 1-3 complete sentences, NOT a single bare headline
- For decisions and solutions, include the reasoning: what was decided AND why (because/since/so that)
- For facts and code patterns, include enough detail that the unit is self-contained without reading the original conversation
- Include any important constraints, caveats, or tradeoffs that were established
- Bad: "Use PostgreSQL" — Good: "Chose PostgreSQL over MySQL because native JSONB indexing was required for the metadata schema; MySQL's JSON support lacked the index types needed for query performance."
- Bad: "Prefer UUIDs" — Good: "Use UUIDs (not auto-increment integers) for all user-facing IDs to enable sharding, avoid exposing row counts, and ensure cross-database uniqueness without coordination."

Return ONLY a JSON array. If nothing is worth extracting, return [].
Each item must have: content, category, topic_tags, confidence, context."""

_EXTRACTION_TOOL = {
    "name": "extract_knowledge",
    "description": "Extract durable knowledge units from the conversation.",
    "input_schema": {
        "type": "object",
        "properties": {
            "units": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["content", "category", "topic_tags", "confidence", "context"],
                    "properties": {
                        "content": {
                            "type": "string",
                            "description": "1-3 sentences capturing the knowledge unit. Must include reasoning (why, because, tradeoffs) for decisions and solutions. Self-contained — readable without the original conversation.",
                        },
                        "category": {
                            "type": "string",
                            "enum": ["decision", "fact", "solution", "code_pattern", "preference", "reference"],
                        },
                        "topic_tags": {
                            "type": "array",
                            "items": {"type": "string"},
                            "maxItems": 4,
                        },
                        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                        "context": {"type": "string"},
                    },
                },
            }
        },
        "required": ["units"],
    },
}

_SUMMARY_SYSTEM = "You are a concise summarizer."
_SUMMARY_PROMPT = (
    "Summarize what was decided, established, or solved in this conversation segment "
    "in 2-3 sentences. Focus on facts and decisions, not the conversational process."
)

_CONV_SUMMARY_PROMPT = (
    "Write a 2-3 paragraph summary of this entire conversation that captures: "
    "(1) the core topic and what was being figured out, "
    "(2) the key conclusions, decisions, or solutions reached, "
    "(3) any important nuances, tradeoffs, or caveats that were established. "
    "Write it so someone reading it cold can immediately understand the full context "
    "and pick up where this conversation left off. Be specific, not vague."
)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class ExtractedUnit:
    content: str
    category: str
    topic_tags: list[str]
    confidence: float
    context: str


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

_CHARS_PER_TOKEN = 4
_CHUNK_TOKEN_LIMIT = 6000
_CHUNK_CHAR_LIMIT = _CHUNK_TOKEN_LIMIT * _CHARS_PER_TOKEN
_MIN_CHUNK_CHARS = 2000 * _CHARS_PER_TOKEN


def _estimate_chars(msgs: list[dict]) -> int:
    return sum(len(m.get("content", "")) for m in msgs)


def _is_topic_shift(msg: dict, recent_msgs: list[dict]) -> bool:
    """Heuristic: user message that doesn't reference recent assistant content."""
    if msg.get("role") != "user":
        return False
    if not recent_msgs:
        return False

    last_asst = next(
        (m for m in reversed(recent_msgs) if m.get("role") == "assistant"), None
    )
    if not last_asst:
        return False

    # Extract key words from last assistant message (crude: first 3 non-stop words)
    asst_words = set(
        w.lower() for w in re.findall(r'\b\w{5,}\b', last_asst.get("content", ""))
    )
    user_words = set(w.lower() for w in re.findall(r'\b\w{5,}\b', msg.get("content", "")))

    # If less than 1 word overlap, it's likely a topic shift
    return len(asst_words & user_words) < 1


def chunk_messages(messages: list[dict]) -> list[list[dict]]:
    """Split messages into chunks for extraction."""
    if not messages:
        return []

    total_chars = _estimate_chars(messages)
    if total_chars <= _CHUNK_CHAR_LIMIT:
        return [messages]

    chunks: list[list[dict]] = []
    current: list[dict] = []
    current_chars = 0

    for msg in messages:
        msg_chars = len(msg.get("content", ""))

        # Natural topic break
        if (
            current_chars >= _MIN_CHUNK_CHARS
            and _is_topic_shift(msg, current)
        ):
            chunks.append(current)
            current = []
            current_chars = 0

        # Hard cap
        if current_chars + msg_chars > _CHUNK_CHAR_LIMIT and current:
            chunks.append(current)
            current = []
            current_chars = 0

        current.append(msg)
        current_chars += msg_chars

    if current:
        chunks.append(current)

    return chunks


def _format_messages(messages: list[dict], preamble: str | None = None) -> str:
    lines: list[str] = []
    if preamble:
        lines.append(f"[Context from earlier in this conversation: {preamble}]\n")
    for msg in messages:
        speaker = "User" if msg.get("role") == "user" else "Assistant"
        lines.append(f"{speaker}: {msg.get('content', '')}\n")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Backend implementations
# ---------------------------------------------------------------------------

async def _extract_anthropic(
    text: str, config: dict[str, Any]
) -> list[ExtractedUnit]:
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=config["anthropic_api_key"])
    response = await client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2048,
        system=_EXTRACTION_SYSTEM,
        messages=[{"role": "user", "content": f"CONVERSATION:\n\n{text}"}],
        tools=[_EXTRACTION_TOOL],
        tool_choice={"type": "tool", "name": "extract_knowledge"},
    )

    for block in response.content:
        if block.type == "tool_use" and block.name == "extract_knowledge":
            units_raw = block.input.get("units", [])
            return [ExtractedUnit(**u) for u in units_raw if u.get("confidence", 0) >= 0.7]

    return []


async def _summarize_anthropic(text: str, config: dict[str, Any]) -> str:
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=config["anthropic_api_key"])
    response = await client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=256,
        system=_SUMMARY_SYSTEM,
        messages=[{"role": "user", "content": f"{_SUMMARY_PROMPT}\n\n{text}"}],
    )
    return response.content[0].text if response.content else ""


async def _extract_openai(
    text: str, config: dict[str, Any]
) -> list[ExtractedUnit]:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=config["openai_api_key"])
    response = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": _EXTRACTION_SYSTEM},
            {"role": "user", "content": f"CONVERSATION:\n\n{text}"},
        ],
        tools=[{"type": "function", "function": {
            "name": _EXTRACTION_TOOL["name"],
            "description": _EXTRACTION_TOOL["description"],
            "parameters": _EXTRACTION_TOOL["input_schema"],
        }}],
        tool_choice={"type": "function", "function": {"name": "extract_knowledge"}},
    )

    choice = response.choices[0]
    if choice.message.tool_calls:
        args = json.loads(choice.message.tool_calls[0].function.arguments)
        units_raw = args.get("units", [])
        return [ExtractedUnit(**u) for u in units_raw if u.get("confidence", 0) >= 0.7]

    return []


async def _summarize_openai(text: str, config: dict[str, Any]) -> str:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=config["openai_api_key"])
    response = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": _SUMMARY_SYSTEM},
            {"role": "user", "content": f"{_SUMMARY_PROMPT}\n\n{text}"},
        ],
        max_tokens=256,
    )
    return response.choices[0].message.content or ""


async def _extract_gemini(
    text: str, config: dict[str, Any]
) -> list[ExtractedUnit]:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=config["gemini_api_key"])
    model = config.get("gemini_model", "gemini-flash-latest")

    prompt = f"{_EXTRACTION_SYSTEM}\n\nCONVERSATION:\n\n{text}"

    response = await client.aio.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema={
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string"},
                        "category": {"type": "string", "enum": ["decision", "fact", "solution", "code_pattern", "preference", "reference"]},
                        "topic_tags": {"type": "array", "items": {"type": "string"}},
                        "confidence": {"type": "number"},
                        "context": {"type": "string"},
                    },
                    "required": ["content", "category", "topic_tags", "confidence", "context"],
                },
            },
        ),
    )

    try:
        units_raw = json.loads(response.text)
        if not isinstance(units_raw, list):
            units_raw = units_raw.get("units", []) if isinstance(units_raw, dict) else []
        return [ExtractedUnit(**u) for u in units_raw if u.get("confidence", 0) >= 0.7]
    except (json.JSONDecodeError, TypeError) as exc:
        logger.warning("Gemini extraction parse error: %s", exc)
        return []


async def _summarize_gemini(text: str, config: dict[str, Any]) -> str:
    from google import genai

    client = genai.Client(api_key=config["gemini_api_key"])
    model = config.get("gemini_model", "gemini-flash-latest")

    response = await client.aio.models.generate_content(
        model=model,
        contents=f"{_SUMMARY_SYSTEM}\n\n{_SUMMARY_PROMPT}\n\n{text}",
    )
    return response.text or ""


async def _extract_ollama(
    text: str, config: dict[str, Any]
) -> list[ExtractedUnit]:
    import httpx

    prompt = (
        f"{_EXTRACTION_SYSTEM}\n\nCONVERSATION:\n\n{text}\n\n"
        "Return ONLY a valid JSON array. No markdown, no code blocks, no explanation."
    )

    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(
            f"{config['ollama_base_url']}/api/generate",
            json={
                "model": config["ollama_model"],
                "prompt": prompt,
                "stream": False,
                "format": "json",
            },
        )
        response.raise_for_status()
        raw_text = response.json().get("response", "[]")

    try:
        # Ollama may wrap in markdown code fence despite format=json
        raw_text = re.sub(r"```(?:json)?\n?", "", raw_text).strip()
        parsed = json.loads(raw_text)
        if isinstance(parsed, dict) and "units" in parsed:
            parsed = parsed["units"]
        if not isinstance(parsed, list):
            return []
        return [
            ExtractedUnit(**u)
            for u in parsed
            if isinstance(u, dict) and u.get("confidence", 0) >= 0.7
        ]
    except (json.JSONDecodeError, TypeError) as exc:
        logger.warning("Ollama extraction parse error: %s", exc)
        return []


async def _summarize_ollama(text: str, config: dict[str, Any]) -> str:
    import httpx

    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            f"{config['ollama_base_url']}/api/generate",
            json={
                "model": config["ollama_model"],
                "prompt": f"{_SUMMARY_PROMPT}\n\n{text}",
                "stream": False,
            },
        )
        response.raise_for_status()
        return response.json().get("response", "")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def extract_from_messages(
    messages: list[dict],
    config: dict[str, Any] | None = None,
) -> list[ExtractedUnit]:
    """Extract knowledge units from a list of messages.

    Handles chunking internally. Uses rolling summary for context continuity
    across chunks.

    Args:
        messages: List of {role, content} dicts.
        config: Config dict. Defaults to _DEFAULT_CONFIG if None.

    Returns:
        List of ExtractedUnit, deduplicated by content within this call.
    """
    cfg = {**_DEFAULT_CONFIG, **(config or {})}
    backend = cfg.get("extraction_backend", "anthropic")

    if backend == "anthropic":
        extract_fn = _extract_anthropic
        summarize_fn = _summarize_anthropic
    elif backend == "openai":
        extract_fn = _extract_openai
        summarize_fn = _summarize_openai
    elif backend == "gemini":
        extract_fn = _extract_gemini
        summarize_fn = _summarize_gemini
    elif backend == "ollama":
        extract_fn = _extract_ollama
        summarize_fn = _summarize_ollama
    else:
        raise ValueError(f"Unknown extraction backend: {backend!r}")

    chunks = chunk_messages(messages)
    all_units: list[ExtractedUnit] = []
    rolling_summary: str | None = None

    for i, chunk in enumerate(chunks):
        text = _format_messages(chunk, preamble=rolling_summary)
        try:
            units = await extract_fn(text, cfg)
            all_units.extend(units)

            # Generate rolling summary for next chunk context
            if i < len(chunks) - 1:
                rolling_summary = await summarize_fn(text, cfg)
        except Exception as exc:
            logger.error("Extraction failed on chunk %d/%d: %s", i + 1, len(chunks), exc)
            # Continue with remaining chunks

    # Deduplicate within this extraction by exact content
    seen: set[str] = set()
    unique: list[ExtractedUnit] = []
    for unit in all_units:
        key = unit.content.strip().lower()
        if key not in seen:
            seen.add(key)
            unique.append(unit)

    return unique


async def summarize_conversation(
    messages: list[dict],
    config: dict[str, Any] | None = None,
) -> str:
    """Generate a rich 2-3 paragraph summary of an entire conversation.

    Truncates to 12000 chars if needed to stay within LLM context limits.
    Returns empty string on failure (never raises).
    """
    if not messages:
        return ""

    cfg = {**_DEFAULT_CONFIG, **(config or {})}
    backend = cfg.get("extraction_backend", "anthropic")

    if backend == "anthropic":
        summarize_fn = _summarize_anthropic
    elif backend == "openai":
        summarize_fn = _summarize_openai
    elif backend == "gemini":
        summarize_fn = _summarize_gemini
    elif backend == "ollama":
        summarize_fn = _summarize_ollama
    else:
        return ""

    # Build full conversation text, truncated to stay within context
    full_text = _format_messages(messages)
    if len(full_text) > 12_000:
        # Keep first third + last two thirds — lose middle filler, keep conclusion
        third = 4_000
        full_text = full_text[:third] + "\n\n[...middle truncated...]\n\n" + full_text[-8_000:]

    try:
        return await summarize_fn(
            f"{_CONV_SUMMARY_PROMPT}\n\nCONVERSATION:\n\n{full_text}", cfg
        )
    except Exception as exc:
        logger.warning("Conversation summary failed: %s", exc)
        return ""
