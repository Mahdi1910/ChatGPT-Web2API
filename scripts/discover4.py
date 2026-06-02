"""Comprehensive Discovery — raw CDP via websockets.

No Patchright, no Playwright. Connects directly to Chrome DevTools
Protocol via websocket. Zero frame enumeration issues.

Intercepts ALL network requests at the browser level regardless of
which tab they come from. User browses normally in Chrome while
this script captures everything.

Usage:
    python scripts/discover4.py --cdp-port 9222

Prerequisite: Chrome running with --remote-debugging-port=9222
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("discover4")

try:
    import websockets
except ImportError:
    logger.error("websockets required: pip install websockets")
    sys.exit(1)

try:
    import httpx
except ImportError:
    import urllib.request
    httpx = None


class CDPCapture:
    """Raw CDP network capture via websocket."""

    def __init__(self, cdp_port: int):
        self.port = cdp_port
        self.ws: Optional[object] = None
        self.msg_id = 0
        self.current_feature = "setup"
        self.captures: dict[str, list[dict]] = {}
        self.all_requests: list[dict] = []
        self.all_responses: list[dict] = []
        self._pending: dict[int, asyncio.Future] = {}
        self._running = False

    def set_feature(self, name: str):
        self.current_feature = name
        if name not in self.captures:
            self.captures[name] = []
        logger.info(">>> Feature: %s", name)

    async def send_cmd(self, method: str, params: dict = None) -> dict:
        self.msg_id += 1
        msg = {"id": self.msg_id, "method": method}
        if params:
            msg["params"] = params
        fut = asyncio.get_event_loop().create_future()
        self._pending[self.msg_id] = fut
        await self.ws.send(json.dumps(msg))
        return await asyncio.wait_for(fut, timeout=10)

    async def start(self, listen_task: asyncio.Task):
        """Connect to browser CDP and enable network capture.

        listen_task must already be running to receive command responses.
        """
        # Get page targets via HTTP
        targets_url = f"http://127.0.0.1:{self.port}/json/list"
        if httpx:
            async with httpx.AsyncClient() as c:
                resp = await c.get(targets_url)
                targets = resp.json()
        else:
            req = urllib.request.Request(targets_url)
            with urllib.request.urlopen(req) as resp:
                targets = json.loads(resp.read())

        logger.info("Found %d targets", len(targets))

        # Connect to each page target's websocket directly
        for target in targets:
            if target.get("type") != "page":
                continue
            ws_url = target.get("webSocketDebuggerUrl")
            page_title = target.get("title", "")[:60]
            if not ws_url:
                continue
            try:
                page_ws = await websockets.connect(ws_url, max_size=50 * 1024 * 1024)
                # Enable Network domain on this page
                self.msg_id += 1
                enable_msg = {"id": self.msg_id, "method": "Network.enable", "params": {}}
                await page_ws.send(json.dumps(enable_msg))
                # Read the response
                resp = await asyncio.wait_for(page_ws.recv(), timeout=5)
                logger.info("  Network enabled on: %s", page_title)
                # Store this ws for listening
                if self.ws is None:
                    self.ws = page_ws
                else:
                    # For multiple pages, we'd need multiple listeners
                    # For now just use the first ChatGPT page
                    pass
            except Exception as e:
                logger.warning("  Failed on %s: %s", page_title, e)

        if self.ws is None:
            raise RuntimeError("No page websocket connected")

        self._running = True
        logger.info("Network capture active\n")

    async def listen(self):
        """Listen for CDP events and command responses."""
        # Wait until ws is set by start()
        for _ in range(100):
            if self.ws is not None:
                break
            await asyncio.sleep(0.1)
        if self.ws is None:
            logger.error("WebSocket never initialized")
            return

        try:
            async for raw in self.ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                # Handle command responses
                if "id" in msg and msg["id"] in self._pending:
                    fut = self._pending.pop(msg["id"])
                    if not fut.done():
                        fut.set_result(msg)
                    continue

                # Handle events
                method = msg.get("method", "")
                params = msg.get("params", {})

                if method == "Network.requestWillBeSent":
                    await self._on_request(params)
                elif method == "Network.responseReceived":
                    await self._on_response(params)
                elif method == "Network.loadingFinished":
                    await self._on_loading_finished(params)

        except websockets.ConnectionClosed:
            logger.info("CDP connection closed")
        except asyncio.CancelledError:
            pass

    async def _on_request(self, params: dict):
        request = params.get("request", {})
        url = request.get("url", "")
        if "chatgpt.com" not in url:
            return

        method = request.get("method", "")
        entry = {
            "url": url.split("?")[0],
            "url_full": url[:500],
            "method": method,
            "resource_type": params.get("type", ""),
            "request_id": params.get("requestId", ""),
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

        if method in ("POST", "PUT", "PATCH") and "/backend-api/" in url:
            base = url.split("?")[0].split("/backend-api/")[1]
            logger.info("  [%s] %s /backend-api/%s", self.current_feature, method, base[:60])
            body = entry.get("body")
            if isinstance(body, dict):
                for key in ("model", "conversation_mode", "action"):
                    if key in body:
                        logger.info("    %s: %s", key, json.dumps(body[key])[:200])
                if "messages" in body:
                    msgs = body["messages"]
                    if isinstance(msgs, list) and msgs:
                        parts = msgs[0].get("content", {}).get("parts", [])
                        logger.info("    messages[0]: %s", str(parts)[:200])

    async def _on_response(self, params: dict):
        response = params.get("response", {})
        url = response.get("url", "")
        if "chatgpt.com" not in url:
            return

        entry = {
            "url": url.split("?")[0],
            "status": response.get("status", 0),
            "method": params.get("type", ""),
            "resource_type": params.get("type", ""),
            "request_id": params.get("requestId", ""),
            "timestamp": time.time(),
            "feature": self.current_feature,
            "headers": response.get("headers", {}),
            "body_preview": None,
        }
        self.all_responses.append(entry)

    async def _on_loading_finished(self, params: dict):
        """Capture response body when loading finishes."""
        request_id = params.get("requestId", "")
        # Find the matching response
        for resp in reversed(self.all_responses):
            if resp.get("request_id") == request_id and "/backend-api/" in resp.get("url", ""):
                try:
                    result = await self.send_cmd("Network.getResponseBody", {
                        "requestId": request_id
                    })
                    body = result.get("result", {}).get("body", "")
                    if body:
                        resp["body_preview"] = body[:50000]
                        base = resp["url"].split("/backend-api/")[1] if "/backend-api/" in resp["url"] else resp["url"][-40:]
                        logger.info("  [%s] Response body: /backend-api/%s (%d bytes)",
                                    resp.get("feature", "?"), base[:50], len(body))
                except Exception:
                    pass
                break

    async def stop(self):
        self._running = False
        if self.ws:
            await self.ws.close()

    def to_dict(self) -> dict:
        return {
            "captures": self.captures,
            "all_requests": self.all_requests,
            "all_responses": self.all_responses,
        }


async def main() -> None:
    parser = argparse.ArgumentParser(description="Raw CDP ChatGPT discovery")
    parser.add_argument("--cdp-port", type=int, required=True)
    parser.add_argument("--output", default="captured_comprehensive.json")
    args = parser.parse_args()

    cap = CDPCapture(args.cdp_port)

    # Start listen loop FIRST so it can receive command responses
    listen_task = asyncio.create_task(cap.listen())
    # Give the listener a moment to start
    await asyncio.sleep(0.1)

    # Now connect and enable network (sends commands that the listener handles)
    await cap.start(listen_task)

    # Guided walkthrough
    features = [
        ("A-models", "Open the MODEL PICKER dropdown. Switch between ALL available models."),
        ("B-normal-chat", "Start a NEW conversation (NOT in a project). Send 'Hello'."),
        ("C-project-shared", "Use/create a project with SHARED memory. Send a message."),
        ("D-project-dedicated", "Use/create a project with DEDICATED memory. Send a message."),
        ("E-deep-research", "Go to chatgpt.com/deep-research. Start a research task."),
        ("F-images", "Go to chatgpt.com/images/ Generate an image."),
        ("G-gpts", "Go to chatgpt.com/apps. Pick a GPT. Start a conversation."),
        ("H-library", "Go to chatgpt.com/library. Browse."),
        ("I-memories", "Go to chatgpt.com/settings or memory page."),
        ("J-misc", "Anything else you want to capture."),
    ]

    print("\n" + "=" * 60)
    print("COMPREHENSIVE CHATGPT DISCOVERY (Raw CDP)")
    print("=" * 60)
    print("\nUsing raw Chrome DevTools Protocol — no Patchright needed.")
    print("Browse ChatGPT normally in your Chrome window.")
    print("ALL network traffic from ALL tabs is captured.\n")

    try:
        for feature_id, instructions in features:
            print("=" * 60)
            print(f"FEATURE: {feature_id}")
            print(f"ACTION:  {instructions}")
            print("=" * 60)
            cap.set_feature(feature_id)
            try:
                input("\nPress Enter when done with this feature (Ctrl+C to stop)...\n")
            except KeyboardInterrupt:
                print("\nStopping...")
                break
    finally:
        listen_task.cancel()
        await cap.stop()

    # Save
    output_path = Path(args.output)
    output_path.write_text(json.dumps(cap.to_dict(), indent=2, default=str))

    # Summary
    print("\n" + "=" * 60)
    print("CAPTURE SUMMARY")
    print("=" * 60)

    from collections import Counter
    for feature, reqs in cap.captures.items():
        api_eps = set()
        models = set()
        conv_modes = set()
        for r in reqs:
            url = r.get("url", "")
            if "/backend-api/" in url:
                api_eps.add(url.split("/backend-api/")[1])
            body = r.get("body", {})
            if isinstance(body, dict):
                if "model" in body:
                    models.add(body["model"])
                if "conversation_mode" in body:
                    conv_modes.add(json.dumps(body["conversation_mode"]))

        print(f"\n{feature}: {len(reqs)} requests")
        for ep in sorted(api_eps):
            print(f"  /backend-api/{ep}")
        if models:
            print(f"  Models: {', '.join(sorted(models))}")
        for cm in sorted(conv_modes):
            print(f"  conv_mode: {cm}")

    print(f"\nTotal: {len(cap.all_requests)} requests, {len(cap.all_responses)} responses")
    print(f"Saved to {output_path}")


if __name__ == "__main__":
    asyncio.run(main())
