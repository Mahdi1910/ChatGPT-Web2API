"""Phase 1b Discovery — capture ALL ChatGPT network traffic.

Broad interception of every request/response to chatgpt.com to find
where the actual message text is sent (the /conversation/init capture
only showed metadata — no messages).

Usage:
    python scripts/discover2.py --cdp-port 9222
    python scripts/discover2.py --cdp-port 9222 --broad
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
logger = logging.getLogger("discover2")

# Patterns to look for in request/response bodies
INTERESTING_PATTERNS = [
    "parts", "content_type", "text", "message",
    "role", "user", "assistant",
    "gizmo_id", "conversation_id", "project",
    "parent_message_id", "action",
]


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Broad ChatGPT traffic capture — find message endpoint",
    )
    parser.add_argument("--cdp-endpoint", default=None,
                        help="CDP WebSocket URL")
    parser.add_argument("--cdp-port", type=int, default=None,
                        help="CDP port")
    parser.add_argument("--project",
                        default="g-p-6a1cbfa6da8c8191bd3674470d2dbc22-orqestra",
                        help="Project ID")
    parser.add_argument("--output", default="captured_broad.json",
                        help="Output file")
    parser.add_argument("--navigate", action="store_true", default=False,
                        help="Auto-navigate to project page")
    args = parser.parse_args()

    try:
        from patchright.async_api import async_playwright
    except ImportError:
        logger.error("patchright is required: pip install super-browser[patchright]")
        sys.exit(1)

    # Storage
    all_requests: list[dict] = []
    all_responses: list[dict] = []
    interesting_requests: list[dict] = []

    browser = None
    context = None
    page = None
    attached = False

    pw = await async_playwright().start()
    try:
        # Connect
        cdp_url = args.cdp_endpoint
        if not cdp_url and args.cdp_port:
            cdp_url = f"http://127.0.0.1:{args.cdp_port}"

        if cdp_url:
            logger.info("Connecting to existing browser at %s", cdp_url)
            browser = await pw.chromium.connect_over_cdp(cdp_url)
            attached = True
            contexts = browser.contexts
            if not contexts:
                logger.error("No contexts found.")
                sys.exit(1)
            context = contexts[0]
            logger.info("Connected. %d page(s).", len(context.pages))
            page = context.pages[0] if context.pages else await context.new_page()
            logger.info("Using page: %s", page.url[:100])
        else:
            logger.info("Launching fresh browser...")
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
            page = await context.new_page()

        # --- Intercept EVERYTHING to chatgpt.com ---
        async def on_route(route):
            request = route.request
            url = request.url

            # Only capture chatgpt.com requests
            if "chatgpt.com" not in url:
                await route.continue_()
                return

            method = request.method
            is_interesting = False
            entry: dict = {
                "url": url,
                "method": method,
                "resource_type": request.resource_type,
                "timestamp": time.time(),
                "headers": {},
                "body": None,
            }

            # Capture headers and body for POST/PUT/PATCH
            if method in ("POST", "PUT", "PATCH"):
                try:
                    headers = await request.all_headers()
                    entry["headers"] = dict(headers)
                except Exception:
                    pass
                try:
                    post_data = request.post_data
                    if post_data:
                        try:
                            entry["body"] = json.loads(post_data)
                        except json.JSONDecodeError:
                            entry["body_raw"] = post_data[:2000]
                except Exception:
                    pass

                # Check if this looks like it carries a message
                body_str = json.dumps(entry.get("body", {})) if entry.get("body") else ""
                for pattern in INTERESTING_PATTERNS:
                    if pattern in body_str:
                        is_interesting = True
                        break

                # Also flag backend-api requests
                if "/backend-api/" in url:
                    is_interesting = True

            all_requests.append(entry)

            if is_interesting:
                interesting_requests.append(entry)
                logger.info("=" * 60)
                logger.info("INTERESTING %s %s", method, url[:120])
                if entry.get("body"):
                    body_keys = list(entry["body"].keys()) if isinstance(entry["body"], dict) else "non-dict"
                    logger.info("  Body keys: %s", body_keys)
                    # Check for message content specifically
                    if isinstance(entry["body"], dict):
                        if "messages" in entry["body"]:
                            logger.info("  >>> HAS 'messages' FIELD! <<<")
                            msgs = entry["body"]["messages"]
                            if isinstance(msgs, list):
                                for m in msgs:
                                    logger.info("  Message: %s", json.dumps(m)[:300])
                        if "action" in entry["body"]:
                            logger.info("  action: %s", entry["body"]["action"])
                        # Look for any field with text content
                        for k, v in entry["body"].items():
                            if isinstance(v, str) and len(v) > 20:
                                logger.info("  %s: %s", k, v[:200])
                            elif isinstance(v, dict):
                                v_keys = list(v.keys())
                                if any(p in str(v_keys).lower() for p in ["parts", "content", "text"]):
                                    logger.info("  %s: %s", k, json.dumps(v)[:300])
                            elif isinstance(v, list) and v:
                                logger.info("  %s: [%d items] first=%s", k, len(v), json.dumps(v[0])[:200] if v else "empty")
                logger.info("=" * 60)

            await route.continue_()

        def on_response(response):
            url = response.url
            if "chatgpt.com" not in url:
                return

            status = response.status
            rt = response.request.resource_type if hasattr(response.request, 'resource_type') else "unknown"
            method = response.request.method

            entry = {
                "url": url,
                "status": status,
                "method": method,
                "resource_type": rt,
                "timestamp": time.time(),
                "body_preview": None,
            }

            # Capture response body for backend-api and interesting responses
            if "/backend-api/" in url or rt in ("xhr", "fetch"):
                async def _cap():
                    try:
                        body = await response.text()
                        entry["body_preview"] = body[:5000] if body else None
                        all_responses.append(entry)

                        # Log interesting responses
                        if "/backend-api/conversation" in url:
                            logger.info("RESPONSE %s %s (status=%d, %d bytes)",
                                        method, url[:80], status, len(body) if body else 0)
                            # Check for message-like content
                            if body and '"parts"' in body:
                                logger.info("  >>> Response contains 'parts' field! <<<")
                            if body and '"content_type"' in body:
                                logger.info("  >>> Response contains 'content_type'! <<<")
                    except Exception as e:
                        logger.debug("Failed to capture response: %s", e)
                asyncio.ensure_future(_cap())
            else:
                all_responses.append(entry)

        # Register broad interception
        await page.route("**/*", on_route)
        page.on("response", on_response)
        logger.info("Broad interception active (all chatgpt.com traffic)")

        # Navigate if needed
        current_url = page.url
        if attached and "chatgpt.com" in current_url:
            logger.info("Already on ChatGPT: %s", current_url[:100])
        elif attached:
            logger.info("Navigating to ChatGPT...")
            await page.goto("https://chatgpt.com", wait_until="domcontentloaded")
            await asyncio.sleep(3)

        if args.navigate:
            project_url = f"https://chatgpt.com/g/{args.project}/project"
            logger.info("Navigating to project: %s", project_url)
            await page.goto(project_url, wait_until="domcontentloaded")
            await asyncio.sleep(3)

        # Ready
        logger.info("=" * 60)
        logger.info("BROAD INTERCEPTION ACTIVE")
        logger.info("Current page: %s", page.url[:100])
        logger.info("")
        logger.info("Now send a message in ChatGPT.")
        logger.info("ALL network traffic will be captured.")
        logger.info("Press Ctrl+C when done.")
        logger.info("=" * 60)

        try:
            while True:
                await asyncio.sleep(1)
                if interesting_requests:
                    logger.info(
                        "[%d total reqs, %d interesting] Ctrl+C to stop.",
                        len(all_requests), len(interesting_requests),
                    )
        except KeyboardInterrupt:
            pass

    finally:
        # Save
        output = {
            "all_requests": all_requests,
            "interesting_requests": interesting_requests,
            "all_responses": all_responses,
        }
        output_path = Path(args.output)
        output_path.write_text(json.dumps(output, indent=2, default=str))

        logger.info("")
        logger.info("=" * 60)
        logger.info("CAPTURE SUMMARY")
        logger.info("=" * 60)
        logger.info("Total requests:       %d", len(all_requests))
        logger.info("Interesting requests:  %d", len(interesting_requests))
        logger.info("Total responses:      %d", len(all_responses))

        # Group requests by URL pattern
        from collections import Counter
        url_groups = Counter()
        for r in all_requests:
            url = r["url"]
            # Strip query params
            base = url.split("?")[0]
            # Group backend-api calls
            if "/backend-api/" in base:
                parts = base.split("/backend-api/")
                url_groups[f"backend-api/{parts[1]}"] += 1
            elif "chatgpt.com" in base:
                url_groups["other chatgpt.com"] += 1

        logger.info("")
        logger.info("REQUEST URL BREAKDOWN:")
        for url, count in url_groups.most_common(30):
            logger.info("  %4d  %s", count, url[:120])

        # Show all interesting request bodies
        if interesting_requests:
            logger.info("")
            logger.info("INTERESTING REQUEST DETAILS:")
            for i, req in enumerate(interesting_requests):
                logger.info("--- Request %d ---", i + 1)
                logger.info("  %s %s", req["method"], req["url"][:120])
                if req.get("body") and isinstance(req["body"], dict):
                    logger.info("  Body: %s", json.dumps(req["body"])[:500])

        if attached:
            logger.info("Disconnecting from browser (stays open)")
        if browser:
            try:
                await browser.close()
            except Exception:
                pass
        try:
            await pw.stop()
        except Exception:
            pass

        logger.info("Saved to %s", output_path)


if __name__ == "__main__":
    asyncio.run(main())
