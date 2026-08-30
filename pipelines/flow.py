"""Local, headless QA flow: groom -> impact (optional, needs --pr-id) -> generate -> execute -> report.

Every stage delegates to a real agent defined under .claude/agents/*.md — the agent identifiers
used below are defaults that match this template's shipped agents, but are overridable via the
`agents` block in pipeline.config.json (see pipeline.config.example.json / docs/CONFIGURATION.md)
in case you rename or replace one. This script does not publish anywhere: no PM-tool writes, no
PR comments, no test-result uploads, no --publish flag.

Usage:
    python3 -m pipelines.flow --ticket-id <id> [--pr-id <id>[,<id>...]]
"""
from __future__ import annotations

import argparse
import json
import os
import time
import sys
from dataclasses import asdict
from pathlib import Path

from pipelines.common import (
    REPO_ROOT, artifacts_dir, snapshot_dir, new_files_since,
    run_agent_sync, write_stage_log, StageResult, load_pipeline_config,
)
from pipelines.evals import (
    evaluate_generate_groundedness, evaluate_report_summary, evals_banner,
)
from pipelines.tracing import (
    observe, update_current_span, update_current_trace, flush_traces,
    set_trace_output, tracing_banner,
)

_CONFIG = load_pipeline_config()
_AGENT_NAMES = _CONFIG.get("agents", {})
_BACKEND = _CONFIG.get("backend", {})
_MCP_TOOL_PREFIX = _BACKEND.get("mcpToolPrefix", "mcp__your-pm-tool__")

AGENT_GROOM = _AGENT_NAMES.get("groom", "us-eval")
AGENT_IMPACT = _AGENT_NAMES.get("impact", "pr-impact-analyzer")
AGENT_GENERATE = _AGENT_NAMES.get("generate", "test-case-generation-agent")
AGENT_EXECUTE = _AGENT_NAMES.get("execute", "manual-test-execution-agent")

# Each stage watches the output directory(ies) its agent actually documents writing to — verify
# this against your own agents' frontmatter/instructions if you swap one out. A stage watching a
# directory nothing writes to will always report "no artifact found", not a helpful error.
#
# Groom watches TWO directories: some grooming setups route through a dedicated agent, others
# fall back to a grooming skill/prompt that writes to a different path. Watch both so this stage
# reports honestly whichever path was actually taken.
GROOMING_REPORTS = REPO_ROOT / "bunker" / "story-grooming-reports"
GROOMING_REPORTS_ALT = REPO_ROOT / "output" / "grooming-reports"
GROOM_DIRS = [GROOMING_REPORTS, GROOMING_REPORTS_ALT]
PR_ANALYSIS_REPORTS = REPO_ROOT / "bunker" / "pr-analysis-reports"
BUNKER_TESTCASES = REPO_ROOT / "bunker" / "test-case-reports"
BUNKER_EXECUTION = REPO_ROOT / "bunker" / "manual-test-execution"

# Appended to every delegating prompt. A delegating session can reply "I've kicked off the
# <agent>, I'll let you know when it's done" and return immediately — the parent sees a normal
# end-of-turn after a couple of turns, the transcript is a status announcement rather than a
# result, and the watched directory has nothing new yet. The stage is only complete when the
# agent's own deliverable exists on disk, so be explicit that background dispatch is not enough.
SYNC_DIRECTIVE = (
    " Run this synchronously and do NOT dispatch it as a background task. Do not reply until "
    "the agent has fully finished and its output files exist on disk. Your final message must "
    "report the agent's actual findings and the absolute paths it wrote - not an announcement "
    "that work has been started."
)

# Tool allowlists per stage, grounded in each agent's real `tools:` frontmatter.
GROOM_TOOLS = ["Task", "Bash", "Read", "Write", "Grep", "Glob", "Edit"]
IMPACT_TOOLS = ["Task", "Bash", "Read", "Write", "Grep", "Glob", "Edit", f"{_MCP_TOOL_PREFIX}*"]

