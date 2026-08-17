from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    text = read(path)
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected 1 occurrence, found {count}: {old[:100]!r}")
    write(path, text.replace(old, new, 1))


def replace_count(path: str, old: str, new: str, expected: int) -> None:
    text = read(path)
    count = text.count(old)
    if count != expected:
        raise RuntimeError(f"{path}: expected {expected} occurrences, found {count}: {old[:100]!r}")
    write(path, text.replace(old, new))


def insert_before_once(path: str, anchor: str, addition: str) -> None:
    text = read(path)
    count = text.count(anchor)
    if count != 1:
        raise RuntimeError(f"{path}: expected unique anchor, found {count}: {anchor[:100]!r}")
    write(path, text.replace(anchor, addition + anchor, 1))


# ---------------------------------------------------------------------------
# mcp_server.py
# ---------------------------------------------------------------------------
MCP = "src/chatgpt_web2api/mcp_server.py"

replace_once(
    MCP,
    "from pydantic import BaseModel, Field\n",
    "from pydantic import BaseModel, Field, field_validator\n",
)

insert_before_once(
    MCP,
    "class DeleteConversationInput(BaseModel):\n",
    '''class BranchConversationInput(BaseModel):
    """Input for branching from one assistant answer in an existing chat."""

    conversation_id: str = Field(
        min_length=1,
        description=(
            "UUID of the source conversation. Use list_conversations to discover "
            "conversation IDs, titles, and update times. Temporary WEB: IDs are also accepted."
        ),
    )
    message_snippet: str = Field(
        min_length=1,
        description=(
            "Text snippet that uniquely identifies the assistant answer to branch from. "
            "If more than one assistant answer matches, no branch is created and all "
            "matching candidates are returned for disambiguation."
        ),
    )
    limit: int = Field(
        default=28,
        ge=1,
        le=500,
        description="Maximum assistant-answer candidates to scan (default: 28).",
    )
    offset: int = Field(
        default=0,
        ge=0,
        description="Number of assistant-answer candidates to skip before scanning.",
    )

    @field_validator("conversation_id", "message_snippet")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value


''',
)

replace_once(
    MCP,
    '    LIST_CONVERSATIONS = "list_conversations"\n    DELETE_CONVERSATION = "delete_conversation"\n',
    '    LIST_CONVERSATIONS = "list_conversations"\n    BRANCH_CONVERSATION = "branch_conversation"\n    DELETE_CONVERSATION = "delete_conversation"\n',
)

insert_before_once(
    MCP,
    "DELETE_RESULT_OUTPUT = {\n",
    '''BRANCH_CONVERSATION_MATCH_ITEM = {
    "type": "object",
    "properties": {
        "candidate": {"type": "integer"},
        "message_id": {"type": "string"},
        "user_prompt": {"type": "string"},
        "assistant_answer": {"type": "string"},
    },
    "required": ["candidate", "message_id", "user_prompt", "assistant_answer"],
}

BRANCH_CONVERSATION_OUTPUT = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["branched", "ambiguous", "not_found"]},
        "success": {"type": "boolean"},
        "source_conversation_id": {"type": "string"},
        "source_title": {"type": "string"},
        "message_snippet": {"type": "string"},
        "offset": {"type": "integer"},
        "limit": {"type": "integer"},
        "matches": {"type": "array", "items": BRANCH_CONVERSATION_MATCH_ITEM},
        "source_message_id": {"type": ["string", "null"]},
        "branched_conversation_id": {"type": ["string", "null"]},
        "url": {"type": ["string", "null"]},
        "temporary": {"type": ["boolean", "null"]},
        "note": {"type": "string"},
        "message": {"type": "string"},
    },
    "required": [
        "status", "success", "source_conversation_id", "source_title",
        "message_snippet", "offset", "limit", "matches", "source_message_id",
        "branched_conversation_id", "url", "temporary", "note", "message",
    ],
}

''',
)

replace_once(
    MCP,
    "        ToolName.CREATE_MEMORY.value,\n        ToolName.ARCHIVE_CONVERSATION.value,\n",
    "        ToolName.CREATE_MEMORY.value,\n        ToolName.ARCHIVE_CONVERSATION.value,\n        ToolName.BRANCH_CONVERSATION.value,\n",
)

replace_once(
    MCP,
    "        ToolName.CHAT_WITH_GPT.value,\n        ToolName.DELETE_CONVERSATION.value,\n",
    "        ToolName.CHAT_WITH_GPT.value,\n        ToolName.BRANCH_CONVERSATION.value,\n        ToolName.DELETE_CONVERSATION.value,\n",
)

insert_before_once(
    MCP,
    "async def do_delete_conversation(driver: CDPDriver, args: dict) -> dict:\n",
    '''async def do_branch_conversation(driver: CDPDriver, args: dict) -> dict:
    """Branch from a uniquely matched assistant answer."""
    validated = BranchConversationInput(**args)
    return await driver.branch_conversation(
        conversation_id=validated.conversation_id,
        message_snippet=validated.message_snippet,
        limit=validated.limit,
        offset=validated.offset,
    )


''',
)

replace_once(
    MCP,
    '    """Build the FULL list of tool definitions (all 15), unfiltered.\n',
    '    """Build the FULL list of tool definitions (all 17), unfiltered.\n',
)

