# ChatGPT Web Protocol Reference — Live-Captured

Captured: 2026-06-02 from ChatGPT Plus, Chrome 148

## Model Catalog (from GET /backend-api/models)

| Slug | Title | Max Tokens | Reasoning | Enabled Tools |
|------|-------|-----------|-----------|---------------|
| `gpt-5-5` | GPT-5.5 | 34,834 | auto | tools, tools2, dalle_3, search, canvas |
| `gpt-5-3` | GPT-5.3 | 34,834 | auto | tools, tools2, dalle_3, search, canvas |
| `gpt-5-2` | GPT-5.2 | 25,384 | auto | tools, tools2, dalle_3, search, canvas |
| `gpt-5-1` | GPT-5.1 | 17,384 | auto | tools, tools2, dalle_3, search, canvas |
| `gpt-5` | GPT-5 | 16,384 | auto | tools, tools2, dalle_3, search, canvas |
| `gpt-5-3-mini` | GPT-5.3 Mini | 34,834 | none | tools, tools2, dalle_3, search, canvas |
| `gpt-5-mini` | GPT-5-mini | 8,191 | none | tools, tools2, dalle_3, search, canvas |
| `auto` | Auto | 16,384 | auto | tools, tools2, dalle_3, search, canvas |

**Thinking effort**: The web client uses a separate `thinking_effort` param in the conversation request (not a model slug). Captured value: `"extended"`. The `gpt-5-5-thinking` slug from our capture was the actual model slug used — but it's NOT in the models list. It appears to be derived: `{model_slug}-thinking` when thinking is enabled.

**Note**: All models support `dalle_3` and `search` — image generation and web search are built-in.

## Additional Endpoints Discovered

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `GET /backend-api/models` | GET | Full model catalog with capabilities |
| `GET /backend-api/me` | GET | Current user info |
| `GET /backend-api/settings/user` | GET | User settings |
| `GET /backend-api/accounts/check/v4-2023-04-27` | GET | Account status |
| `GET /backend-api/accounts/optimized/check` | GET | Optimized account check |
| `GET /backend-api/user_granular_consent` | GET | User consent state |
| `GET /backend-api/user_system_messages` | GET | System messages |
| `GET /backend-api/system_hints` | GET | System configuration hints |
| `GET /backend-api/pins` | GET | Pinned conversations |
| `GET /backend-api/calpico/chatgpt/rooms/summary` | GET | Rooms/chats summary |

## Conversation Flow (Complete)

### Step 1: Init
```
POST /backend-api/conversation/init
Body: {gizmo_id, conversation_id, timezone_offset_min, requested_default_model}
```

### Step 2: Prepare (predictive)
```
POST /backend-api/f/conversation/prepare
Body: {action, conversation_id, parent_message_id, model, conversation_mode, partial_query, ...}
```

### Step 3: Conversation (actual message)
```
POST /backend-api/f/conversation
Body: {action, messages, conversation_id, parent_message_id, model,
       conversation_mode, enable_message_followups, supports_buffering,
       supported_encodings, thinking_effort, ...}
```

## Project Scoping

```json
"conversation_mode": {
  "kind": "gizmo_interaction",
  "gizmo_id": "g-p-<hex>"
}
```

## Sentinel Flow (3-Step)

```
1. POST /backend-api/sentinel/chat-requirements/prepare
2. POST /backend-api/sentinel/chat-requirements/finalize
3. POST /backend-api/sentinel/req
```

## Required Headers

```
Authorization: Bearer <access_token>
Content-Type: application/json
oai-device-id: <uuid>
oai-session-id: <uuid>
oai-client-build-number: 7079867
oai-client-version: prod-36401cb188ce4e77c4aeaf3e74996e3602a1410d
oai-language: en-US
```

## CDP Discovery Technique

Chrome 148+ requires `Network.enable` + page reload to capture events.
Connect to page-level WS (`/json/list` → `webSocketDebuggerUrl`), enable Network,
then reload page with `Page.reload`.

## Files

- `captured_models.json` — Full model catalog from `/backend-api/models`
- `captured_request.json` — Phase 1 captures (conversation/init, sentinel)
- `captured_broad.json` — Phase 1b captures (full conversation flow discovered)