# The PR-impact agent's default instructions assume PR diffs are reachable via a `gh`-style CLI
# or a REST API tied to your PM tool. Some on-prem setups (e.g. a pre-migration TFS instance)
# need a different diff-fetching recipe than the agent's own default — this override exists for
# that case. It is entirely opt-in and TFS-flavored because TFS is this template's one shipped
# reference adapter (see docs/adapters/tfs.md); if your backend needs an equivalent override,
# add one following this same pattern rather than editing the agent prompt itself.
#
# Host, project and repo are deliberately NOT hardcoded: they are site-specific, and baking one
# organization's TFS instance into the source makes the pipeline unportable. Configure via env,
# e.g. in a local .env (which is gitignored):
#   TFS_API_BASE=https://<tfs-host>/tfs/<collection>/<project>/_apis
#   TFS_DIFF_REPO_NAME=<repo>
#   TFS_DIFF_REPO_ID=<repo guid>
# With TFS_API_BASE unset the override is empty and the agent follows its own default
# instructions unchanged.
TFS_API_BASE = os.environ.get("TFS_API_BASE", "").rstrip("/")
TFS_DIFF_REPO_NAME = os.environ.get("TFS_DIFF_REPO_NAME", "")
TFS_DIFF_REPO_ID = os.environ.get("TFS_DIFF_REPO_ID", "")


def tfs_diff_override() -> str:
    """Instructions for pulling a PR diff straight from the TFS REST API (see docs/adapters/tfs.md).

    Returns "" when TFS_API_BASE is unset, so an unconfigured checkout behaves as though this
    override did not exist rather than emitting a half-formed recipe with placeholder hosts.
    """
    if not TFS_API_BASE:
        return ""
    repo_hint = ""
    if TFS_DIFF_REPO_NAME or TFS_DIFF_REPO_ID:
        repo_id = f" (id {TFS_DIFF_REPO_ID})" if TFS_DIFF_REPO_ID else ""
        repo_hint = f" Repo {TFS_DIFF_REPO_NAME or '<repo>'}{repo_id}."
    return (
        " IMPORTANT OVERRIDE - read before planning. This PR may not resolve through your usual"
        " PR-diff path (e.g. it predates a migration off TFS). Do NOT write a data-collection"
        " script for a human to run - you have a working shell, so make the calls yourself."
        " Fetch the diff from the TFS REST API using Windows Integrated Auth"
        " (PowerShell Invoke-RestMethod -UseDefaultCredentials); do not use a stored PAT, which"
        " may be expired." + repo_hint +
        f" $b='{TFS_API_BASE}';"
        " PR metadata:   $b/git/pullrequests/<PR>?api-version=3.0 ;"
        " iterations:    $b/git/repositories/<repoId>/pullRequests/<PR>/iterations?api-version=3.0 ;"
        " changed files: $b/git/repositories/<repoId>/pullRequests/<PR>/iterations/<lastId>/changes"
        "?api-version=3.0 ;"
        " file content:  $b/git/repositories/<repoId>/items?path=<path>"
        "&versionDescriptor.version=<commitId>&versionDescriptor.versionType=commit&api-version=3.0 ."
        " Run each call inline rather than from a saved .ps1 - script-file invocation against"
        " this server is unreliable (see docs/adapters/tfs.md). Base the analysis on the REAL"
        " changed files you retrieve."
    )


GENERATE_TOOLS = ["Task", "Bash", "Read", "Write", "Grep", "Glob", "Edit",
                   "mcp__playwright__*", f"{_MCP_TOOL_PREFIX}*"]
EXECUTE_TOOLS = ["Task", "Bash", "Read", "Write", "Grep", "Glob", "Edit",
                  "mcp__playwright__*", f"{_MCP_TOOL_PREFIX}*"]


def _record_stage(result: StageResult, extra_metadata: dict = None) -> StageResult:
    """Mirror a StageResult onto its own span, and pass it straight through.

    This is not decoration for its own sake. Stages NEVER raise: a stage that burned 200 turns
    and produced nothing still returns normally, with ok=False. A tracer that only notices
    exceptions would therefore paint four healthy green spans over a run that produced nothing,
    which is precisely the failure this pipeline has. Writing `ok` explicitly is what makes a
    failed stage legible in the trace.

    extra_metadata (e.g. an eval outcome) is merged in here rather than via a second
    update_current_span call: deepeval's update_current_span *replaces* current_span.metadata
    wholesale rather than merging it, so a second call would silently wipe the ok/note/artifacts
    fields this function exists to record.
    """
    metadata = {
        "ok": result.ok,
        "note": result.note,
        "artifact_count": len(result.new_artifacts),
        # Cap the list: a generate stage can drop dozens of files and the span should stay
        # readable. The full set is already on disk under the stage's output directory.
        "new_artifacts": [str(x) for x in result.new_artifacts][:40],
    }
    if extra_metadata:
        metadata.update(extra_metadata)
    update_current_span(
        output=f"ok={result.ok}" + (f" note={result.note}" if result.note else ""),
        metadata=metadata,
    )
    return result


