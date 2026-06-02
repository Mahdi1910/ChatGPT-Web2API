"""Comprehensive Discovery — raw CDP via websockets.

Single-reader architecture. No race conditions.

Usage:
    python scripts/discover5.py --cdp-port 9222

Prerequisite: Chrome running with --remote-debugging-port=9222
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("discover5")

try:
    import websockets
except ImportError:
    logger.error("websockets required: pip install websockets")
    sys.exit(1)


class CDPCapture:
    """Raw CDP network capture — single reader, inline event handling."""

    def __init__(self, cdp_port: int):
        self.port = cdp_port
        self.ws = None
        self.msg_id = 0
        self._pending: dict[int, asyncio.Future] = {}

        # Capture state
        self.current_feature = "setup"
        self.captures: dict[str, list[dict]] = {}
        self.all_requests: list[dict] = []
        self.all_responses: list[dict] = []
        self._response_bodies_pending: dict[str, dict] = {}  # reqId -> response entry
        self._request_map: dict[str, dict] = {}  # reqId -> request entry

    # ── Low-level CDP ────────────────────────────────────────

    async def send_cmd(self, method: str, params: dict = None) -> dict:
        """Send a CDP command and wait for response (via event loop)."""
        self.msg_id += 1
        msg = {"id": self.msg_id, "method": method}
        if params:
            msg["params"] = params
        fut = asyncio.get_event_loop().create_future()
        self._pending[self.msg_id] = fut
        await self.ws.send(json.dumps(msg))
        return await asyncio.wait_for(fut, timeout=10)

    async def read_one(self, timeout: float = 0.05) -> dict | None:
        """Read one CDP message with timeout. Returns None on timeout."""
        try:
            raw = await asyncio.wait_for(self.ws.recv(), timeout=timeout)
            return json.loads(raw)
        except (asyncio.TimeoutError, TimeoutError):
            return None
        except Exception:
            return None

    def handle_msg(self, msg: dict):
        """Process a single CDP message (command response or event)."""
        # Command responses
        if "id" in msg and msg["id"] in self._pending:
            fut = self._pending.pop(msg["id"])
            if not fut.done():
                fut.set_result(msg)
            return

        method = msg.get("method", "")
        params = msg.get("params", {})

        if method == "Network.requestWillBeSent":
            self._on_request(params)
        elif method == "Network.responseReceived":
            self._on_response(params)
        elif method == "Network.loadingFinished":
            self._on_loading_finished(params)

    # ── Event handlers ───────────────────────────────────────

    def _on_request(self, params: dict):
        request = params.get("request", {})
        url = request.get("url", "")
        if "chatgpt.com" not in url:
            return

        req_id = params.get("requestId", "")
        method = request.get("method", "")

        entry = {
            "url": url.split("?")[0],
            "url_full": url[:500],
            "method": method,
            "resource_type": params.get("type", ""),
            "request_id": req_id,
            "timestamp": time.time(),
            "headers": request.get("headers", {}),
            "body": None,
            "feature": self.current_feature,
        }

        post_data = request.get("postData")
        if post_data:
            try:
                entry["body"] = json.loads(post_data)
            except Exception:
                entry["body_raw"] = post_data[:5000]

        self.all_requests.append(entry)
        self.captures.setdefault(self.current_feature, []).append(entry)
        self._request_map[req_id] = entry

        if method in ("POST", "PUT", "PATCH") and "/backend-api/" in url:
            base = url.split("?")[0].split("/backend-api/")[1]
            logger.info("  [%s] %s /backend-api/%s", self.current_feature, method, base[:60])
            body = entry.get("body")
            if isinstance(body, dict):
                for key in ("model", "conversation_mode", "action", "thinking_effort"):
                    if key in body:
                        logger.info("    %s: %s", key, json.dumps(body[key])[:200])
                if "messages" in body:
                    msgs = body["messages"]
                    if isinstance(msgs, list) and msgs:
                        parts = msgs[0].get("content", {}).get("parts", [])
                        if parts:
                            logger.info("    msg: %s", str(parts)[:200])

    def _on_response(self, params: dict):
        response = params.get("response", {})
        url = response.get("url", "")
        if "chatgpt.com" not in url:
            return

        req_id = params.get("requestId", "")
        entry = {
            "url": url.split("?")[0],
            "status": response.get("status", 0),
            "resource_type": params.get("type", ""),
            "request_id": req_id,
            "timestamp": time.time(),
            "feature": self.current_feature,
            "headers": response.get("headers", {}),
            "body_preview": None,
        }
        self.all_responses.append(entry)

        if "/backend-api/" in url:
            self._response_bodies_pending[req_id] = entry

    def _on_loading_finished(self, params: dict):
        req_id = params.get("requestId", "")
        if req_id in self._response_bodies_pending:
            self._response_bodies_pending[req_id]["_need_body"] = req_id

    # ── Top-level pump ───────────────────────────────────────

    async def pump(self, duration: float = 0):
        """Process CDP messages for `duration` seconds (0 = forever)."""
        deadline = time.time() + duration if duration else float("inf")
        while time.time() < deadline:
            msg = await self.read_one(timeout=0.1)
            if msg:
                self.handle_msg(msg)
            # Also drain any pending body fetches
            await self._fetch_bodies()

    async def pump_until_enter(self, prompt: str = ""):
        """Process CDP messages until user presses Enter."""
        loop = asyncio.get_event_loop()
        enter_fut = loop.run_in_executor(None, input, prompt)
        while not enter_fut.done():
            msg = await self.read_one(timeout=0.1)
            if msg:
                self.handle_msg(msg)
            await self._fetch_bodies()
        try:
            enter_fut.result()
        except KeyboardInterrupt:
            raise

    async def _fetch_bodies(self):
        """Fetch response bodies for completed requests."""
        to_fetch = []
        for req_id, entry in list(self._response_bodies_pending.items()):
            if "_need_body" in entry:
                to_fetch.append((req_id, entry))
                del entry["_need_body"]

        for req_id, entry in to_fetch:
            try:
                result = await self.send_cmd("Network.getResponseBody", {
                    "requestId": req_id
                })
                body = result.get("result", {}).get("body", "")
                if body:
                    entry["body_preview"] = body[:100000]
                    base = entry["url"].split("/backend-api/")[1] if "/backend-api/" in entry["url"] else ""
                    logger.info("  [body] /backend-api/%s (%d bytes)", base[:50], len(body))
            except Exception:
                pass

    # ── Output ───────────────────────────────────────────────

    def to_dict(self) -> dict:
        # Clean up internal markers
        for entry in self.all_responses:
            entry.pop("_need_body", None)
        return {
            "captures": self.captures,
            "all_requests": self.all_requests,
            "all_responses": self.all_responses,
        }


async def main() -> None:
    parser = argparse.ArgumentParser(description="Raw CDP ChatGPT discovery v5")
    parser.add_argument("--cdp-port", type=int, required=True)
    parser.add_argument("--output", default="captured_comprehensive.json")
    args = parser.parse_args()

    cap = CDPCapture(args.cdp_port)

    # ── Connect ──────────────────────────────────────────────
    import urllib.request
    targets_url = f"http://127.0.0.1:{args.cdp_port}/json/list"
    req = urllib.request.Request(targets_url)
    with urllib.request.urlopen(req) as resp:
        targets = json.loads(resp.read())

    chatgpt = [t for t in targets if t.get("type") == "page" and "chatgpt.com" in t.get("url", "")]
    if not chatgpt:
        logger.error("No ChatGPT page found!")
        return

    target = chatgpt[0]
    ws_url = target["webSocketDebuggerUrl"]
    logger.info("Connecting to: %s", target.get("title", "")[:60])

    cap.ws = await websockets.connect(ws_url, max_size=50 * 1024 * 1024)

    # Enable Network
    result = await cap.send_cmd("Network.enable", {"maxPostDataSize": 65536})
    logger.info("Network.enable OK")

    # Reload to activate capture
    logger.info("Reloading page...")
    cap.msg_id += 1
    await cap.ws.send(json.dumps({"id": cap.msg_id, "method": "Page.reload", "params": {}}))

    # Drain reload events for 5 seconds
    await cap.pump(duration=5)
    logger.info("Page reloaded, capture active\n")

    # ── Guided walkthrough ───────────────────────────────────
    features = [
        ("A-models", "Open the MODEL PICKER dropdown. Switch between ALL available models."),
        ("B-normal-chat", "Start a NEW conversation (NOT in a project). Send 'Hello, what model are you?'."),
        ("C-project-shared", "Use/create a project with SHARED memory. Send a message in it."),
        ("D-project-dedicated", "Use/create a project with DEDICATED memory. Send a message in it."),
        ("E-deep-research", "Start a Deep Research task (use the toggle or go to deep-research)."),
        ("F-images", "Generate an image (ask ChatGPT to create one)."),
        ("G-gpts", "Go to chatgpt.com/apps. Pick a GPT. Start a conversation."),
        ("H-library", "Go to chatgpt.com/library. Browse around."),
        ("I-memories", "Go to Settings > Memory or the memory page."),
        ("J-misc", "Anything else you want to capture."),
    ]

    print("\n" + "=" * 60)
    print("COMPREHENSIVE CHATGPT DISCOVERY (Raw CDP v5)")
    print("=" * 60)
    print("\nBrowse ChatGPT normally in your Chrome window.")
    print("All network traffic is captured in real-time.\n")

    try:
        for feature_id, instructions in features:
            print("=" * 60)
            print(f"FEATURE: {feature_id}")
            print(f"ACTION:  {instructions}")
            print("=" * 60)
            cap.current_feature = feature_id
            if feature_id not in cap.captures:
                cap.captures[feature_id] = []
            await cap.pump_until_enter(prompt="\nPress Enter when done with this feature (Ctrl+C to stop)...\n")
    except KeyboardInterrupt:
        print("\nStopping...")

    # ── Save ─────────────────────────────────────────────────
    output_path = Path(args.output)
    output_path.write_text(json.dumps(cap.to_dict(), indent=2, default=str))

    # Summary
    print("\n" + "=" * 60)
    print("CAPTURE SUMMARY")
    print("=" * 60)

    for feature, reqs in cap.captures.items():
        api_eps = Counter()
        models = set()
        conv_modes = set()
        for r in reqs:
            url = r.get("url", "")
            if "/backend-api/" in url:
                api_eps[url.split("/backend-api/")[1].split("?")[0]] += 1
            body = r.get("body", {})
            if isinstance(body, dict):
                if "model" in body:
                    models.add(body["model"])
                if "conversation_mode" in body:
                    conv_modes.add(json.dumps(body["conversation_mode"]))

        print(f"\n{feature}: {len(reqs)} requests")
        for ep, count in api_eps.most_common():
            print(f"  [{count}x] /backend-api/{ep}")
        if models:
            print(f"  Models: {', '.join(sorted(models))}")
        for cm in sorted(conv_modes):
            print(f"  conv_mode: {cm}")

    print(f"\nTotal: {len(cap.all_requests)} requests, {len(cap.all_responses)} responses")
    print(f"Saved to {output_path}")


if __name__ == "__main__":
    asyncio.run(main())
