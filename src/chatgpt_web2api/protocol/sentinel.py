"""Sentinel chat-requirements protocol.

Handles the /backend-api/sentinel/chat-requirements endpoint which
returns PoW parameters, Turnstile requirements, and the token needed
for the actual conversation request.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from chatgpt_web2api.auth import AuthSession
from chatgpt_web2api.browser import ChatGPTBrowser
from chatgpt_web2api.protocol.pow import generate_config, solve_pow

logger = logging.getLogger(__name__)


@dataclass
class SentinelResult:
    """Result from the sentinel chat-requirements endpoint."""
    token: str
    proofofwork: Optional[str] = None
    turnstile: Optional[dict] = None
    has_pow: bool = False
    has_turnstile: bool = False


class SentinelClient:
    """Handles the ChatGPT sentinel challenge pipeline.

    The flow is:
    1. POST /backend-api/sentinel/chat-requirements with AccessToken
    2. Response contains PoW seed/difficulty and/or Turnstile challenge
    3. Solve PoW if required
    4. Return the assembled requirements token for the conversation request
    """

    def __init__(
        self,
        browser: ChatGPTBrowser,
        auth: AuthSession,
        base_url: str = "https://chatgpt.com",
    ) -> None:
        self._browser = browser
        self._auth = auth
        self._base_url = base_url.rstrip("/")

    async def get_requirements(self) -> SentinelResult:
        """Fetch and solve chat requirements.

        Returns a SentinelResult with the token and any solved challenges.
        """
        access_token = await self._auth.get_access_token()

        # Build the request body
        config = generate_config()
        body = {
            "p": json.dumps(config),
        }

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "*/*",
            "oai-device-id": self._get_device_id(),
            "oai-language": "en-US",
        }

        logger.info("Fetching chat requirements from sentinel...")
        resp = await self._browser.fetch(
            f"{self._base_url}/backend-api/sentinel/chat-requirements",
            method="POST",
            headers=headers,
            body=body,
        )

        if resp.status != 200:
            raise SentinelError(
                f"Sentinel request failed: status={resp.status} "
                f"body={resp.text()[:500]}"
            )

        data = resp.json()
        logger.info("Sentinel response keys: %s", list(data.keys()))

        token = data.get("token", "")
        result = SentinelResult(token=token)

        # Handle Proof-of-Work
        pow_data = data.get("proofofwork")
        if pow_data and isinstance(pow_data, dict):
            result.has_pow = True
            seed = pow_data.get("seed", "")
            difficulty = pow_data.get("difficulty", "")
            logger.info("PoW required: seed=%s... difficulty=%s", seed[:20], difficulty)

            if seed and difficulty:
                solution = solve_pow(seed, difficulty)
                if solution:
                    result.proofofwork = solution
                    logger.info("PoW solved: %s", solution)
                else:
                    logger.warning("Failed to solve PoW")

        # Handle Turnstile
        turnstile_data = data.get("turnstile")
        if turnstile_data and isinstance(turnstile_data, dict):
            result.has_turnstile = True
            result.turnstile = turnstile_data
            # Turnstile typically requires solving in-browser
            # For authenticated Plus sessions, this may be skipped
            logger.info("Turnstile challenge present: %s", turnstile_data.get("type", "unknown"))

        return result

    def _get_device_id(self) -> str:
        """Generate a consistent device ID.

        In a real session, this comes from the browser's localStorage.
        For now, generate a stable UUID based on the session.
        """
        import hashlib
        return hashlib.sha256(b"chatgpt-web2api-device").hexdigest()[:32]


class SentinelError(Exception):
    """Sentinel protocol error."""