def _snapshot_all(dirs: list) -> dict:
    return {d: snapshot_dir(d) for d in dirs}


def _new_files_all(before: dict) -> list:
    out = []
    for d, seen in before.items():
        out.extend(new_files_since(d, seen))
    return out


def _await_artifacts(before: dict, timeout_s: int = 420, poll_s: int = 5) -> tuple:
    """Poll for the stage's deliverable before declaring it missing.

    Belt to SYNC_DIRECTIVE's braces. The directive asks the delegating session not to return
    early; this makes the check robust when it does anyway, since instruction-following is not
    a guarantee. Returns as soon as anything appears, so a well-behaved stage pays nothing.

    Deliberately bounded: a stage that genuinely produced nothing must still be reported
    ok=False rather than hanging. `artifact_wait_ms` goes into the span so a run that only
    passed because of this wait is visibly distinguishable from one that never needed it.
    """
    t0 = time.monotonic()
    found = _new_files_all(before)
    while not found and (time.monotonic() - t0) < timeout_s:
        time.sleep(poll_s)
        found = _new_files_all(before)
    return found, int((time.monotonic() - t0) * 1000)


@observe(type="agent", available_tools=GROOM_TOOLS)
def stage_groom(ticket_id: str, ticket_dir: Path) -> StageResult:
    before = _snapshot_all(GROOM_DIRS)
    transcript = run_agent_sync(
        f'Use the Task tool with subagent_type="{AGENT_GROOM}" to groom the following '
        f'ticket: {ticket_id}' + SYNC_DIRECTIVE,
        allowed_tools=GROOM_TOOLS,
    )
    write_stage_log(ticket_dir, "01-groom", transcript)
    artifacts, wait_ms = _await_artifacts(before)
    return _record_stage(StageResult("groom", ok=bool(artifacts), transcript=transcript,
                                     new_artifacts=artifacts), {"artifact_wait_ms": wait_ms})


@observe(type="agent", available_tools=IMPACT_TOOLS)
def stage_impact(pr_ids, ticket_dir: Path) -> StageResult:
    """Analyse EVERY PR behind the ticket, not just one.

    A feature can be delivered across several linked PRs rather than one, and those PRs often
    hang off sub-tickets rather than the top-level ticket itself — so nothing can resolve them
    automatically; the ids have to be supplied.

    Analysing one and reporting on "the feature" would understate the blast radius, so each PR
    gets its own agent call and its own llm span. The stage is ok only if EVERY PR produced a
    report - a partial analysis is not a pass, because the missing PR is exactly the one whose
    regressions would go unnoticed.
    """
    if isinstance(pr_ids, str):
        pr_ids = [x.strip() for x in pr_ids.split(",") if x.strip()]

    before = _snapshot_all([PR_ANALYSIS_REPORTS])
    transcripts, reported = [], []
    for pr in pr_ids:
        t = run_agent_sync(
            f'Use the Task tool with subagent_type="{AGENT_IMPACT}" to analyze the '
            f'following pull request: {pr}' + tfs_diff_override() + SYNC_DIRECTIVE,
            allowed_tools=IMPACT_TOOLS,
        )
        transcripts.append(f"===== PR {pr} =====\n{t}")
        reported.append(pr)
    transcript = "\n\n".join(transcripts)
    write_stage_log(ticket_dir, "02-impact", transcript)
    artifacts, wait_ms = _await_artifacts(before)

    # pr-impact-analyzer.md documents a sandboxed-shell fallback: the agent writes a
    # .py/.mjs/.ps1 data-collection script and asks a HUMAN to run it, then re-invoke with "data
    # collected for {prId}". There is no human in this loop — treat that fallback as a hard stop
    # instead of silently treating a dropped script as a successful report.
    if artifacts and all(p.suffix in (".py", ".mjs", ".ps1") for p in artifacts):
        return _record_stage(StageResult(
            "impact", ok=False, transcript=transcript, new_artifacts=artifacts,
            note=f"{AGENT_IMPACT} fell back to a data-collection script (shell sandboxed) "
                 "instead of producing a report. This stage cannot complete headlessly as-is.",
        ))

    md_reports = [p for p in artifacts if p.suffix == ".md"]
    for md in md_reports:
        (ticket_dir / f"impact-{md.stem}.md").write_text(
            md.read_text(encoding="utf-8"), encoding="utf-8")

    # One report per PR is the bar. Fewer means a PR was silently skipped.
    complete = len(md_reports) >= len(pr_ids)
    note = None
    if md_reports and not complete:
        note = (f"only {len(md_reports)} report(s) for {len(pr_ids)} PRs "
                f"({', '.join(pr_ids)}) - at least one PR was not analysed")
    return _record_stage(
        StageResult("impact", ok=bool(md_reports) and complete, transcript=transcript,
                    new_artifacts=artifacts, note=note),
        {"artifact_wait_ms": wait_ms, "pr_ids": pr_ids,
         "pr_count": len(pr_ids), "md_report_count": len(md_reports)})


