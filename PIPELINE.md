# QA Pipeline — interactive playbook

**This is the canonical definition of this project's end-to-end QA sequence.** It is the
single source of truth for what "run QA pipeline on \<ticket\>" does. Edit *this file* to change
the sequence — the trigger command (`.claude/commands/qa-pipeline.md`) is a thin router that
just reads and executes what's here.

This template ships one working reference backend adapter, TFS/Azure DevOps (see
[`docs/adapters/tfs.md`](docs/adapters/tfs.md)), and no application-specific knowledge — fill in
[`pipeline.config.json`](pipeline.config.example.json) for your own apps/environments/repos
before running this (see [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md)).

> This is the **interactive** path — Claude drives it inside a live Claude Code session (live
> browser, live DB, judgement calls). The Python `pipelines/flow.py` in this repo is a **partial,
> headless** alternative (groom → impact → generate → execute → report, no DB verify, no TFS
> writes) that has never been run end-to-end. See `README.md`.

---

## Trigger

Any of these, with a work-item/ticket id (User Story / Bug / Feature — or your PM tool's
equivalent):

- `run QA pipeline on <id>`
- `run QA pipeline on ticket <id>`
- `/qa-pipeline <id>`

**The id is the only required input.** The PR-impact and cross-system-impact stages resolve the
ticket's linked PRs themselves — do not ask for a PR id. If the user *volunteers* a PR id, pass
it through.

---

## Standing preferences (these are pre-answered — DO NOT ask about them per run)

The whole point of this playbook is zero back-and-forth. The following are decided; run on them
without asking:

1. **Environment — auto-detect from the ticket's linked PR branch**, do not ask: read the
   mapping from `pipeline.config.json`'s `environments.branchMapping` (e.g.
   `develop → staging`). **Default to `environments.default`** when there's no linked PR or the
   branch is ambiguous. State which env you picked and why.
2. **Test data — find it in the live system, don't seed it.** Use existing entities/records.
   Craft fixtures (PDFs, etc.) only when nothing suitable exists. If your app has a known trick
   for surfacing eligible test-data candidates (e.g. clearing a stale filter), keep it documented
   as project-specific setup rather than hardcoded here.
3. **Grounding is mandatory — no hallucination.** Every locator/step is grounded in the LIVE app
   + existing page objects (path from `pipeline.config.json`'s `repos.pageObjectsPath`) before it
   is written or executed. No invented locators, no static data.
4. **Additive & non-destructive.** Never overwrite a populated test-case link/id; append test
   cases, never replace. Never log or commit decoded (base64) credentials.
5. **The DB (or your source-of-truth backend state) beats the green checkmark.** A UI pass is
   not a pass until backend state is confirmed (see Stage 6).
6. **Exactly ONE human gate: the write-back to your PM tool (Stage 8).** Every stage before it
   runs straight through. Ship / no-ship is *reported*, never auto-approved.

---

## The sequence

Run stages in order. After each stage, confirm it produced its artifact before continuing. A
**HARD** stage that produces nothing stops the pipeline with a clear message (don't silently
proceed and call an incomplete run a pass). A **SOFT** stage logs a warning and continues.

All artifacts live under `bunker/` in per-ticket folders. Trust each agent's *reported*
output path over the paths guessed here.

### Stage 1 — Groom + evaluate  *(HARD)*
- **Agent:** `us-eval` (defined in `.claude/agents/user-story-evaluation-agent.md`).
- **Invoke:** `Use the Task tool with subagent_type="us-eval" to groom and evaluate ticket <id>.`
- **Done when:** a story-analysis report exists under `bunker/story-analysis-reports/`.
- **Use it for:** the acceptance criteria + quality gaps that shape which test cases matter.

### Stage 2 — PR impact analysis  *(SOFT — skip if the ticket has no linked PRs)*
- **Agent:** `pr-impact-analyzer` (accepts a work-item id and resolves its linked PRs).
- **Invoke:** `Use the Task tool with subagent_type="pr-impact-analyzer" to analyze work item
  <id>.`
- **Done when:** a report exists under `bunker/pr-analysis-reports/`.
- **Note:** if this agent falls back to writing a data-collection script for a human to run
  (sandboxed shell), treat that as "couldn't complete headlessly" — log it, don't count it as a
  pass. Regression/breakage findings feed Stage 4 and the ship/no-ship call.

### Stage 3 — Cross-system impact  *(SOFT)*
- **Agent:** `cct-impact-analyzer-agent`.
- **Invoke:** `Use the Task tool with subagent_type="cct-impact-analyzer-agent" for cross-system
  impact of <id>.`
- **Done when:** output exists under `bunker/cct-impact-reports/<TYPE>_<id>/`.
- **Use it for:** which *other* applications and integrations (enumerated from
  `pipeline.config.json`'s `apps` list) and cross-system workflows must also be exercised. Merge
  these scenarios into the Stage 5 execution scope — don't test the changed screen in isolation.

### Stage 4 — Generate test cases  *(HARD)*
- **Agent:** `test-case-generation-agent`.
- **Invoke:** `Use the Task tool with subagent_type="test-case-generation-agent" to generate
  test cases for work item <id>.`
- **Feed it:** the Stage 1 gaps, Stage 2 impact, and Stage 3 cross-system scenarios so coverage
  reflects the real blast radius.
- **Done when:** a canonical test-suite JSON (+ CSV/Excel/MD/HTML + PM-tool push payloads) exists
  under `bunker/test-case-reports/<id>/`.

### Stage 5 — Execute live  *(HARD)*
- **Agent:** `manual-test-execution-agent`, **Work-Item Mode** (bare id, so it runs its own
  TC-discovery: PM-tool-linked → the local bunker folder Stage 4 just wrote).
- **Invoke:** `Use the Task tool with subagent_type="manual-test-execution-agent" to execute
  ticket <id>.`
- **Environment:** the agent auto-detects per the mapping above; confirm it landed on the
  expected env.
- **Done when:** a `*-summary.json` (+ HTML/PDF + per-step screenshots + `.spec.ts`) exists
  under the agent's execution folder (`bunker/manual-test-execution/<TYPE>_<id>/` or
  `bunker/test-execution-reports/<id>/`). This agent does **not** write back to your PM tool.
- **Evidence discipline:** capture per-step screenshots named per test case (e.g. `TCn_stepK_PASS.png`)
  so each executed TC has its own evidence bundle. These are the exact screenshots that get posted
  to **each Test Case's Discussion** at Stage 8 — one evidence comment *per test case* (its verdict +
  its own screenshots), not just a single roll-up on the ticket.
