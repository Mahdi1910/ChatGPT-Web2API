# Implementation Plan ID 2 — `branch_conversation` New-Tab Handoff and Temporary `WEB:` Conversation Lifecycle

## Status

**Planning only. Do not implement this document yet.**

This plan fixes the behavior discovered after Implementation Plan ID 1 was implemented and tested against the real ChatGPT UI.

The current `branch_conversation` tool successfully finds the correct assistant answer, opens its **More actions** menu, and clicks **Branch in new chat**. The mutation itself succeeds. The remaining failure is in the browser-target lifecycle after that click.

The key live finding is:

```text
source tab
https://chatgpt.com/c/<source-uuid>
        |
        | More actions -> Branch in new chat
        v
ChatGPT OPENS A NEW BROWSER TAB / CDP TARGET
https://chatgpt.com/c/WEB:<temporary-uuid>
```

The original source tab does **not** navigate away. The current implementation keeps polling `location.href` on the original source tab, so it waits 15 seconds, still sees `/c/<source-uuid>`, and incorrectly reports:

```text
branch did not reach a new /c/<id> URL within 15s
```

even though the branch was successfully created in another Chrome tab.

A second live finding is equally important:

- a newly created `WEB:<uuid>` branch is temporary;
- before the first new user message is sent into that branch, it does not behave like a normal persisted conversation;
- in observed testing it does **not** appear in normal `list_conversations` results;
- the normal backend conversation lookup/listing path therefore must not be used as proof that a new temporary branch exists;
- after the first prompt is sent in the temporary branch, ChatGPT promotes it to a normal permanent conversation UUID.

This plan therefore covers two connected problems:

1. **Detect and safely hand the `CDPDriver` from the source tab to the newly opened branch tab.**
2. **Route the first `chat_completion(conversation_id="WEB:...")` to that live temporary branch tab without depending on `list_conversations` or ordinary permanent-conversation navigation.**

The design must preserve the repository's existing tab-ownership, mutation-locking, session-affinity, registry, reconnect, identity-listener, and fail-closed guarantees.

---

# 1. Exact Current Failure

## 1.1 What currently works

The current implementation already correctly performs all of the following:

1. Fetches the source conversation mapping.
2. Walks the active branch from `current_node`.
3. Matches the requested `message_snippet` against assistant answers.
4. Refuses to branch on zero matches.
5. Refuses to branch on two or more matches.
6. Retains the backend assistant `message_id`.
7. Opens the requested source conversation with `ensure_current_conversation`.
8. Finds the exact live assistant DOM message by:

```css
div[data-message-author-role="assistant"][data-message-id="<backend-message-id>"]
```

9. Re-verifies the visible assistant text.
10. Finds that turn's local **More actions** button.
11. Opens the menu.
12. Finds the exact `role="menuitem"` whose normalized text is `Branch in new chat`.
13. Clicks it.

Do **not** redesign those matching/safety parts unless a test demonstrates a separate bug.

## 1.2 What currently fails

The current `ChatGPTDom.branch_assistant_answer()` performs the UI click and then continues polling:

```javascript
location.href
```

through the driver's current page websocket.

That websocket still belongs to the **source tab**.

The live UI actually does this:

```text
CDP target A (source)
/c/6a831f4c-...
       |
       | click Branch in new chat
       |
       +---------------------------> CDP target B (new tab)
                                     /c/WEB:e90ba83c-...

CDP target A remains:
/c/6a831f4c-...
```

The existing URL parser already accepts literal `WEB:` IDs and defensively accepts URL-encoded `WEB%3A` IDs. The bug is therefore **not** temporary-ID parsing.

The bug is **watching the wrong CDP target after the click**.

---

# 2. Required End-to-End Behavior

After this plan is implemented, the intended lifecycle is:

```text
branch_conversation(source_id, snippet)
        |
        v
match exact backend assistant message_id
        |
        v
ensure source conversation is open in current owned tab
        |
        v
snapshot browser page targets BEFORE branch click
        |
        v
click More actions -> Branch in new chat
        |
        v
observe Chrome browser-level targets
        |
        +---- if current source tab itself navigates in a future ChatGPT version:
        |       accept verified new /c/<id> on same target
        |
        +---- current observed behavior:
                detect NEW page target
                verify it was created by the branch action
                wait for its final ChatGPT conversation URL
                /c/WEB:<uuid>
        |
        v
transactionally hand CDPDriver control to branch target
        |
        v
set:
_current_conv_id = "WEB:<uuid>"
_target_id       = <new branch target>
        |
        v
return temporary branch ID + URL
        |
        v
caller sends:
chat_completion(conversation_id="WEB:<uuid>")
        |
        v
DO NOT use list_conversations
DO NOT navigate blindly to /c/WEB:<uuid>
        |
        v
verify/reuse the exact live temporary branch target
        |
        v
send first user message in that tab
        |
        v
ChatGPT promotes route:
/c/WEB:<uuid> -> /c/<permanent-uuid>
        |
        v
resolve permanent ID early enough for completion/reconciliation
        |
        v
_current_conv_id = permanent UUID
clear/migrate temporary target state
        |
        v
return permanent conversation_id from chat_completion
```

---

# 3. Architectural Findings That Must Be Preserved

## 3.1 `CDPDriver` currently owns one active page websocket

`src/chatgpt_web2api/cdp_driver.py` keeps these important fields on one driver:

- `_ws` — persistent websocket to one page target;
- `_reader_task` — sole receiver for that page websocket;
- `_pending` — request futures for that websocket;
- `_target_id` — active Chrome page target ID;
- `_owns_target` — whether this driver is allowed to close that target;
- `_current_conv_id` — conversation proven to be live on the active target;
- `_current_model` — active-model cache;
- `_identity_listener` — network-event listener attached to the active page target;
- `_tab_registry` — persistent lease/ownership record for the driver's owned target;
- `_heartbeat_task` — periodically heartbeats the current `_target_id` into the registry.

This means branch handling cannot simply return a new target ID while leaving the driver attached to the source websocket. If later `chat_completion` uses that same driver, DOM operations would still happen in the source tab.

The branch lifecycle therefore requires a real **page-target handoff**.