@observe(type="agent", available_tools=GENERATE_TOOLS)
def stage_generate(ticket_id: str, ticket_dir: Path, acceptance_criteria: str = ""):
    before = _snapshot_all([BUNKER_TESTCASES])
    transcript = run_agent_sync(
        f'Use the Task tool with subagent_type="{AGENT_GENERATE}" to generate test '
        f'cases for the following ticket: {ticket_id}' + SYNC_DIRECTIVE,
        allowed_tools=GENERATE_TOOLS,
        max_turns=80,
    )
    write_stage_log(ticket_dir, "03-generate", transcript)
    artifacts, wait_ms = _await_artifacts(before)
    testsuite_json = next((p for p in artifacts if p.name.endswith("-tests.testsuite.json")), None)

    if artifacts:
        gen_dir = ticket_dir / "generated-tests"
        gen_dir.mkdir(exist_ok=True)
        for p in artifacts:
            if p.suffix in (".json", ".md", ".csv"):
                (gen_dir / p.name).write_text(p.read_text(encoding="utf-8"), encoding="utf-8")

    result = StageResult("generate", ok=bool(testsuite_json), transcript=transcript, new_artifacts=artifacts)

    eval_metadata = {}
    if testsuite_json is not None:
        # See pipelines/evals.py: opt-in (PIPELINE_EVALS=1), never fails the stage, and does not
        # yet gate ok/ship-no-ship — this is a new, uncalibrated signal, not a pass/fail gate.
        outcome = evaluate_generate_groundedness(
            ticket_id=ticket_id,
            acceptance_criteria=acceptance_criteria,
            testsuite_text=testsuite_json.read_text(encoding="utf-8"),
        )
        if outcome is not None:
            eval_metadata["eval_groundedness"] = asdict(outcome)
            score = f"{outcome.score:.2f}" if outcome.score is not None else "n/a"
            print(f"[flow] eval(generate groundedness): score={score} "
                  f"threshold={outcome.threshold} success={outcome.success} — {outcome.reason[:200]}")

    eval_metadata["artifact_wait_ms"] = wait_ms
    return _record_stage(result, eval_metadata), testsuite_json


@observe(type="agent", available_tools=EXECUTE_TOOLS)
def stage_execute(ticket_id: str, ticket_dir: Path) -> StageResult:
    before = _snapshot_all([BUNKER_EXECUTION])
    # Work-Item Mode: pass the bare ticket id so the agent runs ITS OWN TC-discovery chain
    # (linked test cases -> local bunker fallback, i.e. exactly what stage_generate just
    # populated) and its own environment auto-detection.
    transcript = run_agent_sync(
        f'Use the Task tool with subagent_type="{AGENT_EXECUTE}" to execute ticket '
        f'{ticket_id}' + SYNC_DIRECTIVE,
        allowed_tools=EXECUTE_TOOLS,
        # This stage drives a live browser through many steps with mandatory
        # wait+snapshot+screenshot per action — a low generic max_turns will cut it off
        # mid-suite. 200 is a rough starting budget; watch for max_turns cutoffs on real runs
        # and raise further if needed (see README "known risks").
        max_turns=200,
    )
    write_stage_log(ticket_dir, "04-execute", transcript)
    artifacts, wait_ms = _await_artifacts(before)

    run_dir = ticket_dir / "run"
    run_dir.mkdir(exist_ok=True)
    # Gate on the documented MD report deliverable, and carry both machine-readable artifacts
    # across: execution_log.json (the per-step working log) and *-summary.json (the agent's
    # structured pass/fail rollup - counts, per-TC/per-step status, screenshot filenames).
    # Check your execution agent's own docs for what it actually writes and adjust this gate if
    # it differs.
    report_md = next((p for p in artifacts if p.name.endswith("-execution-report.md")), None)
    if report_md:
        (run_dir / report_md.name).write_text(
            report_md.read_text(encoding="utf-8"), encoding="utf-8")
    exec_log = next((p for p in artifacts if p.name == "execution_log.json"), None)
    if exec_log:
        (run_dir / exec_log.name).write_text(
            exec_log.read_text(encoding="utf-8"), encoding="utf-8")
    summary_json = next((p for p in artifacts if p.name.endswith("-summary.json")), None)
    if summary_json:
        (run_dir / summary_json.name).write_text(
            summary_json.read_text(encoding="utf-8"), encoding="utf-8")

    return _record_stage(StageResult("execute", ok=bool(report_md), transcript=transcript,
                                     new_artifacts=artifacts), {"artifact_wait_ms": wait_ms})


