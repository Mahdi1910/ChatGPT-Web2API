"""Focused tests for MCP branch_conversation."""

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

    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr(dom_mod.asyncio, "sleep", _no_sleep)
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
