"""Local, headless QA flow: groom -> impact (optional, needs --pr-id) -> generate -> execute -> report.

Every stage delegates to the SAME agents this repo's slash-commands use, verified against
.claude/commands/*.md and .claude/agents/*.md on 2026-07-11. See pipelines/README.md for the
full list of corrections made against the original draft this was built from. This script does
not publish anywhere: no TFS writes, no PR comments, no test-result uploads, no --publish flag.

Usage:
    python3 -m pipelines.flow --story-id <id> [--pr-id <id>]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pipelines.common import (
    REPO_ROOT, artifacts_dir, snapshot_dir, new_files_since,
    run_agent_sync, write_stage_log, StageResult,
)

GROOMING_REPORTS = REPO_ROOT / "grooming-reports"
PR_ANALYSIS_REPORTS = REPO_ROOT / "pr-analysis-reports"
BUNKER_TESTCASES = REPO_ROOT / "bunker" / "test-case-reports"
BUNKER_EXECUTION = REPO_ROOT / "bunker" / "manual-test-execution"

# Tool allowlists per stage, grounded in each agent's real `tools:` frontmatter.
GROOM_TOOLS = ["Task", "Bash", "Read", "Write", "Grep", "Glob", "Edit"]
IMPACT_TOOLS = ["Task", "Bash", "Read", "Write", "Grep", "Glob", "Edit"]
# test-case-generation-agent.md declares "All tools" — grant the concrete set it actually
# needs (TFS MCP for the story/AC + Playwright MCP if it inspects the live app for grounding).
GENERATE_TOOLS = ["Task", "Bash", "Read", "Write", "Grep", "Glob", "Edit",
                   "mcp__playwright__*", "mcp__rp-azure-devops__*", "mcp__rpdevops__*"]
# manual-test-execution-agent.md tools: Task, Bash, Read, Write, Grep, Glob, Edit,
# mcp__playwright__*, mcp__rp-azure-devops__*, mcp__rpdevops__*
# The draft this replaced only granted mcp__playwright__* here. TC discovery (Step 0.5 in the
# agent) and env auto-detection both need the TFS MCP servers too, so they're included below.
EXECUTE_TOOLS = ["Task", "Bash", "Read", "Write", "Grep", "Glob", "Edit",
                  "mcp__playwright__*", "mcp__rp-azure-devops__*", "mcp__rpdevops__*"]


def stage_groom(story_id: str, story_dir: Path) -> StageResult:
    before = snapshot_dir(GROOMING_REPORTS)
    transcript = run_agent_sync(
        f'Use the Task tool with subagent_type="user-story-groomer" to groom the following '
        f'user story: {story_id}',
        allowed_tools=GROOM_TOOLS,
    )
    write_stage_log(story_dir, "01-groom", transcript)
    artifacts = new_files_since(GROOMING_REPORTS, before)
    return StageResult("groom", ok=bool(artifacts), transcript=transcript, new_artifacts=artifacts)


def stage_impact(pr_id: str, story_dir: Path) -> StageResult:
    before = snapshot_dir(PR_ANALYSIS_REPORTS)
    transcript = run_agent_sync(
        f'Use the Task tool with subagent_type="pr-impact-analyzer" to analyze the following '
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
        return StageResult(
            "impact", ok=False, transcript=transcript, new_artifacts=artifacts,
            note="pr-impact-analyzer fell back to a data-collection script (shell sandboxed) "
                 "instead of producing a report. This stage cannot complete headlessly as-is.",
        )

    impact_report = next((p for p in artifacts if p.suffix == ".md"), None)
    if impact_report:
        (story_dir / "impact-report.md").write_text(
            impact_report.read_text(encoding="utf-8"), encoding="utf-8")
    return StageResult("impact", ok=bool(impact_report), transcript=transcript, new_artifacts=artifacts)


def stage_generate(story_id: str, story_dir: Path):
    before = snapshot_dir(BUNKER_TESTCASES)
    transcript = run_agent_sync(
        f'Use the Task tool with subagent_type="test-case-generation-agent" to generate test '
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

    return StageResult("generate", ok=bool(testsuite_json), transcript=transcript,
                        new_artifacts=artifacts), testsuite_json


def stage_execute(story_id: str, story_dir: Path) -> StageResult:
    before = snapshot_dir(BUNKER_EXECUTION)
    # Work-Item Mode: pass the bare story id so the agent runs ITS OWN TC-discovery chain
    # (TFS-linked -> local bunker, i.e. exactly what stage_generate just populated) and its own
    # env auto-detection. The draft this replaced routed through the /execute-tests skill,
    # which requires planId+suiteId we don't have at this point in the flow — using the agent
    # directly with a story id is the better-grounded fit for this pipeline.
    transcript = run_agent_sync(
        f'Use the Task tool with subagent_type="manual-test-execution-agent" to execute user '
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
    summary_json = next((p for p in artifacts if p.name.endswith("-summary.json")), None)
    if summary_json:
        (run_dir / summary_json.name).write_text(
            summary_json.read_text(encoding="utf-8"), encoding="utf-8")
    html_report = next((p for p in artifacts if p.suffix == ".html"), None)
    if html_report:
        (run_dir / html_report.name).write_text(
            html_report.read_text(encoding="utf-8"), encoding="utf-8")

    return StageResult("execute", ok=bool(summary_json), transcript=transcript, new_artifacts=artifacts)


def stage_report(story_id: str, story_dir: Path, groom: StageResult,
                  impact, generate: StageResult, execute: StageResult) -> Path:
    """Synthesize the final ship/no-ship qa-report.md natively — no agent call here.

    The draft this replaced assumed this stage should call automation-report-analyzer /
    /analyze-report. That agent is grounded specifically in *Extent* HTML reports produced by
    the Selenium/TestNG suites under src/test/java (its own description says "Extent automation
    report... browser, OS, Selenium version, hostname"). The execute stage above produces a
    DIFFERENT report shape — manual-test-execution-agent's own HTML/PDF/summary.json. Feeding
    one into the other would misparse it, so this stage reads execute's summary.json directly.
    automation-report-analyzer remains a separate, useful tool for actual `mvn test` /
    TestNG regression-suite reports — just not for this pipeline's manual-execution stage.
    """
    summary_path = next((story_dir / "run").glob("*-summary.json"), None)
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path else {}

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
        lines.append("## Execution summary (from manual-test-execution-agent)")
        lines.append(f"```json\n{json.dumps(summary, indent=2)}\n```")
        lines.append("")
        lines.append("## Ship / no-ship")
        lines.append("Manual call required — inspect the counts above. This script "
                      "deliberately does not auto-approve a ship decision.")
    else:
        lines.append("## Execution summary")
        lines.append("No summary.json was found under bunker/manual-test-execution — "
                      "treat this run as INCOMPLETE, not a pass.")
        lines.append("")
        lines.append("## Ship / no-ship\nNO-SHIP (incomplete pipeline)")

    out = story_dir / "qa-report.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--story-id", required=True)
    parser.add_argument("--pr-id", default=None)
    args = parser.parse_args()

    story_dir = artifacts_dir(args.story_id)
    print(f"[flow] artifacts -> {story_dir}")

    groom = stage_groom(args.story_id, story_dir)
    print(f"[flow] groom: {'OK' if groom.ok else 'NO ARTIFACT FOUND'}")

    impact = None
    if args.pr_id:
        impact = stage_impact(args.pr_id, story_dir)
        suffix = f" — {impact.note}" if impact.note else ""
        print(f"[flow] impact: {'OK' if impact.ok else 'FAILED'}{suffix}")
        if not impact.ok:
            print("[flow] stopping: impact analysis stage did not produce a usable report.")
            stage_report(args.story_id, story_dir, groom, impact,
                         StageResult("generate", False, ""), StageResult("execute", False, ""))
            return 1

    generate, testsuite_json = stage_generate(args.story_id, story_dir)
    print(f"[flow] generate: {'OK' if generate.ok else 'NO TESTSUITE JSON FOUND'}")
    if not generate.ok:
        print("[flow] stopping: no test cases were generated to execute.")
        stage_report(args.story_id, story_dir, groom, impact, generate, StageResult("execute", False, ""))
        return 1

    execute = stage_execute(args.story_id, story_dir)
    print(f"[flow] execute: {'OK' if execute.ok else 'NO SUMMARY.JSON FOUND'}")

    report_path = stage_report(args.story_id, story_dir, groom, impact, generate, execute)
    print(f"[flow] qa-report -> {report_path}")
    return 0 if execute.ok else 1


if __name__ == "__main__":
    sys.exit(main())
