"""ChatGPT conversation protocol — request building and SSE parsing.

Builds the request body for /backend-api/conversation and parses
the Server-Sent Events response into text deltas.

PROJECT SCOPING:
    The exact field that scopes a conversation to a project is determined
    by the Phase 1 discovery script.  Based on URL structure analysis
    (g-p- prefix sharing /g/ namespace with GPTs), the most likely
    candidates are:

    1. conversation_mode = {"kind": "project_interaction", "project_id": "g-p-XXX"}
    2. conversation_mode = {"kind": "gizmo_interaction", "gizmo_id": "g-p-XXX"}
    3. A separate project_id field in the request body

    The implementation supports all three and will be finalized after
    Phase 1 captures the actual format.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Optional

from chatgpt_web2api.auth import AuthSession
from chatgpt_web2api.browser import ChatGPTBrowser
from chatgpt_web2api.protocol.models import resolve_model, ModelInfo
from chatgpt_web2api.protocol.sentinel import SentinelClient, SentinelResult

logger = logging.getLogger(__name__)


# Conversation mode kinds discovered from reverse engineering
MODE_PRIMARY_ASSISTANT = "primary_assistant"
MODE_GIZMO_INTERACTION = "gizmo_interaction"
MODE_PROJECT_INTERACTION = "project_interaction"  # Hypothesis — TBD from Phase 1


@dataclass
class ConversationRequest:
    """Built request body for /backend-api/conversation."""
    body: dict[str, Any]
    headers: dict[str, str]
    url: str


@dataclass
class ConversationResult:
    """Result from a conversation request."""
    message_id: str
    conversation_id: str
    text: str
    model: str
    finish_reason: str = "stop"
    usage: dict = field(default_factory=dict)


class ConversationBuilder:
    """Builds ChatGPT conversation requests with project scoping support."""

    def __init__(
        self,
        browser: ChatGPTBrowser,
        auth: AuthSession,
        sentinel: SentinelClient,
        base_url: str = "https://chatgpt.com",
    ) -> None:
        self._browser = browser
        self._auth = auth
        self._sentinel = sentinel
        self._base_url = base_url.rstrip("/")

    async def send_message(
        self,
        message: str,
        *,
        model: str = "gpt-4o",
        project_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        parent_message_id: Optional[str] = None,
        stream: bool = True,
    ) -> AsyncIterator[str]:
        """Send a message to ChatGPT and yield text deltas.

        Args:
            message: The user message text.
            model: Model name (OpenAI API format).
            project_id: Optional project ID (e.g. "g-p-6a1c...").
            conversation_id: Existing conversation to continue.
            parent_message_id: Parent message ID for threading.
            stream: Whether to stream SSE deltas.

        Yields:
            Text delta strings.
        """
        access_token = await self._auth.get_access_token()
        model_info = resolve_model(model)

        # Get sentinel requirements (PoW + Turnstile)
        sentinel_result = await self._sentinel.get_requirements()

        # Build request
        request = self._build_request(
            message=message,
            model_info=model_info,
            access_token=access_token,
            sentinel_result=sentinel_result,
            project_id=project_id,
            conversation_id=conversation_id,
            parent_message_id=parent_message_id,
        )

        logger.info(
            "Sending conversation request (model=%s, project=%s, conv=%s, stream=%s)",
            model_info.openai_id,
            project_id or "none",
            conversation_id or "new",
            stream,
        )

        # Send via browser
        resp = await self._browser.fetch(
            request.url,
            method="POST",
            headers=request.headers,
            body=json.dumps(request.body),
        )

        if resp.status != 200:
            error_body = resp.text()[:2000]
            logger.error(
                "Conversation request failed: status=%d body=%s",
                resp.status, error_body,
            )
            raise ConversationError(
                f"Conversation request failed: status={resp.status} "
                f"body={error_body}"
            )

        # Parse SSE response
        full_text = ""
        response_text = resp.text()

        if stream:
            async for delta in self._parse_sse(response_text):
                full_text += delta
                yield delta
        else:
            full_text = self._parse_non_streaming(response_text)
            yield full_text

    def _build_request(
        self,
        *,
        message: str,
        model_info: ModelInfo,
        access_token: str,
        sentinel_result: SentinelResult,
        project_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        parent_message_id: Optional[str] = None,
    ) -> ConversationRequest:
        """Build the full conversation request."""

        message_id = str(uuid.uuid4())

        # --- Conversation mode ---
        # This is the critical field.  Three possibilities:
        #
        # 1. Normal chat:     {"kind": "primary_assistant"}
        # 2. Inside a GPT:    {"kind": "gizmo_interaction", "gizmo_id": "g-XXX"}
        # 3. Inside a project: ???  (discovered in Phase 1)
        #
        # We try project_interaction first, fall back to gizmo_interaction
        # with the g-p- prefix, then primary_assistant.

        if project_id:
            conversation_mode = {
                "kind": MODE_PROJECT_INTERACTION,
                "project_id": project_id,
            }
        else:
            conversation_mode = {"kind": MODE_PRIMARY_ASSISTANT}

        # --- Build body ---
        body: dict[str, Any] = {
            "action": "next",
            "messages": [
                {
                    "id": message_id,
                    "author": {"role": "user"},
                    "content": {
                        "content_type": "text",
                        "parts": [message],
                    },
                    "metadata": {},
                }
            ],
            "parent_message_id": parent_message_id or str(uuid.uuid4()),
            "model": model_info.slug,
            "timezone_offset_min": -300,
            "suggestions": [],
            "history_and_training_disabled": False,
            "conversation_mode": conversation_mode,
            "force_paragen": False,
            "force_paragen_model_slug": "",
            "force_nulligen": False,
            "force_rate_limit": False,
            "reset_rate_limits": False,
            "websocket_request_id": str(uuid.uuid4()),
            "system_hints": [],
            "supported_encodings": ["v1"],
            "conversation_origin": None,
        }

        # Add conversation_id if continuing
        if conversation_id:
            body["conversation_id"] = conversation_id

        # Add sentinel token (PoW solution)
        if sentinel_result.token:
            body["sentinel_chat_requirements_token"] = sentinel_result.token

        if sentinel_result.proofofwork:
            body["proofofwork_token"] = sentinel_result.proofofwork

        # --- Build headers ---
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "oai-device-id": self._sentinel._get_device_id(),
            "oai-language": "en-US",
        }

        return ConversationRequest(
            body=body,
            headers=headers,
            url=f"{self._base_url}/backend-api/conversation",
        )

    def _parse_sse(self, text: str) -> list[str]:
        """Parse SSE response into text deltas.

        ChatGPT uses a custom SSE format. Each line may contain:
        - `data: {json}` with message deltas
        - Lines containing `wrb.fr` encoded data

        Returns a list of text delta strings.
        """
        deltas: list[str] = []

        for line in text.split("\n"):
            line = line.strip()
            if not line or not line.startswith("data:"):
                continue

            data_str = line[5:].strip()
            if data_str == "[DONE]":
                break

            try:
                data = json.loads(data_str)
            except json.JSONDecodeError:
                continue

            # Extract text from the message delta
            msg = data.get("message", {})
            if not msg:
                continue

            content = msg.get("content", {})
            if isinstance(content, dict):
                parts = content.get("parts", [])
                if parts and isinstance(parts, list):
                    # Get the full text so far, we'll compute delta
                    full = "".join(str(p) for p in parts if isinstance(p, str))
                    deltas.append(full)

            # Also check for direct text field
            text_field = msg.get("text")
            if text_field and isinstance(text_field, str):
                deltas.append(text_field)

        # Compute actual deltas (SSE gives full text each time, not deltas)
        result: list[str] = []
        prev_len = 0
        for full_text in deltas:
            if len(full_text) > prev_len:
                result.append(full_text[prev_len:])
                prev_len = len(full_text)

        return result

    def _parse_non_streaming(self, text: str) -> str:
        """Parse a non-streaming response to get the final text."""
        deltas = self._parse_sse(text)
        return "".join(deltas)


class ConversationError(Exception):
    """Conversation protocol error."""
