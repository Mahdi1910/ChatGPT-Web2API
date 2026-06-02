# Phase 1 Complete — ChatGPT Web Protocol (Live-Captured)

Captured: 2026-06-02 from ChatGPT Plus, Chrome 148, project-scoped conversation

## Conversation Flow (3 Requests)

### Step 1: Init
```
POST /backend-api/conversation/init
Body: {gizmo_id, conversation_id, timezone_offset_min, requested_default_model}
```
Returns conversation metadata. No message content.

### Step 2: Prepare (predictive — as user types)
```
POST /backend-api/f/conversation/prepare
```
Sent while the user is still typing (`partial_query` has partial text).
Enables "buffering" — the server starts preparing before submit.

### Step 3: Conversation (the actual message)
```
POST /backend-api/f/conversation
```
**This carries the actual messages.** Returns SSE stream.

## The Actual Request Body

```json
{
  "action": "next",
  "messages": [
    {
      "id": "<uuid>",
      "author": {"role": "user"},
      "create_time": 1780364131.52,
      "content": {
        "content_type": "text",
        "parts": ["<user message here>"]
      },
      "metadata": {
        "developer_mode_connector_ids": [],
        "selected_sources": [],
        "selected_github_repos": [],
        "selected_all_github_repos": false,
        "serialization_metadata": {"custom_symbol_offsets": []}
      }
    }
  ],
  "conversation_id": "<uuid or null for new>",
  "parent_message_id": "<uuid>",
  "model": "gpt-5-5-thinking",
  "client_prepare_state": "success",
  "timezone_offset_min": -180,
  "timezone": "Asia/Riyadh",
  "conversation_mode": {
    "kind": "gizmo_interaction",
    "gizmo_id": "g-p-<hex>"
  },
  "enable_message_followups": true,
  "system_hints": [],
  "supports_buffering": true,
  "supported_encodings": ["v1"],
  "client_contextual_info": {
    "is_dark_mode": true,
    "time_since_loaded": 604,
    "page_height": 911,
    "page_width": 1920,
    "pixel_ratio": 1,
    "screen_height": 1080,
    "screen_width": 1920,
    "app_name": "chatgpt.com"
  },
  "paragen_cot_summary_display_override": "allow",
  "force_parallel_switch": "auto",
  "thinking_effort": "extended"
}
```

## Project Scoping

```json
"conversation_mode": {
  "kind": "gizmo_interaction",
  "gizmo_id": "g-p-6a1cbfa6da8c8191bd3674470d2dbc22"
}
```

- Projects ARE gizmo interactions
- `gizmo_id` = the full `g-p-<hex>` (no slug suffix)
- For non-project chats: `{"kind": "primary_assistant"}`

## Sentinel Flow (3 Requests)

```
1. POST /backend-api/sentinel/chat-requirements/prepare
   Body: {"p": "<base64 config>"}

2. POST /backend-api/sentinel/chat-requirements/finalize
   Body: {"prepare_token": "...", "proofofwork": "<solved>", "turnstile": "<token>"}
   Returns: {"token": "...", "expire_after": ..., "expire_at": ...}

3. POST /backend-api/sentinel/req
   Body: {"p": "<base64>", "id": "<device-id>", "flow": "conversation"}
   (Sent during the conversation request)
```

## Required Headers

```
Authorization: Bearer <access_token>
Content-Type: application/json
Accept: text/event-stream
oai-device-id: <uuid>
oai-session-id: <uuid>
oai-client-build-number: 7079867
oai-client-version: prod-36401cb188ce4e77c4aeaf3e74996e3602a1410d
oai-language: en-US
```

## Model Names

Captured model: `gpt-5-5-thinking` (GPT-5.5 with extended thinking)
thinking_effort: "extended"

## Streaming

Response is SSE with encoding "v1".
Responses contain `parts` fields with incremental text.
Stream items have UUIDs for tracking.

## All Backend-API Endpoints Observed

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/conversation/init` | POST | Initialize/load conversation metadata |
| `/f/conversation/prepare` | POST | Predictive pre-flight while user types |
| `/f/conversation` | POST | **Actual message + SSE response** |
| `/sentinel/chat-requirements/prepare` | POST | Start sentinel challenge |
| `/sentinel/chat-requirements/finalize` | POST | Submit PoW + Turnstile solution |
| `/sentinel/req` | POST | Additional sentinel check during request |
| `/sentinel/ping` | POST | Keepalive (every ~5s) |
| `/sentinel/heartbeat` | POST | Heartbeat |
| `/conversation/{id}` | GET | Load conversation history |
| `/conversation/{id}/stream_status` | GET | Check stream state |
| `/conversation/{id}/textdocs` | GET | Get text documents |
| `/files/library` | POST | List uploaded files |
| `/memories` | GET/POST | Memory management |
| `/beacons/home` | POST | Analytics beacon |
| `/apps/sources_dropdown` | POST | Sources dropdown data |
| `/lat/r` | POST | Latency reporting |
