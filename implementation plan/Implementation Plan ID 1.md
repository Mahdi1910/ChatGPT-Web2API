# Implementation Plan ID 1 — MCP `branch_conversation` Tool

## Status

Planning only. This document does **not** implement the feature.

## Revision Note

This plan was updated with live-captured ChatGPT DOM and URL-lifecycle information supplied after the first draft.

The most important new finding is that an assistant DOM message exposes `data-message-id`, and that value corresponds 1-to-1 with the backend conversation mapping node/message UUID. The implementation should therefore use the backend message/node UUID as the **primary bridge between API matching and DOM clicking**, instead of relying primarily on message ordinal or visible text.

The captured branch lifecycle is:

`source conversation` → click **More actions** → click **Branch in new chat** → transient `/branch/{source_conversation_id}/{message_id}` → final temporary `/c/WEB:<uuid>` → first prompt sent → permanent `/c/<uuid>`.

---

## Goal

Add a new MCP tool named `branch_conversation` that reproduces ChatGPT's **Branch in new chat** action for a specific assistant answer inside an existing conversation.

The tool must be safe against choosing the wrong answer: it may branch only when exactly one assistant answer matches the supplied snippet. If zero or multiple answers match, it must stop without clicking the branch action.

The new tool must reuse the repository's existing conversation-fetching, verified navigation, mutation locking, MCP routing, and post-send conversation-ID lifecycle wherever possible.

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
7. A `do_*` business function.
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

The new branch tool should reuse the same raw conversation mapping and active-branch traversal idea, but it must retain the backend node/message UUID for every assistant answer so the exact same answer can later be found in the DOM.

### Existing conversation navigation

`CDPDriver.navigate_conversation(conversation_id)` already:

- navigates to `https://chatgpt.com/c/{conversation_id}`;
- verifies the real live URL;
- waits for the page/app shell/composer to become ready;
- sets `_current_conv_id` only after verified success;
- fails closed when navigation lands in the wrong place.

`CDPDriver.ensure_current_conversation(conversation_id)` is the preferred reuse point because it:

- does nothing when the requested conversation is already open;
- otherwise calls the verified navigation path;
- rechecks the live URL afterward;
- refuses to continue on an unknown/wrong conversation.

**Decision:** `branch_conversation` should use `ensure_current_conversation(conversation_id)` before any DOM mutation. Do not duplicate the navigation code from `chat_completion`.

### Existing DOM boundary

`chatgpt_dom.py` is the canonical ChatGPT page DOM-interaction layer. It already owns composer selectors, typing, send-button interaction, rate-limit popup dismissal, and selector diagnostics.

The Three Dots + **Branch in new chat** clicking behavior belongs in `ChatGPTDom`, with a thin delegator on `CDPDriver`, following the existing extraction/interception seam.

### Existing temporary-to-permanent behavior

After a successful send, `CDPDriver.send_and_stream` reads the current `/c/{id}` URL and replaces `_current_conv_id` with the ID found in the live URL.

That means when a branch initially exists as `WEB:...`, and the first message changes the browser URL to a permanent UUID, the existing send completion path may already capture the new permanent UUID automatically.

**Decision:** do not build a second independent temporary-ID lifecycle unless tests prove it is necessary. First verify that the existing URL resolution + `_current_conv_id` update naturally performs the `WEB:` → UUID upgrade.

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

`limit` and `offset` are for scanning the assistant-answer candidates in the source conversation.

The implementation should first reconstruct the active visible conversation chain so each assistant answer keeps:

- its exact backend message/node UUID;
- its visible answer text;
- its preceding visible user prompt.

Then apply `offset`/`limit` to the assistant-answer candidates before snippet matching. This keeps a user-prompt/assistant-answer pair intact instead of cutting the pair at a raw-message pagination boundary.

### Matching semantics

Use deterministic substring matching, not fuzzy matching.

Before comparison:

- strip leading/trailing whitespace;
- normalize Unicode consistently;
- normalize repeated/editor-style whitespace;
- compare case-insensitively with `casefold()` or equivalent.

Preserve the original unmodified assistant answer text in returned results.

