# Implementation Plan ID 1 — MCP `branch_conversation` Tool

## Status

Planning only. This document does **not** implement the feature.

## Goal

Add a new MCP tool named `branch_conversation` that reproduces ChatGPT's **Branch in new chat** action for a specific assistant answer inside an existing conversation.

The tool must be safe against choosing the wrong answer: it may branch only when exactly one assistant answer matches the supplied snippet. If zero or multiple answers match, it must stop without clicking the branch action.

---

## 1. Current Architecture Findings to Reuse

The current MCP implementation already provides most of the infrastructure this feature needs. The implementation should reuse these paths instead of duplicating them.

### Current MCP tool architecture

`src/chatgpt_web2api/mcp_server.py` currently has 16 tools and uses the following pattern for each tool:

1. Pydantic input model.
2. `ToolName` enum entry.
3. JSON output schema.
4. Declarative `mcp_types.Tool` definition in `_build_tools()`.
5. Access-gating classification.
6. Mutation-lock classification when the tool changes browser/account state.
7. A pure-ish `do_*` business function.
8. Tool routing in both singleton and session-pooled MCP execution paths.
9. Shared result formatting and exception mapping.
10. Unit, gating, integration, and optional E2E tests.

The new tool must follow this complete pattern. It must work in both normal singleton MCP mode and the session-affine MCP driver pool.

### Existing `list_conversations`

The current `list_conversations` input already uses:

- `limit` default `28`
- `offset` default `0`

Its current output already includes the useful discovery metadata discussed for this feature:

- `id`
- `title`
- `update_time`
- `gizmo_id`

No new conversation-list implementation should be created for the branching feature. Agents can continue using `list_conversations` when they need to discover a `conversation_id`.

### Existing `get_conversation`

`driver.get_conversation(conversation_id)` already fetches the complete ChatGPT conversation mapping from `/backend-api/conversation/{id}`.

The public MCP `get_conversation` business function then walks backward from `current_node`, follows parent links, reverses the chain, filters to user/assistant text, and applies message pagination.

The new branch tool should reuse the same raw conversation mapping and active-branch traversal idea, but it must retain enough information to identify a particular assistant answer and its preceding user prompt.

### Existing conversation navigation

`CDPDriver.navigate_conversation(conversation_id)` already:

- navigates to `https://chatgpt.com/c/{conversation_id}`;
- verifies the real live URL;
- waits for the page/app shell/composer to become ready;
- sets `_current_conv_id` only after verified success;
- fails closed when navigation lands in the wrong place.

`CDPDriver.ensure_current_conversation(conversation_id)` is even better for this new tool because it:

- does nothing when the requested conversation is already open;
- otherwise calls the verified navigation path;
- rechecks the live URL afterward;
- refuses to continue on an unknown/wrong conversation.

**Decision:** `branch_conversation` should use `ensure_current_conversation(conversation_id)` before any DOM mutation. Do not duplicate the navigation code from `chat_completion`.

### Existing DOM boundary

`chatgpt_dom.py` is the canonical ChatGPT page DOM-interaction layer. It already owns composer selectors, typing, send-button interaction, rate-limit popup dismissal, and selector diagnostics.

The Three Dots + **Branch in new chat** clicking behavior belongs in `ChatGPTDom`, with a thin delegator on `CDPDriver`, following the existing extraction/interception seam.

### Existing temporary-to-permanent behavior that may already solve part of the requirement

After a successful send, `CDPDriver.send_and_stream` reads the current `/c/{id}` URL and replaces `_current_conv_id` with the ID found in the live URL.

That means if ChatGPT opens a branch as `WEB:...`, and the first message changes the browser URL to a permanent UUID, the existing send completion path may already capture the new permanent UUID automatically.

**Decision:** do not build a second independent temporary-ID lifecycle unless live tests prove it is necessary. First verify that the existing URL resolution + `_current_conv_id` update naturally performs the `WEB:` → UUID upgrade.

---

## 2. New MCP Tool Contract

### Tool name

`branch_conversation`

### Inputs

Create `BranchConversationInput` with:

- `conversation_id: str` — required.
- `message_snippet: str` — required and must not be empty/whitespace.
- `limit: int = 28` — optional; positive bounded integer.
- `offset: int = 0` — optional; non-negative integer.

### Pagination meaning

For this tool, `limit` and `offset` should paginate the **assistant answers eligible to be scanned**, because the operation searches AI answers rather than conversations.