insert_before_once(
    MCP,
    '''        mcp_types.Tool(
            name=ToolName.DELETE_CONVERSATION.value,
''',
    '''        mcp_types.Tool(
            name=ToolName.BRANCH_CONVERSATION.value,
            title="Branch Conversation",
            description=(
                "Create a new ChatGPT branch from one specific assistant answer in an "
                "existing conversation. Use list_conversations to discover the source "
                "conversation_id, then provide a message_snippet from the assistant answer. "
                "The tool scans assistant answers only. If zero answers match it returns a "
                "not-found result; if multiple answers match it returns every candidate with "
                "the preceding user prompt and does NOT branch. With one unique match, it "
                "opens the source conversation, targets the exact assistant message by its "
                "backend/DOM message UUID, and uses ChatGPT's More actions → Branch in new chat "
                "UI. Successful branches may initially have a temporary WEB: ID; sending the "
                "first later chat_completion to that ID upgrades it to a permanent UUID."
            ),
            inputSchema=BranchConversationInput.model_json_schema(),
            outputSchema=BRANCH_CONVERSATION_OUTPUT,
            annotations=mcp_types.ToolAnnotations(
                title="Branch Conversation",
                readOnlyHint=False,
                destructiveHint=False,
                idempotentHint=False,
                openWorldHint=False,
            ),
        ),
''',
)

insert_before_once(
    MCP,
    "    # Status operations return status text + structured output\n",
    '''    if name == ToolName.BRANCH_CONVERSATION.value:
        status = result.get("status")
        if status == "branched":
            text = (
                f"Branched conversation: {result.get('branched_conversation_id', '')}\\n"
                f"{result.get('url', '')}"
            )
            if result.get("note"):
                text += f"\\n\\n{result['note']}"
        else:
            text = result.get("message") or (
                "Multiple assistant answers matched; no branch was created."
                if status == "ambiguous"
                else "No matching AI answer found"
            )
        return [mcp_types.TextContent(type="text", text=text)], result
    # Status operations return status text + structured output
''',
)

insert_before_once(
    MCP,
    "    if isinstance(exc, OwnedTabRequiredError):\n",
    '''    if isinstance(exc, BranchConversationError):
        return mcp_types.CallToolResult(
            content=[mcp_types.TextContent(
                type="text",
                text=f"Branch operation failed: {exc}. (branch_conversation_failed)",
            )],
            isError=True,
        )
''',
)

replace_once(
    MCP,
    "    AuthExpiredError,\n    CDPDriver,\n",
    "    AuthExpiredError,\n    BranchConversationError,\n    CDPDriver,\n",
)

replace_count(
    MCP,
    "            ToolName.LIST_CONVERSATIONS.value: lambda: do_list_conversations(driver, arguments),\n",
    "            ToolName.LIST_CONVERSATIONS.value: lambda: do_list_conversations(driver, arguments),\n            ToolName.BRANCH_CONVERSATION.value: lambda: do_branch_conversation(driver, arguments),\n",
    1,
)

replace_count(
    MCP,
    "            ToolName.LIST_CONVERSATIONS.value: lambda: do_list_conversations(_driver, arguments),\n",
    "            ToolName.LIST_CONVERSATIONS.value: lambda: do_list_conversations(_driver, arguments),\n            ToolName.BRANCH_CONVERSATION.value: lambda: do_branch_conversation(_driver, arguments),\n",
    1,
)

# ---------------------------------------------------------------------------
# cdp_driver.py
# ---------------------------------------------------------------------------
CDP = "src/chatgpt_web2api/cdp_driver.py"

replace_once(CDP, "import time\nimport urllib.parse\n", "import time\nimport unicodedata\nimport urllib.parse\n")

insert_before_once(
    CDP,
    "class CDPReconnectError(RuntimeError):\n",
    '''class BranchConversationError(RuntimeError):
    """Raised when a branch UI action cannot be safely verified.

    Branching is fail-closed: the driver never guesses which assistant answer,
    More-actions button, menu item, or resulting route should be used.
    """


''',
)

replace_once(
    CDP,
    '        parts = [p for p in parsed.path.split("/") if p]\n',
    '        parts = [urllib.parse.unquote(p) for p in parsed.path.split("/") if p]\n',
)

insert_before_once(
    CDP,
    "    # ── Response Retrieval ────────────────────────────────────\n",
    '''    async def branch_assistant_answer(
        self,
        message_id: str,
        message_snippet: str,
        source_conversation_id: str,
    ) -> dict:
        """Branch the exact assistant DOM turn selected by backend message UUID.

        Delegated to ChatGPTDom so page-DOM behavior remains in the canonical
        DOM layer while CDPDriver preserves the interception/monkeypatch seam.
        """
        return await self._dom.branch_assistant_answer(
            message_id=message_id,
            message_snippet=message_snippet,
            source_conversation_id=source_conversation_id,
        )

''',
)

