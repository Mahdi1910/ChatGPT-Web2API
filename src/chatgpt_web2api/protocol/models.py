"""Model name mapping — OpenAI API names to ChatGPT web model slugs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ModelInfo:
    """A ChatGPT web model."""
    slug: str
    display_name: str
    openai_id: str  # The OpenAI API model name
    supports_streaming: bool = True
    supports_vision: bool = False


# Known ChatGPT web model slugs
MODELS: dict[str, ModelInfo] = {
    "gpt-4o": ModelInfo(
        slug="auto",
        display_name="GPT-4o",
        openai_id="gpt-4o",
        supports_streaming=True,
        supports_vision=True,
    ),
    "gpt-4o-mini": ModelInfo(
        slug="auto",
        display_name="GPT-4o mini",
        openai_id="gpt-4o-mini",
        supports_streaming=True,
        supports_vision=True,
    ),
    "o1": ModelInfo(
        slug="o1",
        display_name="o1",
        openai_id="o1",
        supports_streaming=True,
        supports_vision=False,
    ),
    "o1-mini": ModelInfo(
        slug="o1-mini",
        display_name="o1-mini",
        openai_id="o1-mini",
        supports_streaming=True,
        supports_vision=False,
    ),
    "o3-mini": ModelInfo(
        slug="o3-mini",
        display_name="o3-mini",
        openai_id="o3-mini",
        supports_streaming=True,
        supports_vision=False,
    ),
    "o3-mini-high": ModelInfo(
        slug="o3-mini-high",
        display_name="o3-mini (high)",
        openai_id="o3-mini-high",
        supports_streaming=True,
        supports_vision=False,
    ),
    "o4-mini": ModelInfo(
        slug="o4-mini",
        display_name="o4-mini",
        openai_id="o4-mini",
        supports_streaming=True,
        supports_vision=True,
    ),
}

# Alias mapping: OpenAI API name → ChatGPT web slug
_ALIASES: dict[str, str] = {
    "gpt-4o": "gpt-4o",
    "gpt-4o-mini": "gpt-4o-mini",
    "o1": "o1",
    "o1-mini": "o1-mini",
    "o3-mini": "o3-mini",
    "o3-mini-high": "o3-mini-high",
    "o4-mini": "o4-mini",
    # Common aliases
    "gpt4o": "gpt-4o",
    "gpt-4": "gpt-4o",
    "gpt4": "gpt-4o",
}


def resolve_model(model_name: str) -> ModelInfo:
    """Resolve an OpenAI API model name to a ChatGPT web ModelInfo.

    Falls back to gpt-4o for unknown model names.
    """
    key = _ALIASES.get(model_name.lower().strip())
    if key and key in MODELS:
        return MODELS[key]
    # Try direct lookup
    if model_name in MODELS:
        return MODELS[model_name]
    # Default to gpt-4o
    return MODELS["gpt-4o"]


def list_models() -> list[dict]:
    """List all available models in OpenAI API format."""
    models = []
    for model in MODELS.values():
        models.append({
            "id": model.openai_id,
            "object": "model",
            "created": 1700000000,
            "owned_by": "chatgpt-web2api",
        })
    # Add project-prefixed variants
    models.append({
        "id": "gpt-4o-project",
        "object": "model",
        "created": 1700000000,
        "owned_by": "chatgpt-web2api",
        "description": "GPT-4o within a ChatGPT project (use project_id in request)",
    })
    return models
