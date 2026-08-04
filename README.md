# QA pipeline — two ways to run

There are two implementations of the QA pipeline in this folder. **They are not equivalent** —
pick the one that matches what you're doing.

## 1. Interactive playbook — `PIPELINE.md`  ← the real, full sequence

This is the canonical, authoritative definition of the end-to-end QA sequence and the one that
reflects how testing is actually conducted here. Claude drives it inside a live Claude Code
session (live browser, live DB, judgement calls). Trigger it with:

```
run QA pipeline on <story-id>      (or)      /qa-pipeline <story-id>
```

Stages: **groom+evaluate → PR impact → CCT cross-center impact → generate → execute live →
DB verification (preview) → report → publish to TFS (one confirmation gate)**. It auto-detects
the environment from the story's linked PR branch and needs only the work-item id. See
`PIPELINE.md` for the full spec and standing preferences; `.claude/commands/qa-pipeline.md` is
the thin router that loads it.

**To change the sequence, edit `PIPELINE.md`** — not this README, not the command.

## 2. Headless Python script — `flow.py`  (partial, never run end-to-end)

`python3 -m pipelines.flow --story-id <id> [--pr-id <id>]` runs a *subset*:

```
groom -> impact (only if --pr-id given) -> generate -> execute -> report
```

It is deliberately narrower than the interactive playbook: **no CCT stage, no DB verification,
and it does not publish to TFS** (no `--publish` flag, no PR comments, no test-result uploads).
Artifacts land in `artifacts/flow/<story_id>/`. Use it only for an unattended groom/generate
subset — not as a substitute for the full `PIPELINE.md` run. It has not been run end-to-end;
see "Known risks / not yet verified" below.

## What this actually wires up, vs. the original draft it was built from

This was authored from a handoff spec that assumed a `qa-pipelines/pipelines/flow.py` would
already exist somewhere on disk. It did not — it wasn't in this repo, and the only "qa" folder
in Downloads was an unrelated Node/TypeScript project. This was written from scratch against the
real `.claude/commands/*.md` and `.claude/agents/*.md` files, on 2026-07-11. Corrections made
along the way:

1. **Argument style.** All of groom-story, analyze-pr, review-pr, generate-tests,
   analyze-report pass `$ARGUMENTS` straight through as the agent's prompt via
   `Task(subagent_type=...)` — free text, not flags. `analyze-pr` additionally accepts inline
   `key=value` pairs (`project=`, `repo=`, `planId=`, `suiteId=`) in that same string.
   `execute-tests` is the odd one out: it's a positional `<planId> <suiteId> [testCaseId] [env]`
   skill invocation, not a Task-based agent call.

2. **Execution stage.** `manual-test-execution-agent.md` does drive a real browser via
   Playwright MCP (`browser_navigate`, `browser_click`, `browser_snapshot`, etc.) — that part of
   the original assumption held. But its `tools:` frontmatter also requires
   `mcp__rp-azure-devops__*` and `mcp__rpdevops__*` for TC discovery and environment
   auto-detection (Steps 0.5/0.75 in that file). A Playwright-only allowlist would break TC
   discovery. `flow.py`'s `EXECUTE_TOOLS` includes all three.

   Also: this pipeline calls the agent directly with a bare story id (Work-Item Mode), not the
   `/execute-tests` skill/command, because that skill requires `planId`+`suiteId` which nothing
   upstream in this flow produces. The agent's own Work-Item Mode already does TFS-linked →
   local-bunker discovery, which lines up with what `generate` just wrote.

3. **MCP server registration.** `.claude/settings.local.json` already has
   `"enabledMcpjsonServers": ["playwright"]` sourced from this repo's `.mcp.json`. `flow.py`
   does not re-register a Playwright MCP server — `setting_sources=["project"]` picks it up.
   **Open question, not resolved:** `mcp__rp-azure-devops__*` / `mcp__rpdevops__*` are NOT in
   this repo's `.mcp.json`, so they must be registered at user/global scope. Whether
   `setting_sources=["project"]` also inherits user-scope MCP servers, or whether the execute
   stage needs those servers passed explicitly via `mcp_servers=`, was not verified — the
   `claude_agent_sdk` package isn't installed yet in this environment. Verify this before
   trusting a real execute-stage run; if TFS MCP calls silently no-op, this is why.