## 3.2 `CDPTransport` assumes the driver owns the active websocket state

`src/chatgpt_web2api/cdp_transport.py` reaches through the driver for:

- `driver._ws`;
- `driver._msg_id`;
- `driver._pending`;
- `driver._reader_task`;
- `driver.reconnect()`.

Do not create a second independent transport architecture for temporary branches.

The correct design is to update the driver's active page target/websocket transactionally so the existing transport continues to work unchanged after the handoff.

## 3.3 Owned tabs and `TabRegistry`

`src/chatgpt_web2api/tab_registry.py` persists one target per logical driver instance.

The registry supports:

- `record(target_id)`;
- `heartbeat(target_id)`;
- restart reclaim;
- `clear_if_owner(target_id)`.

A branch-created tab is opened because of this driver's explicit UI mutation. After the branch target is uniquely verified, it can be treated as a driver-owned target for lifecycle/cleanup purposes.

When the handoff succeeds, the registry must be updated to point at the **new branch target**, otherwise:

- the heartbeat could continue preserving the old source target;
- a process restart could reclaim the wrong tab;
- cleanup could close/retain the wrong page;
- per-target lock identity and ownership observability would diverge from reality.

## 3.4 Mutation locking

`src/chatgpt_web2api/lock_resolver.py` resolves mutation locking as:

- port-wide when parallel tabs are disabled;
- target-specific when `parallel_tabs=true` and the driver owns a target.

`branch_conversation` is already in `_MUTATING_TOOLS`.

The outer MCP mutation lock is acquired **before** the branch click using the source target ID. That is correct for the actual mutation being performed: the branch menu is clicked in the source tab.

The handoff to the newly created branch target occurs as part of the same operation. No chat send should occur on the new branch target inside `branch_conversation`, so there is no need to nest a second target-specific mutation lock while the source-target lock is held.

After `branch_conversation` returns, the next mutating MCP call will resolve its lock from the driver's new `_target_id`, therefore using the branch target's own lock.

Important: do **not** add a post-operation invariant that requires `_target_id` to equal the source lock key. Target change is intentional for this one tool.

The existing pre-operation drift check — "target changed while waiting for mutation lock" — must remain.

## 3.5 MCP session-affine driver pool

`src/chatgpt_web2api/mcp_driver_pool.py` gives each MCP session its own `CDPDriver` and owned tab when pool mode is enabled.

That is compatible with this design because the temporary branch state can live on the same session-affine driver.

The first follow-up on a temporary branch must be sent through the **same MCP session/driver** whenever pool mode is enabled.

Do not store a temporary branch target in global MCP state where another session could accidentally claim it.

## 3.6 `list_conversations` is not a temporary-branch registry

Do not change `list_conversations` into a synthetic list that mixes temporary tabs with persisted backend conversations.

Its current meaning should remain: conversations returned by ChatGPT's normal backend conversations endpoint.

A `WEB:` branch that has not received its first prompt can legitimately be absent from that list.

This is expected state, not evidence that branch creation failed.

---

# 4. Responsibility Split

The new behavior should keep clear layer ownership.

## `chatgpt_dom.py`

Own only the **DOM action**:

- verify exact assistant message;
- reveal turn actions;
- click More actions;
- verify menu;
- click `Branch in new chat`.

It should no longer be responsible for proving where the browser-level new tab landed.

## `cdp_driver.py`

Own the **browser target lifecycle**:

- snapshot page targets;
- detect same-target vs new-target branch behavior;
- correlate a new target with the branch action;
- wait for the final branch URL;
- attach the driver to the correct page target;
- update ownership/registry/listener/current conversation state;
- route temporary `WEB:` sends;
- reconcile temporary -> permanent ID promotion.

## `backend_client.py`

Keep normal backend fetch behavior.

Only adjust live-conversation-ID resolution if necessary so a stale temporary `_current_conv_id` does not hide the permanent UUID after the first send.

Do not make backend conversation listing responsible for temporary branch discovery.

## `mcp_server.py`

Keep business routing thin.

It should:

- continue exposing `branch_conversation`;
- make `chat_completion` use the driver's temporary-aware conversation-context helper instead of blindly calling normal permanent navigation for every explicit `conversation_id`;
- format any new typed/structured temporary-state failures clearly.

Do not implement target enumeration in MCP code.

---

# 5. Refactor `ChatGPTDom.branch_assistant_answer`

Current `ChatGPTDom.branch_assistant_answer()` combines two jobs:

1. safely click the branch action;
2. poll the current tab's URL for the new branch.

The second responsibility is now proven wrong for current ChatGPT behavior.

Refactor it so it ends after a verified menu-item click.

Recommended return shape:

```python
{
    "clicked": True,
    "message_id": message_id,
}
```

or another small internal record.

It must continue to do all safety checks from Implementation Plan ID 1:

1. exact `data-message-id` target;
2. assistant role verification;
3. snippet/rendered-text re-verification;
4. turn-scoped More-actions lookup;
5. menu-open verification;
6. exact normalized `Branch in new chat` text match;
7. fail closed when any control is missing;
8. selector diagnostics on failure;
9. no direct `/branch/...` navigation fallback;
10. no page-global arbitrary three-dot click.

Remove the current final 15-second loop that repeatedly asks the source page websocket for `location.href`.

Browser target/URL detection moves to `CDPDriver`.

---

# 6. Browser-Level Target Discovery Helpers in `cdp_driver.py`

Add focused Layer-2 helpers instead of duplicating `/json/list` parsing throughout the branch method.

## 6.1 Browser target snapshot helper

Add a helper conceptually like:

```python
async def _get_page_targets(self) -> dict[str, BrowserPageTarget]
```

The implementation may use browser-level:

```text
Target.getTargets
```

through existing `_browser_cdp`, or `/json/list` where websocket URLs are needed.

Recommended target record fields:

- `target_id`;
- `type`;
- `url`;
- `title`;
- `opener_id` when Chrome exposes it;
- `web_socket_debugger_url` when available/needed.

Prefer browser-level `Target.getTargets` for target identity/opener correlation and `/json/list` for resolving a page websocket URL by known target ID.