@observe(name="stage_report")
def stage_report(ticket_id: str, ticket_dir: Path, groom: StageResult,
                  impact, generate: StageResult, execute: StageResult) -> Path:
    """Synthesize the final ship/no-ship qa-report.md natively — no agent call here.

    The execute stage produces its own MD/PDF deliverables plus two machine-readable artifacts;
    this stage reads those directly rather than delegating to a separate report-analysis agent,
    since a generic report-analysis agent tuned for a different report shape (e.g. an
    automated-test-suite HTML report) would misparse manual-execution output. Prefer
    *-summary.json (the agent's structured pass/fail rollup) over execution_log.json (the raw
    per-step working log) when both exist — it's the purpose-built, already-aggregated view.
    """
    run_dir = ticket_dir / "run"
    summary_path = next(run_dir.glob("*-summary.json"), None) if run_dir.exists() else None
    log_path = next(run_dir.glob("execution_log.json"), None) if run_dir.exists() else None
    source_path = summary_path or log_path
    summary = json.loads(source_path.read_text(encoding="utf-8")) if source_path else {}

    lines = [f"# QA Report — Ticket {ticket_id}", "", "## Stage status"]
    lines.append(f"- Groom: {'ok' if groom.ok else 'FAILED'}")
    if impact is not None:
        note = f" — {impact.note}" if impact.note else ""
        lines.append(f"- Impact analysis: {'ok' if impact.ok else 'FAILED'}{note}")
    else:
        lines.append("- Impact analysis: skipped (no --pr-id given)")
    lines.append(f"- Generate tests: {'ok' if generate.ok else 'FAILED'}")
    lines.append(f"- Execute tests: {'ok' if execute.ok else 'FAILED'}")
    lines.append("")

    if summary:
        lines.append(f"## Execution summary (from {AGENT_EXECUTE})")
        lines.append(f"```json\n{json.dumps(summary, indent=2)}\n```")
        lines.append("")
        lines.append("## Ship / no-ship")
        lines.append("Manual call required — inspect the counts above. This script "
                      "deliberately does not auto-approve a ship decision.")
    else:
        lines.append("## Execution summary")
        lines.append("Neither a *-summary.json nor an execution_log.json was found under "
                      "bunker/manual-test-execution — treat this run as INCOMPLETE, not a pass.")
        lines.append("")
        lines.append("## Ship / no-ship\nNO-SHIP (incomplete pipeline)")

    report_text = "\n".join(lines)
    out = ticket_dir / "qa-report.md"
    out.write_text(report_text, encoding="utf-8")

    if summary:
        # See pipelines/evals.py: opt-in (PIPELINE_EVALS=1), never fails the stage, and does not
        # yet gate ship/no-ship — this is a new, uncalibrated signal, not a pass/fail gate. This
        # is the ONLY update_current_span call in this stage's span, so it's safe to call with
        # just this metadata (no earlier call here to accidentally clobber).
        outcome = evaluate_report_summary(source_text=json.dumps(summary, indent=2), summary_text=report_text)
        if outcome is not None:
            update_current_span(metadata={"eval_summarization": asdict(outcome)})
            score = f"{outcome.score:.2f}" if outcome.score is not None else "n/a"
            print(f"[flow] eval(report summarization): score={score} "
                  f"threshold={outcome.threshold} success={outcome.success} — {outcome.reason[:200]}")

    return out


