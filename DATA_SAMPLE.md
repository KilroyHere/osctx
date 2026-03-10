# OSCTX — Data Samples & Expected Outputs

## 1. ChatGPT conversations.json Structure

The export file is a JSON array of conversation objects. Each conversation has a `mapping` field — a dict of UUID → node. Nodes form a tree. Must traverse from root to leaf to get linear message sequence.

### Full structural example (one conversation):

```json
[
  {
    "id": "conv_abc123",
    "title": "PostgreSQL schema for auth system",
    "create_time": 1730649600.0,
    "update_time": 1730653200.0,
    "mapping": {
      "node_root": {
        "id": "node_root",
        "message": null,
        "parent": null,
        "children": ["node_sys_1"]
      },
      "node_sys_1": {
        "id": "node_sys_1",
        "message": {
          "id": "node_sys_1",
          "author": {"role": "system"},
          "content": {
            "content_type": "text",
            "parts": ["You are a helpful assistant."]
          },
          "create_time": 1730649600.0
        },
        "parent": "node_root",
        "children": ["node_user_1"]
      },
      "node_user_1": {
        "id": "node_user_1",
        "message": {
          "id": "node_user_1",
          "author": {"role": "user"},
          "content": {
            "content_type": "text",
            "parts": ["I'm building a SaaS auth system. Should I use UUIDs or auto-increment integers for user IDs?"]
          },
          "create_time": 1730649660.0
        },
        "parent": "node_sys_1",
        "children": ["node_asst_1"]
      },
      "node_asst_1": {
        "id": "node_asst_1",
        "message": {
          "id": "node_asst_1",
          "author": {"role": "assistant"},
          "content": {
            "content_type": "text",
            "parts": ["For a SaaS auth system, I strongly recommend UUIDs (specifically UUID v4) over auto-increment integers for several reasons:\n\n1. **Security**: Auto-increment IDs are enumerable — an attacker can iterate user_id=1,2,3 to probe your API. UUIDs are not guessable.\n2. **Distributed systems**: If you ever shard your database or merge data from multiple sources, UUIDs prevent collisions without coordination.\n3. **Portability**: You can generate the UUID client-side before insert, which simplifies some patterns.\n\nThe main tradeoff is storage (16 bytes vs 4-8 bytes) and slightly slower B-tree inserts due to random ordering. For most SaaS apps this is negligible. Use `gen_random_uuid()` in PostgreSQL 13+."]
          },
          "create_time": 1730649720.0
        },
        "parent": "node_user_1",
        "children": ["node_user_2"]
      },
      "node_user_2": {
        "id": "node_user_2",
        "message": {
          "id": "node_user_2",
          "author": {"role": "user"},
          "content": {
            "content_type": "text",
            "parts": ["Good call. What about the sessions table? Should I store sessions in the DB or use JWTs?"]
          },
          "create_time": 1730649780.0
        },
        "parent": "node_asst_1",
        "children": ["node_asst_2"]
      },
      "node_asst_2": {
        "id": "node_asst_2",
        "message": {
          "id": "node_asst_2",
          "author": {"role": "assistant"},
          "content": {
            "content_type": "text",
            "parts": ["For a SaaS app, I recommend **database sessions over JWTs** for user-facing auth:\n\n**Use DB sessions when:**\n- You need instant revocation (user clicks logout → session deleted → immediately invalid)\n- You want to show users their active sessions and let them revoke specific devices\n- You're storing user preferences server-side anyway\n\n**Schema suggestion:**\n```sql\nCREATE TABLE sessions (\n    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),\n    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,\n    token_hash  TEXT NOT NULL UNIQUE,  -- SHA256 of the actual token\n    user_agent  TEXT,\n    ip_address  INET,\n    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),\n    expires_at  TIMESTAMPTZ NOT NULL,\n    last_active TIMESTAMPTZ NOT NULL DEFAULT NOW()\n);\nCREATE INDEX ON sessions(user_id);\nCREATE INDEX ON sessions(expires_at);\n```\n\nStore a random 32-byte token in the cookie. Hash it before DB storage (so a DB leak doesn't let anyone log in). JWTs are fine for service-to-service auth where revocation isn't needed."]
          },
          "create_time": 1730649840.0
        },
        "parent": "node_user_2",
        "children": ["node_user_3"]
      },
      "node_user_3": {
        "id": "node_user_3",
        "message": {
          "id": "node_user_3",
          "author": {"role": "user"},
          "content": {
            "content_type": "text",
            "parts": ["Perfect. One more — for the users table, should email be the primary key or should I have a separate UUID?"]
          },
          "create_time": 1730649900.0
        },
        "parent": "node_asst_2",
        "children": ["node_asst_3"]
      },
      "node_asst_3": {
        "id": "node_asst_3",
        "message": {
          "id": "node_asst_3",
          "author": {"role": "assistant"},
          "content": {
            "content_type": "text",
            "parts": ["Separate UUID, always. Email as primary key causes problems:\n\n1. **Email changes**: Users change emails. Cascading updates to all foreign keys is painful.\n2. **Privacy**: Email appears in URLs, logs, JWTs if used as ID. UUID is opaque.\n3. **Index efficiency**: Text comparison is slower than UUID comparison.\n\nFull users table:\n```sql\nCREATE TABLE users (\n    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),\n    email           TEXT NOT NULL UNIQUE,\n    email_verified  BOOLEAN NOT NULL DEFAULT FALSE,\n    password_hash   TEXT,  -- NULL if OAuth-only\n    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),\n    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()\n);\nCREATE UNIQUE INDEX ON users(email);\n```"]
          },
          "create_time": 1730649960.0
        },
        "parent": "node_user_3",
        "children": []
      }
    }
  }
]
```

