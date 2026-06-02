"""CLI entry point for chatgpt-web2api.

Usage:
    python -m chatgpt_web2api [options]
    chatgpt-web2api [options]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path

from aiohttp import web

from chatgpt_web2api import __version__
from chatgpt_web2api.config import AppConfig
from chatgpt_web2api.browser import ChatGPTBrowser
from chatgpt_web2api.auth import AuthSession
from chatgpt_web2api.protocol.sentinel import SentinelClient
from chatgpt_web2api.protocol.conversation import ConversationBuilder
from chatgpt_web2api.projects import ProjectManager
from chatgpt_web2api.server.handler import APIHandler

logger = logging.getLogger("chatgpt_web2api")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="chatgpt-web2api",
        description="OpenAI-compatible API proxy through ChatGPT web with project memory",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("-c", "--config", default=None, help="Config file path")
    parser.add_argument("--port", type=int, default=None, help="Server port (default: 8082)")
    parser.add_argument("--host", default=None, help="Server host (default: 0.0.0.0)")
    parser.add_argument("--headless", action="store_true", help="Run browser in headless mode")
    parser.add_argument("--project", default=None, help="Default project ID")
    parser.add_argument("--proxy", default=None, help="Proxy URL for browser")
    parser.add_argument("--cdp-endpoint", default=None,
                        help="Connect to running browser via CDP WebSocket URL (e.g. ws://localhost:9222)")
    parser.add_argument("--cdp-port", type=int, default=None,
                        help="Connect to running browser via CDP port (shorthand for --cdp-endpoint ws://localhost:PORT)")
    parser.add_argument("--log-level", default=None, choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args()


async def run_server(config: AppConfig) -> None:
    """Initialize all components and run the HTTP server."""
    # --- Browser ---
    browser = ChatGPTBrowser(
        config,
        cdp_endpoint=args.cdp_endpoint,
        cdp_port=args.cdp_port,
    )
    await browser.start()

    try:
        # If attached to existing browser, skip login check
        if browser._attached:
            logger.info("Attached to existing browser — skipping login check")
        else:
            await browser.navigate(config.chatgpt.base_url)

            # Check login
            import asyncio as aio
            await aio.sleep(3)

            if not await browser.is_logged_in():
                logger.info("Not logged in. Please log in using the browser window.")
                await browser.wait_for_login(timeout=300)
            else:
                logger.info("Already logged in.")

        # --- Auth ---
        auth = AuthSession(browser, config.chatgpt.base_url)
        token = await auth.get_access_token()
        logger.info("Access token obtained (length=%d)", len(token))

        # --- Protocol ---
        sentinel = SentinelClient(browser, auth, config.chatgpt.base_url)
        conversation = ConversationBuilder(browser, auth, sentinel, config.chatgpt.base_url)

        # --- Projects ---
        projects = ProjectManager(browser, config.chatgpt.base_url)

        # --- Server ---
        handler = APIHandler(config, browser, auth, conversation, projects)

        app = web.Application()
        app.router.add_get("/", handler.handle_health)
        app.router.add_get("/v1/models", handler.handle_models)
        app.router.add_post("/v1/chat/completions", handler.handle_chat_completions)
        app.router.add_get("/v1/projects", handler.handle_projects)
        app.router.add_get("/v1/projects/{project_id}/chats", handler.handle_project_chats)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, config.server.host, config.server.port)
        await site.start()

        logger.info(
            "Server running at http://%s:%d",
            config.server.host,
            config.server.port,
        )
        logger.info("Endpoints:")
        logger.info("  GET  /                     Health check")
        logger.info("  GET  /v1/models            List models")
        logger.info("  POST /v1/chat/completions  Chat completion")
        logger.info("  GET  /v1/projects          List projects")
        logger.info("  GET  /v1/projects/:id/chats List project chats")
        if config.chatgpt.default_project_id:
            logger.info("Default project: %s", config.chatgpt.default_project_id)

        # Run forever
        stop_event = asyncio.Event()

        def _signal_handler():
            stop_event.set()

        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _signal_handler)
            except NotImplementedError:
                # Windows doesn't support add_signal_handler
                pass

        await stop_event.wait()
        logger.info("Shutting down...")

    finally:
        await browser.stop()


def main() -> None:
    args = parse_args()

    # Build config: file → env → CLI overrides
    config = AppConfig()
    if args.config:
        config = AppConfig.from_file(args.config)
    else:
        # Look for config.json in current directory
        local_config = Path("config.json")
        if local_config.exists():
            config = AppConfig.from_file(local_config)

    # CLI overrides
    if args.port is not None:
        config.server.port = args.port
    if args.host is not None:
        config.server.host = args.host
    if args.headless:
        config.browser.headless = True
    if args.project:
        config.chatgpt.default_project_id = args.project
    if args.proxy:
        config.browser.proxy = args.proxy
    if args.log_level:
        config.log.log_level = args.log_level

    # Logging
    logging.basicConfig(
        level=getattr(logging, config.log.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    logger.info("ChatGPT-Web2API v%s starting...", __version__)

    try:
        asyncio.run(run_server(config))
    except KeyboardInterrupt:
        logger.info("Interrupted")
    except Exception as e:
        logger.error("Fatal error: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