The full active conversation chain should first be reconstructed so each assistant answer keeps its preceding user prompt. Pagination is then applied to the assistant-answer candidates before snippet matching.

This avoids breaking a user-prompt/assistant-answer pair at a pagination boundary.

### Matching semantics

Use deterministic substring matching, not fuzzy matching.

Before comparison:

- strip leading/trailing whitespace;
- normalize Unicode consistently;
- normalize repeated/DOM-style whitespace;
- compare case-insensitively using a safe case-fold operation.

Preserve the original unmodified assistant answer text in returned results.

Do not silently choose the “closest” answer.

---

## 3. Candidate Extraction from the Conversation Mapping

Add high-level branching logic in `cdp_driver.py`, while keeping backend fetches in the existing backend layer.

### Active-chain traversal

1. Call the existing `get_conversation(conversation_id)`.
2. Read `mapping` and `current_node`.
3. Walk parent links from `current_node` to the root.
4. Reverse to chronological order.
5. Ignore system/internal-only entries.
6. Build visible user/assistant message records.
7. For each visible assistant answer, associate the nearest preceding visible user prompt.

The matching layer must avoid treating internal assistant records such as empty reasoning recap/thought nodes as branchable AI answers.

For each candidate retain internal targeting information needed later, such as:

- assistant answer text;
- preceding user prompt text;
- assistant answer ordinal among visible assistant turns;
- backend message/node ID when available.

The internal ID/ordinal does not have to be exposed publicly unless it proves useful for diagnostics.

---

## 4. The Three Required Outcomes

### A. Exactly one match — branch

When exactly one assistant answer matches:

1. Call `ensure_current_conversation(conversation_id)`.
2. Locate and verify the same assistant answer in the live DOM.
3. Scroll that assistant turn into view if required.
4. Open its Three Dots / More-actions menu.
5. Click the exact **Branch in new chat** menu item.
6. Wait for ChatGPT to navigate to the new branch.
7. Read `location.href` from the live tab.
8. Parse the newly-created conversation ID.
9. Return the new ID, URL, source conversation information, and lifecycle note.

Required lifecycle note in the successful output:

> This is a temporary link. Once you send your first message to this temporary conversation ID, ChatGPT will automatically upgrade it to a permanent UUID.

If the returned ID does not start with `WEB:` because ChatGPT changed behavior or immediately creates a permanent UUID, return the actual observed ID and clearly report whether it is temporary.

### B. Two or more matches — ambiguity

Do **not** navigate/click Branch after ambiguity is known.

Return a structured ambiguity result containing every matching candidate with at least:

- `user_prompt`
- `assistant_answer`

Optionally include an ordinal/candidate number for readability.

The result must tell the caller to provide a more-specific `message_snippet`.

### C. Zero matches — not found

Do not branch and do not mutate the browser/account.

Return a structured result/error containing:

`No matching AI answer found`

Also return the source `conversation_id`, requested snippet, `limit`, and `offset` so the agent can reason about whether it should retry with different pagination or a different snippet.

---

## 5. Proposed Structured Output

Create a dedicated `BRANCH_CONVERSATION_OUTPUT` schema rather than reusing an unrelated status schema.

Recommended common fields:

- `status`: `"branched" | "ambiguous" | "not_found"`
- `source_conversation_id`
- `source_title`
- `message_snippet`
- `offset`
- `limit`
- `matches`: array

Each match item:

- `candidate`
- `user_prompt`
- `assistant_answer`

Successful branch fields:

- `branched_conversation_id`
- `url`
- `temporary`: boolean
- `note`

Ambiguous fields:

- all matching candidates in `matches`
- a clear explanatory `message`

Not-found fields:

- empty `matches`
- `message = "No matching AI answer found"`

The MCP result should contain useful human-readable text **and** `structuredContent`, not only an opaque success string.

---

## 6. Live DOM Discovery Before Writing the Click Logic

There is currently no Branch-menu helper in `chatgpt_dom.py`. Do not guess the final selectors from memory.

Before implementing the production helper, inspect a real currently-logged-in ChatGPT conversation through CDP and capture the current DOM shape for:

1. assistant message container;
2. Three Dots / More-actions button inside one assistant turn;
3. menu/dialog created after clicking it;
4. exact accessible label/text/role for **Branch in new chat**;
5. whether the action buttons require hover/focus to render;
6. what the browser URL becomes immediately after branching;
7. whether the temporary ID appears literally as `WEB:...` or URL-encoded.

