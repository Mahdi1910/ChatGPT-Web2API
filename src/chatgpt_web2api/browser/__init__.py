"""Browser lifecycle manager for ChatGPT sessions.

Wraps Super Browser's PatchrightEngine to provide a persistent,
stealth-capable browser instance for routing all ChatGPT HTTP traffic
through BrowserFetch (CDP-level).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

from chatgpt_web2api.config import AppConfig

logger = logging.getLogger(__name__)

# Lazy imports — Super Browser may not be installed during development
_try_browser = True
try:
    from patchright.async_api import async_playwright, Browser, BrowserContext, Page
except ImportError:
    _try_browser = False


class ChatGPTBrowser:
    """Manages a Patchright browser instance for ChatGPT traffic.

    All HTTP to chatgpt.com goes through the browser's cookie jar and
    TLS stack via Patchright's route interception.  This ensures TLS
    fingerprints, cookies, and proxy settings are indistinguishable
    from a real browser session.

    Two connection modes:

    * **Launch** — start a fresh Patchright browser (default).
    * **Attach** — connect to a running browser via CDP WebSocket.
      Use `cdp_endpoint` (full ws:// URL) or `cdp_port` (localhost).
      The existing session's cookies, tabs, and auth state are reused
      as-is — no login needed.
    """

    def __init__(
        self,
        config: AppConfig,
        *,
        cdp_endpoint: Optional[str] = None,
        cdp_port: Optional[int] = None,
    ) -> None:
        self._config = config
        self._cdp_endpoint = cdp_endpoint
        self._cdp_port = cdp_port
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None
        self._started = False
        self._attached = False  # True when connected to external browser

    # -- Lifecycle --

    async def start(self) -> None:
        """Start the browser — launch fresh or attach to existing session."""
        if self._started:
            return
        if not _try_browser:
            raise RuntimeError(
                "patchright is required. Install with: "
                "pip install super-browser[patchright]"
            )

        self._playwright = await async_playwright()

        # --- Attach to existing browser via CDP ---
        cdp_url = self._resolve_cdp_url()
        if cdp_url:
            await self._attach(cdp_url)
            return

        # --- Launch fresh browser ---
        await self._launch()

    # -- Internal start methods --

    def _resolve_cdp_url(self) -> Optional[str]:
        """Return the CDP WebSocket URL to connect to, or None."""
        if self._cdp_endpoint:
            return self._cdp_endpoint
        if self._cdp_port:
            return f"http://localhost:{self._cdp_port}"
        return None

    async def _attach(self, cdp_url: str) -> None:
        """Connect to an already-running browser via CDP.

        The browser's existing pages, cookies, and auth state are
        reused directly — no new context or cookies are created.
        """
        logger.info("Attaching to existing browser at %s", cdp_url)
        self._browser = await self._playwright.chromium.connect_over_cdp(cdp_url)
        self._attached = True

        # Use the first existing context (the real browser session)
        contexts = self._browser.contexts
        if contexts:
            self._context = contexts[0]
            logger.info("Using existing context with %d page(s)", len(self._context.pages))
        else:
            raise RuntimeError(
                "Connected to browser but no contexts found. "
                "Make sure the browser has at least one tab open."
            )

        # Find an existing page or create one
        pages = self._context.pages
        if pages:
            self._page = pages[0]
            logger.info("Using existing page: %s", self._page.url[:80])
        else:
            self._page = await self._context.new_page()
            logger.info("Created new page in existing context")

        self._started = True
        logger.info("Attached to existing browser session")

    async def _launch(self) -> None:
        """Launch a fresh Patchright browser with stealth settings."""
        logger.info("Launching Patchright browser...")

        launch_args = [
            "--disable-blink-features=AutomationControlled",
        ]
        if self._config.browser.proxy:
            launch_args.append(f"--proxy-server={self._config.browser.proxy}")

        self._browser = await self._playwright.chromium.launch(
            headless=self._config.browser.headless,
            args=launch_args,
        )

        self._context = await self._browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/137.0.0.0 Safari/537.36"
            ),
        )

        # Restore cookies if available
        cookie_file = self._config.session.cookie_file
        if cookie_file and Path(cookie_file).exists():
            cookies = json.loads(Path(cookie_file).read_text())
            await self._context.add_cookies(cookies)
            logger.info("Restored %d cookies from %s", len(cookies), cookie_file)

        self._page = await self._context.new_page()
        self._started = True
        logger.info("Browser started (headless=%s)", self._config.browser.headless)

    async def stop(self) -> None:
        """Close the browser and save cookies.

        When attached to an external browser, only disconnects —
        the browser itself keeps running.
        """
        if not self._started:
            return

        # Save cookies (works in both modes)
        await self.save_cookies()

        if self._attached:
            # Disconnect without closing the user's browser
            logger.info("Disconnecting from external browser")
            try:
                await self._browser.close()  # CDP disconnect, not browser close
            except Exception:
                pass
            self._attached = False
        else:
            try:
                await self._browser.close()
            except Exception:
                pass
        try:
            await self._playwright.stop()
        except Exception:
            pass
        self._started = False
        logger.info("Browser stopped")

    @property
    def page(self) -> Any:
        """The main browser page."""
        if not self._started:
            raise RuntimeError("Browser not started")
        return self._page

    @property
    def context(self) -> Any:
        """The browser context."""
        if not self._started:
            raise RuntimeError("Browser not started")
        return self._context

    # -- Session Management --

    async def save_cookies(self) -> None:
        """Save current cookies to file."""
        cookie_file = self._config.session.cookie_file
        if not cookie_file:
            return
        if self._context is None:
            return
        try:
            cookies = await self._context.cookies()
            path = Path(cookie_file)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(cookies, indent=2))
            logger.info("Saved %d cookies to %s", len(cookies), cookie_file)
        except Exception as e:
            logger.warning("Failed to save cookies: %s", e)

    async def navigate(self, url: str) -> None:
        """Navigate the browser to a URL."""
        if not self._started:
            raise RuntimeError("Browser not started")
        logger.info("Navigating to %s", url)
        await self._page.goto(url, wait_until="domcontentloaded")

    async def is_logged_in(self) -> bool:
        """Check if the browser is currently logged into ChatGPT."""
        if not self._started:
            return False
        url = self._page.url
        return (
            "chatgpt.com" in url
            and "auth/login" not in url
            and "auth0" not in url
        )

    async def wait_for_login(self, timeout: int = 300) -> None:
        """Wait for the user to complete login in the visible browser.

        Blocks until the page redirects away from the login URL,
        up to `timeout` seconds.
        """
        logger.info("Waiting for user login (timeout=%ds)...", timeout)
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            if await self.is_logged_in():
                logger.info("Login detected!")
                await self.save_cookies()
                return
            await asyncio.sleep(1)
        raise TimeoutError(f"Login not completed within {timeout}s")

    # -- HTTP via Browser --

    async def fetch(
        self,
        url: str,
        *,
        method: str = "GET",
        headers: Optional[dict[str, str]] = None,
        body: Optional[str | dict] = None,
    ) -> "BrowserResponse":
        """Make an HTTP request through the browser.

        Uses Patchright's page.evaluate + fetch() to route requests
        through the browser's TLS stack and cookie jar.
        """
        if not self._started:
            raise RuntimeError("Browser not started")

        init_parts = [f'method: "{method}"']
        if headers:
            header_entries = ", ".join(
                f'"{k}": "{v}"' for k, v in headers.items()
            )
            init_parts.append(f"headers: {{{header_entries}}}")
        if body is not None:
            if isinstance(body, dict):
                body_str = json.dumps(body)
            else:
                body_str = body
            # Escape for JS string
            body_escaped = body_str.replace("\\", "\\\\").replace("`", "\\`")
            init_parts.append(f"body: `{body_escaped}`")

        init_js = ", ".join(init_parts)

        js = (
            "async () => {"
            "  try {"
            f"    const r = await fetch(\"{url}\", {{{init_js}}});"
            "    const text = await r.text();"
            "    const headers = {};"
            "    r.headers.forEach((v, k) => { headers[k] = v; });"
            "    return {"
            "      status: r.status,"
            "      ok: r.ok,"
            "      headers: headers,"
            "      body: text"
            "    };"
            "  } catch (e) {"
            "    return {"
            "      status: 0,"
            "      ok: false,"
            "      headers: {},"
            "      body: e.message"
            "    };"
            "  }"
            "}"
        )

        result = await self._page.evaluate(js)
        return BrowserResponse(
            status=result["status"],
            ok=result["ok"],
            headers=result.get("headers", {}),
            body=result.get("body", ""),
        )

    async def __aenter__(self) -> ChatGPTBrowser:
        await self.start()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.stop()


class BrowserResponse:
    """Response from a browser-routed fetch."""

    def __init__(self, status: int, ok: bool, headers: dict, body: str) -> None:
        self.status = status
        self.ok = ok
        self.headers = headers
        self._body = body

    def text(self) -> str:
        return self._body

    def json(self) -> Any:
        return json.loads(self._body)
