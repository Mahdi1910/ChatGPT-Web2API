"""HTTP handler — OpenAI-compatible API endpoints.

Serves /v1/chat/completions, /v1/models, and custom project endpoints.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Optional

from aiohttp import web

from chatgpt_web2api.auth import AuthSession
from chatgpt_web2api.browser import ChatGPTBrowser
from chatgpt_web2api.config import AppConfig
from chatgpt_web2api.projects import ProjectManager
from chatgpt_web2api.protocol.conversation import ConversationBuilder, ConversationError
from chatgpt_web2api.protocol.models import resolve_model
from chatgpt_web2api.protocol.sentinel import SentinelClient, SentinelError
from chatgpt_web2api.server.formatter import (
    format_chat_completion,
    format_chunk,
    format_chunk_done,
    format_error,
    format_models_list,
    parse_model_project,
)

logger = logging.getLogger(__name__)


class APIHandler:
    """Handles OpenAI-compatible API requests."""

    def __init__(
        self,
        config: AppConfig,
        browser: ChatGPTBrowser,
        auth: AuthSession,
        conversation: ConversationBuilder,
        projects: ProjectManager,
    ) -> None:
        self._config = config
        self._browser = browser
        self._auth = auth
        self._conversation = conversation
        self._projects = projects

    async def handle_health(self, request: web.Request) -> web.Response:
        """GET / — health check."""
        return web.json_response({
            "status": "ok",
            "service": "chatgpt-web2api",
            "version": "0.1.0",
            "browser_active": self._browser._started,
            "logged_in": await self._browser.is_logged_in(),
        })

    async def handle_models(self, request: web.Request) -> web.Response:
        """GET /v1/models — list available models."""
        self._check_api_key(request)
        return web.json_response(format_models_list())

    async def handle_chat_completions(self, request: web.Request) -> web.Response:
        """POST /v1/chat/completions — chat completion endpoint."""
        self._check_api_key(request)

        try:
            body = await request.json()
        except json.JSONDecodeError as e:
            return web.json_response(
                format_error(message=f"Invalid JSON: {e}", status=400),
                status=400,
            )

        # Extract parameters
        messages = body.get("messages", [])
        model_str = body.get("model", self._config.chatgpt.default_model)
        stream = body.get("stream", False)
        project_id = body.get("project_id") or self._config.chatgpt.default_project_id

        if not messages:
            return web.json_response(
                format_error(message="No messages provided", status=400),
                status=400,
            )

        # Parse model and project from model string
        model_name, model_project = parse_model_project(model_str)
        if model_project:
            project_id = model_project

        # Extract the last user message
        user_message = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, list):
                    # Multi-part content — extract text parts
                    user_message = "\n".join(
                        p.get("text", "") for p in content
                        if isinstance(p, dict) and p.get("type") == "text"
                    )
                else:
                    user_message = str(content)
                break

        if not user_message:
            return web.json_response(
                format_error(message="No user message found", status=400),
                status=400,
            )

        # Build context from system/assistant messages for conversation continuity
        # For now, send only the last user message
        # TODO: Implement multi-turn conversation with history

        logger.info(
            "Chat completion request: model=%s project=%s stream=%s msg_len=%d",
            model_name, project_id, stream, len(user_message),
        )

        try:
            if stream:
                return await self._handle_streaming(
                    request, user_message, model_name, project_id
                )
            else:
                return await self._handle_non_streaming(
                    request, user_message, model_name, project_id
                )
        except ConversationError as e:
            logger.error("Conversation error: %s", e)
            return web.json_response(
                format_error(message=str(e), status=502),
                status=502,
            )
        except SentinelError as e:
            logger.error("Sentinel error: %s", e)
            return web.json_response(
                format_error(message=f"Sentinel challenge failed: {e}", status=503),
                status=503,
            )

    async def _handle_streaming(
        self,
        request: web.Request,
        message: str,
        model: str,
        project_id: Optional[str],
    ) -> web.StreamResponse:
        """Handle a streaming chat completion request."""
        resp = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
        await resp.prepare(request)

        msg_id = uuid.uuid4().hex[:12]

        # Send role chunk
        await resp.write(
            format_chunk(
                model=model,
                delta_role="assistant",
                message_id=msg_id,
            ).encode()
        )

        try:
            async for delta in self._conversation.send_message(
                message,
                model=model,
                project_id=project_id,
                stream=True,
            ):
                if delta:
                    chunk = format_chunk(
                        model=model,
                        delta_content=delta,
                        message_id=msg_id,
                    )
                    await resp.write(chunk.encode())

            # Send finish chunk
            await resp.write(
                format_chunk(
                    model=model,
                    finish_reason="stop",
                    message_id=msg_id,
                ).encode()
            )
            await resp.write(format_chunk_done().encode())
        except Exception as e:
            logger.error("Streaming error: %s", e)
            error_chunk = format_chunk(
                model=model,
                delta_content=f"\n\n[Error: {e}]",
                message_id=msg_id,
            )
            await resp.write(error_chunk.encode())
            await resp.write(format_chunk_done().encode())

        await resp.write_eof()
        return resp

    async def _handle_non_streaming(
        self,
        request: web.Request,
        message: str,
        model: str,
        project_id: Optional[str],
    ) -> web.Response:
        """Handle a non-streaming chat completion request."""
        full_text = ""
        async for delta in self._conversation.send_message(
            message,
            model=model,
            project_id=project_id,
            stream=True,
        ):
            full_text += delta

        completion = format_chat_completion(
            model=model,
            text=full_text,
            project_id=project_id,
        )
        return web.json_response(completion)

    async def handle_projects(self, request: web.Request) -> web.Response:
        """GET /v1/projects — list ChatGPT projects (custom endpoint)."""
        self._check_api_key(request)
        try:
            projects = await self._projects.list_projects()
            return web.json_response({
                "object": "list",
                "data": [
                    {
                        "id": p.id,
                        "name": p.name,
                        "hex_id": p.hex_id,
                        "slug": p.slug,
                        "url": p.url,
                        "chat_count": p.chat_count,
                    }
                    for p in projects
                ],
            })
        except Exception as e:
            logger.error("Failed to list projects: %s", e)
            return web.json_response(
                format_error(message=str(e), status=500),
                status=500,
            )

    async def handle_project_chats(self, request: web.Request) -> web.Response:
        """GET /v1/projects/{id}/chats — list project conversations."""
        self._check_api_key(request)
        project_id = request.match_info["project_id"]
        try:
            chats = await self._projects.list_project_chats(project_id)
            return web.json_response({
                "object": "list",
                "project_id": project_id,
                "data": [
                    {
                        "id": c.id,
                        "title": c.title,
                        "url": c.url,
                    }
                    for c in chats
                ],
            })
        except Exception as e:
            logger.error("Failed to list project chats: %s", e)
            return web.json_response(
                format_error(message=str(e), status=500),
                status=500,
            )

    def _check_api_key(self, request: web.Request) -> None:
        """Validate API key if configured."""
        if not self._config.api_keys:
            return

        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            key = auth_header[7:]
        else:
            key = request.query.get("key", "")

        if key not in self._config.api_keys:
            raise web.HTTPUnauthorized(
                text=json.dumps(format_error(
                    message="Invalid API key",
                    status=401,
                )),
                content_type="application/json",
            )