insert_before_once(
    CDP,
    '    @diagnose("delete_conversation")\n',
    '''    @diagnose("branch_conversation")
    async def branch_conversation(
        self,
        conversation_id: str,
        message_snippet: str,
        limit: int = 28,
        offset: int = 0,
    ) -> dict:
        """Branch from one uniquely matched assistant answer.

        Matching is performed against the source conversation's active backend
        chain. The backend message UUID is retained and becomes the primary DOM
        identity key; the user snippet is re-verified in the live DOM before
        any click occurs.
        """
        data = await self.get_conversation(conversation_id)
        mapping = data.get("mapping", {}) if isinstance(data, dict) else {}
        current_node = data.get("current_node") if isinstance(data, dict) else None
        source_title = data.get("title", "") if isinstance(data, dict) else ""

        chain: list[dict] = []
        visited: set[str] = set()
        node_id = current_node
        while node_id and node_id not in visited:
            visited.add(node_id)
            node_data = mapping.get(node_id, {}) if isinstance(mapping, dict) else {}
            msg = node_data.get("message") if isinstance(node_data, dict) else None
            if isinstance(msg, dict):
                role = msg.get("author", {}).get("role", "unknown")
                content = msg.get("content") or {}
                parts = content.get("parts", []) if isinstance(content, dict) else []
                text = " ".join(part for part in parts if isinstance(part, str)).strip()
                if text and role in ("user", "assistant"):
                    chain.append(
                        {
                            "role": role,
                            "text": text,
                            "message_id": str(msg.get("id") or node_id),
                        }
                    )
            node_id = node_data.get("parent") if isinstance(node_data, dict) else None

        chain.reverse()
        candidates: list[dict] = []
        last_user_prompt = ""
        for item in chain:
            if item["role"] == "user":
                last_user_prompt = item["text"]
                continue
            candidates.append(
                {
                    "candidate": len(candidates) + 1,
                    "message_id": item["message_id"],
                    "user_prompt": last_user_prompt,
                    "assistant_answer": item["text"],
                }
            )

        scanned = candidates[offset : offset + limit]

        def _normalize(value: str) -> str:
            normalized = unicodedata.normalize("NFC", value or "")
            return " ".join(normalized.split()).casefold()

        needle = _normalize(message_snippet)
        matches = [c for c in scanned if needle in _normalize(c["assistant_answer"])]

        def _result_base() -> dict:
            return {
                "source_conversation_id": conversation_id,
                "source_title": source_title,
                "message_snippet": message_snippet,
                "offset": offset,
                "limit": limit,
                "matches": matches,
                "source_message_id": None,
                "branched_conversation_id": None,
                "url": None,
                "temporary": None,
                "note": "",
                "message": "",
            }

        if not matches:
            result = _result_base()
            result.update(
                {
                    "status": "not_found",
                    "success": False,
                    "message": (
                        f"No matching AI answer found for snippet {message_snippet!r} "
                        f"in conversation {conversation_id}."
                    ),
                }
            )
            return result

        if len(matches) > 1:
            result = _result_base()
            result.update(
                {
                    "status": "ambiguous",
                    "success": False,
                    "message": (
                        f"{len(matches)} assistant answers matched the snippet; no branch "
                        "was created. Provide a more specific message_snippet."
                    ),
                }
            )
            return result

        match = matches[0]
        # Navigation + click are mutations. Enforce the owned-tab invariant for
        # direct driver callers as well as MCP's outer MutationLock.
        self._assert_owned_tab_required()
        await self.ensure_current_conversation(conversation_id)
        branch = await self.branch_assistant_answer(
            message_id=match["message_id"],
            message_snippet=message_snippet,
            source_conversation_id=conversation_id,
        )

        branch_id = str(branch.get("id") or "")
        branch_url = str(branch.get("url") or "")
        if not branch_id or not branch_url or branch_id == conversation_id:
            raise BranchConversationError("branch landing did not produce a new conversation ID")

        self._current_conv_id = branch_id
        temporary = bool(branch.get("temporary"))
        note = (
            "This is a temporary link. Once you send your first message to this temporary "
            "conversation ID, ChatGPT will automatically upgrade it to a permanent UUID."
            if temporary
            else "ChatGPT returned a permanent conversation ID immediately."
        )
        result = _result_base()
        result.update(
            {
                "status": "branched",
                "success": True,
                "source_message_id": match["message_id"],
                "branched_conversation_id": branch_id,
                "url": branch_url,
                "temporary": temporary,
                "note": note,
                "message": "Conversation branch created successfully.",
            }
        )
        return result

''',
)

# ---------------------------------------------------------------------------
# backend_client.py: decode WEB%3A defensively
# ---------------------------------------------------------------------------
BACKEND = "src/chatgpt_web2api/backend_client.py"
replace_once(BACKEND, "import time\n\nfrom .breakers", "import time\nimport urllib.parse\n\nfrom .breakers")
replace_once(
    BACKEND,
    '        return url.split("/c/")[1].split("/")[0].split("?")[0]\n',
    '        return urllib.parse.unquote(url.split("/c/")[1].split("/")[0].split("?")[0])\n',
)

# ---------------------------------------------------------------------------
# diagnostics.py
# ---------------------------------------------------------------------------
DIAG = "src/chatgpt_web2api/diagnostics.py"
replace_once(
    DIAG,
    '    "get_conversation": {"kind": "dict", "required_keys": ["id", "messages"]},\n',
    '    "get_conversation": {"kind": "dict", "required_keys": ["id", "messages"]},\n    "branch_conversation": {"kind": "dict", "required_keys": ["status", "source_conversation_id", "matches"]},\n',
)

