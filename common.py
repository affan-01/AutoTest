"""Shared helpers for the local QA flow pipeline (groom -> impact -> generate -> execute -> report).

Every stage delegates to a real agent defined under .claude/agents/. This module wraps the
Claude Agent SDK to run one stage headlessly, and provides directory-diffing helpers to discover
each stage's actual output artifacts (grooming-reports/, pr-analysis-reports/, bunker/*, ...)
instead of guessing filenames the agents never documented.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS_ROOT = REPO_ROOT / "artifacts" / "flow"

# Real TFS auth env-var names referenced by this repo's own agents (see
# .claude/agent-memory/pr-impact-analyzer/auth-and-api-versions.md) — NOT "TFS_PAT".
# As of the last check, the stored PAT there is dead (401 on everything) and TFS calls
# fall back to Windows Integrated Auth on this machine (see that agent's
# powershell-gotchas.md memory). A headless run under a different account/service
# (e.g. a future ADO pipeline agent) will NOT get that fallback for free — do not
# assume ADO_PAT alone is sufficient there without checking.
ADO_PAT_ENV = "ADO_PAT"
TFS_ORG_URL_ENV = "TFS_ORG_URL"


def artifacts_dir(story_id: str) -> Path:
    d = ARTIFACTS_ROOT / str(story_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def snapshot_dir(path: Path) -> set:
    if not path.exists():
        return set()
    return {str(p) for p in path.rglob("*") if p.is_file()}


def new_files_since(path: Path, before: set) -> list:
    after = snapshot_dir(path)
    return [Path(p) for p in sorted(after - before)]


@dataclass
class StageResult:
    stage: str
    ok: bool
    transcript: str
    new_artifacts: list = field(default_factory=list)
    note: Optional[str] = None


async def run_agent(prompt: str, *, allowed_tools: list, max_turns: int = 40,
                     permission_mode: str = "bypassPermissions",
                     setting_sources: Optional[list] = None) -> str:
    """Run a single headless agent turn via the Claude Agent SDK and return the concatenated
    assistant text.

    Import of claude_agent_sdk is local so `pipelines.common` remains importable (e.g. for its
    path constants) before `pip install claude-agent-sdk` has actually been run.

    NOTE: setting_sources=["project"] only loads .claude/settings.json + .mcp.json from THIS
    repo. The manual-test-execution-agent needs mcp__rp-azure-devops__*/mcp__rpdevops__* for
    TFS reads, and those servers are NOT registered in this repo's .mcp.json (only "playwright"
    is — see .claude/settings.local.json's enabledMcpjsonServers). If those TFS MCP servers are
    only registered at user/global scope, "project"-only setting_sources will silently starve
    the execute stage of TFS access. This needs to be verified against the installed SDK's
    actual scope-merging behavior before the execute stage is trusted end-to-end.
    """
    from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, ResultMessage  # type: ignore

    options = ClaudeAgentOptions(
        cwd=str(REPO_ROOT),
        allowed_tools=allowed_tools,
        permission_mode=permission_mode,
        max_turns=max_turns,
        setting_sources=setting_sources or ["project"],
    )

    # Verified against claude-agent-sdk 0.2.116: query() yields UserMessage | AssistantMessage |
    # SystemMessage | ResultMessage | StreamEvent | RateLimitEvent. Only AssistantMessage.content
    # holds the model's own text (as a list of blocks — TextBlock has .text, other block types
    # like ToolUseBlock don't). UserMessage.content instead carries tool_result payloads being
    # fed back to the model — deliberately NOT included here, or the transcript would be
    # swamped with raw tool output rather than the agent's narration.
    chunks = []
    result_message = None
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                block_text = getattr(block, "text", None)
                if block_text:
                    chunks.append(block_text)
        elif isinstance(message, ResultMessage):
            result_message = message

    if result_message is not None and result_message.is_error:
        chunks.append(f"\n[query() reported an error result: {result_message.subtype} — "
                       f"{result_message.result}]")
    return "\n".join(chunks)


def run_agent_sync(prompt: str, **kwargs) -> str:
    return asyncio.run(run_agent(prompt, **kwargs))


def write_stage_log(story_dir: Path, stage: str, transcript: str) -> Path:
    out = story_dir / f"{stage}.transcript.txt"
    out.write_text(transcript, encoding="utf-8")
    return out
