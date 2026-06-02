"""ChatGPT Project management via browser automation.

Provides project listing, selection, and conversation scoping.
Projects are identified by the g-p- prefix (e.g. "g-p-6a1cbfa6da8c8191...").
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from chatgpt_web2api.browser import ChatGPTBrowser

logger = logging.getLogger(__name__)

# Pattern to extract project IDs from URLs
_PROJECT_URL_RE = re.compile(r"/g/(g-p-[a-f0-9]+-[a-z0-9-]+)")
_PROJECT_ID_RE = re.compile(r"(g-p-[a-f0-9]+-[a-z0-9-]+)")


@dataclass
class Project:
    """A ChatGPT project."""
    id: str  # g-p-XXXXX-slug
    name: str = ""
    hex_id: str = ""  # The hex part without prefix/slug
    slug: str = ""
    url: str = ""
    chat_count: int = 0
    source_count: int = 0


@dataclass
class ProjectChat:
    """A conversation within a project."""
    id: str
    title: str = ""
    url: str = ""
    project_id: str = ""


class ProjectManager:
    """Manage ChatGPT projects via browser automation.

    Uses the browser to navigate to ChatGPT's project pages and scrape
    project metadata.  In the future, if project-related backend API
    endpoints are discovered, this will use those instead.
    """

    def __init__(
        self,
        browser: ChatGPTBrowser,
        base_url: str = "https://chatgpt.com",
    ) -> None:
        self._browser = browser
        self._base_url = base_url.rstrip("/")
        self._projects_cache: dict[str, Project] = {}

    async def list_projects(self) -> list[Project]:
        """List all available projects by scraping the ChatGPT sidebar.

        Navigates to the ChatGPT home page and extracts project links
        from the DOM.
        """
        logger.info("Listing projects...")
        await self._browser.navigate(f"{self._base_url}")

        # Wait for sidebar to load
        import asyncio
        await asyncio.sleep(3)

        # Extract project links from the page
        projects = await self._browser.page.evaluate("""
            () => {
                const projects = [];
                // Look for links with /g/g-p- pattern in the sidebar
                const links = document.querySelectorAll('a[href*="/g/g-p-"]');
                links.forEach(link => {
                    const href = link.getAttribute('href');
                    const text = link.textContent.trim();
                    projects.push({ url: href, name: text });
                });
                return projects;
            }
        """)

        result: list[Project] = []
        for p in projects:
            url = p.get("url", "")
            match = _PROJECT_URL_RE.search(url)
            if match:
                project_id = match.group(1)
                # Parse the ID into components
                parts = project_id.split("-", 2)  # ["g", "p", "hex-slug"]
                hex_id = parts[2] if len(parts) > 2 else ""
                # Split hex from slug
                slug_match = re.match(r"([a-f0-9]+)-(.+)", hex_id)
                if slug_match:
                    hex_part = slug_match.group(1)
                    slug = slug_match.group(2)
                else:
                    hex_part = hex_id
                    slug = ""

                proj = Project(
                    id=project_id,
                    name=p.get("name", ""),
                    hex_id=hex_part,
                    slug=slug,
                    url=f"{self._base_url}/g/{project_id}/project",
                )
                result.append(proj)
                self._projects_cache[project_id] = proj

        logger.info("Found %d projects", len(result))
        return result

    async def get_project(self, project_id: str) -> Optional[Project]:
        """Get details for a specific project.

        If the project is in cache, returns it directly.
        Otherwise, navigates to the project page and scrapes metadata.
        """
        if project_id in self._projects_cache:
            return self._projects_cache[project_id]

        # Validate format
        if not project_id.startswith("g-p-"):
            logger.warning("Invalid project ID format: %s (expected g-p-...)", project_id)
            return None

        logger.info("Fetching project: %s", project_id)
        project_url = f"{self._base_url}/g/{project_id}/project"
        await self._browser.navigate(project_url)

        import asyncio
        await asyncio.sleep(3)

        # Scrape project details
        details = await self._browser.page.evaluate("""
            () => {
                const title = document.querySelector('h1, [class*="project-title"]');
                const sources = document.querySelectorAll('[class*="source"], [class*="file"]');
                const chats = document.querySelectorAll('[class*="chat-item"], [class*="conversation"]');
                return {
                    title: title ? title.textContent.trim() : '',
                    sourceCount: sources.length,
                    chatCount: chats.length,
                };
            }
        """)

        # Parse ID components
        id_part = project_id[4:]  # Remove "g-p-"
        slug_match = re.match(r"([a-f0-9]+)-(.+)", id_part)
        hex_id = slug_match.group(1) if slug_match else id_part
        slug = slug_match.group(2) if slug_match else ""

        proj = Project(
            id=project_id,
            name=details.get("title", ""),
            hex_id=hex_id,
            slug=slug,
            url=project_url,
            chat_count=details.get("chatCount", 0),
            source_count=details.get("sourceCount", 0),
        )
        self._projects_cache[project_id] = proj
        return proj

    async def list_project_chats(self, project_id: str) -> list[ProjectChat]:
        """List conversations within a project."""
        logger.info("Listing chats for project: %s", project_id)

        chats_url = f"{self._base_url}/g/{project_id}/project?tab=chats"
        await self._browser.navigate(chats_url)

        import asyncio
        await asyncio.sleep(3)

        # Scrape chat links
        chats = await self._browser.page.evaluate("""
            () => {
                const chats = [];
                // Look for chat links within the project
                const links = document.querySelectorAll('a[href*="/c/"]');
                links.forEach(link => {
                    const href = link.getAttribute('href');
                    const text = link.textContent.trim();
                    chats.push({ url: href, title: text });
                });
                return chats;
            }
        """)

        result: list[ProjectChat] = []
        for c in chats:
            url = c.get("url", "")
            # Extract chat ID from URL
            chat_match = re.search(r"/c/([a-f0-9-]+)", url)
            if chat_match:
                result.append(ProjectChat(
                    id=chat_match.group(1),
                    title=c.get("title", ""),
                    url=url,
                    project_id=project_id,
                ))

        logger.info("Found %d chats in project %s", len(result), project_id)
        return result

    def parse_project_id(self, input_str: str) -> Optional[str]:
        """Parse a project ID from various input formats.

        Accepts:
        - Full project ID: "g-p-6a1cbfa6da8c8191bd3674470d2dbc22-orqestra"
        - Project URL: "https://chatgpt.com/g/g-p-.../project"
        - Hex only: "6a1cbfa6da8c8191bd3674470d2dbc22"
        """
        if input_str.startswith("g-p-"):
            return input_str

        # Try URL
        match = _PROJECT_URL_RE.search(input_str)
        if match:
            return match.group(1)

        # Try bare hex — need a slug to construct full ID
        if re.match(r"^[a-f0-9]{32}$", input_str):
            return f"g-p-{input_str}"

        # Try full project ID pattern
        match = _PROJECT_ID_RE.search(input_str)
        if match:
            return match.group(1)

        return None