# ---------------------------------------------------------------------------
# chatgpt_dom.py
# ---------------------------------------------------------------------------
DOM = "src/chatgpt_web2api/chatgpt_dom.py"
replace_once(
    DOM,
    "import logging\nimport time\nimport unicodedata\n",
    "import logging\nimport re\nimport time\nimport unicodedata\nimport urllib.parse\n",
)

insert_before_once(
    DOM,
    "class ChatGPTDom:\n",
    '''def _branch_snippet_matches(snippet: str, visible_text: str) -> bool:
    """Compare a backend-matched snippet against rendered DOM text.

    Markdown punctuation is intentionally ignored by comparing Unicode word
    tokens first; this lets a backend snippet such as ``**important**`` match
    rendered ``important`` while still providing an independent text check on
    top of the exact data-message-id identity.
    """
    snippet_nfc = unicodedata.normalize("NFC", snippet or "").casefold()
    visible_nfc = unicodedata.normalize("NFC", visible_text or "").casefold()
    snippet_words = re.findall(r"\\w+", snippet_nfc, flags=re.UNICODE)
    visible_words = re.findall(r"\\w+", visible_nfc, flags=re.UNICODE)
    if snippet_words:
        width = len(snippet_words)
        return any(
            visible_words[i : i + width] == snippet_words
            for i in range(max(0, len(visible_words) - width + 1))
        )
    compact_snippet = "".join(snippet_nfc.split())
    compact_visible = "".join(visible_nfc.split())
    return bool(compact_snippet) and compact_snippet in compact_visible


''',
)

