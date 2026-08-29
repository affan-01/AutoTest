"""Shared helpers for the local QA flow pipeline (groom -> impact -> generate -> execute -> report).

Every stage delegates to a real agent defined under .claude/agents/. This module wraps the
Claude Agent SDK to run one stage headlessly, and provides directory-diffing helpers to discover
each stage's actual output artifacts (grooming-reports/, pr-analysis-reports/, bunker/*, ...)
instead of guessing filenames the agents never documented.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from pipelines.tracing import observe, update_current_span, update_llm_span

REPO_ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS_ROOT = REPO_ROOT / "artifacts" / "flow"

# Real TFS auth env-var names referenced by this repo's own agents (see
# .claude/agent-memory/core-pr-impact-analyzer/auth-and-api-versions.md) — NOT "TFS_PAT".
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


@observe(type="llm")
async def run_agent(prompt: str, *, allowed_tools: list, max_turns: int = 40,
                     permission_mode: str = "bypassPermissions",
                     setting_sources: Optional[list] = None) -> str:
    """Run a single headless agent turn via the Claude Agent SDK and return the concatenated
    assistant text.

    Import of claude_agent_sdk is local so `pipelines.common` remains importable (e.g. for its
    path constants) before `pip install claude-agent-sdk` has actually been run.

    NOTE: setting_sources=["project"] only loads .claude/settings.json + .mcp.json from THIS
    repo. RESOLVED 2026-08-27: there is no .mcp.json at this repo root at all. The MCP servers
    this pipeline needs — "playwright", "rp-azure-devops", "azure-devops-files" — are registered
    at USER scope in ~/.claude.json, and no project defines any. setting_sources=["project"]
    therefore resolved every mcp__* entry to nothing, starving the generate/execute stages of
    both Playwright and TFS. Hence the ["user", "project"] default below.

    Note this pulls in the user's global settings (permissions included), which is the point —
    but it does mean a headless run under a different account (e.g. a future ADO pipeline agent)
    inherits nothing and will silently starve again. Verify MCP availability there rather than
    assuming. Also note "mcp__rpdevops__*" in flow.py's allowlists matches NO registered server
    (the real one is "rp-azure-devops"); it is dead but harmless.

    MCP TOOLS ARRIVE LATE AND DEFERRED (verified 2026-08-27). At init every server reports
    status "pending" and the agent's immediate tool list contains NO mcp__* entries at all —
    asked to list them on turn 1, it truthfully answers "none". They attach a few seconds
    later, and even then they are DEFERRED: the sub-agent must call ToolSearch to load their
    schemas before it can invoke one. Two consequences for the stage budgets above: a stage
    that finishes in one or two turns may never see Playwright or TFS at all, and the turns
    spent on ToolSearch come out of max_turns. Do not read an early "no MCP tools" as proof
    of misconfiguration — it is the expected shape of a very short session.
    """
    from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, ResultMessage  # type: ignore

    options = ClaudeAgentOptions(
        cwd=str(REPO_ROOT),
        allowed_tools=allowed_tools,
        permission_mode=permission_mode,
        max_turns=max_turns,
        setting_sources=setting_sources or ["user", "project"],
    )

    # Verified against claude-agent-sdk 0.2.116: query() yields UserMessage | AssistantMessage |
    # SystemMessage | ResultMessage | StreamEvent | RateLimitEvent. Only AssistantMessage.content
    # holds the model's own text (as a list of blocks — TextBlock has .text, other block types
    # like ToolUseBlock don't). UserMessage.content instead carries tool_result payloads being
    # fed back to the model — deliberately NOT included here, or the transcript would be
    # swamped with raw tool output rather than the agent's narration.
    wall_t0 = time.monotonic()
    chunks = []
    tool_names = []
    result_message = None
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                block_text = getattr(block, "text", None)
                if block_text:
                    chunks.append(block_text)
                elif getattr(block, "name", None):
                    # ToolUseBlocks were previously discarded entirely. Collecting the name
                    # here is free, and it is the only in-process record of what the agent
                    # actually did: the sub-agent runs in its OWN process, so nothing below
                    # this call is visible to the tracer.
                    tool_names.append(block.name)
        elif isinstance(message, ResultMessage):
            result_message = message

    if result_message is not None and result_message.is_error:
        chunks.append(f"\n[query() reported an error result: {result_message.subtype} — "
                       f"{result_message.result}]")
    transcript = "\n".join(chunks)
    wall_ms = int((time.monotonic() - wall_t0) * 1000)
    _record_session(prompt, transcript, tool_names, result_message, max_turns, wall_ms)
    return transcript


def _record_session(prompt: str, transcript: str, tool_names: list,
                     result, max_turns: int, wall_ms: int = 0) -> None:
    """Push the SDK's own session accounting onto the enclosing llm span.

    run_agent previously read only `is_error` and discarded the rest. The discarded fields are
    the ones that actually explain a failed run: `stop_reason == "max_turns"` (the README's top
    known risk), `permission_denials` (proves MCP starvation), and `api_error_status` (a 401
    from a stale ANTHROPIC_API_KEY is otherwise indistinguishable from a hang).

    COST/TURN/DURATION UNDER-REPORT WHEN A STAGE DELEGATES. Measured on two real groom runs of
    US 2928495 (2026-08-28). When the session did the work inline every field agreed; when it
    delegated to a sub-agent via the Agent tool, ResultMessage described only the PARENT's own
    turns and the aggregates collapsed:

        num_turns      = 2         while 35 tool calls were recorded
        duration_ms    = 8,955     while the wall clock was 629,000  (70x)
        total_cost_usd = $0.2577   while model_usage summed to $2.2775  (9x)

    A sub-agent runs in its own context, so the parent never accounts for it. `model_usage` is
    the one field that survives - it carries per-model costUSD for the whole tree. Cost is
    therefore taken from model_usage, `total_cost_usd` is kept beside it, and a divergence
    between the two is itself the signal that delegation happened. Cost is still never derived
    from token counts (cache-read and cache-creation price differently) - costUSD is the SDK's
    own per-model figure, not our arithmetic.
    """
    usage = (getattr(result, "model_usage", None) or {}) if result is not None else {}

    # model_usage is keyed BY MODEL, and one session genuinely spans several: a trivial
    # "reply ok" call was verified to use claude-haiku-4-5 AND claude-sonnet-5. Name the model
    # that did the most token work and keep the full breakdown in metadata, rather than
    # asserting one model that would be wrong much of the time.
    def _total(v):
        return (v.get("inputTokens") or 0) + (v.get("outputTokens") or 0)

    dominant = max(usage, key=lambda k: _total(usage[k])) if usage else None
    update_llm_span(
        model=dominant,
        input_token_count=sum((v.get("inputTokens") or 0) for v in usage.values()) or None,
        output_token_count=sum((v.get("outputTokens") or 0) for v in usage.values()) or None,
    )

    # Sum the SDK's own per-model costUSD. This survives delegation; total_cost_usd does not.
    usage_cost = sum((v.get("costUSD") or 0) for v in usage.values()) or None

    meta = {"tool_calls": tool_names, "transcript_chars": len(transcript),
            "max_turns_configured": max_turns,
            "wall_ms": wall_ms,
            "cost_usd": usage_cost}
    if result is None:
        meta["no_result_message"] = True
    else:
        for f in ("subtype", "is_error", "stop_reason", "num_turns", "session_id",
                   "duration_ms", "duration_api_ms", "total_cost_usd", "api_error_status"):
            meta[f] = getattr(result, f, None)
        meta["model_usage"] = usage
        meta["permission_denials"] = getattr(result, "permission_denials", None) or []
        meta["errors"] = getattr(result, "errors", None) or []
        # The most useful derived signal: was this stage TRUNCATED rather than finished?
        meta["hit_max_turns"] = (getattr(result, "num_turns", 0) or 0) >= max_turns

        # Delegation markers. Either alone is enough to distrust ResultMessage's aggregates,
        # so record both rather than inferring one from the other.
        turns = getattr(result, "num_turns", 0) or 0
        reported = getattr(result, "total_cost_usd", None)
        meta["delegated"] = len(tool_names) > turns
        meta["cost_reported_vs_usage"] = {
            "total_cost_usd": reported, "model_usage_sum": usage_cost,
            "diverges": bool(reported is not None and usage_cost
                             and abs(reported - usage_cost) > 0.01),
        }
        dur = getattr(result, "duration_ms", None) or 0
        # wall_ms is measured around the whole SDK call, so it includes sub-agent time that
        # duration_ms omits. A large ratio is the clearest evidence of delegated work.
        meta["wall_vs_duration_ratio"] = round(wall_ms / dur, 1) if dur else None

    # A 200-turn execute transcript can be megabytes, and write_stage_log() already writes the
    # full text to disk. Keep only a tail in the span so the trace stays readable.
    update_current_span(input=prompt[:2000], output=transcript[-4000:], metadata=meta)


def run_agent_sync(prompt: str, **kwargs) -> str:
    return asyncio.run(run_agent(prompt, **kwargs))


def write_stage_log(story_dir: Path, stage: str, transcript: str) -> Path:
    out = story_dir / f"{stage}.transcript.txt"
    out.write_text(transcript, encoding="utf-8")
    return out