Do not silently choose the “closest” answer.

---

## 3. Candidate Extraction from the Conversation Mapping

Add high-level branching orchestration in `cdp_driver.py`, while continuing to use the existing backend fetch layer for `get_conversation`.

### Active-chain traversal

1. Call existing `get_conversation(conversation_id)`.
2. Read `mapping` and `current_node`.
3. Walk parent links from `current_node` to the root.
4. Reverse to chronological order.
5. Ignore system/internal-only entries.
6. Build visible user/assistant message records.
7. For every visible assistant answer, associate the nearest preceding visible user prompt.
8. Retain the exact backend node/message UUID for that assistant answer.

The matching layer must avoid treating internal assistant records such as empty reasoning recap/thought nodes as branchable AI answers.

### Candidate record

Internally, each branchable candidate should retain at least:

- `message_id` / backend node UUID;
- assistant answer text;
- preceding user prompt text;
- assistant ordinal among branchable answers, useful only as secondary diagnostics/fallback.

### Primary identity rule

The supplied live DOM capture shows:

`div[data-message-author-role="assistant"]` has `data-message-id="<uuid>"`, and that UUID corresponds 1-to-1 with the relevant backend mapping node/message UUID.

Therefore:

**Backend `message_id` / node UUID is the primary identity key.**

The intended path is:

`snippet match in backend mapping` → `unique candidate message_id` → `ensure source conversation open` → `find exact DOM assistant element by data-message-id` → `verify role/text` → click that turn's menu.

This is substantially safer than using only assistant ordinal or a second text search in the DOM.

---

## 4. The Three Required Outcomes

### A. Exactly one match — branch

When exactly one assistant answer matches:

1. Keep its exact backend `message_id`.
2. Call `ensure_current_conversation(conversation_id)`.
3. Locate the exact assistant DOM message using `data-message-id` and verify `data-message-author-role="assistant"`.
4. Re-read enough visible answer text from the DOM to confirm it still corresponds to the matched backend candidate.
5. Resolve the enclosing turn container if needed for action controls.
6. Scroll the turn into view if necessary.
7. Open that turn's **More actions** / Three-Dots menu.
8. Click the exact **Branch in new chat** menu item.
9. Observe the branch navigation lifecycle.
10. Wait for the final `/c/...` branch landing.
11. Parse and return the observed new branch ID and URL.

Required lifecycle note in successful temporary output:

> This is a temporary link. Once you send your first message to this temporary conversation ID, ChatGPT will automatically upgrade it to a permanent UUID.

If ChatGPT changes behavior and returns a permanent UUID immediately, return the actual observed ID and set `temporary=false` rather than incorrectly claiming it is temporary.

### B. Two or more matches — ambiguity

Do **not** navigate/click Branch after ambiguity is known.

Return every matching candidate with at least:

- `user_prompt`
- `assistant_answer`

Recommended additional diagnostic field:

- `message_id`

The tool must tell the caller to provide a more-specific `message_snippet`.

### C. Zero matches — not found

Do not branch and do not mutate the browser/account.

Return a structured result/error containing:

`No matching AI answer found`

Also return the source `conversation_id`, requested snippet, `limit`, and `offset` so the caller can decide whether to retry with different pagination or a different snippet.

---

## 5. Proposed Structured Output

Create a dedicated `BRANCH_CONVERSATION_OUTPUT` schema.

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
- `message_id`
- `user_prompt`
- `assistant_answer`

Successful branch fields:

- `source_message_id`
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

The MCP result should provide useful human-readable text **and** full `structuredContent`.

---

## 6. Captured DOM and URL Facts to Use

These are current observed selectors/behaviors supplied for the implementation plan. They are strong starting evidence, but because ChatGPT's DOM changes frequently, the implementation should still verify them against the live page during implementation/E2E.

### Assistant message

Observed base element:

```css
div[data-message-author-role="assistant"]
```

Important attribute:

```text
data-message-id="<exact assistant message UUID>"
```

This message UUID corresponds 1-to-1 with the backend conversation mapping node/message UUID.

### Turn container

Observed ancestor/container classes include:

- `agent-turn`
- `group/turn-messages`

These classes should be treated as secondary helpers only because generated/product CSS classes are less stable than semantic attributes.

### Three-Dots / More-actions button

Observed primary selector:

```css
button[aria-label="More actions"]
```

Fallback:

```css
button[aria-label*="More" i]
```

Useful attributes:

- `type="button"`
- `aria-label="More actions"`
- `aria-haspopup="menu"`
- `data-state="closed"` → `"open"` after click

The Radix-generated `id` is dynamic and must **not** be used as a stable selector.

The visual CSS classes and SVG sprite identifier must also not be treated as canonical selectors.

### Branch menu

Observed menu container:

```css
div[role="menu"]
```

and/or a Radix menu content element.

Observed branch item:

```html
<div role="menuitem">...<div class="truncate">Branch in new chat</div>...</div>
```

The canonical selection rule should be:

- find the currently open menu;
- inspect its `[role="menuitem"]` descendants;
- choose the one whose normalized visible text equals `Branch in new chat`.

**Important implementation note:** `:has-text("Branch in new chat")` is a Playwright selector extension, not standard browser CSS. This project drives the page with CDP + `Runtime.evaluate`, so production code must not pass `:has-text(...)` to `document.querySelector`. Use normal DOM iteration + text comparison instead.

### Observed branch URL lifecycle

Clicking the menu item causes:

1. transient route:

```text
https://chatgpt.com/branch/{original_conversation_id}/{message_id}
```

2. immediate redirect to temporary branch:

```text
https://chatgpt.com/c/WEB:<uuid>
```

Example:

```text
https://chatgpt.com/c/WEB:2f2b5fb8-6c16-4a8e-b807-201d5225baed
```

3. page title observed as:

```text
Branch · <Original Title>
```

4. the `WEB:<uuid>` remains temporary until the first prompt is sent in that branch;
5. after that first prompt, ChatGPT upgrades/navigates the branch to a normal permanent UUID.

### Navigation validation rule

The DOM helper should not consider the operation successful merely because the URL changed once.

It should tolerate the intermediate `/branch/{source}/{message}` route and wait until a final ChatGPT conversation route is observed:

```text
/c/<new-id>
```

The final ID must be different from the source conversation ID.

The title prefix `Branch ·` can be used as a secondary diagnostic signal, but URL/ID verification should remain authoritative.

---

## 7. `chatgpt_dom.py` Changes

Add a focused DOM helper, for example:

`branch_assistant_answer(message_id, expected_text, source_conversation_id)`

Responsibilities:

1. Find the exact assistant element whose `data-message-id` equals the matched backend `message_id`.
2. Verify it also has `data-message-author-role="assistant"`.
3. Re-read/normalize the visible assistant text and verify it still corresponds to `expected_text` or the requested unique snippet.
4. Fail closed if the DOM target cannot be proven to be the backend candidate.
5. Resolve/scoped-search within that assistant turn/turn container.
6. Scroll into view.
7. Reveal action controls if hover/focus is required.
8. Find the target turn's `button[aria-label="More actions"]`, with the case-insensitive More fallback only if necessary.
9. Click the button and verify the menu opened (`data-state="open"` and/or visible `role="menu"`).
10. Within the opened menu, find a `role="menuitem"` whose normalized visible text is exactly `Branch in new chat`.
11. Click only that exact menu item.
12. Wait through the optional/transient `/branch/{source_conversation_id}/{message_id}` route.
13. Continue waiting for the final `/c/<new-id>` route.
14. Return the observed final branch URL and ID information.

### Selector safety

Do not use a page-global “first three dots” selector.

The More-actions lookup must be scoped to the verified target assistant turn.

Do not rely on:

- Radix-generated element IDs;
- long CSS class strings;
- SVG sprite hashes;
- Playwright-only selector syntax.

### Data passing safety

Prefer passing `message_id`, expected text, and other values through the existing `_js_with_data_strict` / structured-data mechanism instead of interpolating arbitrary text directly into JavaScript selectors.