Prefer stable semantic attributes (`data-testid`, `aria-label`, `role`, exact menu text) over generated CSS classes.

Do not use a page-global “first three dots” selector. The button search must be scoped to the already-verified target assistant turn.

---

## 7. `chatgpt_dom.py` Changes

Add a focused DOM helper, for example:

`branch_assistant_answer(...)`

Responsibilities:

1. Find visible assistant turns using the same established base selector already used by completion detection: `[data-message-author-role="assistant"]`.
2. Identify the intended assistant turn using the candidate ordinal/text information supplied by `CDPDriver`.
3. Re-read and normalize the visible answer text before clicking.
4. Fail closed if the live DOM no longer corresponds to the unique backend candidate.
5. Scroll into view.
6. Reveal action controls if hover/focus is necessary.
7. click the turn-scoped More/Three-Dots control.
8. click only the exact Branch menu action.
9. wait for a navigation/URL change.
10. return the observed branch URL.

If any selector/action cannot be verified, capture useful DOM diagnostics and raise a typed error rather than clicking a possibly-wrong control.

Add a thin `CDPDriver` delegator so test monkeypatching continues to work through the established driver seam.

---

## 8. `cdp_driver.py` Changes

Add the high-level orchestration method, for example:

`branch_conversation(conversation_id, message_snippet, limit=28, offset=0)`

Responsibilities:

1. fetch the source conversation using the existing backend method;
2. build the active message chain;
3. build assistant-answer candidates with preceding user prompts;
4. apply `offset`/`limit`;
5. perform normalized snippet matching;
6. return not-found/ambiguous results without DOM mutation;
7. for one match, call `ensure_current_conversation`;
8. ask the DOM layer to branch the verified assistant turn;
9. parse the resulting URL/conversation ID;
10. update `_current_conv_id` only after the branch landing is verified;
11. return the structured branch result.

### Temporary ID parsing

The URL parser must support a temporary `WEB:` ID exactly as ChatGPT exposes it.

If live discovery shows the colon is URL encoded (`WEB%3A...`), use URL decoding in the ID parser and URL matcher rather than adding special string hacks in multiple places.

---

## 9. Temporary `WEB:` → Permanent UUID Lifecycle

The expected caller flow is:

1. `branch_conversation(...)` returns `WEB:...` and the temporary URL.
2. The agent later calls `chat_completion` with that temporary ID as `conversation_id`.
3. `chat_completion` uses the existing verified conversation-navigation path.
4. The first send happens in the temporary branch.
5. ChatGPT changes the live URL to `/c/{permanent-uuid}`.
6. Existing `send_and_stream` URL reconciliation should update `_current_conv_id` to that UUID.
7. `chat_completion` should therefore return the permanent UUID in its existing `conversation_id` field.

Implementation should first test this existing behavior.

Only add new transition-specific code if one of these existing pieces rejects the temporary identifier:

- `navigate_conversation` URL construction;
- `_is_url_at_conversation` exact-path matching;
- `_conversation_id_from_url` parsing;
- the post-send URL reconciliation.

This keeps the feature small and reuses the existing lifecycle machinery.

---

## 10. `mcp_server.py` Integration

Add the new tool through every current MCP layer.

### Input and enum

- Add `BranchConversationInput`.
- Add `ToolName.BRANCH_CONVERSATION = "branch_conversation"`.

The full tool surface becomes **17 tools**.

### Tool definition

Add a rich MCP definition explaining:

- it branches a specific assistant response;
- `conversation_id` can be discovered with `list_conversations`;
- `message_snippet` is matched against assistant answers;
- duplicates cause a structured ambiguity response and no branch;
- successful branch IDs may initially be `WEB:` temporary IDs.

Recommended annotations:

- `readOnlyHint=False`
- `destructiveHint=False`
- `idempotentHint=False`
- `openWorldHint=False`

### Access gate

Recommended classification: **WRITE-gated** (`W2A_ENABLE_WRITE=1`).

Reason: branching creates a new conversation/account object, but it does not delete or irreversibly alter the source conversation, so it is a write mutation rather than a destructive operation.

### Mutation locking

Add `branch_conversation` to `_MUTATING_TOOLS`.

The branch operation navigates and clicks inside a real browser tab, so it must participate in the same singleton/per-target mutation locking as chat and other browser mutations.

### Business function

Add `do_branch_conversation(driver, args)` which:

- validates `BranchConversationInput`;
- calls `driver.branch_conversation(...)`;
- returns its structured result.

Do not reimplement matching/navigation in `mcp_server.py`.

### Routing

Register the handler in:

- `_build_tool_handler(...)` for session-pooled MCP mode;
- the singleton `call_tool` handler map.

This is important: adding only one path would make the tool work in one MCP mode and fail in the other.

### Result formatting

Extend `_format_tool_result` so branch results provide a concise human-readable text summary plus the complete structured result.

---

## 11. Tests

### A. Candidate/matching unit tests

Create focused tests for:

1. one unique assistant match;
2. zero matches;
3. two identical matching answers;
4. two answers that both contain a short snippet;
5. case/whitespace normalization;
6. empty snippet rejected by Pydantic;
7. correct pairing with the immediately preceding user prompt;
8. internal/system/reasoning nodes ignored;
9. `offset`/`limit` applied to assistant candidates correctly;
10. branch is never attempted on 0 or 2+ matches.

### B. Navigation reuse tests

Verify unique-match branching calls `ensure_current_conversation(conversation_id)`.

Test both:

- conversation already open → no redundant navigation;
- different conversation open → verified navigation occurs.

### C. DOM tests

Extend `tests/test_chatgpt_dom.py` or create a focused branch DOM test file.

Test:

1. correct assistant turn selected;
2. visible answer re-verification before click;
3. correct turn-scoped Three-Dots button clicked;
4. exact Branch menu item clicked;
5. missing More button fails closed;
6. missing Branch menu item fails closed;
7. DOM text no longer matches backend candidate → no click;
8. URL-change wait succeeds;
9. selector diagnostics run on failure.

### D. Temporary-ID tests

Test:

1. parser accepts `WEB:...`;
2. parser handles URL-encoded temporary IDs if live ChatGPT uses them;
3. `navigate_conversation("WEB:...")` can verify the temporary route;
4. after a simulated first send changes the live URL to a UUID, `_current_conv_id` becomes that UUID;
5. `do_chat_completion` returns the permanent UUID after the transition.

### E. MCP business/protocol tests

Update `tests/test_business.py` with `do_branch_conversation` cases.

Update protocol integration tests to prove:

- tool appears when the write gate is enabled;
- tool is hidden/refused without the write gate;
- unique result returns structured branch output;
- ambiguity returns candidates instead of branching;
- pooled and singleton tool routing both reach the same business logic.

### F. Tool-count/gating invariants

The repository currently has several tests/comments pinned to the 16-tool surface.

Update all affected invariants from 16 → 17, including at minimum:

- `tests/test_unit.py`
- `tests/test_deep.py`
- `tests/test_gating.py`
- `tests/test_integration.py`

Also update the exact default/write-gated visible-name sets so the new tool is classified correctly.

### G. Opt-in live E2E

Add a live E2E scenario against a real authenticated ChatGPT account:

1. create/source a test conversation with a unique known assistant marker;
2. call `branch_conversation` through the real MCP protocol;
3. verify the browser moved to a new branch and the returned URL/ID matches the live page;
4. if the ID is `WEB:...`, call `chat_completion` using that temporary ID;
5. verify the returned ID becomes a normal permanent UUID after the first message;
6. register the permanent test conversation in the existing `e2e_created` cleanup registry.

Also add a duplicate-answer E2E or high-fidelity mocked test proving the tool does not branch when more than one candidate matches.

---

## 12. Documentation Updates During Implementation

After code/tests pass, update the documentation that describes the MCP surface:

- `README.md`
- `src/chatgpt_web2api/guide.md`
- `docs/api-reference.md`
- `CHANGELOG.md` under Unreleased
- `.env.example` write-gate comments

Update all stale “16 tools” references to 17 where they represent the current surface.

Document the new workflow:

1. `list_conversations` → discover ID/title/update time.
2. `branch_conversation` → select source answer by snippet.
3. resolve ambiguity if returned.
4. receive temporary branch ID.
5. `chat_completion(conversation_id="WEB:...")` → first message.
6. receive permanent UUID.

---

## 13. Installing the Updated MCP on the Computer

No separate MCP package should be invented. The MCP executable is already provided by the package through the existing `chatgpt-web2api-mcp = chatgpt_web2api.mcp_server:main` entry point in `pyproject.toml`.

After the feature is implemented in this fork, update the installed package from the fork/local checkout.

### Development/local checkout

From the repository folder:

```bash
pip install -e ".[dev]"
```

