# QA Pipeline — interactive playbook

**This is the canonical definition of Mohammed Affan's end-to-end QA sequence.** It is the
single source of truth for what "run QA pipeline on \<story\>" does. Edit *this file* to change
the sequence — the trigger command (`.claude/commands/qa-pipeline.md`) is a thin router that
just reads and executes what's here.

> This is the **interactive** path — Claude drives it inside a live Claude Code session (live
> browser, live DB, judgement calls). The Python `flow.py` in this folder is a **partial,
> headless** alternative (groom → impact → generate → execute → report, no DB verify, no TFS
> writes) that has never been run end-to-end. See `README.md`.

---

## Trigger

Any of these, with a TFS work-item id (User Story / Bug / Feature):

- `run QA pipeline on <id>`
- `run QA pipeline on user story <id>`
- `/qa-pipeline <id>`

**The id is the only required input.** The PR-impact and CCT stages resolve the story's linked
PRs themselves — do not ask for a PR id. If the user *volunteers* a PR id, pass it through.

---

## Standing preferences (these are pre-answered — DO NOT ask about them per run)

The whole point of this playbook is zero back-and-forth. The following are decided; run on them
without asking:

1. **Environment — auto-detect from the story's linked PR branch**, do not ask:
   `release-upcoming → preview`, `patch-upcoming → sat`, `develop → qa`. **Default to `preview`**
   when there's no linked PR or the branch is ambiguous. State which env you picked and why.
2. **Test data — find it in the live system, don't seed it.** Use existing entities/invoices.
   For invoices in a required workflow status, clear the Invoice Summary "from" date to surface
   candidates (see memory `invoice-edit-test-data-via-cleared-from-date`). Craft fixtures (PDFs,
   etc.) only when nothing suitable exists.
3. **Grounding is mandatory — no hallucination.** Every locator/step is grounded in the LIVE app
   + existing page objects (`src/test/java/com/rp/ao/pages/`) before it is written or executed.
   No invented locators, no static data.
4. **Additive & non-destructive.** Never overwrite a populated `@AzureTestCaseId`; append test
   cases, never replace. Never log or commit decoded (base64) credentials.
5. **DB (preview) is the source of truth, not the green checkmark.** A UI pass is not a pass
   until backend state is confirmed (see Stage 6).
6. **Exactly ONE human gate: the TFS write (Stage 8).** Every stage before it runs straight
   through. Ship / no-ship is *reported*, never auto-approved.

---

## The sequence

Run stages in order. After each stage, confirm it produced its artifact before continuing. A
**HARD** stage that produces nothing stops the pipeline with a clear message (don't silently
proceed and call an incomplete run a pass). A **SOFT** stage logs a warning and continues.

All artifacts live under `bunker/` in per-work-item folders. Trust each agent's *reported*
output path over the paths guessed here.

### Stage 1 — Groom + evaluate  *(HARD)*
- **Agent:** `user-story-groomer` (a thin alias that routes into the unified **us-eval**
  evaluation+grooming flow).
- **Invoke:** `Use the Task tool with subagent_type="user-story-groomer" to groom and evaluate
  user story <id>.`
- **Done when:** a story-analysis report exists under `bunker/story-analysis-reports/`.
- **Use it for:** the acceptance criteria + quality gaps that shape which test cases matter.

### Stage 2 — PR impact analysis  *(SOFT — skip if the story has no linked PRs)*
- **Agent:** `pr-impact-analyzer` (accepts a work-item id and resolves its linked PRs).
- **Invoke:** `Use the Task tool with subagent_type="pr-impact-analyzer" to analyze work item
  <id>.`
- **Done when:** a report exists under `bunker/pr-analysis-reports/`.
- **Note:** if this agent falls back to writing a data-collection script for a human to run
  (sandboxed shell), treat that as "couldn't complete headlessly" — log it, don't count it as a
  pass. Regression/breakage findings feed Stage 4 and the ship/no-ship call.

### Stage 3 — CCT cross-center impact  *(SOFT)*
- **Agent:** `cct-impact-analyzer-agent`.
- **Invoke:** `Use the Task tool with subagent_type="cct-impact-analyzer-agent" for cct impact
  of <id>.`
- **Done when:** output exists under `bunker/cct-impact-reports/<TYPE>_<id>/`.
- **Use it for:** which *other* apps (OpsBuyer → Merchant → Susan → OpsCapture → OpsBid,
  OneSite/OpsXchange) and cross-center workflows must also be exercised. Merge these scenarios
  into the Stage 5 execution scope — don't test the changed screen in isolation.

### Stage 4 — Generate test cases  *(HARD)*
- **Agent:** `test-case-generation-agent`.
- **Invoke:** `Use the Task tool with subagent_type="test-case-generation-agent" to generate
  test cases for work item <id>.`
- **Feed it:** the Stage 1 gaps, Stage 2 impact, and Stage 3 cross-center scenarios so coverage
  reflects the real blast radius.
- **Done when:** a canonical test-suite JSON (+ CSV/Excel/MD/HTML + TFS push payloads) exists
  under `bunker/test-case-reports/<id>/`.

### Stage 5 — Execute live  *(HARD)*
- **Agent:** `manual-test-execution-agent`, **Work-Item Mode** (bare id, so it runs its own
  TC-discovery: TFS-linked → the local bunker folder Stage 4 just wrote).
