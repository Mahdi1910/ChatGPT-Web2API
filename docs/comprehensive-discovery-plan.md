# Comprehensive Discovery Plan

## Objective

Capture the complete ChatGPT web API surface — all features, all models,
all project types — in a single structured capture session. This becomes
the definitive reference for building the proxy.

## Feature Map

### A. Chat (Core)
- Normal chat (no project)
- Project chat — shared memory
- Project chat — dedicated memory
- GPT (gizmo) interaction
- Temporary chat
- Model switching

### B. Projects
- List projects
- Create project (shared memory)
- Create project (dedicated memory)
- Project detail (instructions, files, chats)
- Project sources

### C. Images (DALL-E)
- URL: `chatgpt.com/images/`
- Generate image
- Edit image
- Image history

### D. Deep Research
- URL: `chatgpt.com/deep-research`
- Start research task
- Research status/progress
- Research results

### E. Library
- URL: `chatgpt.com/library`
- Saved prompts
- Shared conversations
- Browse library

### F. Apps (GPTs)
- URL: `chatgpt.com/apps`
- Browse/search GPTs
- GPT detail
- Start GPT conversation

### G. System
- Auth (session, CSRF, token refresh)
- Sentinel (PoW, Turnstile)
- Memories
- Files (upload, library)
- Settings

## Known vs Unknown by Feature

| Feature | Endpoints Known | Request Format Known | Response Format Known |
|---------|:-:|:-:|:-:|
| Normal chat | ❌ | ❌ | ❌ |
| Project chat (shared) | ✅ | ✅ | ❌ (SSE) |
| Project chat (dedicated) | ❌ | ❌ | ❌ |
| GPT interaction | ❌ | ❌ | ❌ |
| Model switching | ❌ | ❌ | N/A |
| Create project | ❌ | ❌ | ❌ |
| Images | ❌ | ❌ | ❌ |
| Deep Research | ❌ | ❌ | ❌ |
| Library | ❌ | ❌ | ❌ |
| Apps/GPTs | ❌ | ❌ | ❌ |
| Auth/token | ❌ | ❌ | ❌ |
| Sentinel/PoW | ✅ (3-step) | ✅ | ❌ |
| Memories | Endpoint only | ❌ | ❌ |
| Files | Endpoint only | ❌ | ❌ |

## Discovery Session Plan

### Script: discover3.py (Comprehensive)

One script, one session, captures everything. User walks through features
while the script records all traffic.

### Phase A: Model Discovery (~2 min)
1. Open model picker/dropdown
2. Observe the model list request/response
3. Switch to each model
4. Capture model slugs

### Phase B: Normal Chat (~3 min)
1. Start a NEW conversation (no project)
2. Send "Hello"
3. Observe the full request/response
4. Capture conversation_mode = primary_assistant

### Phase C: Project — Shared Memory (~3 min)
1. Create a new project with SHARED memory
2. Send a message
3. Observe request differences from dedicated

### Phase D: Project — Dedicated Memory (~3 min)
1. Create a new project with DEDICATED memory
2. Send a message
3. Observe memory_scope, context_scopes differences

### Phase E: Deep Research (~5 min)
1. Navigate to chatgpt.com/deep-research
2. Start a research task
3. Observe the endpoints used

### Phase F: Images (~3 min)
1. Navigate to chatgpt.com/images/
2. Generate an image
3. Observe the endpoints used

### Phase G: GPT Interaction (~3 min)
1. Navigate to chatgpt.com/apps
2. Pick a GPT
3. Start a conversation
4. Observe gizmo_interaction format

### Phase H: Library (~2 min)
1. Navigate to chatgpt.com/library
2. Browse/search
3. Observe endpoints

## Key Corrections from Capture Session

1. **PoW algorithm**: Uses `hash-wasm` (SHA-256 based), NOT FNV-1a.
   Our current solver is wrong.
2. **Sentinel flow**: 3-step (prepare → finalize → req), not single call
3. **Message endpoint**: `/f/conversation`, not `/conversation`
4. **Project scoping**: `gizmo_interaction` kind, not a separate `project_interaction`
5. **Init is separate**: `/conversation/init` just loads metadata

## Output Format

For each captured request, save:
```json
{
  "feature": "project-chat-dedicated",
  "step": "send-message",
  "request": {
    "method": "POST",
    "url": "/backend-api/f/conversation",
    "headers": {...},
    "body": {...}
  },
  "response": {
    "status": 200,
    "headers": {...},
    "body_preview": "...(first 50000 chars)"
  }
}
```
