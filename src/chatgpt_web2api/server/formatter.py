"""OpenAI API response formatting.

Converts ChatGPT web responses into OpenAI API compatible format.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Optional


def format_chat_completion(
    *,
    model: str,
    text: str,
    conversation_id: str = "",
    message_id: str = "",
    finish_reason: str = "stop",
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    project_id: Optional[str] = None,
) -> dict[str, Any]:
    """Format a complete (non-streaming) chat completion response."""
    return {
        "id": f"chatcmpl-{message_id or uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": text,
                },
                "finish_reason": finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
        # Custom metadata
        "chatgpt_web2api": {
            "conversation_id": conversation_id,
            "message_id": message_id,
            "project_id": project_id,
        },
    }


def format_chunk(
    *,
    model: str,
    delta_content: str = "",
    delta_role: Optional[str] = None,
    finish_reason: Optional[str] = None,
    message_id: str = "",
    conversation_id: str = "",
    project_id: Optional[str] = None,
) -> str:
    """Format a streaming chunk as an SSE data line.

    Returns a string like "data: {...}\\n\\n" suitable for streaming.
    """
    chunk: dict[str, Any] = {
        "id": f"chatcmpl-{message_id or uuid.uuid4().hex[:12]}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": {},
                "finish_reason": finish_reason,
            }
        ],
    }

    if delta_role:
        chunk["choices"][0]["delta"]["role"] = delta_role
    if delta_content:
        chunk["choices"][0]["delta"]["content"] = delta_content

    return f"data: {chunk}\n\n"


def format_chunk_done() -> str:
    """Format the final SSE chunk."""
    return "data: [DONE]\n\n"


def format_models_list() -> dict[str, Any]:
    """Format the /v1/models response."""
    from chatgpt_web2api.protocol.models import list_models

    return {
        "object": "list",
        "data": list_models(),
    }


def format_error(
    *,
    message: str,
    error_type: str = "server_error",
    code: str = "internal_error",
    status: int = 500,
) -> dict[str, Any]:
    """Format an error response."""
    return {
        "error": {
            "message": message,
            "type": error_type,
            "code": code,
            "status": status,
        }
    }


def parse_model_project(model_str: str) -> tuple[str, Optional[str]]:
    """Parse a model string that may contain a project ID.

    Formats:
    - "gpt-4o" → ("gpt-4o", None)
    - "gpt-4o-project:g-p-XXXX" → ("gpt-4o", "g-p-XXXX")
    """
    if "-project:" in model_str:
        parts = model_str.split("-project:", 1)
        return parts[0], parts[1]
    if model_str.startswith("project:"):
        # project:ID → use default model with project
        return "gpt-4o", model_str.split(":", 1)[1]
    return model_str, None
