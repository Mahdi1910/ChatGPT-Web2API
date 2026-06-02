# Phase 1 Capture Results

Captured: 2026-06-02 04:29 GMT+3

## Protocol Discovery

### 1. Endpoint

**`POST /backend-api/conversation/init`** (NOT `/backend-api/conversation`)

This is the actual endpoint ChatGPT uses for project-scoped conversations.

### 2. Project Scoping

Projects use **`gizmo_id`** — the SAME field as GPTs.

```json
{
  "gizmo_id": "g-p-6a1cbfa6da8c8191bd3674470d2dbc22",
  "requested_default_model": null,
  "conversation_id": null,
  "timezone_offset_min": -180
}
```

**Key:**
- The `g-p-` prefix IS included in the gizmo_id value
- The slug portion (`-orqestra`) is NOT included
- For a new conversation: `conversation_id = null`
- To continue: set `conversation_id` to the UUID

### 3. Gizmo Type

Response metadata shows:
- `gizmo_type: "snorlax"` — this is the internal type for projects
- `conversation_template_id` = same as `gizmo_id` for projects

### 4. Sentinel Flow (3-Step)

```
Step 1: POST /backend-api/sentinel/chat-requirements/prepare
        Body: {"p": "<config_json>"}
        Returns: prepare_token, PoW seed/difficulty, turnstile challenge

Step 2: POST /backend-api/sentinel/chat-requirements/finalize  
        Body: {"prepare_token": "...", "proofofwork": "<solved>", "turnstile": "<token>"}
        Returns: {"token": "...", "expire_after": ..., "expire_at": ...}

Step 3: Use the returned token in the conversation request
```

**NOT** a single `/backend-api/sentinel/chat-requirements` call.
It's a two-phase prepare→finalize flow.

### 5. Required Headers

```
Authorization: Bearer <access_token>
oai-device-id: 87c65f22-31e2-4f62-970f-05e4bd43da81
oai-session-id: 2d36e495-5844-464f-b7cc-ac764290879d
oai-client-build-number: 7079867
oai-client-version: prod-36401cb188ce4e77c4aeaf3e74996e3602a1410d
oai-language: en-US
Content-Type: application/json
```

### 6. PoW Details

- Seed: float like `0.6353036061864151`
- Difficulty: hex like `061a80`
- Turnstile: **required** — includes a large encrypted blob (`dx` field)

### 7. Limits (from response)

- deep_research: 25 remaining (monthly)
- odyssey: 40 remaining (monthly) 
- file_upload: 80 remaining (hourly)
- paste_text_to_file: 80 remaining (hourly)

### 8. Continuation

For continuing an existing project conversation:
```json
{
  "gizmo_id": "g-p-6a1cbfa6da8c8191bd3674470d2dbc22",
  "requested_default_model": null,
  "conversation_id": "6a1ddedf-54b0-83eb-9962-e023d83c0100",
  "timezone_offset_min": -180
}
```

## Corrections to Previous Assumptions

| Previous Assumption | Actual |
|---|---|
| Endpoint: `/backend-api/conversation` | `/backend-api/conversation/init` |
| `conversation_mode` with `kind: "project_interaction"` | No `conversation_mode` field at all |
| Separate project ID field | `gizmo_id` (same as GPTs, `g-p-` prefix included) |
| Single sentinel call | Two-step prepare→finalize flow |
| Turnstile might not be required | Turnstile IS required |
| Request has `messages` array | Request has NO messages — only metadata |
| PoW was FNV-1a | Unknown algorithm (need to study frontend JS) |

## Critical Gap: Where Are the Messages?

The captured `/conversation/init` request contains only metadata — no message text.
This means either:
1. The actual message is sent in a **separate follow-up request** (not captured)
2. The message is sent via **WebSocket** (not HTTP)
3. The `page.route()` interception missed the actual conversation request

**Next step:** Re-run capture with broader interception pattern (`**chatgpt.com/**`)