insert_before_once(
    DOM,
    "    # ── Rate-limit popup ──────────────────────────────────────\n",
    '''    async def branch_assistant_answer(
        self,
        *,
        message_id: str,
        message_snippet: str,
        source_conversation_id: str,
    ) -> dict:
        """Click ChatGPT's Branch in new chat action for one exact assistant turn.

        ``message_id`` is the primary identity bridge from the backend mapping
        to ``data-message-id`` in the live DOM. The snippet is independently
        verified against rendered text before any mutation. Every later step is
        fail-closed; this method never falls back to constructing /branch URLs.
        """
        from .cdp_driver import BranchConversationError, CDPJSError

        d = self._driver
        probe_js = (
            "(function(){"
            "  var nodes=document.querySelectorAll('div[data-message-author-role=\\\"assistant\\\"]');"
            "  var target=null;"
            "  for(var i=0;i<nodes.length;i++){"
            "    if(nodes[i].getAttribute('data-message-id')===__D.message_id){target=nodes[i];break;}"
            "  }"
            "  if(!target) return JSON.stringify({found:false});"
            "  target.scrollIntoView({block:'center',inline:'nearest'});"
            "  target.dispatchEvent(new MouseEvent('mouseover',{bubbles:true,cancelable:true,view:window}));"
            "  return JSON.stringify({found:true,role:target.getAttribute('data-message-author-role')||'',text:target.innerText||target.textContent||''});"
            "})()"
        )
        try:
            raw = await d._js_with_data_strict(probe_js, {"message_id": message_id}, timeout=10)
            probe = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except (CDPJSError, json.JSONDecodeError, TypeError) as exc:
            await d._capture_selector_diagnostic("branch target probe")
            raise BranchConversationError(f"could not inspect target assistant message: {exc}") from exc

        if not probe.get("found") or probe.get("role") != "assistant":
            await d._capture_selector_diagnostic("branch target missing")
            raise BranchConversationError(
                f"assistant message {message_id} is not present in the live conversation DOM"
            )
        if not _branch_snippet_matches(message_snippet, probe.get("text", "")):
            await d._capture_selector_diagnostic("branch target text mismatch")
            raise BranchConversationError(
                "live assistant text no longer matches the uniquely selected backend snippet"
            )

        # Locate the nearest turn container and its More-actions control. The
        # control can render only after hover, so poll briefly instead of using
        # a one-shot selector. Generated Radix IDs/classes are deliberately not
        # used as stable selectors.
        more_js = (
            "(function(){"
            "  var nodes=document.querySelectorAll('div[data-message-author-role=\\\"assistant\\\"]');"
            "  var target=null;"
            "  for(var i=0;i<nodes.length;i++){if(nodes[i].getAttribute('data-message-id')===__D.message_id){target=nodes[i];break;}}"
            "  if(!target) return JSON.stringify({clicked:false,reason:'target-missing'});"
            "  var turn=null,cur=target;"
            "  for(var depth=0;cur&&cur!==document.body&&depth<12;depth++,cur=cur.parentElement){"
            "    if(cur.classList&&(cur.classList.contains('agent-turn')||cur.classList.contains('group/turn-messages'))){turn=cur;break;}"
            "  }"
            "  if(!turn){"
            "    cur=target;"
            "    for(var d2=0;cur&&cur!==document.body&&d2<12;d2++,cur=cur.parentElement){"
            "      if(cur.querySelector&&cur.querySelector('button[aria-label=\\\"More actions\\\"],button[aria-label*=\\\"More\\\" i]')){turn=cur;break;}"
            "    }"
            "  }"
            "  if(!turn) return JSON.stringify({clicked:false,reason:'turn-missing'});"
            "  turn.dispatchEvent(new MouseEvent('mouseover',{bubbles:true,cancelable:true,view:window}));"
            "  var btn=turn.querySelector('button[aria-label=\\\"More actions\\\"]')||turn.querySelector('button[aria-label*=\\\"More\\\" i]');"
            "  if(!btn) return JSON.stringify({clicked:false,reason:'more-missing'});"
            "  var evts=['pointerdown','mousedown','pointerup','mouseup','click'];"
            "  for(var j=0;j<evts.length;j++){btn.dispatchEvent(new MouseEvent(evts[j],{bubbles:true,cancelable:true,view:window}));}"
            "  return JSON.stringify({clicked:true,state:btn.getAttribute('data-state')||'',controls:btn.getAttribute('aria-controls')||''});"
            "})()"
        )
        more_state = None
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            try:
                raw = await d._js_with_data_strict(more_js, {"message_id": message_id}, timeout=10)
                state = json.loads(raw) if isinstance(raw, str) else (raw or {})
            except (CDPJSError, json.JSONDecodeError, TypeError):
                state = {}
            if state.get("clicked"):
                more_state = state
                break
            await asyncio.sleep(0.25)
        if not more_state:
            await d._capture_selector_diagnostic("branch More actions")
            raise BranchConversationError("More actions button did not appear for the target turn")

        menu_js = (
            "(function(){"
            "  function visible(el){if(!el)return false;var s=getComputedStyle(el),r=el.getBoundingClientRect();return s.display!=='none'&&s.visibility!=='hidden'&&r.width>0&&r.height>0;}"
            "  var menu=__D.controls?document.getElementById(__D.controls):null;"
            "  if(!menu||!visible(menu)){var menus=document.querySelectorAll('[role=\\\"menu\\\"]');for(var i=menus.length-1;i>=0;i--){if(visible(menus[i])){menu=menus[i];break;}}}"
            "  if(!menu) return JSON.stringify({clicked:false,reason:'menu-missing'});"
            "  var items=menu.querySelectorAll('[role=\\\"menuitem\\\"]');"
            "  for(var j=0;j<items.length;j++){"
            "    var text=(items[j].innerText||items[j].textContent||'').replace(/\\s+/g,' ').trim();"
            "    if(text==='Branch in new chat'){"
            "      var evts=['pointerdown','mousedown','pointerup','mouseup','click'];"
            "      for(var k=0;k<evts.length;k++){items[j].dispatchEvent(new MouseEvent(evts[k],{bubbles:true,cancelable:true,view:window}));}"
            "      return JSON.stringify({clicked:true});"
            "    }"
            "  }"
            "  return JSON.stringify({clicked:false,reason:'branch-item-missing'});"
            "})()"
        )
        menu_clicked = False
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            try:
                raw = await d._js_with_data_strict(
                    menu_js,
                    {"controls": more_state.get("controls", "")},
                    timeout=10,
                )
                state = json.loads(raw) if isinstance(raw, str) else (raw or {})
            except (CDPJSError, json.JSONDecodeError, TypeError):
                state = {}
            if state.get("clicked"):
                menu_clicked = True
                break
            await asyncio.sleep(0.25)
        if not menu_clicked:
            await d._capture_selector_diagnostic("branch menu item")
            raise BranchConversationError("Branch in new chat menu item did not appear")

        # ChatGPT currently visits /branch/{source}/{message} transiently and
        # then redirects to /c/WEB:<uuid>. Only the final /c/<new-id> is a
        # successful branch landing. Any unrelated navigation fails closed.
        deadline = time.monotonic() + 15.0
        last_url = ""
        while time.monotonic() < deadline:
            try:
                raw = await d._js_strict(
                    "(function(){return JSON.stringify({url:location.href,title:document.title});})()",
                    timeout=5,
                )
                state = json.loads(raw) if isinstance(raw, str) else (raw or {})
            except (CDPJSError, json.JSONDecodeError, TypeError):
                await asyncio.sleep(0.25)
                continue
            url = state.get("url", "") or ""
            last_url = url
            try:
                parsed = urllib.parse.urlparse(url)
            except ValueError:
                parsed = None
            host = (parsed.hostname or "").lower() if parsed else ""
            if host and host != "chatgpt.com" and not host.endswith(".chatgpt.com"):
                raise BranchConversationError(f"branch navigation left chatgpt.com: {url}")
            parts = [urllib.parse.unquote(p) for p in (parsed.path.split("/") if parsed else []) if p]
            if len(parts) >= 3 and parts[0] == "branch":
                if parts[1] != source_conversation_id or parts[2] != message_id:
                    raise BranchConversationError(f"branch route targeted an unexpected source/message: {url}")
                await asyncio.sleep(0.25)
                continue
            for i in range(len(parts) - 1):
                if parts[i] != "c":
                    continue
                new_id = parts[i + 1]
                if new_id == source_conversation_id:
                    break
                if new_id:
                    return {
                        "id": new_id,
                        "url": url,
                        "temporary": new_id.startswith("WEB:"),
                        "title": state.get("title", "") or "",
                    }
            # Remaining on the source URL while the SPA reacts is expected;
            # changing to some unrelated route is not.
            if url and source_conversation_id not in url and "/branch/" not in url:
                raise BranchConversationError(f"unexpected branch navigation state: {url}")
            await asyncio.sleep(0.25)

        await d._capture_selector_diagnostic("branch final URL")
        raise BranchConversationError(
            f"branch did not reach a new /c/<id> URL within 15s (last URL: {last_url})"
        )

''',
)