def main() -> int:
    """Arg parsing and trace plumbing only.

    argparse deliberately runs OUTSIDE the traced root: parse_args() raises SystemExit on bad
    args and on --help, and SystemExit is a BaseException, not an Exception. If it fired inside
    the root span, a tracer that only catches Exception would leave that span open and flush
    nothing. Keeping it out here also gives the root span meaningful arguments instead of ().
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ticket-id", required=True,
                         help="The work item / issue / user story id to run the pipeline for.")
    parser.add_argument("--pr-id", default=None,
                        help="One PR id, or a comma-separated list. Every PR is analysed "
                             "separately and ALL must produce a report for the stage to pass.")
    args = parser.parse_args()

    ticket_dir = artifacts_dir(args.ticket_id)
    # deepeval discards traces entirely when no Confident AI key is set, so the local sink is
    # what makes tracing worth anything here. One file per ticket, appended per run.
    set_trace_output(ticket_dir / "trace.jsonl")
    print(tracing_banner())
    print(evals_banner())
    print(f"[flow] artifacts -> {ticket_dir}")

    try:
        return run(args.ticket_id, args.pr_id, ticket_dir)
    finally:
        # Explicit rather than trusting interpreter-exit ordering: a background/daemon exporter
        # can be killed mid-flight at shutdown, and the trace that gets dropped is the one you
        # most wanted to read.
        flush_traces()


@observe(name="qa_pipeline")
def run(ticket_id: str, pr_id, ticket_dir: Path) -> int:
    """The traced root. One pipeline run == one trace."""
    ran, skipped = [], []

    def finish(code: int, note: str = "") -> int:
        # Distinguish SKIPPED from FAILED. main() fabricates StageResult(..., False, "") for
        # stages that never executed, and stage_report renders those as "FAILED" — so without
        # this the trace would claim a stage failed while containing no span for it at all.
        update_current_trace(
            output=f"exit={code}" + (f" ({note})" if note else ""),
            metadata={"ticket_id": ticket_id, "pr_id": pr_id, "ticket_dir": str(ticket_dir),
                      "exit_code": code, "stages_run": ran, "stages_skipped": skipped,
                      "outcome": note or ("ok" if code == 0 else "failed")},
        )
        return code

    update_current_trace(
        name="qa_pipeline", thread_id=str(ticket_id), tags=["qa-pipeline"],
        input=f"ticket_id={ticket_id} pr_id={pr_id}",
        metadata={"ticket_id": ticket_id, "pr_id": pr_id, "ticket_dir": str(ticket_dir)},
    )

    groom = stage_groom(ticket_id, ticket_dir); ran.append("groom")
    print(f"[flow] groom: {'OK' if groom.ok else 'NO ARTIFACT FOUND'}")

    impact = None
    if pr_id:
        impact = stage_impact(pr_id, ticket_dir); ran.append("impact")
        suffix = f" — {impact.note}" if impact.note else ""
        print(f"[flow] impact: {'OK' if impact.ok else 'FAILED'}{suffix}")
        if not impact.ok:
            print("[flow] stopping: impact analysis stage did not produce a usable report.")
            skipped += ["generate", "execute"]
            stage_report(ticket_id, ticket_dir, groom, impact,
                         StageResult("generate", False, ""), StageResult("execute", False, ""))
            return finish(1, "impact stage produced no usable report")
    else:
        skipped.append("impact")

    # groom's artifacts are never copied into ticket_dir the way impact's/generate's are (see the
    # GROOM_DIRS comment above), so read them straight from groom.new_artifacts here — the only
    # place those Path objects are in scope. Feeds stage_generate's groundedness eval; a failed
    # groom (groom.ok=False) leaves this empty and the eval reports "no context" rather than
    # guessing.
    acceptance_criteria = "\n\n".join(
        p.read_text(encoding="utf-8") for p in groom.new_artifacts if p.suffix == ".md"
    ) if groom.ok else ""

    generate, testsuite_json = stage_generate(ticket_id, ticket_dir, acceptance_criteria); ran.append("generate")
    print(f"[flow] generate: {'OK' if generate.ok else 'NO TESTSUITE JSON FOUND'}")
    if not generate.ok:
        print("[flow] stopping: no test cases were generated to execute.")
        skipped.append("execute")
        stage_report(ticket_id, ticket_dir, groom, impact, generate, StageResult("execute", False, ""))
        return finish(1, "no test cases generated")

    execute = stage_execute(ticket_id, ticket_dir); ran.append("execute")
    print(f"[flow] execute: {'OK' if execute.ok else 'NO EXECUTION REPORT FOUND'}")

    report_path = stage_report(ticket_id, ticket_dir, groom, impact, generate, execute)
    ran.append("report")
    print(f"[flow] qa-report -> {report_path}")
    return finish(0 if execute.ok else 1)


if __name__ == "__main__":
    sys.exit(main())