This makes the installed `chatgpt-web2api-mcp` executable use the modified source directly.

### Install directly from the fork

```bash
pip install --upgrade --force-reinstall git+https://github.com/Mahdi1910/ChatGPT-Web2API.git
```

### Runtime

REST/Chrome still starts through the existing application, and MCP still uses the existing entry point/transport:

```bash
chatgpt-web2api
chatgpt-web2api-mcp --transport sse --port 8090
```

or use the repository's existing `chatgpt-web2api ensure` workflow where appropriate.

Because the proposed branch tool is WRITE-gated, run MCP with:

```bash
W2A_ENABLE_WRITE=1 chatgpt-web2api-mcp --transport sse --port 8090
```

On Windows, set the equivalent environment variable in the command/supervisor configuration.

After reinstalling/updating the package, restart the MCP server/client connection so the client refreshes `list_tools`. The new `branch_conversation` tool should then appear.

No new MCP client configuration format is required merely because a new tool was added; it uses the existing MCP server connection.

---

## 14. Verification Commands After Implementation

Run normal non-E2E validation first:

```bash
pytest -m "not e2e"
ruff check .
```

Then, only with an authenticated test account and explicit opt-in:

```bash
W2A_E2E_RUN=1 pytest -m e2e -v
```

For manual MCP verification:

1. start REST/Chrome;
2. start MCP with `W2A_ENABLE_WRITE=1`;
3. call `list_tools` and confirm `branch_conversation` appears;
4. call `list_conversations` and confirm `id`, `title`, `update_time`, `gizmo_id` output;
5. call `branch_conversation` with a unique snippet;
6. verify the returned URL is exactly the browser's new branch URL;
7. send the first follow-up using the returned temporary ID;
8. confirm `chat_completion` returns the permanent UUID.

---

## 15. Acceptance Criteria

The implementation is complete only when all of these are true:

- [ ] `branch_conversation` is exposed as the 17th MCP tool.
- [ ] It accepts exactly the requested `conversation_id`, `message_snippet`, `limit`, and `offset` contract.
- [ ] It reuses existing conversation fetching and verified navigation code.
- [ ] It automatically opens the requested conversation when it is not already open.
- [ ] It searches assistant answers deterministically.
- [ ] Zero matches causes no branch and returns a clear not-found result.
- [ ] Multiple matches cause no branch and return every candidate with preceding user prompt + matching answer.
- [ ] Exactly one match opens that assistant answer's Three-Dots menu and clicks **Branch in new chat**.
- [ ] DOM identity is re-verified before mutation so the wrong answer cannot be branched silently.
- [ ] Successful output returns the observed new conversation ID and URL.
- [ ] Successful temporary branches include the exact lifecycle note requested by the user.
- [ ] A returned `WEB:` temporary conversation can be passed to `chat_completion`.
- [ ] After the first message, ChatGPT's permanent UUID is captured and returned through the existing conversation-ID lifecycle.
- [ ] The tool obeys MCP write gating and mutation locks.
- [ ] It works in singleton and session-pooled MCP modes.
- [ ] Existing `list_conversations` continues to expose `id`, `title`, `update_time`, and `gizmo_id`.
- [ ] All non-E2E tests and lint pass.
- [ ] A real opt-in E2E branch + first-message upgrade succeeds.
- [ ] Documentation/tool-count references are updated from 16 to 17 where applicable.

---

## Files Expected to Change During Implementation

Primary implementation:

- `src/chatgpt_web2api/chatgpt_dom.py`
- `src/chatgpt_web2api/cdp_driver.py`
- `src/chatgpt_web2api/mcp_server.py`

Likely tests:

- new focused branch test file(s)
- `tests/test_business.py`
- `tests/test_chatgpt_dom.py`
- `tests/test_conversation_guard.py` or equivalent temporary-ID tests
- `tests/test_unit.py`
- `tests/test_deep.py`
- `tests/test_gating.py`
- `tests/test_integration.py`
- `tests/test_e2e_mcp.py` or a dedicated branch E2E file

Documentation/config comments:

- `README.md`
- `src/chatgpt_web2api/guide.md`
- `docs/api-reference.md`
- `CHANGELOG.md`
- `.env.example`

`backend_client.py` should not need a new branch endpoint because the actual branch operation is a ChatGPT UI/DOM action. It should change only if implementation discovers a genuinely reusable backend-read requirement that cannot be satisfied through the existing `get_conversation` method.