# ---------------------------------------------------------------------------
# Tests: exact invariants + focused branch behavior
# ---------------------------------------------------------------------------
replace_once("tests/test_unit.py", "    assert len(tools) == 16\n", "    assert len(tools) == 17\n")
replace_once("tests/test_deep.py", "    assert len(ToolName) == 16\n", "    assert len(ToolName) == 17\n")
replace_once("tests/test_gating.py", "    assert len(build_tools()) == 16\n", "    assert len(build_tools()) == 17\n")
replace_once("tests/test_integration.py", "        assert len(names) == 16\n", "        assert len(names) == 17\n")

replace_once(
    "tests/test_gating.py",
    "        ToolName.CREATE_MEMORY, ToolName.ARCHIVE_CONVERSATION,\n",
    "        ToolName.CREATE_MEMORY, ToolName.ARCHIVE_CONVERSATION,\n        ToolName.BRANCH_CONVERSATION,\n",
)

replace_once(
    "tests/test_unit.py",
    "        ArchiveConversationInput,\n        ChatCompletionInput,\n",
    "        ArchiveConversationInput,\n        BranchConversationInput,\n        ChatCompletionInput,\n",
)
replace_once(
    "tests/test_unit.py",
    "        ArchiveConversationInput, ListMemoriesInput, CreateMemoryInput,\n",
    "        ArchiveConversationInput, BranchConversationInput, ListMemoriesInput, CreateMemoryInput,\n",
)

