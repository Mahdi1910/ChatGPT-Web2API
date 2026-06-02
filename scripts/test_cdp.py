"""Minimal CDP test — verify Network.enable works."""

import asyncio
import json
import urllib.request

try:
    import websockets
except ImportError:
    print("pip install websockets")
    exit(1)


async def main():
    # Get page WS URL
    req = urllib.request.Request("http://127.0.0.1:9222/json/list")
    with urllib.request.urlopen(req) as resp:
        targets = json.loads(resp.read())

    page = [t for t in targets if t.get("type") == "page"][0]
    ws_url = page["webSocketDebuggerUrl"]
    print(f"Connecting to: {ws_url}")
    print(f"Page: {page.get('title', '?')}")

    async with websockets.connect(ws_url, max_size=50*1024*1024) as ws:
        # Enable Network
        await ws.send(json.dumps({"id": 1, "method": "Network.enable", "params": {}}))
        resp = await asyncio.wait_for(ws.recv(), timeout=5)
        print(f"Network.enable response: {resp[:200]}")

        # Enable Page events too
        await ws.send(json.dumps({"id": 2, "method": "Page.enable", "params": {}}))
        resp = await asyncio.wait_for(ws.recv(), timeout=5)
        print(f"Page.enable response: {resp[:200]}")

        print("\nListening for 30 seconds... Browse ChatGPT!")
        event_counts = {}
        try:
            for _ in range(300):  # 30 seconds at 0.1s intervals
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=0.1)
                    msg = json.loads(raw)
                    method = msg.get("method", "")
                    if method:
                        event_counts[method] = event_counts.get(method, 0) + 1
                        if "Network" in method:
                            params = msg.get("params", {})
                            url = params.get("request", {}).get("url", params.get("response", {}).get("url", ""))
                            if "chatgpt.com" in url:
                                print(f"  {method}: {url[:100]}")
                except asyncio.TimeoutError:
                    continue
        except KeyboardInterrupt:
            pass

        print(f"\nEvent counts: {json.dumps(event_counts, indent=2)}")

asyncio.run(main())