If CSS escaping is needed, use `CSS.escape(...)` inside the page or avoid selector interpolation by iterating assistant elements and comparing `getAttribute('data-message-id')` directly.

### Failure behavior

If any selector/action cannot be verified, capture useful DOM diagnostics and raise a typed error rather than clicking a possibly-wrong control.

Add a thin `CDPDriver` delegator so existing monkeypatch/interception conventions remain intact.

---

## 8. `cdp_driver.py` Changes

Add the high-level orchestration method, for example:

`branch_conversation(conversation_id, message_snippet, limit=28, offset=0)`

Responsibilities:

1. fetch the source conversation using the existing backend method;
2. build the active message chain;
3. build assistant-answer candidates with `message_id` + preceding user prompts;
4. apply `offset`/`limit`;
5. perform normalized snippet matching;
6. return not-found/ambiguous results without DOM mutation;
7. for one match, call `ensure_current_conversation(conversation_id)`;
8. pass the unique candidate's exact `message_id` and expected answer text to the DOM helper;
9. validate/parse the returned final `/c/...` URL;
10. set `_current_conv_id` to the observed temporary/permanent branch ID only after the final branch landing is verified;
11. return the structured branch result.

### Why `message_id` is preferred over ordinal

The first draft planned to use text/ordinal information to identify the same turn in the DOM. The captured `data-message-id` mapping makes that unnecessary as the primary path.

Use:

- backend message/node UUID → primary targeting;
- visible text/snippet → safety re-verification;
- ordinal → diagnostics/fallback only, not normal targeting.

### Temporary ID parsing

The URL parser must support literal `WEB:` identifiers.

The observed current route contains the literal colon, but URL parsing should still be robust if the browser later reports it encoded as `WEB%3A...`; decode the path segment centrally rather than adding special-case string replacements across several call sites.

---

## 9. Temporary `WEB:` → Permanent UUID Lifecycle

Expected caller flow:

1. `branch_conversation(...)` returns `WEB:...` and its temporary URL.
2. The agent calls existing `chat_completion` with that temporary ID as `conversation_id`.
3. `chat_completion` uses existing verified conversation navigation.
4. The first prompt is sent in the temporary branch.
5. ChatGPT changes the live route from `/c/WEB:...` to `/c/{permanent-uuid}`.
6. Existing `send_and_stream` URL reconciliation should update `_current_conv_id` to that permanent UUID.
7. `chat_completion` should return that permanent UUID through its existing `conversation_id` field.

Implementation must first test the existing behavior before adding lifecycle-specific code.

Specifically verify these existing pieces accept `WEB:`:

- `navigate_conversation` URL construction;
- `_is_url_at_conversation` exact-path matching;
- `_conversation_id_from_url` parsing;
- post-send URL reconciliation;
- `ChatCompletionInput.conversation_id` validation (currently plain string and expected to accept it).

Only add new transition-specific code if one of those pieces demonstrably rejects or mishandles the temporary ID.

---

## 10. `mcp_server.py` Integration

Add the new tool through every current MCP layer.

### Input and enum

- Add `BranchConversationInput`.
- Add `ToolName.BRANCH_CONVERSATION = "branch_conversation"`.

The full tool surface becomes **17 tools**.

### Tool definition

Add a rich MCP description explaining:

- it branches a specific assistant response;
- `conversation_id` can be discovered with `list_conversations`;
- `message_snippet` is matched against assistant answers;
- duplicate matches return structured candidates and do not branch;
- successful branch IDs are expected initially to be `WEB:` temporary IDs;
- the first later `chat_completion` on that temporary ID upgrades it to a permanent UUID.

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

The operation navigates and clicks inside a real browser tab, so it must use the same singleton/per-target mutation locking as other browser mutations.

### Business function

Add `do_branch_conversation(driver, args)` which:

- validates `BranchConversationInput`;
- calls `driver.branch_conversation(...)`;
- returns its structured result.

Do not reimplement matching/navigation in `mcp_server.py`.

### Routing

Register the handler in both:

- `_build_tool_handler(...)` for session-pooled MCP mode;
- singleton `call_tool` routing.

Adding only one route would make the tool fail in one MCP mode.