branch_tests = r'''"""Focused tests for MCP branch_conversation."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from chatgpt_web2api.cdp_driver import BranchConversationError, CDPDriver
from chatgpt_web2api.chatgpt_dom import ChatGPTDom, _branch_snippet_matches


def _conversation(*turns):
    mapping = {}
    parent = None
    for index, (role, text, message_id) in enumerate(turns):
        node_id = message_id or f"node-{index}"
        mapping[node_id] = {
            "parent": parent,
            "message": {
                "id": message_id or node_id,
                "author": {"role": role},
                "content": {"parts": [text]},
            },
        }
        parent = node_id
    return {"id": "conv-source", "title": "Source Chat", "current_node": parent, "mapping": mapping}


@pytest.mark.asyncio
async def test_unique_match_uses_exact_backend_message_id_and_returns_temporary_note():
    d = CDPDriver(cdp_port=9222)
    d.get_conversation = AsyncMock(return_value=_conversation(
        ("user", "Question one", "u-1"),
        ("assistant", "A very unique assistant answer", "a-1"),
    ))
    d._assert_owned_tab_required = MagicMock()
    d.ensure_current_conversation = AsyncMock()
    d.branch_assistant_answer = AsyncMock(return_value={
        "id": "WEB:branch-1",
        "url": "https://chatgpt.com/c/WEB:branch-1",
        "temporary": True,
        "title": "Branch · Source Chat",
    })

    result = await d.branch_conversation("conv-source", "unique assistant", limit=28, offset=0)

    assert result["status"] == "branched"
    assert result["success"] is True
    assert result["source_message_id"] == "a-1"
    assert result["branched_conversation_id"] == "WEB:branch-1"
    assert result["temporary"] is True
    assert "automatically upgrade it to a permanent UUID" in result["note"]
    assert d._current_conv_id == "WEB:branch-1"
    d.ensure_current_conversation.assert_awaited_once_with("conv-source")
    d.branch_assistant_answer.assert_awaited_once_with(
        message_id="a-1",
        message_snippet="unique assistant",
        source_conversation_id="conv-source",
    )


@pytest.mark.asyncio
async def test_ambiguous_returns_every_candidate_with_preceding_user_prompt_and_does_not_mutate():
    d = CDPDriver(cdp_port=9222)
    d.get_conversation = AsyncMock(return_value=_conversation(
        ("user", "First prompt", "u-1"),
        ("assistant", "Shared phrase answer one", "a-1"),
        ("user", "Second prompt", "u-2"),
        ("assistant", "Shared phrase answer two", "a-2"),
    ))
    d.ensure_current_conversation = AsyncMock()
    d.branch_assistant_answer = AsyncMock()

    result = await d.branch_conversation("conv-source", "shared phrase")

    assert result["status"] == "ambiguous"
    assert result["success"] is False
    assert [m["message_id"] for m in result["matches"]] == ["a-1", "a-2"]
    assert [m["user_prompt"] for m in result["matches"]] == ["First prompt", "Second prompt"]
    d.ensure_current_conversation.assert_not_awaited()
    d.branch_assistant_answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_not_found_is_structured_and_does_not_mutate():
    d = CDPDriver(cdp_port=9222)
    d.get_conversation = AsyncMock(return_value=_conversation(
        ("user", "Prompt", "u-1"),
        ("assistant", "Different answer", "a-1"),
    ))
    d.ensure_current_conversation = AsyncMock()
    d.branch_assistant_answer = AsyncMock()

    result = await d.branch_conversation("conv-source", "missing text")

    assert result["status"] == "not_found"
    assert result["matches"] == []
    assert "No matching AI answer found" in result["message"]
    d.ensure_current_conversation.assert_not_awaited()
    d.branch_assistant_answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_pagination_applies_to_assistant_candidates_not_raw_messages():
    d = CDPDriver(cdp_port=9222)
    d.get_conversation = AsyncMock(return_value=_conversation(
        ("user", "P1", "u-1"),
        ("assistant", "target first", "a-1"),
        ("user", "P2", "u-2"),
        ("assistant", "target second", "a-2"),
    ))
    d._assert_owned_tab_required = MagicMock()
    d.ensure_current_conversation = AsyncMock()
    d.branch_assistant_answer = AsyncMock(return_value={
        "id": "WEB:x", "url": "https://chatgpt.com/c/WEB:x", "temporary": True,
    })

    result = await d.branch_conversation("conv-source", "target", offset=1, limit=1)
    assert result["status"] == "branched"
    assert result["source_message_id"] == "a-2"
    assert result["matches"][0]["user_prompt"] == "P2"


@pytest.mark.asyncio
async def test_matching_normalizes_case_unicode_and_whitespace():
    d = CDPDriver(cdp_port=9222)
    d.get_conversation = AsyncMock(return_value=_conversation(
        ("user", "P", "u-1"),
        ("assistant", "CAFÉ   has   spaces", "a-1"),
    ))
    d._assert_owned_tab_required = MagicMock()
    d.ensure_current_conversation = AsyncMock()
    d.branch_assistant_answer = AsyncMock(return_value={
        "id": "WEB:x", "url": "https://chatgpt.com/c/WEB:x", "temporary": True,
    })
    result = await d.branch_conversation("conv-source", "cafe\u0301 has spaces")
    assert result["status"] == "branched"


def test_url_matcher_accepts_encoded_web_temporary_id():
    assert CDPDriver._is_url_at_conversation(
        "https://chatgpt.com/c/WEB%3Aabc-123", "WEB:abc-123"
    ) is True


@pytest.mark.asyncio
async def test_backend_url_parser_decodes_web_temporary_id():
    d = CDPDriver(cdp_port=9222)
    d._js_strict = AsyncMock(return_value="https://chatgpt.com/c/WEB%3Aabc-123")
    assert await d._conversation_id_from_url() == "WEB:abc-123"


def test_branch_input_rejects_blank_fields():
    from chatgpt_web2api.mcp_server import BranchConversationInput

    with pytest.raises(Exception):
        BranchConversationInput(conversation_id="   ", message_snippet="answer")
    with pytest.raises(Exception):
        BranchConversationInput(conversation_id="abc", message_snippet="   ")


@pytest.mark.asyncio
async def test_do_branch_conversation_forwards_validated_arguments():
    from chatgpt_web2api.mcp_server import do_branch_conversation

    driver = MagicMock()
    driver.branch_conversation = AsyncMock(return_value={"status": "not_found"})
    result = await do_branch_conversation(driver, {
        "conversation_id": "abc",
        "message_snippet": "needle",
        "limit": 9,
        "offset": 3,
    })
    assert result == {"status": "not_found"}
    driver.branch_conversation.assert_awaited_once_with(
        conversation_id="abc", message_snippet="needle", limit=9, offset=3
    )


def test_rendered_snippet_match_ignores_markdown_punctuation():
    assert _branch_snippet_matches("This is **important**", "This is important") is True
    assert _branch_snippet_matches("wrong phrase", "This is important") is False


@pytest.mark.asyncio
async def test_dom_branch_happy_path_waits_through_transient_route_to_web_id():
    driver = MagicMock()
    driver._capture_selector_diagnostic = AsyncMock()
    driver._js_with_data_strict = AsyncMock(side_effect=[
        json.dumps({"found": True, "role": "assistant", "text": "This is important"}),
        json.dumps({"clicked": True, "state": "open", "controls": "menu-1"}),
        json.dumps({"clicked": True}),
    ])
    driver._js_strict = AsyncMock(side_effect=[
        json.dumps({
            "url": "https://chatgpt.com/branch/conv-source/a-1",
            "title": "Source Chat",
        }),
        json.dumps({
            "url": "https://chatgpt.com/c/WEB:branch-1",
            "title": "Branch · Source Chat",
        }),
    ])
    dom = ChatGPTDom(driver)

    result = await dom.branch_assistant_answer(
        message_id="a-1",
        message_snippet="**important**",
        source_conversation_id="conv-source",
    )

    assert result["id"] == "WEB:branch-1"
    assert result["temporary"] is True
    assert result["title"] == "Branch · Source Chat"


@pytest.mark.asyncio
async def test_dom_branch_text_mismatch_fails_before_more_menu_click():
    driver = MagicMock()
    driver._capture_selector_diagnostic = AsyncMock()
    driver._js_with_data_strict = AsyncMock(return_value=json.dumps({
        "found": True, "role": "assistant", "text": "Different rendered answer",
    }))
    dom = ChatGPTDom(driver)

    with pytest.raises(BranchConversationError, match="no longer matches"):
        await dom.branch_assistant_answer(
            message_id="a-1",
            message_snippet="unique needle",
            source_conversation_id="conv-source",
        )
    assert driver._js_with_data_strict.await_count == 1
    driver._capture_selector_diagnostic.assert_awaited()


@pytest.mark.asyncio
async def test_dom_branch_missing_more_button_fails_closed(monkeypatch):
    import chatgpt_web2api.chatgpt_dom as dom_mod

    ticks = iter([0.0, 0.1, 10.0, 10.1])
    monkeypatch.setattr(dom_mod.time, "monotonic", lambda: next(ticks, 10.1))
    driver = MagicMock()
    driver._capture_selector_diagnostic = AsyncMock()
    driver._js_with_data_strict = AsyncMock(side_effect=[
        json.dumps({"found": True, "role": "assistant", "text": "unique needle"}),
        json.dumps({"clicked": False, "reason": "more-missing"}),
    ])
    dom = ChatGPTDom(driver)

    with pytest.raises(BranchConversationError, match="More actions"):
        await dom.branch_assistant_answer(
            message_id="a-1",
            message_snippet="unique needle",
            source_conversation_id="conv-source",
        )
'''
write("tests/test_branch_conversation.py", branch_tests)