Do not identify targets only by title.

## 6.2 Exact target-websocket lookup

Generalize the existing owned-target lookup into a helper that can resolve any exact known page target:

```python
_find_target_ws(target_id: str) -> str | None
```

`_find_owned_tab_ws()` can remain as a compatibility wrapper if tests/external seams rely on it.

The branch code must never call the existing broad `_find_page_ws()` to choose the branch tab, because that helper is intentionally allowed to return an arbitrary suitable ChatGPT page and would violate branch safety.

## 6.3 Target URL parser

Centralize parsing of a target URL into a conversation ID using path segments and `urllib.parse.unquote`.

It must support:

```text
/c/WEB:e90...
/c/WEB%3Ae90...
/c/<permanent-uuid>
/g/<gizmo>/c/<id>   (where relevant to existing generic matching)
```

Do not require the ID to match a UUID regex. `WEB:` is intentionally not a normal UUID.

---

# 7. Capture Target Baseline Before Clicking Branch

In `CDPDriver.branch_conversation`, after unique backend matching and after the source conversation is verified open:

1. Record:
   - `source_target_id = self._target_id`;
   - `source_owns_target = self._owns_target`;
   - `source_conversation_id`;
   - live source URL;
   - current page-target IDs from the browser.
2. Require a valid active source target.
3. Call the DOM helper to perform the branch click.
4. Begin browser-level target observation immediately after the click.

The baseline is necessary so the driver can distinguish:

- the known source target;
- pre-existing unrelated ChatGPT tabs;
- genuinely new target(s) created by this branch action.

Do not scan all existing ChatGPT tabs after the click and choose whichever happens to contain `WEB:`. A user could already have another temporary branch tab open.

---

# 8. Detect the Branch Result Correctly

The detector should support both current behavior and a possible future return to same-tab navigation.

## 8.1 Outcome A — source tab itself navigates

During the observation window, if the original source target's URL becomes a valid new conversation route:

```text
/c/<new-id>
```

where `new-id != source_conversation_id`, accept that target after verification.

No target handoff is necessary because the driver is already attached to it.

This preserves compatibility with the behavior Implementation Plan ID 1 originally expected.

## 8.2 Outcome B — new tab / new target appears (current observed behavior)

Poll browser targets and compute:

```text
new target IDs = current page target IDs - pre-click page target IDs
```

Track only new `type="page"` targets.

A new target can briefly begin as:

- `about:blank`;
- the transient `/branch/{source}/{message}` route;
- another short loading URL;
- the final `/c/WEB:<uuid>` route.

Do not reject a newly created target solely because its very first observed URL is not final.

## 8.3 Correlation rules

Use multiple signals to correlate the new target to the branch action.

Strong signals:

1. Target was not present in the pre-click baseline.
2. Target's `openerId`, when available, equals `source_target_id`.
3. Target reaches exact transient route:

```text
/branch/{source_conversation_id}/{message_id}
```

4. Same target later reaches a final ChatGPT conversation route.

Final success requirement:

```text
host == chatgpt.com (or approved chatgpt subdomain handling consistent with current code)
AND
route contains /c/<new-id>
AND
new-id != source_conversation_id
```

Expected normal temporary case:

```text
new-id.startswith("WEB:")
```

but if ChatGPT someday creates a permanent ID immediately, accept the verified permanent ID and set `temporary=False`.

## 8.4 Multiple new targets

Never choose arbitrarily.

If multiple new targets appear:

- prefer an exact route-correlated target;
- use `openerId == source_target_id` as a strong filter when available;
- if two targets remain equally plausible, fail closed with a typed branch-target ambiguity error;
- include target IDs/URLs in diagnostics, but do not click Branch again automatically.

This is important because the branch mutation may already have succeeded. Retrying the click could create duplicate branches.

## 8.5 Timeout semantics

A timeout must distinguish:

```text
A. No evidence that a branch target was created.
B. A new branch target was observed, but it never reached a verified final conversation URL.
C. A verified branch URL was observed, but driver handoff failed.
```

These are operationally different states and should not all collapse to the old generic:

```text
branch_conversation_failed
```

At minimum, diagnostics must state whether a new target was ever observed.

---

# 9. Transactional Page-Target Handoff

Once a unique target has reached a verified final branch URL, the driver needs to transfer its active page connection.

Add a dedicated helper conceptually like:

```python
async def _switch_active_target(
    self,
    target_id: str,
    *,
    owns_target: bool,
    expected_conversation_id: str,
) -> None
```

This is not ordinary reconnect. It is an intentional, verified target migration.

## 9.1 State that must migrate

The handoff must correctly update/restart:

- `_target_id`;
- `_owns_target`;
- `_ws`;
- `_reader_task`;
- `_pending`;
- `_cdp_event_handlers` / identity-listener attachment;
- `_current_conv_id`;
- `_current_model` (clear or re-resolve; do not trust source-tab cache);
- `_tab_registry` target record;
- heartbeat target identity.

## 9.2 Identity listener

Before closing the old page websocket:

- detach the identity listener from the old target.

After the new page websocket + reader are running:

- re-attach the identity listener;
- re-enable the Network domain on the new target;
- verify listener readiness best-effort using the existing attach contract.

This is essential because the first message sent to the temporary branch should still benefit from UUID capture/turn correlation.

## 9.3 Pending CDP futures

The switch occurs inside an MCP operation serialized by the per-session `call_lock`, and after the branch DOM click has completed.

There should normally be no unrelated in-flight page CDP calls.

Still:

- do not silently leave futures from the old websocket unresolved;
- either require `_pending` to be empty at the handoff point or fail/cancel stale futures deterministically before switching;
- add a test proving the handoff does not leave a future waiting on a closed source websocket.

## 9.4 New branch target ownership

After unique correlation proves the target was created as a consequence of this driver's branch action, mark the branch target as owned by the driver:

```python
self._owns_target = True
```

That lets:

- parallel-target lock resolution continue to work;
- clean shutdown close the branch tab;
- registry reclaim work after process restart where applicable.

## 9.5 Registry update

After successful attachment/verification, overwrite this logical instance's registry entry with the branch target:

```text
instance_id -> new branch target_id
```

Do not leave the registry pointing at the source target.

The heartbeat loop reads `self._target_id` dynamically, so once state and registry are updated, later heartbeats should continue leasing the new target.

## 9.6 What to do with the old source tab

If `source_owns_target=True`, the old source tab was created/owned by this driver.

After the new branch target is successfully attached and all new state is committed, close the old source target with `Target.closeTarget` to avoid orphan-tab accumulation.

Important ordering:

- **never close the old source target before the branch target is verified and the driver is successfully attached to it.**

If the source tab was adopted (`source_owns_target=False`), leave it open because it belongs to the user/browser rather than this driver.

## 9.7 Rollback

Implement the handoff transactionally.

Capture enough source state to restore the source connection if attaching the new branch target fails.

Recommended failure behavior:

1. The branch target has already been verified, so do **not** pretend the branch click never happened.
2. Attempt to restore attachment to the source target if it is still live.
3. Do not automatically click Branch again.
4. Preserve/log the verified branch target ID + URL for diagnosis.
5. Return/map a distinct error such as `branch_created_handoff_failed` rather than a generic pre-click failure.

Do not destroy the verified new branch tab during rollback merely to make state look clean; that would discard a branch the user successfully created.

---

# 10. Temporary Conversation State on `CDPDriver`

Add explicit driver-local state for temporary branches.

Recommended internal record:

```python
@dataclass
class TemporaryConversationTarget:
    conversation_id: str       # WEB:...
    target_id: str
    url: str
    source_conversation_id: str | None
    created_at: float
```

Recommended driver field:

```python
self._temporary_conversation_targets: dict[str, TemporaryConversationTarget]
```

Even if the normal workflow has only one active temporary branch, explicit state makes the contract testable and avoids inferring persistence from backend lists.

When branch handoff succeeds:

```text
WEB:<id> -> exact branch target_id
```

must be registered.

Also set:

```python
self._current_conv_id = "WEB:<id>"
```

only after the new target's live URL has been verified.

---

# 11. Temporary-Aware Conversation Routing

The current MCP `do_chat_completion` does this for every explicit conversation ID:

```python
await driver.navigate_conversation(validated.conversation_id)
```

That is wrong for an unpersisted temporary branch.

Introduce one shared driver-level context resolver, conceptually:

```python
async def ensure_conversation_for_send(self, conversation_id: str) -> None
```

Behavior:

## Permanent ID

For IDs that do not begin with `WEB:`:

- preserve existing behavior;
- use existing verified `ensure_current_conversation` / `navigate_conversation` semantics.

## Temporary `WEB:` ID

Do **not** use `list_conversations`.

Do **not** assume backend `get_conversation(WEB:...)` exists.

Do **not** blindly call `Page.navigate("https://chatgpt.com/c/WEB:...")` as the primary path.

Instead:

1. Look up the temporary ID in `_temporary_conversation_targets`.
2. Verify the mapped target is still live.
3. If the driver is already attached to that target, verify its live URL is exactly the requested temporary conversation.
4. If the driver is not attached but the mapped target is legitimately recoverable within the same driver/session lifecycle, switch to that exact target rather than navigating an arbitrary tab.
5. If mapping is absent, allow a narrow self-heal only when the **currently active target's live URL itself** exactly matches `/c/WEB:<requested-id>`; rebuild the in-memory mapping from that proven state.
6. Optionally, a browser-target scan may recover an exact `/c/WEB:<requested-id>` only when ownership/session safety can be proven. Do not steal a target leased by another live driver/session.
7. If the temporary target cannot be proven, fail closed with a typed error such as:

```text
temporary_conversation_unavailable
```

and explain that temporary `WEB:` conversations are live-tab state until the first prompt persists them.

This prevents the dangerous fallback:

```text
WEB ID absent from list -> navigate somewhere -> type message into wrong conversation
```

---

# 12. Protect an Unpersisted Temporary Branch from Accidental Navigation

A temporary branch can be lost if the only live tab containing it is navigated somewhere else before its first message.

Therefore, while the active driver is on a registered `WEB:` branch that has not yet been promoted:

- read-only backend tools may continue normally if they do not navigate the browser;
- `chat_completion` for the **same** `WEB:` ID is allowed and is the intended next operation;
- an unrelated operation that would navigate the active tab to another conversation/new chat must not silently destroy the temporary branch.

For the initial implementation, use a fail-closed rule rather than introducing a broad multi-target tab manager:

```text
If active conversation is a pending WEB: branch and a mutating operation wants
another browser context, return a clear temporary-branch-pending error instead
of navigating away.
```

This keeps the scope focused and prevents data loss.

The expected agent workflow is intentionally:

```text
branch_conversation -> chat_completion(WEB:...) -> permanent UUID
```

After promotion, normal navigation can resume.

If a future feature needs multiple simultaneously pending temporary branches, design that separately as a real multi-target driver feature rather than silently expanding this patch.

---

# 13. The First Send: `WEB:` -> Permanent UUID Promotion

The first send is more subtle than only changing `_current_conv_id` at the end.

## 13.1 Existing useful behavior

At the end of `send_and_stream`, current code reads `window.location.href`, extracts the `/c/<id>` segment, and sets:

```python
self._current_conv_id = conv_id
```

That will correctly parse a permanent UUID after promotion.

## 13.2 Existing hidden risk during generation

`BackendClient._get_live_conversation_id_best_effort()` currently prefers:

```python
self._driver._current_conv_id
```

before reading the live URL.

For a temporary branch, `_current_conv_id` begins as:

```text
WEB:<temporary-id>
```

After the first send, ChatGPT may change the live URL to the permanent UUID **before generation has completed**, while `_current_conv_id` is still the stale `WEB:` value until the terminal code runs.

If completion/reconciliation asks for the live conversation ID during this interval, it can incorrectly keep returning `WEB:` even though the page is already `/c/<permanent-uuid>`.

That matters because backend fetches against the temporary ID may return 404/not-yet-persisted behavior.

## 13.3 Required temporary-aware live-ID resolution

Adjust the live-ID resolver so temporary state does not mask a promoted permanent URL.

