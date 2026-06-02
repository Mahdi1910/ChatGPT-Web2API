"""ChatGPT authentication — AccessToken retrieval and session management."""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

from chatgpt_web2api.browser import ChatGPTBrowser

logger = logging.getLogger(__name__)


class AuthSession:
    """Manages ChatGPT AccessToken lifecycle.

    AccessToken is a JWT obtained from /api/auth/session.  It expires
    periodically and needs to be refreshed.  This class handles fetching,
    caching, and refreshing the token.
    """

    def __init__(self, browser: ChatGPTBrowser, base_url: str = "https://chatgpt.com") -> None:
        self._browser = browser
        self._base_url = base_url.rstrip("/")
        self._access_token: Optional[str] = None
        self._token_expires: float = 0.0
        self._user_info: Optional[dict] = None

    async def get_access_token(self) -> str:
        """Get a valid AccessToken, refreshing if necessary.

        Returns the current token if it hasn't expired, otherwise
        fetches a new one from /api/auth/session.
        """
        if self._access_token and time.monotonic() < self._token_expires:
            return self._access_token

        logger.info("Fetching new AccessToken from /api/auth/session...")
        resp = await self._browser.fetch(
            f"{self._base_url}/api/auth/session",
            method="GET",
        )

        if resp.status != 200:
            raise AuthError(
                f"Failed to get access token: status={resp.status} "
                f"body={resp.text()[:500]}"
            )

        try:
            data = resp.json()
        except json.JSONDecodeError as e:
            raise AuthError(f"Invalid JSON from /api/auth/session: {e}") from e

        self._access_token = data.get("accessToken")
        if not self._access_token:
            raise AuthError(
                f"No accessToken in response. Keys: {list(data.keys()) if isinstance(data, dict) else type(data)}"
            )

        # Cache user info
        self._user_info = {
            k: data.get(k)
            for k in ("user", "expires", "authProvider")
            if k in data
        }

        # Set expiry — refresh 5 minutes before actual expiry
        expires_str = data.get("expires")
        if expires_str:
            # Parse ISO format expiry
            try:
                from datetime import datetime, timezone
                dt = datetime.fromisoformat(expires_str.replace("Z", "+00:00"))
                remaining = (dt - datetime.now(timezone.utc)).total_seconds()
                self._token_expires = time.monotonic() + max(0, remaining - 300)
                logger.info(
                    "AccessToken expires in %.0f seconds, will refresh in %.0f",
                    remaining,
                    max(0, remaining - 300),
                )
            except Exception:
                # If we can't parse, refresh every 10 minutes
                self._token_expires = time.monotonic() + 600
                logger.warning("Could not parse token expiry, refreshing in 600s")
        else:
            # Default: refresh every 10 minutes
            self._token_expires = time.monotonic() + 600

        logger.info(
            "AccessToken obtained (length=%d, user=%s)",
            len(self._access_token),
            data.get("user", {}).get("name", "unknown"),
        )
        return self._access_token

    @property
    def user_info(self) -> Optional[dict]:
        """Cached user info from last token fetch."""
        return self._user_info

    def invalidate(self) -> None:
        """Force token refresh on next get_access_token call."""
        self._access_token = None
        self._token_expires = 0.0


class AuthError(Exception):
    """Authentication error."""