### Edge cases the parser MUST handle:
- `message` field is `null` on some nodes (root nodes, or pruned branches) → skip
- `content.parts` may contain non-string items (multimodal: dicts with image refs) → extract string parts only, skip dicts
- `content.content_type` may be `"tether_browsing_display"`, `"tether_quote"`, `"multimodal_text"` — only extract `"text"` content type for now
- Branched conversations (node has multiple children) → always follow the last child (most recent branch)
- `author.role` may be `"tool"` (function call results) → skip
- `create_time` on message may be `null` → use conversation `create_time` as fallback

---

## 2. Raw Capture Payload (from Browser Extension)

This is what the extension POSTs to `/ingest`. 4-5 exchange example:

```json
{
  "source": "chatgpt",
  "url": "https://chat.openai.com/c/conv_abc123",
  "captured_at": 1730649960,
  "messages": [
    {
      "role": "user",
      "content": "I'm building a SaaS auth system. Should I use UUIDs or auto-increment integers for user IDs?"
    },
    {
      "role": "assistant",
      "content": "For a SaaS auth system, I strongly recommend UUIDs (specifically UUID v4) over auto-increment integers for several reasons:\n\n1. Security: Auto-increment IDs are enumerable — an attacker can iterate user_id=1,2,3 to probe your API. UUIDs are not guessable.\n2. Distributed systems: If you ever shard your database, UUIDs prevent collisions without coordination.\n3. Portability: You can generate the UUID client-side before insert.\n\nThe main tradeoff is storage (16 bytes vs 4-8 bytes) and slightly slower B-tree inserts. Use gen_random_uuid() in PostgreSQL 13+."
    },
    {
      "role": "user",
      "content": "Good call. What about the sessions table? Should I store sessions in the DB or use JWTs?"
    },
    {
      "role": "assistant",
      "content": "For a SaaS app, I recommend database sessions over JWTs for user-facing auth. Use DB sessions when you need instant revocation, want to show users active sessions, or store user preferences server-side. Store a random 32-byte token in the cookie, hash it before DB storage. JWTs are fine for service-to-service auth where revocation isn't needed."
    },
    {
      "role": "user",
      "content": "For the users table, should email be the primary key or should I have a separate UUID?"
    },
    {
      "role": "assistant",
      "content": "Separate UUID always. Email as primary key causes problems: users change emails (cascading updates to all foreign keys), email appears in URLs and logs (privacy issue), and text comparison is slower than UUID comparison."
    }
  ]
}
```

