"""Autonomous Discovery — inject fetch() calls via CDP Runtime.evaluate.

No manual browsing required. Uses the browser's existing auth session
to make API calls directly from the ChatGPT page context.

Usage:
    python scripts/discover6.py --cdp-port 9222

Prerequisite: Chrome running with --remote-debugging-port=9222
              ChatGPT logged in on any tab
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("discover6")

try:
    import websockets
except ImportError:
    logger.error("pip install websockets")
    sys.exit(1)

import urllib.request


class AutonomousDiscovery:
    def __init__(self, cdp_port: int):
        self.port = cdp_port
        self.ws = None
        self.msg_id = 0
        self.results = {}
        self._access_token = ""

    async def connect(self):
        """Connect to ChatGPT page's CDP websocket."""
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/json/list")
        with urllib.request.urlopen(req) as resp:
            targets = json.loads(resp.read())

        chatgpt = [t for t in targets if t.get("type") == "page" and "chatgpt.com" in t.get("url", "")]
        if not chatgpt:
            # Try any page — we can navigate
            pages = [t for t in targets if t.get("type") == "page"]
            if not pages:
                raise RuntimeError("No page targets found")
            chatgpt = pages

        target = chatgpt[0]
        ws_url = target["webSocketDebuggerUrl"]
        logger.info("Connected to: %s", target.get("title", "")[:60])
        self.ws = await websockets.connect(ws_url, max_size=100 * 1024 * 1024)

    async def eval_js(self, expr: str, timeout: float = 30) -> dict:
        """Evaluate JS in page context and return result."""
        self.msg_id += 1
        msg = {
            "id": self.msg_id,
            "method": "Runtime.evaluate",
            "params": {
                "expression": expr,
                "awaitPromise": True,
                "returnByValue": True,
                "timeout": int(timeout * 1000),
            }
        }
        await self.ws.send(json.dumps(msg))

        while True:
            raw = await asyncio.wait_for(self.ws.recv(), timeout=timeout + 5)
            resp = json.loads(raw)
            if resp.get("id") == self.msg_id:
                return resp
            # Skip events

    async def fetch_api(self, path: str, method: str = "GET", body: dict = None,
                        timeout: float = 30) -> dict:
        """Fetch a ChatGPT backend API endpoint using the page's auth."""
        # Build auth header if we have a token
        auth_header = f"Bearer {self._access_token}" if self._access_token else ""
        auth_line = f"'Authorization': '{auth_header}'," if auth_header else ""

        # Build the JS expression
        if body is not None:
            body_json = json.dumps(body).replace("</script", "<\\/script")
            js = f"""
            (async () => {{
                try {{
                    const r = await fetch('{path}', {{
                        method: '{method}',
                        headers: {{{auth_line}'Content-Type': 'application/json'}},
                        credentials: 'include',
                        body: JSON.stringify({body_json})
                    }});
                    const text = await r.text();
                    return JSON.stringify({{status: r.status, ok: r.ok, body: text}});
                }} catch(e) {{
                    return JSON.stringify({{status: 0, ok: false, error: e.message}});
                }}
            }})()
            """
        else:
            js = f"""
            (async () => {{
                try {{
                    const r = await fetch('{path}', {{
                        method: '{method}',
                        headers: {{{auth_line}'Content-Type': 'application/json'}},
                        credentials: 'include'
                    }});
                    const text = await r.text();
                    return JSON.stringify({{status: r.status, ok: r.ok, body: text}});
                }} catch(e) {{
                    return JSON.stringify({{status: 0, ok: false, error: e.message}});
                }}
            }})()
            """

        resp = await self.eval_js(js, timeout=timeout)
        result_val = resp.get("result", {}).get("result", {}).get("value")

        if result_val:
            try:
                return json.loads(result_val)
            except json.JSONDecodeError:
                return {"status": -1, "body": result_val}
        return {"status": -1, "error": "no result", "raw": str(resp)[:500]}

    async def fetch_sse(self, path: str, body: dict, timeout: float = 60) -> dict:
        """Fetch an SSE streaming endpoint, collecting the full response."""
        body_json = json.dumps(body).replace("</script", "<\\/script")
        auth_header = f"Bearer {self._access_token}" if self._access_token else ""
        auth_line = f"'Authorization': '{auth_header}'," if auth_header else ""
        js = f"""
        (async () => {{
            try {{
                const r = await fetch('{path}', {{
                    method: 'POST',
                    headers: {{{auth_line}'Content-Type': 'application/json'}},
                    credentials: 'include',
                    body: JSON.stringify({body_json})
                }});
                const status = r.status;
                const reader = r.body.getReader();
                const decoder = new TextDecoder();
                let full = '';
                let chunks = [];
                while (true) {{
                    const {{done, value}} = await reader.read();
                    if (done) break;
                    const chunk = decoder.decode(value, {{stream: true}});
                    full += chunk;
                    chunks.push(chunk);
                    if (full.length > 500000) break;  // safety limit
                }}
                return JSON.stringify({{
                    status: status,
                    headers: Object.fromEntries(r.headers.entries()),
                    body: full,
                    chunkCount: chunks.length,
                    bodyLength: full.length
                }});
            }} catch(e) {{
                return JSON.stringify({{status: 0, error: e.message}});
            }}
        }})()
        """
        resp = await self.eval_js(js, timeout=timeout)
        result_val = resp.get("result", {}).get("result", {}).get("value")
        if result_val:
            try:
                return json.loads(result_val)
            except json.JSONDecodeError:
                return {"status": -1, "body": result_val}
        return {"status": -1, "error": "no result"}

    async def run(self):
        """Run the full autonomous discovery."""
        await self.connect()

        # ── 0. Get access token ──────────────────────────────
        logger.info("=== Extracting access token ===")
        token_resp = await self.fetch_api("/api/auth/session")
        if token_resp.get("ok"):
            try:
                sess = json.loads(token_resp["body"])
                self._access_token = sess.get("accessToken", "")
                user = sess.get("user", {})
                logger.info("  Token obtained (%d chars)", len(self._access_token))
                logger.info("  User: %s (%s)", user.get("name"), user.get("email"))
                self._save("auth_session", {
                    "user_id": user.get("id"),
                    "name": user.get("name"),
                    "email": user.get("email"),
                    "expires": sess.get("expires"),
                    "token_length": len(self._access_token),
                })
            except Exception as e:
                logger.error("  Failed to parse session: %s", e)
                self._access_token = ""
        else:
            logger.error("  Failed to get session: %s", token_resp.get("body", "")[:200])
            self._access_token = ""

        if not self._access_token:
            logger.error("Cannot proceed without access token")
            return

        print("\n" + "=" * 70)
        print("AUTONOMOUS CHATGPT PROTOCOL DISCOVERY")
        print("=" * 70)

        # ── 1. User info ─────────────────────────────────────
        logger.info("=== Fetching user info ===")
        me = await self.fetch_api("/backend-api/me")
        self._save("me", me)
        if me.get("ok"):
            me_data = json.loads(me["body"])
            logger.info("  User: %s (id: %s)", me_data.get("name"), me_data.get("id"))
        else:
            logger.error("  Failed: %s", me.get("body", "")[:200])

        # ── 2. Models ────────────────────────────────────────
        logger.info("=== Fetching model catalog ===")
        models_resp = await self.fetch_api("/backend-api/models?iim=false&is_gizmo=false")
        self._save("models", models_resp)
        if models_resp.get("ok"):
            models_data = json.loads(models_resp["body"])
            model_slugs = [m["slug"] for m in models_data.get("models", [])]
            default = models_data.get("default_model_slug")
            logger.info("  %d models, default=%s: %s", len(model_slugs), default, model_slugs)
        else:
            model_slugs = ["gpt-5-5", "auto"]

        # ── 3. GPT models (gizmo models) ─────────────────────
        logger.info("=== Fetching GPT model catalog ===")
        gpt_models = await self.fetch_api("/backend-api/models/gpts")
        self._save("models_gpts", gpt_models)
        if gpt_models.get("ok"):
            try:
                gm = json.loads(gpt_models["body"])
                count = len(gm) if isinstance(gm, list) else len(gm.get("models", []))
                logger.info("  %d GPT models", count)
            except:
                pass

        # ── 4. Settings ──────────────────────────────────────
        logger.info("=== Fetching settings ===")
        settings = await self.fetch_api("/backend-api/settings/user")
        self._save("settings", settings)

        # ── 5. Account check ─────────────────────────────────
        logger.info("=== Fetching account status ===")
        acct = await self.fetch_api("/backend-api/accounts/check/v4-2023-04-27")
        self._save("account", acct)
        if acct.get("ok"):
            try:
                acct_data = json.loads(acct["body"])
                # Find the account plan
                accounts = acct_data.get("accounts", {}).get("accounts", {}).get("accounts", [])
                if isinstance(accounts, list):
                    for a in accounts:
                        plan = a.get("account", {}).get("plan_type", "")
                        if plan:
                            logger.info("  Plan: %s", plan)
            except:
                pass

        # ── 6. Conversations list ────────────────────────────
        logger.info("=== Fetching conversations ===")
        convs = await self.fetch_api("/backend-api/conversations?offset=0&limit=5&order=updated")
        self._save("conversations", convs)
        conv_ids = []
        if convs.get("ok"):
            try:
                conv_data = json.loads(convs["body"])
                items = conv_data if isinstance(conv_data, list) else conv_data.get("items", [])
                for c in items[:5]:
                    cid = c.get("id", "")
                    title = c.get("title", "")[:40]
                    conv_ids.append(cid)
                    logger.info("  %s: %s", cid[:12], title)
            except:
                pass

        # ── 7. Gizmos/Projects ───────────────────────────────
        logger.info("=== Fetching projects/gizmos ===")
        gizmos_sidebar = await self.fetch_api(
            "/backend-api/gizmos/snorlax/sidebar?owned_only=true&conversations_per_gizmo=5&limit=20"
        )
        self._save("gizmos_sidebar", gizmos_sidebar)
        project_ids = []
        if gizmos_sidebar.get("ok"):
            try:
                giz_data = json.loads(gizmos_sidebar["body"])
                items = giz_data if isinstance(giz_data, list) else giz_data.get("gizmos", giz_data.get("items", []))
                if isinstance(items, dict):
                    items = list(items.values()) if items else []
                for g in (items if isinstance(items, list) else []):
                    gid = g.get("gizmo", {}).get("id", g.get("id", ""))
                    title = g.get("gizmo", {}).get("name", g.get("title", g.get("name", "")))[:40]
                    if gid:
                        project_ids.append(gid)
                        logger.info("  Project: %s (%s)", gid, title)
            except Exception as e:
                logger.warning("  Parse error: %s", e)

        # ── 8. Memory/Memories ───────────────────────────────
        logger.info("=== Fetching memories ===")
        mem = await self.fetch_api("/backend-api/memories?exclusive_to_gizmo=false&include_memory_entries=false")
        self._save("memories", mem)
        if mem.get("ok"):
            try:
                mem_data = json.loads(mem["body"])
                count = len(mem_data) if isinstance(mem_data, list) else "?"
                logger.info("  %s memories", count)
            except:
                pass

        # ── 9. Files library ─────────────────────────────────
        logger.info("=== Fetching file library ===")
        files = await self.fetch_api("/backend-api/files/library")
        self._save("files_library", files)

        # ── 10. System hints ─────────────────────────────────
        logger.info("=== Fetching system hints ===")
        for mode in ["basic", "connectors", "custom_agents"]:
            hints = await self.fetch_api(f"/backend-api/system_hints?mode={mode}")
            self._save(f"system_hints_{mode}", hints)

        # ── 11. Connectors ───────────────────────────────────
        logger.info("=== Fetching connectors ===")
        connectors = await self.fetch_api(
            "/backend-api/aip/connectors/list_accessible?skip_actions=true&external_logos=true"
        )
        self._save("connectors", connectors)

        # ── 12. Sentinel prepare ─────────────────────────────
        logger.info("=== Testing sentinel prepare ===")
        sentinel_prep = await self.fetch_api(
            "/backend-api/sentinel/chat-requirements/prepare",
            method="POST",
            body={}
        )
        self._save("sentinel_prepare", sentinel_prep)
        if sentinel_prep.get("ok"):
            try:
                sp = json.loads(sentinel_prep["body"])
                logger.info("  Sentinel prepare keys: %s", list(sp.keys())[:10])
                # Save for later use
                self._sentinel_prep = sp
            except:
                self._sentinel_prep = {}
        else:
            self._sentinel_prep = {}

        # ── 13. Conversation init ────────────────────────────
        logger.info("=== Testing conversation init ===")
        init_body = {"timezone_offset_min": -180}
        init_resp = await self.fetch_api("/backend-api/conversation/init", method="POST", body=init_body)
        self._save("conversation_init", init_resp)
        if init_resp.get("ok"):
            try:
                init_data = json.loads(init_resp["body"])
                logger.info("  Init keys: %s", list(init_data.keys())[:10])
                self._conversation_id = init_data.get("conversation_id", "")
                logger.info("  Conversation ID: %s", self._conversation_id)
            except:
                self._conversation_id = ""
        else:
            self._conversation_id = ""

        # ── 14. Send actual message (normal chat) ────────────
        logger.info("=== Sending test message (normal chat) ===")
        await self._send_test_message(
            feature="normal_chat",
            message="Hello, what model are you? Reply in one sentence.",
            model=model_slugs[0] if model_slugs else "auto",
        )

        # ── 15. Send message with thinking ───────────────────
        if len(model_slugs) > 1:
            logger.info("=== Sending test message with thinking ===")
            await self._send_test_message(
                feature="thinking_chat",
                message="What is 2+2? Show your reasoning.",
                model=model_slugs[0],
                thinking_effort="extended",
            )

        # ── 16. Send message in project context ──────────────
        if project_ids:
            logger.info("=== Sending test message in project context ===")
            await self._send_test_message(
                feature="project_chat",
                message="What files do you have access to in this project?",
                model=model_slugs[0] if model_slugs else "auto",
                gizmo_id=project_ids[0],
            )

        # ── 17. Fetch project details ────────────────────────
        for pid in project_ids[:3]:
            logger.info("=== Fetching project details: %s ===", pid[:20])
            proj = await self.fetch_api(f"/backend-api/gizmos/{pid}")
            self._save(f"project_{pid[:12]}", proj)

        # ── 18. Fetch conversation details ───────────────────
        if conv_ids:
            cid = conv_ids[0]
            logger.info("=== Fetching conversation: %s ===", cid[:12])
            conv = await self.fetch_api(f"/backend-api/conversation/{cid}")
            self._save(f"conversation_{cid[:12]}", conv)

        # ── 19. Try Deep Research ────────────────────────────
        logger.info("=== Testing deep research endpoint ===")
        # Deep research might use a different endpoint or conversation_mode
        # Try with research mode flag
        await self._send_test_message(
            feature="deep_research",
            message="What is the current population of Tokyo?",
            model=model_slugs[0] if model_slugs else "auto",
            extra_fields={"web_search_requests": True},
        )

        # ── 20. Images bootstrap ─────────────────────────────
        logger.info("=== Fetching images bootstrap ===")
        img = await self.fetch_api("/backend-api/images/bootstrap")
        self._save("images_bootstrap", img)

        # ── 21. Pins ─────────────────────────────────────────
        logger.info("=== Fetching pins ===")
        pins = await self.fetch_api("/backend-api/pins")
        self._save("pins", pins)

        # ── 22. User segments ────────────────────────────────
        logger.info("=== Fetching user segments ===")
        segs = await self.fetch_api("/backend-api/user_segments")
        self._save("user_segments", segs)

        # ── 23. Rooms ────────────────────────────────────────
        logger.info("=== Fetching rooms ===")
        rooms = await self.fetch_api("/backend-api/calpico/chatgpt/rooms/summary?limit=10&include_pinned=true")
        self._save("rooms", rooms)

        # ── 24. Subscriptions ────────────────────────────────
        logger.info("=== Fetching subscriptions ===")
        subs = await self.fetch_api("/backend-api/subscriptions")
        self._save("subscriptions", subs)

        # ── 25. Tasks ────────────────────────────────────────
        logger.info("=== Fetching tasks ===")
        tasks = await self.fetch_api("/backend-api/tasks")
        self._save("tasks", tasks)

        # ── 26. Apps/sources dropdown ────────────────────────
        logger.info("=== Fetching apps/sources ===")
        apps = await self.fetch_api("/backend-api/apps/sources_dropdown")
        self._save("apps_sources", apps)

        # ── 27. Client strings ───────────────────────────────
        logger.info("=== Fetching client strings ===")
        strings = await self.fetch_api("/backend-api/client/strings")
        self._save("client_strings", strings)

        # ── Summary ──────────────────────────────────────────
        self._print_summary()

    async def _send_test_message(self, feature: str, message: str, model: str = "auto",
                                  thinking_effort: str = None, gizmo_id: str = None,
                                  extra_fields: dict = None):
        """Send a test message via the conversation endpoint."""
        import uuid

        conversation_id = str(uuid.uuid4())
        parent_message_id = str(uuid.uuid4())
        message_id = str(uuid.uuid4())

        conversation_mode = {"kind": "primary_assistant"}
        if gizmo_id:
            conversation_mode = {"kind": "gizmo_interaction", "gizmo_id": gizmo_id}

        body = {
            "action": "next",
            "messages": [{
                "id": message_id,
                "author": {"role": "user"},
                "content": {"content_type": "text", "parts": [message]},
            }],
            "conversation_id": conversation_id,
            "parent_message_id": parent_message_id,
            "model": model,
            "timezone_offset_min": -180,
            "conversation_mode": conversation_mode,
            "enable_message_followups": True,
            "supports_buffering": True,
            "supported_encodings": ["v1"],
        }

        if thinking_effort:
            body["thinking_effort"] = thinking_effort

        if extra_fields:
            body.update(extra_fields)

        logger.info("  Sending: model=%s, gizmo=%s, thinking=%s", model, gizmo_id or "none", thinking_effort or "none")

        resp = await self.fetch_sse("/backend-api/f/conversation", body, timeout=60)
        self._save(feature, resp)

        if resp.get("status") == 200:
            body_text = resp.get("body", "")
            logger.info("  Response: %d bytes, %d chunks", len(body_text), resp.get("chunkCount", 0))
            # Parse SSE events
            events = self._parse_sse(body_text)
            if events:
                logger.info("  SSE events: %s", list(events.keys()))
                # Save parsed events too
                self._save(f"{feature}_sse", events)
        else:
            logger.warning("  Failed: status=%s, body=%s", resp.get("status"), resp.get("body", "")[:300])

    def _parse_sse(self, raw: str) -> dict:
        """Parse SSE stream into structured events."""
        events = {}
        for line in raw.split("\n"):
            line = line.strip()
            if not line.startswith("data: "):
                continue
            data = line[6:]
            if data == "[DONE]":
                events["done"] = True
                continue
            try:
                obj = json.loads(data)
                msg_type = obj.get("type", "unknown")
                if msg_type not in events:
                    events[msg_type] = []
                events[msg_type].append(obj)
            except json.JSONDecodeError:
                pass
        return events

    def _save(self, name: str, data: dict):
        self.results[name] = data

    def _print_summary(self):
        print("\n" + "=" * 70)
        print("AUTONOMOUS DISCOVERY SUMMARY")
        print("=" * 70)

        ok_count = sum(1 for v in self.results.values() if v.get("ok") or v.get("status") == 200)
        total = len(self.results)
        print(f"Endpoints tested: {total}, successful: {ok_count}")

        for name, data in sorted(self.results.items()):
            status = data.get("status", "?")
            body_len = len(data.get("body", ""))
            ok = "✓" if (data.get("ok") or status == 200) else "✗"
            print(f"  {ok} [{status:>3}] {name:35s} ({body_len:>6} bytes)")

    async def save(self, path: str):
        output = Path(path)
        output.write_text(json.dumps(self.results, indent=2, default=str))
        logger.info("\nSaved to %s", output)


async def main():
    parser = argparse.ArgumentParser(description="Autonomous ChatGPT protocol discovery")
    parser.add_argument("--cdp-port", type=int, required=True)
    parser.add_argument("--output", default="captured_autonomous.json")
    args = parser.parse_args()

    disco = AutonomousDiscovery(args.cdp_port)
    try:
        await disco.run()
    except Exception as e:
        logger.error("Fatal: %s", e, exc_info=True)
    finally:
        await disco.save(args.output)


if __name__ == "__main__":
    asyncio.run(main())
