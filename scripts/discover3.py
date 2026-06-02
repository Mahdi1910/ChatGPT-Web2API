"""Comprehensive Discovery — capture ALL ChatGPT features.

Walks through every ChatGPT feature while capturing all network traffic.
Organizes captures by feature for easy reference.

Usage:
    python scripts/discover3.py --cdp-port 9222

Workflow:
    1. Connect to debug Chrome
    2. User walks through features (guided by on-screen instructions)
    3. Script captures and organizes everything
    4. Ctrl+C to save and stop at any time
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
logger = logging.getLogger("discover3")


class CaptureOrganizer:
    """Organizes captured traffic by feature."""

    def __init__(self):
        self.captures: dict[str, list[dict]] = {}
        self.current_feature: str = "setup"
        self.all_requests: list[dict] = []
        self.all_responses: list[dict] = []

    def set_feature(self, name: str):
        self.current_feature = name
        if name not in self.captures:
            self.captures[name] = []
            logger.info(">>> Now capturing: %s", name)

    def add_request(self, entry: dict):
        entry["feature"] = self.current_feature
        self.all_requests.append(entry)
        self.captures.setdefault(self.current_feature, []).append(entry)

    def add_response(self, entry: dict):
        entry["feature"] = self.current_feature
        self.all_responses.append(entry)

    def to_dict(self) -> dict:
        return {
            "captures": self.captures,
            "all_requests": self.all_requests,
            "all_responses": self.all_responses,
        }


async def main() -> None:
    parser = argparse.ArgumentParser(description="Comprehensive ChatGPT discovery")
    parser.add_argument("--cdp-endpoint", default=None)
    parser.add_argument("--cdp-port", type=int, default=None)
    parser.add_argument("--output", default="captured_comprehensive.json")
    args = parser.parse_args()

    try:
        from patchright.async_api import async_playwright
    except ImportError:
        logger.error("patchright required: pip install super-browser[patchright]")
        sys.exit(1)

    org = CaptureOrganizer()
    browser = None
    attached = False

    pw = await async_playwright().start()
    try:
        cdp_url = args.cdp_endpoint
        if not cdp_url and args.cdp_port:
            cdp_url = f"http://127.0.0.1:{args.cdp_port}"

        if cdp_url:
            logger.info("Connecting to browser at %s", cdp_url)
            browser = await pw.chromium.connect_over_cdp(cdp_url)
            attached = True
            ctx = browser.contexts[0] if browser.contexts else None
            if not ctx:
                logger.error("No contexts. Open a tab.")
                sys.exit(1)

            # Create a fresh about:blank page to avoid "Frame was detached"
            # when ChatGPT's Turnstile iframes are dynamically loading.
            page = await ctx.new_page()
            await page.goto("about:blank")
            logger.info("Connected via fresh tab (avoids iframe detachment)")
        else:
            logger.info("Launching fresh browser...")
            browser = await pw.chromium.launch(headless=False, args=["--disable-blink-features=AutomationControlled"])
            ctx = await browser.new_context(viewport={"width": 1440, "height": 900})
            page = await ctx.new_page()

        # --- Intercept everything ---
        async def on_route(route):
            req = route.request
            url = req.url
            if "chatgpt.com" not in url:
                await route.continue_()
                return

            method = req.method
            entry = {
                "url": url.split("?")[0],
                "url_full": url[:500],
                "method": method,
                "resource_type": req.resource_type,
                "timestamp": time.time(),
                "headers": {},
                "body": None,
            }

            if method in ("POST", "PUT", "PATCH", "DELETE"):
                try:
                    headers = await req.all_headers()
                    entry["headers"] = {k: (v[:80] + "..." if len(v) > 80 else v) for k, v in headers.items()}
                except Exception:
                    pass
                try:
                    pd = req.post_data
                    if pd:
                        try:
                            entry["body"] = json.loads(pd)
                        except Exception:
                            entry["body_raw"] = pd[:5000]
                except Exception:
                    pass

                org.add_request(entry)

                # Log interesting backend-api calls
                if "/backend-api/" in url:
                    base = url.split("?")[0].split("/backend-api/")[1]
                    logger.info("  [%s] POST /backend-api/%s", org.current_feature, base[:60])
                    if entry.get("body") and isinstance(entry["body"], dict):
                        if "messages" in entry["body"]:
                            msgs = entry["body"]["messages"]
                            if isinstance(msgs, list) and msgs:
                                parts = msgs[0].get("content", {}).get("parts", [])
                                logger.info("    messages[0].content.parts: %s", str(parts)[:200])
                        if "conversation_mode" in entry["body"]:
                            logger.info("    conversation_mode: %s", json.dumps(entry["body"]["conversation_mode"]))
                        if "model" in entry["body"]:
                            logger.info("    model: %s", entry["body"]["model"])
            else:
                org.add_request(entry)

            await route.continue_()

        def on_response(response):
            url = response.url
            if "chatgpt.com" not in url:
                return
            rt = getattr(response.request, 'resource_type', 'unknown')
            method = response.request.method

            entry = {
                "url": url.split("?")[0],
                "status": response.status,
                "method": method,
                "resource_type": rt,
                "timestamp": time.time(),
                "body_preview": None,
            }

            if "/backend-api/" in url or rt in ("xhr", "fetch"):
                async def _cap():
                    try:
                        body = await response.text()
                        # Save up to 50K chars for backend-api responses
                        entry["body_preview"] = body[:50000] if body else None
                        org.add_response(entry)
                        if "/backend-api/" in url:
                            base = url.split("?")[0].split("/backend-api/")[1]
                            logger.info("  [%s] %d /backend-api/%s (%d bytes)",
                                        org.current_feature, response.status, base[:50],
                                        len(body) if body else 0)
                    except Exception:
                        pass
                asyncio.ensure_future(_cap())
            else:
                org.add_response(entry)

        await page.route("**/*", on_route)
        page.on("response", on_response)
        logger.info("Broad interception active\n")

        # --- Guided feature walkthrough ---
        features = [
            ("A-models", "Open the model picker/dropdown. Switch between models. Watch for model slug changes."),
            ("B-normal-chat", "Start a NEW conversation (NOT in a project). Send 'Hello'."),
            ("C-project-shared", "Create/use a project with SHARED memory. Send a message."),
            ("D-project-dedicated", "Create/use a project with DEDICATED memory. Send a message."),
            ("E-deep-research", "Go to chatgpt.com/deep-research. Start a research task."),
            ("F-images", "Go to chatgpt.com/images/ Generate an image."),
            ("G-gpts", "Go to chatgpt.com/apps Pick a GPT. Start a conversation."),
            ("H-library", "Go to chatgpt.com/library Browse/search."),
            ("I-memories", "Check chatgpt.com settings or memory page."),
            ("J-misc", "Anything else you want to capture."),
        ]

        print("\n" + "=" * 60)
        print("COMPREHENSIVE CHATGPT DISCOVERY")
        print("=" * 60)
        print("\nThe script will guide you through features.")
        print("For each feature, perform the actions described.")
        print("Press Enter to move to the next feature.")
        print("Press Ctrl+C at any time to save what you have.\n")

        for feature_id, instructions in features:
            print("=" * 60)
            print(f"FEATURE: {feature_id}")
            print(f"ACTION:  {instructions}")
            print("=" * 60)

            org.set_feature(feature_id)
            try:
                input("\nPress Enter when done with this feature (or Ctrl+C to stop)...")
            except KeyboardInterrupt:
                print("\nStopping early...")
                break
            print()

        print("\nAll features captured! Saving...")

    finally:
        # Save
        output = org.to_dict()
        output_path = Path(args.output)
        output_path.write_text(json.dumps(output, indent=2, default=str))

        # Summary
        print("\n" + "=" * 60)
        print("CAPTURE SUMMARY")
        print("=" * 60)

        from collections import Counter
        for feature, reqs in org.captures.items():
            # Count unique backend-api endpoints
            api_endpoints = set()
            for r in reqs:
                url = r.get("url", "")
                if "/backend-api/" in url:
                    base = url.split("/backend-api/")[1]
                    api_endpoints.add(base)

            post_reqs = [r for r in reqs if r.get("method") == "POST" and "/backend-api/" in r.get("url", "")]
            models_seen = set()
            conv_modes = set()
            for r in post_reqs:
                body = r.get("body", {})
                if isinstance(body, dict):
                    if "model" in body:
                        models_seen.add(body["model"])
                    if "conversation_mode" in body:
                        cm = body["conversation_mode"]
                        if isinstance(cm, dict):
                            conv_modes.add(json.dumps(cm))

            print(f"\n{feature}:")
            print(f"  Total requests: {len(reqs)}")
            if api_endpoints:
                for ep in sorted(api_endpoints):
                    print(f"  API: /backend-api/{ep}")
            if models_seen:
                print(f"  Models: {', '.join(sorted(models_seen))}")
            if conv_modes:
                for cm in sorted(conv_modes):
                    print(f"  conversation_mode: {cm}")

        # Cleanup
        if attached:
            logger.info("Disconnecting (browser stays open)")
        if browser:
            try:
                await browser.close()
            except Exception:
                pass
        try:
            await pw.stop()
        except Exception:
            pass

        print(f"\nSaved to {output_path}")
        print(f"Total: {len(org.all_requests)} requests, {len(org.all_responses)} responses")


if __name__ == "__main__":
    asyncio.run(main())