---

## 3. Expected Extraction Output

Given the conversation above, the extraction LLM should produce this JSON array:

```json
[
  {
    "content": "Use UUID v4 (not auto-increment integers) for user IDs in SaaS systems — UUIDs prevent enumeration attacks and avoid collision issues in distributed/sharded databases.",
    "category": "decision",
    "topic_tags": ["database", "postgresql", "auth", "user-ids"],
    "confidence": 0.97,
    "context": "Explicit architectural decision made for SaaS auth system database design."
  },
  {
    "content": "Store auth sessions in the database (not JWTs) for user-facing SaaS auth — enables instant revocation, active session listing, and per-device logout.",
    "category": "decision",
    "topic_tags": ["auth", "sessions", "jwt", "database"],
    "confidence": 0.95,
    "context": "Decision between JWT and DB sessions for SaaS application."
  },
  {
    "content": "Sessions table schema: id (UUID PK), user_id (UUID FK), token_hash (TEXT UNIQUE — SHA256 of actual token), user_agent, ip_address, created_at, expires_at, last_active. Index on user_id and expires_at.",
    "category": "code_pattern",
    "topic_tags": ["database", "postgresql", "auth", "sessions"],
    "confidence": 0.93,
    "context": "Concrete schema definition for sessions table with security note about hashing tokens."
  },
  {
    "content": "Users table schema: id (UUID PK, gen_random_uuid()), email (TEXT UNIQUE, not PK), email_verified (BOOLEAN), password_hash (TEXT, nullable for OAuth-only), created_at, updated_at.",
    "category": "code_pattern",
    "topic_tags": ["database", "postgresql", "auth", "users"],
    "confidence": 0.94,
    "context": "Concrete users table schema with rationale for separate UUID vs email as PK."
  },
  {
    "content": "Never use email as primary key — emails change (painful cascading updates), email in URLs/logs is a privacy issue, and text PK comparison is slower than UUID.",
    "category": "fact",
    "topic_tags": ["database", "postgresql", "schema-design"],
    "confidence": 0.91,
    "context": "Rationale for UUID vs email primary key decision."
  }
]
```

### Extraction quality rules (derived from this example):
- Extract **standalone sentences** — the content must make sense without the conversation context
- Include **schema definitions verbatim** when they appear — these are high-value code patterns
- **Merge** closely related facts into one unit when they form a coherent decision (don't split "use UUIDs" from "because enumerable IDs are insecure")
- **Don't extract** the question/framing from the user side unless it provides context for the answer
- Confidence < 0.7 → don't extract. The example above has no items below 0.91 because this was a substantive technical conversation.

---

## 4. Gemini Takeout Structure (for Phase 2 importer)

Google Takeout for Gemini produces a `Gemini Apps Activity.json` inside `Takeout/Gemini Apps Activity/`:

```json
{
  "gmr:chatSessionList": [
    {
      "gmr:chatSession": {
        "@type": "type.googleapis.com/search.sidekick.proto.ChatSession",
        "gmr:id": "session_abc123",
        "gmr:title": "Python async patterns",
        "gmr:createTime": "2025-11-03T14:00:00Z",
        "gmr:turnList": [
          {
            "gmr:turn": {
              "gmr:userContent": {
                "gmr:parts": [{"gmr:text": "What's the difference between asyncio.gather and asyncio.TaskGroup?"}]
              },
              "gmr:modelContent": {
                "gmr:parts": [{"gmr:text": "asyncio.gather runs coroutines concurrently and returns all results..."}]
              }
            }
          }
        ]
      }
    }
  ]
}
```

**Note:** Gemini Takeout structure varies by export date. The parser should handle both `gmr:` prefixed and non-prefixed variants. Always check for both.