Recommended semantics:

```text
if _current_conv_id starts with "WEB:":
    first inspect live location.href
    if live /c/<id> exists:
        return that live id (WEB or permanent)
    otherwise fall back cautiously to _current_conv_id
else:
    preserve existing cheap current-id-first behavior
```

Do not globally reverse the resolver order for every conversation unless necessary; permanent conversations currently benefit from the cheap cached path.

Add tests proving:

```text
_current_conv_id = WEB:temp
live URL = /c/permanent
=> best-effort resolver returns permanent
```

This lets completion/reconciliation follow the promoted conversation while generation is still running.

## 13.4 Pre-send backend anchor

`_capture_pre_send_fallback_anchor()` may attempt a backend projection using the temporary ID before it has been persisted.

Current code already degrades a transient backend failure into `degraded_existing` mode using sent text + wall-clock freshness.

Preserve that fallback.

Add a specific regression test for a temporary branch proving a backend 404/fetch failure before first send does not abort the send merely because the temporary branch is absent from normal backend storage.

Auth errors must continue to propagate rather than being degraded.

## 13.5 Identity listener

The identity listener is armed before send with the driver's current conversation/target identity.

Because the first temporary-branch send can trigger a route promotion, test the listener path with:

- `conversation_id=WEB:...` at arm time;
- same branch target ID;
- network request occurring during promotion;
- captured user-message UUID when possible.

If current listener correlation rejects the request specifically because the conversation changes from `WEB:` to permanent during the send, adjust only that correlation rule. Do not disable identity capture for all temporary branches without evidence.

---

# 14. Promotion State Cleanup

When a send that began from a registered temporary branch resolves to a different non-`WEB:` permanent ID:

1. Confirm the live URL is a valid ChatGPT `/c/<permanent-id>` route.
2. Set `_current_conv_id = permanent_id`.
3. Remove the old `WEB:` entry from `_temporary_conversation_targets`.
4. Keep the current target ID — the tab itself has become the normal permanent conversation tab.
5. Continue registry heartbeat on the same target.
6. Return the permanent UUID from `chat_completion` through its existing `conversation_id` output.

Optional but useful session-local compatibility:

Maintain a small alias map:

```python
WEB:<old-id> -> <permanent-id>
```

for the life of the driver, so a caller that accidentally uses the just-promoted temporary ID one more time can receive a clear "promoted to" response or be routed safely.

If implemented, the alias must be session-local and bounded; it is not a replacement for normal backend identity.

Do not return the temporary ID after the first send when the live page is already permanent.

---

# 15. Reconnect and Restart Behavior

Temporary branches are fundamentally more fragile than persisted conversations.

## 15.1 Same process, socket reconnect

If the active target still exists and `_target_id` is the temporary branch target, existing reconnect should re-find that exact target and restore the page websocket.

After reconnect:

- re-read the live URL;
- if it is `/c/WEB:...`, rebuild/confirm the temporary mapping;
- if it has already become permanent, update current state accordingly.

## 15.2 Target disappeared before first send

If the temporary branch target is gone, do not create a fresh owned tab and pretend it represents the same temporary branch.

Raise a typed temporary-conversation-unavailable error.

A new empty ChatGPT tab is not equivalent to the ephemeral branch state.

## 15.3 Process restart and `TabRegistry`

In singleton mode, after branch handoff the registry should point to the branch target. A restarted process with the same logical instance can therefore reclaim that target.

On connect/reclaim, inspect its live URL. If it is a `WEB:` conversation, reconstruct the temporary mapping from the proven current target.

## 15.4 MCP pooled SSE reconnect limitation

Pool session identities are derived from the MCP transport session. A brand-new SSE connection can produce a new session key and therefore a different pool slot/instance identity.

Do not silently steal a `WEB:` target from another live pool slot.

Document the first implementation contract:

> In session-pool mode, a temporary branch must be promoted using the same MCP session that created it.

If the transport reconnects before promotion and ownership cannot be proven, fail closed.

Solving cross-session transfer of ephemeral targets should be a separate design, because it requires registry-level ownership transfer semantics.

---

# 16. Error Model

Add or refine typed errors so the caller can distinguish where failure occurred.

Recommended types/codes:

## `BranchTargetNotFoundError`

The DOM click succeeded or was attempted, but no correlatable branch target appeared before timeout.

## `BranchTargetAmbiguousError`

Multiple new targets are plausible and the driver refuses to choose one.

## `BranchTargetHandoffError`

A verified branch target/URL exists, but switching the driver's active CDP connection to it failed.

Important: this means the account mutation may already have happened. Error text should warn callers **not to automatically retry the branch click**.

## `TemporaryConversationUnavailableError`

A caller supplied `WEB:...`, but the originating live temporary target cannot be proven/reached safely.

## `TemporaryConversationPendingError`

An unrelated mutation would navigate away from a pending temporary branch before promotion.

Map these through existing MCP exception formatting with stable machine-readable suffixes/codes where the current architecture supports them.

Do not label a verified-created branch as a simple "branch failed" just because target handoff later failed.

---

# 17. `mcp_server.py` Changes

## 17.1 `do_chat_completion`

Replace the explicit-conversation branch:

```python
await driver.navigate_conversation(validated.conversation_id)
```

with the new temporary-aware helper:

```python
await driver.ensure_conversation_for_send(validated.conversation_id)
```

For permanent IDs this delegates to the current verified navigation behavior.

For `WEB:` IDs it uses live temporary target state.

## 17.2 Auto-continue

Existing auto-continue uses `ensure_current_conversation(_current_conv_id)`.

If `_current_conv_id` can be `WEB:...`, auto-continue must use the same temporary-aware send-context helper, otherwise a call that omits `conversation_id` immediately after branching could accidentally invoke permanent-conversation navigation semantics.

Recommended rule:

```text
explicit conversation_id -> ensure_conversation_for_send(id)
auto-current conversation -> ensure_conversation_for_send(_current_conv_id)
```

## 17.3 `branch_conversation` output

Normal successful output remains:

- `branched_conversation_id`;
- `url`;
- `temporary`;
- lifecycle note.

