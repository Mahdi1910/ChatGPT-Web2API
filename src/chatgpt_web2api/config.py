"""Configuration management for chatgpt-web2api."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class ServerConfig:
    port: int = 8082
    host: str = "0.0.0.0"


@dataclass
class ChatGPTConfig:
    base_url: str = "https://chatgpt.com"
    default_model: str = "gpt-4o"
    default_project_id: Optional[str] = None


@dataclass
class BrowserConfig:
    headless: bool = False
    stealth_profile: str = "windows-chrome-stable"
    proxy: Optional[str] = None


@dataclass
class SessionConfig:
    cookie_file: Optional[str] = None
    retry_attempts: int = 3
    retry_delay_sec: float = 2.0
    request_timeout_sec: int = 180


@dataclass
class LogConfig:
    log_requests: bool = True
    log_level: str = "INFO"


@dataclass
class AppConfig:
    server: ServerConfig = field(default_factory=ServerConfig)
    chatgpt: ChatGPTConfig = field(default_factory=ChatGPTConfig)
    browser: BrowserConfig = field(default_factory=BrowserConfig)
    session: SessionConfig = field(default_factory=SessionConfig)
    log: LogConfig = field(default_factory=LogConfig)
    api_keys: list[str] = field(default_factory=list)

    @classmethod
    def from_file(cls, path: str | Path) -> AppConfig:
        """Load configuration from a JSON file."""
        p = Path(path)
        if not p.exists():
            return cls()
        with open(p) as f:
            data = json.load(f)
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict) -> AppConfig:
        """Build config from a dictionary."""
        cfg = cls()
        if "port" in data:
            cfg.server.port = data["port"]
        if "host" in data:
            cfg.server.host = data["host"]
        if "chatgpt_base_url" in data:
            cfg.chatgpt.base_url = data["chatgpt_base_url"]
        if "default_model" in data:
            cfg.chatgpt.default_model = data["default_model"]
        if "default_project_id" in data:
            cfg.chatgpt.default_project_id = data["default_project_id"]
        if "headless" in data:
            cfg.browser.headless = data["headless"]
        if "stealth_profile" in data:
            cfg.browser.stealth_profile = data["stealth_profile"]
        if "proxy" in data:
            cfg.browser.proxy = data["proxy"]
        if "cookie_file" in data:
            cfg.session.cookie_file = data["cookie_file"]
        if "retry_attempts" in data:
            cfg.session.retry_attempts = data["retry_attempts"]
        if "retry_delay_sec" in data:
            cfg.session.retry_delay_sec = data["retry_delay_sec"]
        if "request_timeout_sec" in data:
            cfg.session.request_timeout_sec = data["request_timeout_sec"]
        if "log_requests" in data:
            cfg.log.log_requests = data["log_requests"]
        if "log_level" in data:
            cfg.log.log_level = data["log_level"]
        if "api_keys" in data:
            cfg.api_keys = data["api_keys"]
        return cfg

    @classmethod
    def from_env(cls) -> AppConfig:
        """Build config from environment variables."""
        cfg = cls()
        if v := os.environ.get("CHATGPT_W2A_PORT"):
            cfg.server.port = int(v)
        if v := os.environ.get("CHATGPT_W2A_HOST"):
            cfg.server.host = v
        if v := os.environ.get("CHATGPT_W2A_BASE_URL"):
            cfg.chatgpt.base_url = v
        if v := os.environ.get("CHATGPT_W2A_DEFAULT_MODEL"):
            cfg.chatgpt.default_model = v
        if v := os.environ.get("CHATGPT_W2A_DEFAULT_PROJECT"):
            cfg.chatgpt.default_project_id = v
        if v := os.environ.get("CHATGPT_W2A_HEADLESS"):
            cfg.browser.headless = v.lower() in ("1", "true", "yes")
        if v := os.environ.get("CHATGPT_W2A_PROXY"):
            cfg.browser.proxy = v
        if v := os.environ.get("CHATGPT_W2A_COOKIE_FILE"):
            cfg.session.cookie_file = v
        if v := os.environ.get("CHATGPT_W2A_API_KEYS"):
            cfg.api_keys = [k.strip() for k in v.split(",") if k.strip()]
        return cfg