- **Invoke:** `Use the Task tool with subagent_type="manual-test-execution-agent" to execute
  user story <id>.`
- **Environment:** the agent auto-detects per the mapping above; confirm it landed on the
  expected env.
- **Done when:** a `*-summary.json` (+ HTML/PDF + per-step screenshots + `.spec.ts`) exists
  under the agent's execution folder (`bunker/manual-test-execution/<TYPE>_<id>/` or
  `bunker/test-execution-reports/<id>/`). This agent does **not** push to TFS.
- **Evidence discipline:** capture per-step screenshots named per test case (e.g. `TCn_stepK_PASS.png`)
  so each executed TC has its own evidence bundle. These are the exact screenshots that get posted
  to **each Test Case's Discussion** at Stage 8 — one evidence comment *per test case* (its verdict +
  its own screenshots), not just a single roll-up on the story.
- **Export/download features — visual proof of the artifact, not just a programmatic check.** When a
  feature produces a downloaded file (Excel/PDF/CSV), **retain the file** and capture a *visual* of its
  actual contents plus a **side-by-side** of the on-screen source vs the exported data (a per-row/
  per-value reconciliation). Verifying the file only by unzipping/parsing it is insufficient evidence —
  the reviewer needs to see both the source UI and the exported artifact. Keep the downloaded file in
  the execution folder so the side-by-side can be regenerated.

### Stage 6 — DB verification (preview)  *(HARD when the feature has backend state; else SOFT)*
- **No agent — Claude does this directly.** Connect to preview and confirm the backend actually
  changed the way the UI implied.
- **How:** preview merchant DB `RCVOPTDBSQL01 / opsmerchantpr` (`op_gateway`), T-SQL via
  `System.Data.SqlClient` in PowerShell (see memory `opsmerchant-preview-db-and-ui-testing`).
  Correlate rows by a tag written during execution (e.g. a Notes value like `QA<id>-TCxx`).
  Big tables (`opsfile`, etc.): always filter on `lastmodified` and use `NOLOCK`, or queries
  time out. For invoice XML lookups see `ops-invoice-xml-where-in-merchant`.
- **Done when:** each executed TC has a recorded DB verdict (pass/fail + the evidence row).

### Stage 7 — Report  *(always runs)*
- **No agent — synthesize natively.** Read the Stage 5 summary.json + Stage 6 verdicts and write
  `bunker/test-case-reports/<id>/qa-report.md` (or alongside the execution artifacts):
  per-stage status, execution counts, DB verdicts, and a **ship / no-ship recommendation**.
- Ship/no-ship is a recommendation for the user — **never auto-approve it.**

### Stage 8 — Publish to TFS  *(THE ONE GATE)*
- **STOP and show the write plan before doing anything.** List exactly what will be written:
  - test cases to create (titles) and how they'll link to the story (**TestedBy-Reverse**
    relation — attach needs no plan/suite id),
  - **per-test-case evidence comments** — one `System.History` comment *on each Test Case work
    item*, carrying that case's verdict (PASS/FAIL/BLOCKED/SKIP + one-line reason) and **its own**
    embedded per-step screenshots + DB verdict. This is mandatory and separate from…
  - a single roll-up **evidence comment on the story** (`System.History`) summarising counts,
    key defects and a few headline screenshots,
  - state/field changes. Test Case **States** here are `Design → To Be Automated → Automated`,
    plus `Manual` and `Closed`. Set UI-automatable passed cases to **`To Be Automated`** and
    manual-only passed cases to **`Manual`**; leave blocked/skipped cases `Design`. NB: moving a
    case to `To Be Automated`/`Automated` **requires** four picklists to be set in the same PATCH
    (`Custom.AutomationType`, `Custom.AutomationPlanned`, `Custom.QATeam`, `Custom.TestCaseOptimized`)
    — mirror an existing case in that state. And **creating** a Test Case requires
    `Microsoft.VSTS.Common.Priority` + `Custom.TestType`. Test Case work items **cannot be deleted**
    via the API. See memory `tfs-create-testcase-and-link-to-story`.
- **Wait for a single confirmation.** On "yes", execute all writes in one batch.
- **Auth:** TFS REST via **Windows Integrated Auth** (`-UseDefaultCredentials`) against
  `.../Realpage/SpendAndAccounting/_apis` — the stored PAT is dead (401). See memories
  `tfs-rest-auth-windows-integrated`, `tfs-create-testcase-and-link-to-story`, and the evidence
  embedding recipe + gotchas in `us2979947-docsplit-qa-pipeline` (upload attachment → `<img>`
  in System.History; helpers must use `Write-Host` not `Write-Output` or the URL corrupts).
- **Never overwrite** a populated `testCaseIds` / existing test case — append only.

---

## Failure & resume

- A HARD stage with no artifact → stop, write the report marking the run INCOMPLETE / NO-SHIP,
  say exactly which stage failed and why. Don't fabricate downstream results.
- Stages are idempotent enough to resume: re-running re-invokes the agent, which appends. If a
  stage already has fresh artifacts in `bunker/`, note it and offer to reuse rather than
  regenerate.
- If Playwright MCP is not connected, the execute agent handles its own fallback — don't
  substitute a different execution method at this orchestration layer.
