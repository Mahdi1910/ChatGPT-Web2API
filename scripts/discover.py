"""Phase 1 Discovery — intercept ChatGPT /backend-api/conversation request.

Connects to an existing browser session via CDP (or launches a new one),
navigates to ChatGPT, and captures the full request payload when you send
a message inside a project.

Usage:
    # Attach to your running browser (recommended — uses existing login):
    python -m scripts.discover --cdp-port 9222

    # Full WebSocket URL:
    python -m scripts.discover --cdp-endpoint ws://localhost:9222

    # Launch a new browser (you'll need to log in):
    python -m scripts.discover

    # With a specific project:
    python -m scripts.discover --cdp-port 9222 --project g-p-XXXXX

How to enable CDP on your browser:
    Chrome:  chrome.exe --remote-debugging-port=9222
    Edge:    msedge.exe --remote-debugging-port=9222
    Brave:   brave.exe --remote-debugging-port=9222

Workflow:
    1. Start browser with --remote-debugging-port=9222
    2. Open chatgpt.com and log in normally
    3. Run this script with --cdp-port 9222
    4. Navigate to your project in the browser
    5. Send a message in the ChatGPT UI
    6. Script captures and dumps the full request/response
    7. Press Ctrl+C to save and exit
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("discover")


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="ChatGPT API discovery script — capture conversation request format",
    )
    conn = parser.add_argument_group("Connection")
    conn.add_argument("--cdp-endpoint", default=None,
                       help="Connect via CDP WebSocket URL (e.g. ws://localhost:9222)")
    conn.add_argument("--cdp-port", type=int, default=None,
                       help="Connect via localhost CDP port")

    parser.add_argument("--project",
                        default="g-p-6a1cbfa6da8c8191bd3674470d2dbc22-orqestra",
                        help="Project ID to test")
    parser.add_argument("--cookie-file", default=None,
                        help="Path to saved cookies JSON")
    parser.add_argument("--output", default="captured_request.json",
                        help="Output file for captured payload")
    parser.add_argument("--navigate", action="store_true", default=False,
                        help="Auto-navigate to the project page")
    args = parser.parse_args()

    try:
        from patchright.async_api import async_playwright
    except ImportError:
        logger.error("patchright is required: pip install super-browser[patchright]")
        sys.exit(1)

    captured_requests: list[dict] = []
    captured_responses: list[dict] = []
    sentinel_requests: list[dict] = []
    auth_requests: list[dict] = []

    browser = None
    context = None
    page = None
    attached = False

    pw = await async_playwright().start()
    try:
        # --- Connect or launch ---
        cdp_url = args.cdp_endpoint
        if not cdp_url and args.cdp_port:
            cdp_url = f"http://127.0.0.1:{args.cdp_port}"

        if cdp_url:
            logger.info("Connecting to existing browser at %s", cdp_url)
            browser = await pw.chromium.connect_over_cdp(cdp_url)
            attached = True

            contexts = browser.contexts
            if not contexts:
                logger.error("No browser contexts found. Open at least one tab.")
                sys.exit(1)

            context = contexts[0]
            logger.info("Connected. Context has %d page(s).", len(context.pages))

            if context.pages:
                page = context.pages[0]
                logger.info("Using existing page: %s", page.url[:100])
            else:
                page = await context.new_page()
                logger.info("Created new page in existing context.")
        else:
            logger.info("Launching new Patchright browser...")
            browser = await pw.chromium.launch(
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
            )
            context = await browser.new_context(
                viewport={"width": 1440, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/137.0.0.0 Safari/537.36"
                ),
            )
            if args.cookie_file and Path(args.cookie_file).exists():
                cookies = json.loads(Path(args.cookie_file).read_text())
                await context.add_cookies(cookies)
                logger.info("Restored %d cookies from %s", len(cookies), args.cookie_file)
            page = await context.new_page()

        # --- Intercept requests ---
        async def on_route(route):
            request = route.request
            url = request.url

            if "/backend-api/conversation" in url and request.method == "POST":
                try:
                    post_data = request.post_data
                    headers = await request.all_headers()
                    entry = {
                        "url": url,
                        "method": request.method,
                        "headers": dict(headers),
                        "body": json.loads(post_data) if post_data else None,
                        "timestamp": time.time(),
                    }
                    captured_requests.append(entry)
                    logger.info("=" * 60)
                    logger.info("CAPTURED /backend-api/conversation REQUEST!")
                    if entry["body"]:
                        logger.info("Body keys: %s", sorted(entry["body"].keys()))
                        cm = entry["body"].get("conversation_mode")
                        if cm:
                            logger.info("conversation_mode: %s", json.dumps(cm, indent=2))
                        for key in entry["body"]:
                            kl = key.lower()
                            if "project" in kl or "gizmo" in kl or "scope" in kl:
                                logger.info("SCOPE FIELD: %s = %s",
                                            key, json.dumps(entry["body"][key], indent=2))
                    logger.info("=" * 60)
                except Exception as e:
                    logger.warning("Failed to capture conversation request: %s", e)

            elif "/backend-api/sentinel/chat-requirements" in url:
                try:
                    post_data = request.post_data
                    headers = await request.all_headers()
                    sentinel_requests.append({
                        "url": url, "method": request.method,
                        "headers": dict(headers),
                        "body": json.loads(post_data) if post_data else None,
                        "timestamp": time.time(),
                    })
                    logger.info("Captured sentinel/chat-requirements request")
                except Exception as e:
                    logger.warning("Failed to capture sentinel request: %s", e)

            elif "/api/auth/session" in url:
                try:
                    headers = await request.all_headers()
                    auth_requests.append({
                        "url": url, "method": request.method,
                        "headers": dict(headers), "timestamp": time.time(),
                    })
                    logger.info("Captured auth/session request")
                except Exception as e:
                    logger.warning("Failed to capture auth request: %s", e)

            await route.continue_()

        def on_response(response):
            url = response.url
            if "/backend-api/conversation" in url:
                async def _cap_conv():
                    try:
                        body = await response.text()
                        headers = await response.all_headers()
                        captured_responses.append({
                            "url": url, "status": response.status,
                            "headers": dict(headers),
                            "body_preview": body[:10000] if body else None,
                            "timestamp": time.time(),
                        })
                        logger.info("Captured conversation response (status=%d, %d bytes)",
                                    response.status, len(body) if body else 0)
                    except Exception as e:
                        logger.warning("Failed to capture conversation response: %s", e)
                asyncio.ensure_future(_cap_conv())

            elif "/backend-api/sentinel/chat-requirements" in url:
                async def _cap_sent():
                    try:
                        body = await response.text()
                        data = json.loads(body) if body else {}
                        sentinel_requests.append({
                            "url": url, "status": response.status,
                            "body": data, "timestamp": time.time(),
                        })
                        if isinstance(data, dict):
                            logger.info("Sentinel response keys: %s", list(data.keys()))
                            if "proofofwork" in data:
                                pw_d = data["proofofwork"]
                                logger.info("PoW: seed=%s... difficulty=%s",
                                            str(pw_d.get("seed", ""))[:20],
                                            pw_d.get("difficulty"))
                            if "turnstile" in data:
                                logger.info("Turnstile: %s", data["turnstile"])
                    except Exception as e:
                        logger.warning("Failed to capture sentinel response: %s", e)
                asyncio.ensure_future(_cap_sent())

            elif "/api/auth/session" in url:
                async def _cap_auth():
                    try:
                        body = await response.text()
                        auth_requests.append({
                            "url": url, "status": response.status,
                            "body_preview": body[:2000] if body else None,
                            "timestamp": time.time(),
                        })
                        logger.info("Captured auth response (status=%d)", response.status)
                    except Exception as e:
                        logger.warning("Failed to capture auth response: %s", e)
                asyncio.ensure_future(_cap_auth())

        await page.route("**/backend-api/**", on_route)
        await page.route("**/api/auth/**", on_route)
        page.on("response", on_response)
        logger.info("Network interception active")

        # --- Navigate if needed ---
        current_url = page.url

        if attached and "chatgpt.com" in current_url:
            logger.info("Already on ChatGPT: %s", current_url[:100])
            if args.navigate:
                await page.goto(
                    f"https://chatgpt.com/g/{args.project}/project",
                    wait_until="domcontentloaded",
                )
                await asyncio.sleep(3)
        elif attached:
            logger.info("Current page: %s", current_url[:100])
            logger.info("Navigating to ChatGPT...")
            await page.goto("https://chatgpt.com", wait_until="domcontentloaded")
            await asyncio.sleep(3)

            if "auth/login" in page.url or "auth0" in page.url:
                logger.info("=" * 60)
                logger.info("NOT LOGGED IN. Log in using the browser, then come back.")
                logger.info("Waiting for login...")
                logger.info("=" * 60)
                for _ in range(300):
                    await asyncio.sleep(1)
                    if "auth/login" not in page.url and "auth0" not in page.url:
                        break
                else:
                    logger.error("Login timeout.")
                    sys.exit(1)

            if args.navigate:
                await page.goto(
                    f"https://chatgpt.com/g/{args.project}/project",
                    wait_until="domcontentloaded",
                )
                await asyncio.sleep(3)
        else:
            logger.info("Navigating to chatgpt.com...")
            await page.goto("https://chatgpt.com", wait_until="domcontentloaded")
            await asyncio.sleep(3)

            if "auth/login" in page.url or "auth0" in page.url:
                logger.info("=" * 60)
                logger.info("NOT LOGGED IN. Please log in using the browser window.")
                logger.info("Waiting for login...")
                logger.info("=" * 60)
                for _ in range(300):
                    await asyncio.sleep(1)
                    if "auth/login" not in page.url and "auth0" not in page.url:
                        break
                else:
                    logger.error("Login timeout (5 minutes).")
                    sys.exit(1)

            cookies = await context.cookies()
            cookie_path = Path(args.output).parent / "captured_cookies.json"
            cookie_path.write_text(json.dumps(cookies, indent=2))
            logger.info("Saved %d cookies to %s", len(cookies), cookie_path)

            if args.navigate:
                await page.goto(
                    f"https://chatgpt.com/g/{args.project}/project",
                    wait_until="domcontentloaded",
                )
                await asyncio.sleep(3)

        # --- Ready ---
        logger.info("=" * 60)
        logger.info("INTERCEPTION ACTIVE. Current page: %s", page.url[:100])
        logger.info("")
        logger.info("Now send a message in ChatGPT (in your project).")
        logger.info("The script will capture the request automatically.")
        logger.info("Press Ctrl+C when done to save captures.")
        logger.info("=" * 60)

        try:
            while True:
                await asyncio.sleep(1)
                if captured_requests:
                    logger.info(
                        "Captured %d request(s). Press Ctrl+C to save and stop.",
                        len(captured_requests),
                    )
        except KeyboardInterrupt:
            pass

    finally:
        # --- Save captures ---
        output = {
            "conversation_requests": captured_requests,
            "conversation_responses": captured_responses,
            "sentinel_requests": sentinel_requests,
            "auth_requests": auth_requests,
        }
        output_path = Path(args.output)
        output_path.write_text(json.dumps(output, indent=2, default=str))

        logger.info("")
        logger.info("=" * 60)
        logger.info("CAPTURE SUMMARY")
        logger.info("=" * 60)
        logger.info("Conversation requests: %d", len(captured_requests))
        logger.info("Conversation responses: %d", len(captured_responses))
        logger.info("Sentinel requests:      %d", len(sentinel_requests))
        logger.info("Auth requests:          %d", len(auth_requests))

        if captured_requests:
            req = captured_requests[0]
            if req.get("body"):
                logger.info("")
                logger.info("REQUEST BODY KEYS:")
                for k in sorted(req["body"].keys()):
                    v = req["body"][k]
                    v_str = json.dumps(v, default=str) if not isinstance(v, str) else v
                    if len(v_str) > 200:
                        v_str = v_str[:200] + "..."
                    logger.info("  %s: %s", k, v_str)

        if attached:
            logger.info("Disconnecting from browser (browser stays open)")
        if browser:
            try:
                await browser.close()
            except Exception:
                pass
        try:
            await pw.stop()
        except Exception:
            pass

        logger.info("Captures saved to %s", output_path)


if __name__ == "__main__":
    asyncio.run(main())