4. **Credentials.** The handoff draft assumed `TFS_PAT`. The real convention already in use in
   this repo (`.claude/agent-memory/pr-impact-analyzer/auth-and-api-versions.md`) is `ADO_PAT` +
   `TFS_ORG_URL` read from a `.env` file — `pipelines/common.py` uses those names instead. Also
   worth knowing: per that same agent's memory, the stored PAT is currently reported dead (401
   on everything) and TFS calls fall back to **Windows Integrated Auth** on this machine. That
   means for local runs under your own Windows session, TFS access may work with **no PAT env
   var set at all**. For the execute stage specifically, `manual-test-execution-agent.md`
   resolves app login credentials itself (explicit input → TFS test-case parameters →
   `src/test/resources/env/{sat,qa}.properties`) — it does not need `QA_TEST_USER` /
   `QA_TEST_PASSWORD` env vars passed in the way the original draft assumed.

5. **The final "report" stage does not call `automation-report-analyzer` / `/analyze-report`.**
   That agent is grounded in *Extent* HTML reports from the Selenium/TestNG suites
   (`mvn test` runs) — a different report shape than what `manual-test-execution-agent`
   produces (its own HTML/PDF + `*-summary.json`). `stage_report()` in `flow.py` reads that
   summary.json directly and writes `qa-report.md` natively instead of routing through a
   mismatched agent. `automation-report-analyzer` is still the right tool if you separately want
   to analyze an actual automation regression run — just not as this pipeline's last stage.

6. **`generate-tests.md`'s documented "Execute `npm run smart-generate <userstoryid>`" step
   does not apply to this repo** — there is no `package.json` here; this is a Maven/Java repo.
   That line in the command doc looks like it was copied from a different (Node-based) project
   template and never adjusted. `flow.py` does not attempt to run that npm command; it invokes
   `test-case-generation-agent` directly, which per its own description grounds generation in
   this repo's page objects and TFS data, not an npm script. Worth fixing the command doc
   itself separately.

## Known risks / not yet verified

- **`claude_agent_sdk` 0.2.116 is now installed and `common.py`'s field names were checked
  against it directly** (`dataclasses.fields(ClaudeAgentOptions)` + `inspect.signature(query)`):
  `allowed_tools`, `permission_mode`, `max_turns`, `setting_sources`, `cwd` all exist as used.
  `query()` yields `UserMessage | AssistantMessage | SystemMessage | ResultMessage | StreamEvent
  | RateLimitEvent` — `run_agent()` now only pulls text from `AssistantMessage.content` blocks
  (not `UserMessage.content`, which carries tool-result payloads, not the agent's own text) and
  surfaces `ResultMessage.is_error`/`.result` if the query itself errored.
- **`max_turns` budgets are rough guesses** (40 for groom/impact, 80 for generate, 200 for
  execute) — the execute stage in particular can run many test cases each requiring several
  Playwright round-trips per step; expect to tune this upward after watching a real suite run.
- **The impact stage's sandboxed-shell fallback is a hard stop, not a workaround.** If
  `pr-impact-analyzer` can't reach TFS directly from this process, `flow.py` reports FAILED
  rather than trying to execute the fallback script itself — that fallback is designed for a
  human in the loop, and this is intentionally not simulating that.
- **`permission_mode="bypassPermissions"` is used for every stage** so the flow can run
  unattended end to end. This should only ever point at sandbox data / non-prod environments —
  it removes the human-approval gate that normally protects against an agent taking an
  unintended action.

## Not done here (by design)

- No `azure/qa-flow.yml` or any other Azure DevOps pipeline/service-hook/variable-group changes.
- No `--publish` mode, no TFS writes, no PR comments.
- No packages installed, no env vars set, no dry run executed — this pipeline has been written
  and grounded against the real agent files, but not yet run. `pip install claude-agent-sdk`
  and any real dry run needs an explicit go-ahead since it involves installing software and
  (for stages beyond groom/generate) potentially touching credentials.