- **Export/download features — visual proof of the artifact, not just a programmatic check.** When a
  feature produces a downloaded file (Excel/PDF/CSV), **retain the file** and capture a *visual* of its
  actual contents plus a **side-by-side** of the on-screen source vs the exported data (a per-row/
  per-value reconciliation). Verifying the file only by unzipping/parsing it is insufficient evidence —
  the reviewer needs to see both the source UI and the exported artifact. Keep the downloaded file in
  the execution folder so the side-by-side can be regenerated.
- **Credential handling is a hard requirement, not a courtesy.** "Don't print it" is not
  sufficient — credentials can leak via channels that never hit stdout (shell env-var exports,
  base64-decode commands, a browser's save-password prompt). Never route the raw/base64
  credential value through a Bash/PowerShell tool call; decode in-model and pass straight into
  the form field; never trigger save-password; never write credentials to scratch/log files. See
  `manual-test-execution-agent.md` Step 2. If you're briefing this stage manually instead of
  letting the agent run its own instructions, repeat that list in the prompt.

### Stage 6 — Backend/DB verification  *(HARD when the feature has backend state; else SOFT)*
- **No agent — Claude does this directly.** Connect to the environment's backend and confirm it
  actually changed the way the UI implied.
- **How:** connect using the connection string named by `pipeline.config.json`'s
  `database.connectionStringEnv`. Correlate rows by a tag written during execution (e.g. a notes
  field like `QA<id>-TCxx`). For large tables, filter on a recency column (e.g. `lastmodified`)
  and use a non-locking read where your DB supports it, or queries can time out.
- **No DB login for this app? Fall back to captured API responses as the source of truth**,
  don't skip Stage 6 or downgrade it to SOFT. Capture the actual backend request/response (e.g.
  a write call and its response body, or a follow-up read's count) during live execution and
  cite that response as the verdict's evidence instead of a row. Note in the report that this
  was API-tier, not row-level, verification, and flag it as a follow-up to request DB access if
  this app recurs.
- **Done when:** each executed TC has a recorded verdict (pass/fail + the evidence row, or the
  API-response evidence per the fallback above).

### Stage 7 — Report  *(always runs)*
- **No agent — synthesize natively.** Read the Stage 5 summary.json + Stage 6 verdicts and write
  `bunker/test-case-reports/<id>/qa-report.md` (or alongside the execution artifacts):
  per-stage status, execution counts, backend verdicts, and a **ship / no-ship recommendation**.
- Ship/no-ship is a recommendation for the user — **never auto-approve it.**

### Stage 8 — Publish to your PM tool  *(THE ONE GATE)*
- **STOP and show the write plan before doing anything.** List exactly what will be written:
  - test cases to create (titles) and how they'll link to the ticket (a "tested by"-style
    relation, or your tool's equivalent),
  - **per-test-case evidence comments** — one comment *on each Test Case work item*, carrying
    that case's verdict (PASS/FAIL/BLOCKED/SKIP + one-line reason) and **its own** embedded
    per-step screenshots + backend verdict. This is mandatory and separate from…
  - a single roll-up **evidence comment on the ticket** summarising counts, key defects and a
    few headline screenshots,
  - state/field changes appropriate to your backend's Test Case state model (design → automated
    → manual → closed, or your tool's equivalent).
- **Wait for a single confirmation.** On "yes", execute all writes in one batch.
- **This template ships a TFS/Azure DevOps reference adapter** — see
  [`docs/adapters/tfs.md`](docs/adapters/tfs.md) for its auth mechanics, required fields, and
  known write-reliability quirks (inline calls vs saved scripts, JSON array collapsing, etc.). A
  different PM tool needs an equivalent adapter doc with its own write mechanics.
- **Never overwrite** a populated test-case link/id — append only.

---

## Failure & resume

- A HARD stage with no artifact → stop, write the report marking the run INCOMPLETE / NO-SHIP,
  say exactly which stage failed and why. Don't fabricate downstream results.
- Stages are idempotent enough to resume: re-running re-invokes the agent, which appends. If a
  stage already has fresh artifacts in `bunker/`, note it and offer to reuse rather than
  regenerate.
- If Playwright MCP is not connected, the execute agent handles its own fallback — don't
  substitute a different execution method at this orchestration layer.