### Result formatting

Extend `_format_tool_result` so branch results provide a concise human-readable summary plus the complete structured result.

Recommended text behavior:

- `branched` → branch ID + URL + temporary lifecycle note;
- `ambiguous` → explain no branch occurred and candidates are in structured output;
- `not_found` → clear no-match message.

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
10. branch is never attempted on 0 or 2+ matches;
11. candidate retains the correct backend `message_id`.

### B. Backend-ID ↔ DOM-target tests

Add a high-priority regression test proving:

- backend unique candidate has `message_id = X`;
- the DOM helper is called with exactly `X`;
- another assistant turn with similar/equal visible text is never selected if its `data-message-id` differs.

This protects the central safety improvement from the captured DOM information.

### C. Navigation reuse tests

Verify unique-match branching calls `ensure_current_conversation(conversation_id)`.

Test both:

- conversation already open → no redundant navigation;
- different conversation open → verified navigation occurs.

### D. DOM tests

Extend `tests/test_chatgpt_dom.py` or create a focused branch DOM test file.

Test:

1. exact assistant selected by `data-message-id`;
2. assistant role verified;
3. visible answer re-verification before mutation;
4. target turn's `button[aria-label="More actions"]` selected;
5. fallback `aria-label*="More"` works when primary label changes;
6. unrelated More buttons are ignored;
7. menu-open state is verified;
8. exact `role="menuitem"` text `Branch in new chat` selected;
9. no Playwright-only `:has-text()` assumption;
10. missing More button fails closed;
11. missing Branch menu item fails closed;
12. DOM text no longer matches backend candidate → no click;
13. transient `/branch/{source}/{message}` is tolerated;
14. final `/c/WEB:...` URL is required before success;
15. selector diagnostics run on failure.

### E. Temporary-ID tests

Test:

1. parser accepts `WEB:...`;
2. parser handles `WEB%3A...` defensively;
3. `navigate_conversation("WEB:...")` can verify the temporary route;
4. `_is_url_at_conversation` handles a literal colon correctly;
5. `_conversation_id_from_url` returns `WEB:...` correctly;
6. after a simulated first send changes the live URL to a UUID, `_current_conv_id` becomes that UUID;
7. `do_chat_completion` returns the permanent UUID after the transition.

### F. Branch URL lifecycle tests

Test the observed sequence explicitly:

1. starting source URL `/c/{source}`;
2. click causes `/branch/{source}/{message_id}`;
3. final redirect becomes `/c/WEB:{uuid}`;
4. tool returns only after step 3;
5. source ID is not mistaken for result ID;
6. wrong-host navigation fails closed;
7. unrelated URL changes fail closed.

Page title `Branch · <Original Title>` may be asserted as a secondary signal, but tests must not make it the sole success criterion.

### G. MCP business/protocol tests

Update `tests/test_business.py` with `do_branch_conversation` cases.

Update protocol integration tests to prove:

- tool appears when write gate is enabled;
- tool is hidden/refused without write gate;
- unique result returns structured branch output;
- ambiguity returns candidates instead of branching;
- pooled and singleton routing both reach the same business logic.

### H. Tool-count/gating invariants

The repository currently has tests/comments pinned to the 16-tool surface.

Update affected invariants from 16 → 17, including at minimum:

- `tests/test_unit.py`
- `tests/test_deep.py`
- `tests/test_gating.py`
- `tests/test_integration.py`

Update exact default/write-gated visible-name sets so the new tool is classified correctly.

### I. Opt-in live E2E

Add a live E2E scenario against a real authenticated ChatGPT account:

1. create/source a test conversation with a unique known assistant marker;
2. fetch that conversation and record the matched backend assistant `message_id`;
3. verify the live DOM element uses the same `data-message-id`;
4. call `branch_conversation` through the real MCP protocol;
5. verify the browser reaches a new `/c/WEB:...` branch and the returned URL/ID match the live page;
6. optionally observe/log the transient `/branch/{source}/{message}` route without requiring it to remain visible long enough for every run;
7. call `chat_completion` using the temporary ID;
8. verify returned ID becomes a normal permanent UUID after the first message;
9. register the permanent test conversation in the existing `e2e_created` cleanup registry.

