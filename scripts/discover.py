"""Phase 1 Discovery — intercept ChatGPT /backend-api/conversation request.

Launches Super Browser, navigates to ChatGPT, and captures the full
request payload when you send a message inside a project. This reveals
the exact conversation_mode, project scoping field, and request schema.

Usage:
    python -m scripts.discover [--project PROJECT_ID] [--cookie-file PATH]

Workflow:
    1. Browser opens to chatgpt.com
    2. If not logged in, you log in manually in the visible window
    3. Script detects login, navigates to your project
    4. You type a message in the ChatGPT UI
    5. Script captures and dumps the full request/response
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path

# Add src to path so we can import super_browser if needed
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("discover")


async def main() -> None:
    parser = argparse.ArgumentParser(description="ChatGPT API discovery script")
    parser.add_argument(
        "--project",
        default="g-p-6a1cbfa6da8c8191bd3674470d2dbc22-orqestra",
        help="Project ID to test (default: Orqestra project)",
    )
    parser.add_argument(
        "--cookie-file",
        default=None,
        help="Path to saved cookies JSON for session restoration",
    )
    parser.add_argument(
        "--output",
        default="captured_request.json",
        help="Output file for captured payload",
    )
    args = parser.parse_args()

    # --- Import Super Browser ---
    try:
        from patchright.async_api import async_playwright
    except ImportError:
        logger.error("patchright is required: pip install super-browser[patchright]")
        sys.exit(1)

    captured_requests: list[dict] = []
    captured_responses: list[dict] = []
    sentinel_requests: list[dict] = []
    auth_requests: list[dict] = []

    logger.info("Launching Patchright browser...")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
            ],
        )
        context = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/137.0.0.0 Safari/537.36"
            ),
        )

        # Restore cookies if provided
        if args.cookie_file and Path(args.cookie_file).exists():
            cookies = json.loads(Path(args.cookie_file).read_text())
            await context.add_cookies(cookies)
            logger.info("Restored %d cookies from %s", len(cookies), args.cookie_file)

        page = await context.new_page()

        # --- Intercept all ChatGPT backend API calls ---
        async def handle_route(route):
            """Intercept and record all requests to chatgpt.com backend."""
            request = route.request
            url = request.url

            # Record but don't block — let everything through
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
                    logger.info("Body keys: %s", list(entry["body"].keys()) if entry["body"] else "None")
                    if entry["body"]:
                        # Log the conversation_mode specifically
                        cm = entry["body"].get("conversation_mode")
                        if cm:
                            logger.info("conversation_mode: %s", json.dumps(cm, indent=2))
                        # Log any project-related fields
                        for key in entry["body"]:
                            if "project" in key.lower() or "gizmo" in key.lower():
                                logger.info("PROJECT-RELATED FIELD: %s = %s", key, json.dumps(entry["body"][key], indent=2))
                    logger.info("=" * 60)
                except Exception as e:
                    logger.warning("Failed to capture conversation request: %s", e)

            elif "/backend-api/sentinel/chat-requirements" in url:
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
                    sentinel_requests.append(entry)
                    logger.info("Captured sentinel/chat-requirements request")
                except Exception as e:
                    logger.warning("Failed to capture sentinel request: %s", e)

            elif "/api/auth/session" in url:
                try:
                    headers = await request.all_headers()
                    entry = {
                        "url": url,
                        "method": request.method,
                        "headers": dict(headers),
                        "timestamp": time.time(),
                    }
                    auth_requests.append(entry)
                    logger.info("Captured auth/session request")
                except Exception as e:
                    logger.warning("Failed to capture auth request: %s", e)

            # Continue the request unmodified
            await route.continue_()

        # Intercept responses too
        async def handle_response(response):
            url = response.url
            if "/backend-api/conversation" in url:
                try:
                    body = await response.text()
                    headers = await response.all_headers()
                    entry = {
                        "url": url,
                        "status": response.status,
                        "headers": dict(headers),
                        "body_preview": body[:5000] if body else None,
                        "timestamp": time.time(),
                    }
                    captured_responses.append(entry)
                    logger.info("Captured conversation response (status=%d, %d bytes)", response.status, len(body) if body else 0)
                except Exception as e:
                    logger.warning("Failed to capture conversation response: %s", e)

            elif "/backend-api/sentinel/chat-requirements" in url:
                try:
                    body = await response.text()
                    entry = {
                        "url": url,
                        "status": response.status,
                        "body": json.loads(body) if body else None,
                        "timestamp": time.time(),
                    }
                    sentinel_requests.append(entry)
                    if sentinel_requests and sentinel_requests[-1].get("body"):
                        sb = sentinel_requests[-1]["body"]
                        logger.info("Sentinel response keys: %s", list(sb.keys()) if isinstance(sb, dict) else "non-dict")
                        if isinstance(sb, dict):
                            if "proofofwork" in sb:
                                pw_data = sb["proofofwork"]
                                logger.info("PoW: seed=%s... difficulty=%s",
                                            str(pw_data.get("seed", ""))[:20],
                                            pw_data.get("difficulty"))
                            if "turnstile" in sb:
                                logger.info("Turnstile required: %s", sb["turnstile"])
                except Exception as e:
                    logger.warning("Failed to capture sentinel response: %s", e)

            elif "/api/auth/session" in url:
                try:
                    body = await response.text()
                    entry = {
                        "url": url,
                        "status": response.status,
                        "body_preview": body[:2000] if body else None,
                        "timestamp": time.time(),
                    }
                    auth_requests.append(entry)
                    logger.info("Captured auth response (status=%d)", response.status)
                except Exception as e:
                    logger.warning("Failed to capture auth response: %s", e)

        # Register route interception on chatgpt.com
        await page.route("**/backend-api/**", handle_route)
        await page.route("**/api/auth/**", handle_route)
        page.on("response", handle_response)

        # --- Navigate to ChatGPT ---
        logger.info("Navigating to chatgpt.com...")
        await page.goto("https://chatgpt.com", wait_until="domcontentloaded")

        # Check if logged in
        await asyncio.sleep(3)
        current_url = page.url
        is_logged_in = "auth/login" not in current_url and "auth0" not in current_url

        if not is_logged_in:
            logger.info("=" * 60)
            logger.info("NOT LOGGED IN. Please log in using the browser window.")
            logger.info("Waiting for login to complete...")
            logger.info("=" * 60)

            # Wait for redirect back to chatgpt.com
            for _ in range(300):  # 5 minutes max
                await asyncio.sleep(1)
                current_url = page.url
                if "auth/login" not in current_url and "auth0" not in current_url and "chatgpt.com" in current_url:
                    is_logged_in = True
                    logger.info("Login detected! Continuing...")
                    break
            else:
                logger.error("Login timeout (5 minutes). Exiting.")
                await browser.close()
                sys.exit(1)
        else:
            logger.info("Already logged in.")

        # Save cookies for future use
        cookies = await context.cookies()
        cookie_path = Path(args.output).parent / "captured_cookies.json"
        cookie_path.write_text(json.dumps(cookies, indent=2))
        logger.info("Saved %d cookies to %s", len(cookies), cookie_path)

        # --- Navigate to project ---
        project_url = f"https://chatgpt.com/g/{args.project}/project"
        logger.info("Navigating to project: %s", project_url)
        await page.goto(project_url, wait_until="domcontentloaded")
        await asyncio.sleep(3)

        logger.info("=" * 60)
        logger.info("PROJECT PAGE LOADED.")
        logger.info("Please send a message in the ChatGPT project chat.")
        logger.info("The script will capture the request automatically.")
        logger.info("Press Ctrl+C when done to save captures.")
        logger.info("=" * 60)

        # Wait for captures — poll until we have at least one conversation request
        try:
            while True:
                await asyncio.sleep(1)
                if captured_requests:
                    logger.info("Have %d conversation request(s), continuing to capture... (Ctrl+C to stop)", len(captured_requests))
        except KeyboardInterrupt:
            pass

        # --- Save all captures ---
        output = {
            "conversation_requests": captured_requests,
            "conversation_responses": captured_responses,
            "sentinel_requests": sentinel_requests,
            "auth_requests": auth_requests,
        }

        output_path = Path(args.output)
        output_path.write_text(json.dumps(output, indent=2, default=str))
        logger.info("Saved captures to %s", output_path)

        # Print summary
        logger.info("")
        logger.info("=" * 60)
        logger.info("CAPTURE SUMMARY")
        logger.info("=" * 60)
        logger.info("Conversation requests: %d", len(captured_requests))
        logger.info("Conversation responses: %d", len(captured_responses))
        logger.info("Sentinel requests: %d", len(sentinel_requests))
        logger.info("Auth requests: %d", len(auth_requests))

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

        await browser.close()

    logger.info("Done. Review %s for full payload.", args.output)


if __name__ == "__main__":
    asyncio.run(main())