Optionally add internal/diagnostic fields if appropriate:

- `opened_in_new_tab: true|false`;
- `branch_target_id` (consider whether exposing raw CDP IDs publicly is desirable; it can remain log-only if not needed by callers).

The public contract does not need the caller to manage CDP target IDs.

## 17.4 Tool description

Update the `branch_conversation` MCP description to state clearly:

- the returned `WEB:` ID is temporary;
- it may not appear in `list_conversations` yet;
- the caller should send the next prompt with `chat_completion(conversation_id="WEB:...")`;
- that first send promotes the branch to a permanent UUID;
- the same MCP session should be used until promotion when session-pool mode is enabled.

---

# 18. REST API Scope

The branch tool currently lives in MCP, and the temporary target belongs to the driver that created it.

In deployments where REST and MCP are separate processes/drivers (as in the current second-laptop setup), a temporary branch created by MCP is **not automatically routable by the REST process**.

Therefore:

- do not promise cross-process `WEB:` portability;
- do not add a fake backend lookup to make REST appear to support it;
- document that the first promotion send should occur through the same MCP driver/session that created the branch;
- once a permanent UUID is returned, normal REST/OpenAI-compatible conversation behavior may use the persisted conversation as usual.

If REST receives a `WEB:` ID without owning/proving its target, it should fail clearly rather than navigate blindly.

A future cross-process temporary-target broker is outside this plan.

---

# 19. Files Expected to Change During Implementation

## Core implementation

### `src/chatgpt_web2api/chatgpt_dom.py`

- remove same-tab final URL polling from branch helper;
- preserve exact safe DOM click sequence;
- return click confirmation to driver.

### `src/chatgpt_web2api/cdp_driver.py`

Primary changes:

- browser page-target snapshot helpers;
- exact target websocket lookup;
- branch target discovery/correlation;
- same-target/new-target support;
- transactional active-target handoff;
- temporary conversation target state;
- temporary-aware conversation send routing;
- promotion cleanup;
- reconnect/temporary safeguards;
- new typed errors;
- registry transition orchestration.

### `src/chatgpt_web2api/backend_client.py`

- temporary-aware live conversation ID resolution during first-send promotion;
- preserve backend 404/degraded anchor behavior.

### `src/chatgpt_web2api/mcp_server.py`

- use temporary-aware conversation context for explicit and auto-continue chat completions;
- map/format new typed lifecycle errors;
- update tool description if needed.

### `src/chatgpt_web2api/tab_registry.py`

Potential small change only if required for an atomic/clear target replacement API or additional ownership introspection.

Prefer reusing `record()`/`heartbeat()` if sufficient.

Do not expand registry scope unnecessarily.

### `src/chatgpt_web2api/identity_listener.py`

Only if tests show the `WEB:` -> permanent transition breaks capture correlation.

Do not change it speculatively.

### `src/chatgpt_web2api/mcp_driver_pool.py`

Likely no behavior change if temporary state stays on each leased driver.

May need tests/docs only.

### `src/chatgpt_web2api/lock_resolver.py`

Likely no implementation change.

Add regression tests/comments if needed to document intentional source-target -> branch-target transition across separate MCP calls.

## Tests

Expected relevant files:

- `tests/test_branch_conversation.py`
- `tests/test_conversation_guard.py`
- `tests/test_chatgpt_dom.py` if branch DOM tests are split there
- `tests/test_mcp_driver_pool.py`
- `tests/test_integration.py`
- `tests/test_business.py` where business routing is covered
- `tests/test_e2e_mcp.py`
- possibly target/registry-specific test files already covering owned-tab lifecycle

## Documentation

After implementation passes tests:

- `README.md`
- `src/chatgpt_web2api/guide.md`
- `docs/api-reference.md`
- `docs/reverse-engineering-notes.md` — record the newly proven "branch opens a new CDP target" behavior
- `CHANGELOG.md`

Do not modify documentation during planning except this implementation-plan file.

---

# 20. Detailed Test Plan

## A. Preserve existing branch selection safety

Keep all current tests for:

1. unique match;
2. not found;
3. duplicate ambiguity;
4. pagination by assistant candidates;
5. case/Unicode/whitespace normalization;
6. backend message ID -> exact DOM ID;
7. rendered text mismatch fail-closed;
8. missing More actions fail-closed;
9. exact Branch menu item.

These must remain green.

## B. New target snapshot tests

Test browser target snapshots containing:

- source target;
- pre-existing unrelated ChatGPT tab;
- non-page targets;
- new branch target.

Prove only IDs absent from the baseline are considered newly created by the branch action.

## C. New-tab happy path

Simulate:

```text
before click:
A = /c/source
X = unrelated pre-existing chat

after click:
A = /c/source
B = about:blank, opener=A

then:
B = /branch/source/message-id

then:
B = /c/WEB:temp-id
```

Expect:

- target B selected;
- target A never mistaken as failed navigation;
- final ID `WEB:temp-id` returned;
- temporary=true;
- driver attached to B;
- `_current_conv_id == WEB:temp-id`;
- registry updated to B.

## D. Fast new-tab path

The transient `/branch/...` route may be too fast to observe.

Test:

```text
new B appears directly as /c/WEB:temp-id
```

and require success.

## E. Same-tab compatibility

Test future behavior where no new target appears but source target A changes:

```text
/c/source -> /branch/source/message -> /c/WEB:temp
```

Expect success without target switch.

## F. Unrelated tab noise

After click, simulate another application/user opening a new ChatGPT tab plus the actual branch tab.

Only the target whose route/opener correlates with source+message can be selected.

## G. Multiple plausible targets

If two new targets both appear plausibly related and no unique correlation exists:

- fail closed;
- no arbitrary target switch;
- no automatic second Branch click.

## H. Wrong-host target

A new target that goes to another host must never be accepted as the branch.

## I. Branch-target timeout

Test separate diagnostics for:

- no target created;
- target created but remains loading/blank;
- target reaches wrong branch source/message;
- target reaches source ID instead of new ID.

## J. Handoff state migration

Test successful switch updates:

- target ID;
- owns-target flag;
- websocket;
- reader task;
- pending table state;
- current conversation;
- model cache reset;
- identity listener detach/attach;
- registry.

## K. Old source cleanup

When source was owned:

- it is closed only after new target attachment succeeds.

When source was adopted:

- it remains open.

## L. Handoff rollback

Force new-target websocket attachment failure after branch URL has been verified.

Expect:

- source target reattachment attempted;
- branch-created state not misreported as pre-click failure;
- branch is not clicked again;
- typed `branch_created_handoff_failed`-style error/diagnostic contains the verified new target/URL.

## M. Temporary routing happy path

After branch success:

```text
chat_completion(conversation_id="WEB:temp")
```

must:

- reuse exact live temporary target;
- not call `list_conversations`;
- not call backend conversation discovery;
- not call ordinary `Page.navigate` when already at the correct temp target;
- send in the branch tab.

## N. Temporary ID absent from list

Mock `list_conversations` so it does not contain the `WEB:` ID.

The first branch send must still work.

This directly pins the live behavior discovered by the user.

## O. Temporary target unavailable

If mapping says target B but B no longer exists:

- fail with `temporary_conversation_unavailable`;
- do not create a new chat and send there;
- do not silently navigate the source/current tab.

## P. Temporary active URL self-heal

If in-memory mapping is missing but current active target's URL exactly equals the requested `/c/WEB:temp`, allow rebuilding the mapping and continue.

## Q. Prevent accidental temp destruction

While a pending temporary branch is active, attempt to send/navigate to another conversation.

Expect fail-closed `temporary_conversation_pending` behavior rather than navigating the temporary tab away.

## R. Promotion during first send

Simulate:

```text
before send:
_current_conv_id = WEB:temp
live URL = /c/WEB:temp

after click_send, during generation:
live URL = /c/permanent-uuid
_current_conv_id is still WEB:temp
```

Prove `_get_live_conversation_id_best_effort()` returns permanent UUID during this window.

## S. Backend anchor fallback

Before promotion, backend projection for `WEB:temp` returns 404/fetch failure.

Expect pre-send anchoring to degrade safely instead of aborting.

AuthExpiredError must still propagate.

## T. Promotion cleanup

After completion:

- `_current_conv_id == permanent_uuid`;
- temporary mapping is removed;
- same target remains active;
- `chat_completion` result returns permanent UUID;
- normal permanent navigation works afterward.

## U. Encoded WEB route

Retain tests for:

```text
WEB:abc
WEB%3Aabc
```

## V. Mutation-lock transition

In parallel mode:

1. `branch_conversation` begins under source target lock A.
2. The operation intentionally switches active driver target to B.
3. Branch call completes and releases lock A.
4. Next `chat_completion(WEB:...)` resolves lock B.

Test that this intentional between-call lock-key transition is accepted while pre-operation drift remains rejected.

## W. MCP pool session affinity

With pool enabled:

- branch call and first temporary completion in same MCP session reuse same driver and succeed;
- different session cannot access/steal the pending temporary target;
- reconnect/new session before promotion fails clearly if ownership cannot be proven.

## X. Process restart/reclaim

Where feasible in unit/integration tests:

- registry entry points to new branch target after handoff;
- simulated restart reclaims that target;
- live `/c/WEB:...` state reconstructs temporary mapping.

## Y. Live E2E

With explicit authenticated E2E opt-in:

1. create/use a source conversation with a unique assistant answer;
2. record browser page targets before branch;
3. call `branch_conversation` through MCP;
4. prove a **new target ID** appears;
5. prove source target remains at source URL;
6. prove branch target lands at `/c/WEB:...`;
7. prove returned ID/URL equal the new target;
8. prove driver is now controlling the branch target;
9. verify the temporary ID is absent from normal `list_conversations` if that remains current ChatGPT behavior (record as observation; do not make backend absence a universal permanent contract if ChatGPT changes);
10. call `chat_completion` using the temporary ID;
11. verify first message is typed into the branch tab;
12. verify route promotes to permanent UUID;
13. verify returned `conversation_id` is permanent;
14. verify permanent conversation appears through normal backend conversation APIs after persistence;
15. clean up the permanent test conversation using existing E2E cleanup mechanisms.

---

# 21. Diagnostics and Logging

Add structured logs around this lifecycle because browser-target bugs are difficult to diagnose from DOM logs alone.

Recommended events:

```text
branch_target_baseline
  source_target_id
  source_conv_id
  page_target_count

branch_click_confirmed
  source_target_id
  message_id

branch_target_observed
  target_id
  opener_id
  url

branch_target_verified
  target_id
  branch_id
  temporary
  url

branch_target_handoff_start
  source_target_id
  branch_target_id

branch_target_handoff_success
  active_target_id
  current_conv_id

branch_target_handoff_failed
  source_target_id
  branch_target_id
  error

temporary_conversation_route
  temporary_id
  target_id

temporary_conversation_promoted
  temporary_id
  permanent_id
  target_id
```

Do not log authentication tokens, cookies, or message contents beyond existing safe diagnostics.

When selector diagnostics are irrelevant (for example target discovery failed after a confirmed click), capture browser-target diagnostics rather than only dumping source-page DOM state.

---

# 22. Implementation Sequence

Implement in this order to keep regressions localized.

## Phase 1 — Target discovery primitives

1. Add target record/helper.
2. Add exact target snapshot/listing.
3. Add exact target websocket lookup.
4. Add URL/route parsing tests.

No branch behavior change yet.

## Phase 2 — Separate DOM click from target lifecycle

1. Refactor `ChatGPTDom.branch_assistant_answer` to stop after verified click.
2. Preserve all existing target/menu safety tests.
3. Update branch DOM mocks accordingly.

## Phase 3 — New-tab detector

1. Capture target baseline in `CDPDriver.branch_conversation`.
2. Click branch.
3. Poll same-target + new-target outcomes.
4. Correlate source/message/opener.
5. Return a uniquely verified branch target record.
6. Add ambiguity/no-target tests.

## Phase 4 — Transactional target handoff

1. Add `_switch_active_target` lifecycle helper.
2. Migrate websocket/reader/listener/state.
3. Update registry.
4. Close old owned source only after commit.
5. Add rollback behavior/tests.