Also add a duplicate-answer E2E or high-fidelity mocked test proving the tool performs **no branch mutation** when more than one answer matches.

---

## 12. Documentation Updates During Implementation

After code/tests pass, update documentation that describes the MCP surface:

- `README.md`
- `src/chatgpt_web2api/guide.md`
- `docs/api-reference.md`
- `CHANGELOG.md` under Unreleased
- `.env.example` write-gate comments

Update stale “16 tools” references to 17 where they represent the current surface.

Document the intended workflow:

1. `list_conversations` → discover `id`, title, update time.
2. `branch_conversation` → select a source assistant answer by snippet.
3. if ambiguity is returned, choose a more-specific snippet and retry.
4. receive `WEB:...` temporary branch ID.
5. `chat_completion(conversation_id="WEB:...")` → send first branch message.
6. receive permanent UUID from the existing chat-completion result.

---

## 13. Installing the Updated MCP on the Computer

No separate MCP package should be created. The MCP executable is already provided by the package through:

```text
chatgpt-web2api-mcp = chatgpt_web2api.mcp_server:main
```

in `pyproject.toml`.

After implementation in this fork, update the installed package from the fork/local checkout.

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

REST/Chrome still starts through the existing application, and MCP still uses its existing entry point/transport:

```bash
chatgpt-web2api
chatgpt-web2api-mcp --transport sse --port 8090
```

or use the repository's existing `chatgpt-web2api ensure` workflow where appropriate.

Because the proposed branch tool is WRITE-gated, run MCP with the equivalent of:

```bash
W2A_ENABLE_WRITE=1 chatgpt-web2api-mcp --transport sse --port 8090
```

On Windows, set the equivalent environment variable in the command/supervisor configuration.

After reinstalling/updating, restart the MCP server/client connection so the client refreshes `list_tools`. No new MCP client protocol/configuration format is required just because a new tool was added.

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
6. confirm the backend-matched `message_id` equals the live DOM target's `data-message-id`;
7. verify the final returned URL is the browser's `/c/WEB:...` URL;
8. send the first follow-up using the returned temporary ID;
9. confirm `chat_completion` returns the permanent UUID.

---

## 15. Acceptance Criteria

The implementation is complete only when all of these are true:

- [ ] `branch_conversation` is exposed as the 17th MCP tool.
- [ ] It accepts exactly `conversation_id`, `message_snippet`, `limit`, and `offset` with the requested defaults.
- [ ] It reuses existing conversation fetching and verified navigation code.
- [ ] It automatically opens the requested conversation when it is not already open.
- [ ] It searches assistant answers deterministically.
- [ ] Backend candidate extraction preserves the exact assistant `message_id` / mapping UUID.
- [ ] The exact DOM target is selected using matching `data-message-id`, not merely message order.
- [ ] Visible role/text is re-verified before any branch click.
- [ ] Zero matches causes no branch and returns a clear not-found result.
- [ ] Multiple matches cause no branch and return every candidate with preceding user prompt + matching answer.
- [ ] Exactly one match opens that assistant answer's turn-scoped More-actions menu and clicks **Branch in new chat**.
- [ ] `button[aria-label="More actions"]` is the primary control selector, with a semantic fallback only.
- [ ] The Branch item is selected from `role="menuitem"` by normalized exact visible text, not Playwright-only selector syntax.
- [ ] The implementation tolerates the transient `/branch/{source_conversation_id}/{message_id}` route.
- [ ] Success is returned only after a final `/c/<new-id>` branch route is verified.
- [ ] Successful temporary output returns the observed `WEB:...` ID and URL.
- [ ] Successful temporary branches include the exact lifecycle note requested by the user.
- [ ] A returned `WEB:` temporary conversation can be passed directly to `chat_completion`.
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

`backend_client.py` should not need a new branch endpoint because the actual branch operation is a ChatGPT UI/DOM action. It should change only if implementation discovers a genuinely reusable backend-read requirement that cannot be satisfied through existing `get_conversation`.