# ---------------------------------------------------------------------------
# Documentation / operator-facing configuration
# ---------------------------------------------------------------------------
replace_once(
    ".env.example",
    "# W2A_ENABLE_WRITE      - Expose mutating tools (create_project, update_project_instructions,\n#                         create_memory, archive_conversation). Hidden by default.\n",
    "# W2A_ENABLE_WRITE      - Expose mutating tools (create_project, update_project_instructions,\n#                         create_memory, archive_conversation, branch_conversation). Hidden by default.\n",
)

replace_once(
    "README.md",
    "| `get_conversation` | Full message history |\n",
    "| `get_conversation` | Full message history |\n| `branch_conversation` | Branch from one uniquely matched assistant answer |\n",
)
replace_once(
    "README.md",
    "| **Write** (account mutation) | `create_project`, `update_project_instructions`, `create_memory`, `archive_conversation` | `W2A_ENABLE_WRITE=1` |\n",
    "| **Write** (account mutation) | `create_project`, `update_project_instructions`, `create_memory`, `archive_conversation`, `branch_conversation` | `W2A_ENABLE_WRITE=1` |\n",
)
replace_once(
    "README.md",
    "| **MCP server** | ✅ 16 tools | ❌ | ❌ | ❌ |\n",
    "| **MCP server** | ✅ 17 tools | ❌ | ❌ | ❌ |\n",
)
replace_once(
    "README.md",
    "list_conversations(limit=10)\narchive_conversation(conversation_id=\"xyz\", archive=True)\n",
    "list_conversations(limit=10)\nbranch_conversation(conversation_id=\"xyz\", message_snippet=\"unique assistant text\")\narchive_conversation(conversation_id=\"xyz\", archive=True)\n",
)

replace_once(
    "docs/api-reference.md",
    "| `archive_conversation` | `conversation_id`, `archive` | Confirmation |\n",
    "| `archive_conversation` | `conversation_id`, `archive` | Confirmation |\n| `branch_conversation` | `conversation_id`, `message_snippet`, `limit?`, `offset?` | Branched WEB:/UUID ID, or structured ambiguity/not-found result |\n",
)

replace_once(
    "src/chatgpt_web2api/guide.md",
    "| List recent chats | `list_conversations` | `limit` (default 28) |\n",
    "| List recent chats | `list_conversations` | `limit` (default 28) |\n| Branch from an assistant answer | `branch_conversation` | `conversation_id` + unique `message_snippet` |\n",
)
insert_before_once(
    "src/chatgpt_web2api/guide.md",
    "## Model Selection\n",
    '''### Branching a Conversation

Use `branch_conversation` when you want to split an existing chat from one
specific assistant answer without changing the source conversation:

```
branch_conversation(
    conversation_id="6a...",
    message_snippet="a unique part of the assistant answer"
)
```

If several assistant answers contain the snippet, the tool creates nothing and
returns every matching candidate with its preceding user prompt. A successful
branch normally returns a temporary `WEB:<uuid>` ID. Pass that ID directly to
`chat_completion` for the first message; ChatGPT then upgrades the live branch
to a normal permanent UUID, which `chat_completion` returns.

''',
)

replace_once(
    "CHANGELOG.md",
    "### Added\n",
    "### Added\n- **`branch_conversation` MCP tool (17th)** — branches from one uniquely matched assistant answer using ChatGPT's real More actions → Branch in new chat UI. Backend message UUIDs map to DOM `data-message-id` for fail-closed targeting; duplicate snippets return structured candidates instead of guessing. Successful branches return the temporary `WEB:` ID/URL and reuse the existing first-send URL reconciliation to upgrade to a permanent UUID. Write-gated under `W2A_ENABLE_WRITE=1`.\n",
)
replace_once(
    "CHANGELOG.md",
    "- The tool surface is now **16 tools** (was 15): added `delete_project` (DELETE `/backend-api/gizmos/{id}`, gated under `W2A_ENABLE_DESTRUCTIVE=1`). The \"15 tools\" invariant across unit/deep/gating/integration tests updated to 16.\n",
    "- The tool surface is now **17 tools**: `branch_conversation` adds a write-gated UI branch operation on top of the existing 16-tool surface. Unit/deep/gating/integration invariants are updated to 17.\n",
)

print("branch_conversation patch applied")