At the end of this phase, `branch_conversation` should correctly return the actual `WEB:` branch from the new tab.

## Phase 5 — Temporary routing

1. Add `TemporaryConversationTarget` state.
2. Register successful `WEB:` branch.
3. Add `ensure_conversation_for_send`.
4. Update MCP explicit and auto-current chat paths.
5. Add fail-closed temporary-unavailable/pending behavior.

## Phase 6 — Promotion correctness

1. Make live-ID resolver prefer live URL while cached ID is temporary.
2. Test backend-anchor degradation before persistence.
3. Test identity listener during promotion.
4. Remove temporary mapping after permanent ID resolves.
5. Return permanent UUID.

## Phase 7 — Pool/reconnect/registry regression coverage

1. Same-session pool test.
2. target lock transition test.
3. registry reclaim test.
4. temporary target gone test.

## Phase 8 — Documentation + E2E

Only after non-E2E tests pass:

1. update docs;
2. run full non-E2E suite;
3. run lint;
4. with explicit authenticated E2E authorization, test real new-tab branch + first-message promotion.

---

# 23. Verification Commands After Implementation

Normal validation:

```bash
pytest -m "not e2e"
ruff check .
```

Recommended focused tests first:

```bash
pytest -v tests/test_branch_conversation.py
pytest -v tests/test_conversation_guard.py
pytest -v tests/test_mcp_driver_pool.py
pytest -v tests/test_integration.py
```

Then full non-E2E suite.

Live E2E only with explicit opt-in/authenticated browser:

```bash
W2A_E2E_RUN=1 pytest -m e2e -v
```

Do not treat mocked same-tab URL tests as sufficient. The bug existed specifically because mocks encoded the wrong assumption about the browser target.

---

# 24. Acceptance Criteria

Implementation Plan ID 2 is complete only when all of the following are true:

- [ ] Branch selection still uses exact backend `message_id` -> exact assistant DOM element.
- [ ] Zero and multiple snippet matches still produce no mutation.
- [ ] DOM code stops after a verified `Branch in new chat` click and no longer assumes the source tab navigates.
- [ ] Driver snapshots page targets before the branch click.
- [ ] Driver can detect the currently observed behavior where ChatGPT creates a new page target.
- [ ] Driver can still support same-tab branch navigation if ChatGPT changes behavior again.
- [ ] New targets are correlated using baseline identity plus route/opener evidence; unrelated tabs are never selected arbitrarily.
- [ ] `/c/WEB:<uuid>` is recognized as a valid temporary branch URL.
- [ ] A final branch ID must differ from the source conversation ID.
- [ ] The driver transactionally switches its active page websocket to the verified branch target.
- [ ] Identity listener is reattached to the branch target.
- [ ] Registry/heartbeat state points to the branch target after handoff.
- [ ] Old source target is closed only if it was driver-owned and only after successful handoff.
- [ ] Adopted/user-owned source tabs are never closed by this feature.
- [ ] A verified-created branch followed by handoff failure is distinguished from a pre-creation branch failure.
- [ ] Temporary `WEB:` state is stored on the originating driver/session.
- [ ] `list_conversations` is not used to validate or route a pending temporary branch.
- [ ] `chat_completion(conversation_id="WEB:...")` reuses the exact live branch target.
- [ ] No blind ordinary navigation is used as the primary temporary-branch routing strategy.
- [ ] If the temporary target is unavailable, the system fails closed instead of sending in another conversation.
- [ ] Unrelated navigation cannot silently destroy the only pending temporary branch before its first prompt.
- [ ] During the first send, a promoted permanent live URL is not masked by stale `_current_conv_id = WEB:...`.
- [ ] Temporary backend 404/non-persistence before the first send does not by itself abort the send.
- [ ] Auth failures still propagate normally.
- [ ] After first send, `_current_conv_id` becomes the permanent UUID.
- [ ] Temporary mapping is cleared/migrated after promotion.
- [ ] `chat_completion` returns the permanent UUID.
- [ ] The same-session MCP pool path works.
- [ ] A different pool session cannot steal an ephemeral target.
- [ ] Mutation locking moves from source-target key on branch call to branch-target key on the next call without weakening pre-operation drift protection.
- [ ] Full non-E2E tests pass.
- [ ] Ruff passes for touched files/repository according to current baseline policy.
- [ ] Live E2E confirms the real source-tab/new-tab behavior before the feature is declared fully validated.

---

# 25. Explicit Non-Goals

Do **not** expand this implementation into unrelated architecture work.

Out of scope:

- replacing legacy MCP SSE with Streamable HTTP;
- fixing the separate `server/discover` MCP compatibility warning;
- synthesizing temporary `WEB:` entries into `list_conversations`;
- making temporary branches portable across unrelated REST/MCP processes;
- supporting unlimited simultaneously pending temporary branches with a full multi-target browser manager;
- changing snippet matching behavior that is already working;
- direct navigation to undocumented `/branch/{source}/{message}` as a fallback;
- broad refactors of CDPTransport, pool, locks, or registry unrelated to this lifecycle.

Those may be separate implementation plans if needed later.

---

# 26. Final Design Summary

The central correction is simple conceptually but touches important lifecycle guarantees:

```text
OLD WRONG ASSUMPTION
Branch click -> current tab changes URL

REAL OBSERVED BEHAVIOR
Branch click -> new Chrome page target opens
```

Therefore the implementation must move from **URL-only observation on one page websocket** to **browser-target-aware branch detection**.

The safe design is:

```text
source backend message_id
        -> exact source DOM turn
        -> verified Branch click
        -> browser-level target delta
        -> uniquely correlated new target
        -> verified /c/WEB:<id>
        -> transactional driver handoff
        -> session-local temporary target registration
        -> chat_completion(WEB:<id>) on that exact tab
        -> first send
        -> live URL promotes to permanent UUID
        -> temporary state removed
        -> normal persisted conversation lifecycle resumes
```

That directly addresses both live findings:

1. **the branch is created in a new tab**, and
2. **the temporary branch is not a normal backend-listed conversation until the first new prompt persists it**.
