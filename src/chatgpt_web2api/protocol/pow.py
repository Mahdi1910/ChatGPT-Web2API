"""Proof-of-Work solver for ChatGPT's sentinel challenge.

ChatGPT requires solving a Proof-of-Work challenge before each conversation
request.  The challenge uses FNV-1a hash brute-forcing against a difficulty
threshold.

Based on the algorithm observed in ChatGPT's frontend JavaScript and
reverse-engineering efforts (chat2api, realasfngl/ChatGPT).
"""

from __future__ import annotations

import hashlib
import logging
import math
import struct
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

# FNV-1a constants (64-bit)
_FNV_OFFSET_BASIS = 0xCBF29CE484222325
_FNV_PRIME = 0x100000001B3
_MOD = 2**64


def fnv1a_64(data: bytes) -> int:
    """Compute FNV-1a 64-bit hash."""
    h = _FNV_OFFSET_BASIS
    for byte in data:
        h ^= byte
        h = (h * _FNV_PRIME) % _MOD
    return h


def solve_pow(
    seed: str,
    difficulty: str,
    *,
    max_iterations: int = 5_000_000,
) -> Optional[str]:
    """Solve the Proof-of-Work challenge.

    Args:
        seed: The seed string from the sentinel response.
        difficulty: The difficulty string (e.g., "003d").
        max_iterations: Maximum brute-force attempts.

    Returns:
        The solution string, or None if no solution found within limit.
    """
    # The difficulty is a hex prefix — we need to find a nonce such that
    # FNV-1a(seed + nonce) has a hex representation starting with the difficulty prefix.
    target_prefix = difficulty.lower()
    start_time = time.monotonic()

    for i in range(max_iterations):
        nonce = str(i)
        payload = f"{seed}{nonce}".encode("utf-8")
        h = fnv1a_64(payload)
        hex_hash = format(h, '016x')

        if hex_hash[:len(target_prefix)] <= target_prefix:
            elapsed = time.monotonic() - start_time
            logger.info(
                "PoW solved: nonce=%d, hash=%s, elapsed=%.3fs",
                i, hex_hash, elapsed,
            )
            return nonce

    logger.warning(
        "PoW not solved within %d iterations (difficulty=%s)",
        max_iterations, difficulty,
    )
    return None


def generate_config() -> dict[str, Any]:
    """Generate the browser configuration payload for the sentinel request.

    This mimics the configuration that ChatGPT's frontend sends to
    /backend-api/sentinel/chat-requirements.
    """
    import random

    # Common screen resolutions
    resolutions = [
        (1920, 1080), (2560, 1440), (1440, 900),
        (1536, 864), (1366, 768), (3840, 2160),
    ]
    width, height = random.choice(resolutions)

    return {
        "screen": {
            "width": width,
            "height": height,
            "availWidth": width,
            "availHeight": height - 40,
            "colorDepth": 24,
            "pixelDepth": 24,
        },
        "navigator": {
            "hardwareConcurrency": random.choice([4, 8, 12, 16]),
            "deviceMemory": random.choice([4, 8, 16, 32]),
            "platform": "Win32",
            "maxTouchPoints": 0,
        },
        "timezone": {
            "timezone": "America/New_York",
            "timezoneOffset": -300,
        },
    }
