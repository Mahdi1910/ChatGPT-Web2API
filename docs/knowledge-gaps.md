# Knowledge Gaps — What We Still Need

## What We Know (Live-Captured)

- ✅ Message endpoint: `POST /backend-api/f/conversation`
- ✅ Project scoping: `conversation_mode.kind = "gizmo_interaction"` + `gizmo_id = "g-p-<hex>"`
- ✅ Message format: `messages[].content.parts[]`
- ✅ Sentinel flow: `prepare` → `finalize` → `req`
- ✅ Required headers (Authorization, oai-device-id, etc.)
- ✅ Init endpoint: `POST /backend-api/conversation/init`
- ✅ Prepare endpoint: `POST /backend-api/f/conversation/prepare`

## What We DON'T Know Yet

### 1. Available Models (Critical for Implementation)
We only captured `gpt-5-5-thinking`. We need the full list of web model slugs.
ChatGPT web likely uses different slugs than the API. We need to either:
- Capture a model switch in the UI (switch model dropdown, observe network)
- Scrape the model list from ChatGPT's frontend JS
- Check the `/backend-api/models` endpoint (if it exists)

Known from previous reverse-engineering (may be outdated):
- `auto`, `gpt-4o`, `gpt-4o-mini`, `o1`, `o1-mini`, `o3-mini`, `o3-mini-high`
- Now also: `gpt-5-5-thinking`, possibly `gpt-4.1`, `o4-mini`

### 2. SSE Response Format (Critical for Parsing)
We captured request payloads but the SSE response body was truncated at 10K.
We need to see:
- The actual SSE line format (is it `data: {json}` or something else?)
- How `parts` update incrementally (full text each time or deltas?)
- The "done" signal format
- How thinking/reasoning tokens are encoded (for `gpt-5-5-thinking`)

### 3. Non-Project Conversation Format
We only tested project-scoped. Need to verify:
- What `conversation_mode` looks like for a normal chat: `{"kind": "primary_assistant"}`
- Whether the request body structure is identical
- Whether a brand new conversation (no `conversation_id`) works the same

### 4. Turnstile Solving (Blocking)
Turnstile IS required — the capture showed a large encrypted challenge.
We need to figure out:
- Can we solve it via the browser automatically?
- Or do we need a CAPTCHA solver service?
- Or can we bypass it by reusing the Turnstile token from the browser session?

### 5. PoW Algorithm Verification
We built an FNV-1a solver but haven't verified it works against the actual PoW.
The captured seed was a float (`0.6353036061864151`), difficulty was hex (`061a80`).
The actual algorithm is in `sentinel/sdk.js` — we should extract and verify it.

### 6. Brand New Conversation Flow
All our captures were continuing existing conversations (had `conversation_id`).
We need to capture the flow for starting a brand new conversation in a project:
- Is `conversation_id: null` sufficient?
- Does the init step create the conversation ID, or does `/f/conversation` do it?
- How is `parent_message_id` set for the first message?

### 7. AccessToken Lifecycle
We captured the Bearer token in headers but didn't capture:
- The `/api/auth/session` response (token structure, expiry)
- Token refresh behavior
- What happens when token expires mid-conversation

### 8. Error Responses
We have no data on what happens when:
- PoW solution is wrong
- Turnstile fails
- Token is expired
- Rate limit hit
- Model is unavailable

### 9. File Upload / Source Attachment
Projects support file uploads. We need:
- The file upload endpoint flow
- How files are referenced in messages
- The `selected_sources` metadata format

### 10. client_version / build_number Stability
The captured values (`prod-36401cb188ce4e77c4aeaf3e74996e3602a1410d`, `7079867`)
change with every ChatGPT deployment. We need a strategy to handle this:
- Hard-code and update manually?
- Scrape from the page?
- Does ChatGPT reject stale versions?

## Recommended Additional Captures

### Capture A: Model Discovery (5 min)
1. Open ChatGPT in debug browser
2. Run discover2.py
3. Click the model dropdown, switch between all models
4. Capture what model slug is sent

### Capture B: SSE Response (5 min)
1. Send a message while capturing
2. This time save the FULL response body (not truncated)
3. Parse the SSE format

### Capture C: New Conversation (3 min)
1. Start a brand new conversation in the project
2. Capture the first message flow (no existing conversation_id)

### Capture D: Normal Chat (3 min)
1. Switch to non-project chat
2. Send a message
3. Verify `conversation_mode` is `primary_assistant`
