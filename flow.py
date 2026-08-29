"""Local, headless QA flow: groom -> impact (optional, needs --pr-id) -> generate -> execute -> report.

Every stage delegates to the SAME agents this repo's slash-commands use. Agent names and
output paths re-verified against .claude/agents/*.md on 2026-08-27 — the previous set was
stale (all four subagent_type strings had been renamed, and two watched output directories
pointed at paths no agent writes to). See pipelines/README.md. This script does not publish
anywhere: no TFS writes, no PR comments, no test-result uploads, no --publish flag.

Usage:
    python3 -m pipelines.flow --story-id <id> [--pr-id <id>]
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from pipelines.common import (
    REPO_ROOT, artifacts_dir, snapshot_dir, new_files_since,
    run_agent_sync, write_stage_log, StageResult,
)
from pipelines.evals import (
    evaluate_generate_groundedness, evaluate_report_summary, evals_banner,
)
from pipelines.tracing import (
    observe, update_current_span, update_current_trace, flush_traces,
    set_trace_output, tracing_banner,
)

# These previously pointed at repo-root folders no agent has ever written to, so
# new_files_since() returned [] unconditionally and groom/impact were always ok=False.
#
# Groom watches TWO directories, and that is deliberate rather than defensive clutter. The
# destination depends on which route actually services the request, verified on a real run
# 2026-08-27: help-user-story-groomer's own file documents bunker/story-grooming-reports/,
# but that agent FAILS TO REGISTER (its YAML frontmatter has an unquoted description
# containing "excluded: <group>", so the colon breaks the parse). The delegating session
# silently falls back to the grooming SKILL, which writes output/grooming-reports/ instead.
# Watch both so this stage reports honestly whichever path is taken; drop the alt once the
# frontmatter is fixed and the agent registers.
GROOMING_REPORTS = REPO_ROOT / "bunker" / "story-grooming-reports"
GROOMING_REPORTS_ALT = REPO_ROOT / "output" / "grooming-reports"
GROOM_DIRS = [GROOMING_REPORTS, GROOMING_REPORTS_ALT]
PR_ANALYSIS_REPORTS = REPO_ROOT / "bunker" / "pr-analysis-reports"
BUNKER_TESTCASES = REPO_ROOT / "bunker" / "test-case-reports"
BUNKER_EXECUTION = REPO_ROOT / "bunker" / "manual-test-execution"

# Tool allowlists per stage, grounded in each agent's real `tools:` frontmatter.
GROOM_TOOLS = ["Task", "Bash", "Read", "Write", "Grep", "Glob", "Edit"]
IMPACT_TOOLS = ["Task", "Bash", "Read", "Write", "Grep", "Glob", "Edit"]
# core-test-case-generator.md declares "All tools" — grant the concrete set it actually
# needs (TFS MCP for the story/AC + Playwright MCP if it inspects the live app for grounding).
GENERATE_TOOLS = ["Task", "Bash", "Read", "Write", "Grep", "Glob", "Edit",
                   "mcp__playwright__*", "mcp__rp-azure-devops__*", "mcp__rpdevops__*"]
# core-manual-testcase-executor.md tools: Task, Bash, Read, Write, Grep, Glob, Edit,
# mcp__playwright__*, mcp__rp-azure-devops__*, mcp__rpdevops__*
# The draft this replaced only granted mcp__playwright__* here. TC discovery (Step 0.5 in the
# agent) and env auto-detection both need the TFS MCP servers too, so they're included below.
EXECUTE_TOOLS = ["Task", "Bash", "Read", "Write", "Grep", "Glob", "Edit",
                  "mcp__playwright__*", "mcp__rp-azure-devops__*", "mcp__rpdevops__*"]


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


@observe(type="agent", available_tools=GROOM_TOOLS)
def stage_groom(story_id: str, story_dir: Path) -> StageResult:
    before = _snapshot_all(GROOM_DIRS)
    transcript = run_agent_sync(
        f'Use the Task tool with subagent_type="help-user-story-groomer" to groom the following '
        f'user story: {story_id}',
        allowed_tools=GROOM_TOOLS,
    )
    write_stage_log(story_dir, "01-groom", transcript)
    artifacts = _new_files_all(before)
    return _record_stage(StageResult("groom", ok=bool(artifacts), transcript=transcript, new_artifacts=artifacts))


@observe(type="agent", available_tools=IMPACT_TOOLS)
def stage_impact(pr_id: str, story_dir: Path) -> StageResult:
    before = snapshot_dir(PR_ANALYSIS_REPORTS)
    transcript = run_agent_sync(
        f'Use the Task tool with subagent_type="core-pr-impact-analyzer" to analyze the following '
        f'pull request: {pr_id}',
        allowed_tools=IMPACT_TOOLS,
    )
    write_stage_log(story_dir, "02-impact", transcript)
    artifacts = new_files_since(PR_ANALYSIS_REPORTS, before)

    # analyze-pr.md documents a sandboxed-shell fallback: the agent writes a .py/.mjs/.ps1
    # data-collection script and asks a HUMAN to run it, then re-invoke with "data collected
    # for {prId}". There is no human in this loop — treat that fallback as a hard stop instead
    # of silently treating a dropped script as a successful report.
    if artifacts and all(p.suffix in (".py", ".mjs", ".ps1") for p in artifacts):
        return _record_stage(StageResult(
            "impact", ok=False, transcript=transcript, new_artifacts=artifacts,
            note="core-pr-impact-analyzer fell back to a data-collection script (shell sandboxed) "
                 "instead of producing a report. This stage cannot complete headlessly as-is.",
        ))

    impact_report = next((p for p in artifacts if p.suffix == ".md"), None)
    if impact_report:
        (story_dir / "impact-report.md").write_text(
            impact_report.read_text(encoding="utf-8"), encoding="utf-8")
    return _record_stage(StageResult("impact", ok=bool(impact_report), transcript=transcript, new_artifacts=artifacts))


@observe(type="agent", available_tools=GENERATE_TOOLS)
def stage_generate(story_id: str, story_dir: Path, acceptance_criteria: str = ""):
    before = snapshot_dir(BUNKER_TESTCASES)
    transcript = run_agent_sync(
        f'Use the Task tool with subagent_type="core-test-case-generator" to generate test '
        f'cases for the following user story: {story_id}',
        allowed_tools=GENERATE_TOOLS,
        max_turns=80,
    )
    write_stage_log(story_dir, "03-generate", transcript)
    artifacts = new_files_since(BUNKER_TESTCASES, before)
    testsuite_json = next((p for p in artifacts if p.name.endswith("-tests.testsuite.json")), None)

    if artifacts:
        gen_dir = story_dir / "generated-tests"
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
            story_id=story_id,
            acceptance_criteria=acceptance_criteria,
            testsuite_text=testsuite_json.read_text(encoding="utf-8"),
        )
        if outcome is not None:
            eval_metadata["eval_groundedness"] = asdict(outcome)
            score = f"{outcome.score:.2f}" if outcome.score is not None else "n/a"
            print(f"[flow] eval(generate groundedness): score={score} "
                  f"threshold={outcome.threshold} success={outcome.success} — {outcome.reason[:200]}")

    return _record_stage(result, eval_metadata or None), testsuite_json


@observe(type="agent", available_tools=EXECUTE_TOOLS)
def stage_execute(story_id: str, story_dir: Path) -> StageResult:
    before = snapshot_dir(BUNKER_EXECUTION)
    # Work-Item Mode: pass the bare story id so the agent runs ITS OWN TC-discovery chain
    # (TFS-linked -> local bunker, i.e. exactly what stage_generate just populated) and its own
    # env auto-detection. The draft this replaced routed through the /execute-tests skill,
    # which requires planId+suiteId we don't have at this point in the flow — using the agent
    # directly with a story id is the better-grounded fit for this pipeline.
    transcript = run_agent_sync(
        f'Use the Task tool with subagent_type="core-manual-testcase-executor" to execute user '
        f'story {story_id}',
        allowed_tools=EXECUTE_TOOLS,
        # This stage drives a live browser through many steps with mandatory
        # wait+snapshot+screenshot per action — a low generic max_turns will cut it off
        # mid-suite. 200 is a rough starting budget; watch for max_turns cutoffs on real runs
        # and raise further if needed (see README "known risks").
        max_turns=200,
    )
    write_stage_log(story_dir, "04-execute", transcript)
    artifacts = new_files_since(BUNKER_EXECUTION, before)

    run_dir = story_dir / "run"
    run_dir.mkdir(exist_ok=True)
    # This stage used to gate on `*-summary.json` and copy `*.html`. Neither can ever exist:
    # core-manual-testcase-executor.md:43 states the MD report and PDF are its ONLY two
    # deliverables — "no summary.json, no persisted HTML" — and its own self-check at line
    # 1150 FAILS the run if a summary.json is found. So execute.ok was hardcoded False by
    # construction, which forced main() to exit 1 and stage_report to write NO-SHIP on every
    # run. Gate on the MD report (the documented deliverable) instead, and carry
    # execution_log.json across as the machine-readable summary — the agent calls it the
    # "single source of truth" the MD is built from, and it is explicitly not deleted.
    report_md = next((p for p in artifacts if p.name.endswith("-execution-report.md")), None)
    if report_md:
        (run_dir / report_md.name).write_text(
            report_md.read_text(encoding="utf-8"), encoding="utf-8")
    exec_log = next((p for p in artifacts if p.name == "execution_log.json"), None)
    if exec_log:
        (run_dir / exec_log.name).write_text(
            exec_log.read_text(encoding="utf-8"), encoding="utf-8")

    return _record_stage(StageResult("execute", ok=bool(report_md), transcript=transcript, new_artifacts=artifacts))


@observe(name="stage_report")
def stage_report(story_id: str, story_dir: Path, groom: StageResult,
                  impact, generate: StageResult, execute: StageResult) -> Path:
    """Synthesize the final ship/no-ship qa-report.md natively — no agent call here.

    The draft this replaced assumed this stage should call automation-report-analyzer /
    /analyze-report. That agent is grounded specifically in *Extent* HTML reports produced by
    the Selenium/TestNG suites under src/test/java (its own description says "Extent automation
    report... browser, OS, Selenium version, hostname"). The execute stage above produces a
    DIFFERENT report shape — core-manual-testcase-executor's own MD/PDF deliverables plus its
    internal execution_log.json. Feeding one into the other would misparse it, so this stage
    reads execute's execution_log.json directly.
    automation-report-analyzer remains a separate, useful tool for actual `mvn test` /
    TestNG regression-suite reports — just not for this pipeline's manual-execution stage.
    """
    log_path = next((story_dir / "run").glob("execution_log.json"), None)
    summary = json.loads(log_path.read_text(encoding="utf-8")) if log_path else {}

    lines = [f"# QA Report — Story {story_id}", "", "## Stage status"]
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
        lines.append("## Execution summary (from core-manual-testcase-executor)")
        lines.append(f"```json\n{json.dumps(summary, indent=2)}\n```")
        lines.append("")
        lines.append("## Ship / no-ship")
        lines.append("Manual call required — inspect the counts above. This script "
                      "deliberately does not auto-approve a ship decision.")
    else:
        lines.append("## Execution summary")
        lines.append("No execution_log.json was found under bunker/manual-test-execution — "
                      "treat this run as INCOMPLETE, not a pass.")
        lines.append("")
        lines.append("## Ship / no-ship\nNO-SHIP (incomplete pipeline)")

    report_text = "\n".join(lines)
    out = story_dir / "qa-report.md"
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
    parser.add_argument("--story-id", required=True)
    parser.add_argument("--pr-id", default=None)
    args = parser.parse_args()

    story_dir = artifacts_dir(args.story_id)
    # deepeval discards traces entirely when no Confident AI key is set, so the local sink is
    # what makes tracing worth anything here. One file per story, appended per run.
    set_trace_output(story_dir / "trace.jsonl")
    print(tracing_banner())
    print(evals_banner())
    print(f"[flow] artifacts -> {story_dir}")

    try:
        return run(args.story_id, args.pr_id, story_dir)
    finally:
        # Explicit rather than trusting interpreter-exit ordering: a background/daemon exporter
        # can be killed mid-flight at shutdown, and the trace that gets dropped is the one you
        # most wanted to read.
        flush_traces()


@observe(name="qa_pipeline")
def run(story_id: str, pr_id, story_dir: Path) -> int:
    """The traced root. One pipeline run == one trace."""
    ran, skipped = [], []

    def finish(code: int, note: str = "") -> int:
        # Distinguish SKIPPED from FAILED. main() fabricates StageResult(..., False, "") for
        # stages that never executed, and stage_report renders those as "FAILED" — so without
        # this the trace would claim a stage failed while containing no span for it at all.
        update_current_trace(
            output=f"exit={code}" + (f" ({note})" if note else ""),
            metadata={"story_id": story_id, "pr_id": pr_id, "story_dir": str(story_dir),
                      "exit_code": code, "stages_run": ran, "stages_skipped": skipped,
                      "outcome": note or ("ok" if code == 0 else "failed")},
        )
        return code

    update_current_trace(
        name="qa_pipeline", thread_id=str(story_id), tags=["qa-pipeline"],
        input=f"story_id={story_id} pr_id={pr_id}",
        metadata={"story_id": story_id, "pr_id": pr_id, "story_dir": str(story_dir)},
    )

    groom = stage_groom(story_id, story_dir); ran.append("groom")
    print(f"[flow] groom: {'OK' if groom.ok else 'NO ARTIFACT FOUND'}")

    impact = None
    if pr_id:
        impact = stage_impact(pr_id, story_dir); ran.append("impact")
        suffix = f" — {impact.note}" if impact.note else ""
        print(f"[flow] impact: {'OK' if impact.ok else 'FAILED'}{suffix}")
        if not impact.ok:
            print("[flow] stopping: impact analysis stage did not produce a usable report.")
            skipped += ["generate", "execute"]
            stage_report(story_id, story_dir, groom, impact,
                         StageResult("generate", False, ""), StageResult("execute", False, ""))
            return finish(1, "impact stage produced no usable report")
    else:
        skipped.append("impact")

    # groom's artifacts are never copied into story_dir the way impact's/generate's are (see the
    # GROOM_DIRS comment above), so read them straight from groom.new_artifacts here — the only
    # place those Path objects are in scope. Feeds stage_generate's groundedness eval; a failed
    # groom (groom.ok=False) leaves this empty and the eval reports "no context" rather than
    # guessing.
    acceptance_criteria = "\n\n".join(
        p.read_text(encoding="utf-8") for p in groom.new_artifacts if p.suffix == ".md"
    ) if groom.ok else ""

    generate, testsuite_json = stage_generate(story_id, story_dir, acceptance_criteria); ran.append("generate")
    print(f"[flow] generate: {'OK' if generate.ok else 'NO TESTSUITE JSON FOUND'}")
    if not generate.ok:
        print("[flow] stopping: no test cases were generated to execute.")
        skipped.append("execute")
        stage_report(story_id, story_dir, groom, impact, generate, StageResult("execute", False, ""))
        return finish(1, "no test cases generated")

    execute = stage_execute(story_id, story_dir); ran.append("execute")
    print(f"[flow] execute: {'OK' if execute.ok else 'NO EXECUTION REPORT FOUND'}")

    report_path = stage_report(story_id, story_dir, groom, impact, generate, execute)
    ran.append("report")
    print(f"[flow] qa-report -> {report_path}")
    return finish(0 if execute.ok else 1)


if __name__ == "__main__":
    sys.exit(main())
