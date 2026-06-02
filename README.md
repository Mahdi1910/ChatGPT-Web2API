# ChatGPT-Web2API

OpenAI-compatible API proxy that routes through ChatGPT's web interface with **project memory support**.

## Features

- **Project-scoped conversations** — leverage ChatGPT Projects (custom instructions, files, cross-chat memory)
- **OpenAI API compatible** — works with any client that supports the OpenAI chat completions format
- **Stealth browser** — uses Super Browser's Patchright backend for anti-detection
- **PoW solver** — built-in Proof-of-Work solver for ChatGPT's sentinel challenges
- **Cookie persistence** — sessions survive restarts

## Quick Start

```bash
# Install
pip install -e .

# Run (opens browser for login)
python -m chatgpt_web2api

# With config
python -m chatgpt_web2api -c config.json

# With project
python -m chatgpt_web2api --project g-p-6a1cbfa6da8c8191bd3674470d2dbc22-orqestra
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /` | GET | Health check |
| `GET /v1/models` | GET | List available models |
| `POST /v1/chat/completions` | POST | Chat completion (OpenAI format) |
| `GET /v1/projects` | GET | List ChatGPT projects |
| `GET /v1/projects/{id}/chats` | GET | List project conversations |

## Usage Examples

### Basic Chat (no project)

```bash
curl http://localhost:8082/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o",
    "messages": [{"role": "user", "content": "Hello!"}],
    "stream": false
  }'
```

### Chat within a Project

```bash
curl http://localhost:8082/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o",
    "project_id": "g-p-6a1cbfa6da8c8191bd3674470d2dbc22-orqestra",
    "messages": [{"role": "user", "content": "What files do I have in this project?"}],
    "stream": false
  }'
```

### Model with embedded project

```bash
curl http://localhost:8082/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o-project:g-p-6a1cbfa6da8c8191bd3674470d2dbc22-orqestra",
    "messages": [{"role": "user", "content": "Hello!"}],
    "stream": true
  }'
```

### List Projects

```bash
curl http://localhost:8082/v1/projects
```

## Discovery Script (Phase 1)

Before the proxy can work correctly, you need to run the discovery script to capture the actual ChatGPT conversation request format:

```bash
python scripts/discover.py --project g-p-YOUR_PROJECT_ID
```

This opens ChatGPT in a browser, lets you log in, and captures the full `/backend-api/conversation` request when you send a message. The output is saved to `captured_request.json`.

## Configuration

Copy `config.example.json` to `config.json` and customize:

```json
{
  "port": 8082,
  "default_model": "gpt-4o",
  "default_project_id": null,
  "api_keys": ["your-secret-key"],
  "headless": false,
  "cookie_file": "cookies/session.json"
}
```

### Environment Variables

| Variable | Description |
|----------|-------------|
| `CHATGPT_W2A_PORT` | Server port |
| `CHATGPT_W2A_HOST` | Server host |
| `CHATGPT_W2A_BASE_URL` | ChatGPT base URL |
| `CHATGPT_W2A_DEFAULT_MODEL` | Default model |
| `CHATGPT_W2A_DEFAULT_PROJECT` | Default project ID |
| `CHATGPT_W2A_HEADLESS` | Run browser headless |
| `CHATGPT_W2A_PROXY` | Proxy URL |
| `CHATGPT_W2A_API_KEYS` | Comma-separated API keys |

## Architecture

```
Client (OpenAI format)
    ↓
/v1/chat/completions
    ↓
ConversationBuilder → SentinelClient (PoW)
    ↓
ChatGPTBrowser (Patchright + stealth)
    ↓
chatgpt.com /backend-api/conversation
    ↓
SSE response → OpenAI format → Client
```

## Status

- [x] Project structure and configuration
- [x] Browser manager (Patchright lifecycle)
- [x] Auth layer (AccessToken management)
- [x] PoW solver (FNV-1a brute force)
- [x] Sentinel client (chat-requirements)
- [x] Conversation builder (with project scoping)
- [x] Project manager (list/get/scrape)
- [x] OpenAI-compatible server
- [x] Discovery script (Phase 1)
- [ ] **Phase 1 execution** — capture actual request format
- [ ] Verify PoW solver against current ChatGPT
- [ ] Finalize conversation_mode for projects
- [ ] End-to-end testing

## License

MIT
