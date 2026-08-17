from pathlib import Path

patch_path = Path(__file__).with_name("branch_conversation_patch.py")
source = patch_path.read_text(encoding="utf-8")

old = '''replace_once(
    "tests/test_gating.py",
    "        ToolName.CREATE_MEMORY, ToolName.ARCHIVE_CONVERSATION,\\n",
    "        ToolName.CREATE_MEMORY, ToolName.ARCHIVE_CONVERSATION,\\n        ToolName.BRANCH_CONVERSATION,\\n",
)'''
new = '''replace_count(
    "tests/test_gating.py",
    "        ToolName.CREATE_MEMORY, ToolName.ARCHIVE_CONVERSATION,\\n",
    "        ToolName.CREATE_MEMORY, ToolName.ARCHIVE_CONVERSATION,\\n        ToolName.BRANCH_CONVERSATION,\\n",
    2,
)'''
if old not in source:
    raise RuntimeError("could not adjust the two test_gating write-tool lists")
source = source.replace(old, new, 1)

# The focused missing-More test accelerates a five-second poll by replacing
# time.monotonic. Also make asyncio.sleep a no-op inside that test so the event
# loop does not wait against the synthetic clock.
needle = '''    ticks = iter([0.0, 0.1, 10.0, 10.1])
    monkeypatch.setattr(dom_mod.time, "monotonic", lambda: next(ticks, 10.1))
'''
replacement = '''    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr(dom_mod.asyncio, "sleep", _no_sleep)
    ticks = iter([0.0, 0.1, 10.0, 10.1])
    monkeypatch.setattr(dom_mod.time, "monotonic", lambda: next(ticks, 10.1))
'''
if needle not in source:
    raise RuntimeError("could not adjust the synthetic-clock DOM test")
source = source.replace(needle, replacement, 1)

# CHANGELOG has historical Added sections. Scope the patch to the current
# Unreleased section instead of allowing the patcher to guess among them.
needle = '    "### Added\\n",\n    "### Added\\n- **`branch_conversation` MCP tool (17th)**'
replacement = '    "## [Unreleased]\\n\\n### Added\\n",\n    "## [Unreleased]\\n\\n### Added\\n- **`branch_conversation` MCP tool (17th)**'
if needle not in source:
    raise RuntimeError("could not narrow the CHANGELOG Added anchor")
source = source.replace(needle, replacement, 1)

namespace = {"__file__": str(patch_path), "__name__": "__main__"}
exec(compile(source, str(patch_path), "exec"), namespace)
