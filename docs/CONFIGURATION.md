# Configuring this pipeline for your project

This pipeline template ships with no knowledge of your applications, environments, or PM tool
baked in — that all lives in one config file you create per project.

## 1. Create your config

**Option A — the dashboard's Configure page (recommended, no JSON editing required):**

```
cd dashboard && npm install && npm run build && cd ..
python3 -m pipelines.api
```

Opens `http://127.0.0.1:8765` in your browser — go to the **Configure** tab. Fill in your
project, PM tool, apps, and environments, then click **Save** — it writes
`pipeline.config.json` for you and checks for obvious mistakes (missing project name, malformed
URLs, duplicate app names) before saving. Re-run any time to edit an existing config — the form
loads your current values. `npm install && npm run build` is only needed once (rerun it after
pulling frontend changes); after that, just `python3 -m pipelines.api`.

Stop the server with `Ctrl+C` once you're done; it only runs while you're using the dashboard.

**Option B — by hand:**

```
cp pipeline.config.example.json pipeline.config.json
```

Either way, `pipeline.config.json` is gitignored — it's expected to hold your org's real URLs,
DB connection-string env-var names, and repo paths, so it shouldn't be committed to a public
fork of this template.

## 2. Fill in the fields

(Skip this section if you used the config UI above — the form's field hints cover the same
ground. This is the reference for editing the JSON directly.)

- **`project.name`** — your work-item-tracking project's name (used anywhere agents previously
  hardcoded a project like `SpendAndAccounting`).
- **`backend`** — which PM/work-item system you're using and how agents should reach it.
  This template ships one working reference adapter, **TFS/Azure DevOps** (`backend.type: "tfs"`
  — see [`docs/adapters/tfs.md`](adapters/tfs.md) for its auth quirks and field-name mapping).
  Using a different PM tool (Jira, GitHub Issues, Linear, ...) means writing an equivalent
  adapter doc and pointing your MCP config at the right server — the agents themselves read
  `backend.mcpToolPrefix` generically and shouldn't need edits for a new backend.
- **`repos.automationRepoRoot` / `repos.pageObjectsPath`** — where your test-automation repo and
  its page-object classes live, so the test-generation and execution agents ground locators in
  your real code instead of inventing them.
- **`environments.branchMapping`** — which environment a linked PR's source branch implies (e.g.
  `develop` → `staging`). The shipped example is illustrative; replace it with your own branch
  naming.
- **`apps`** — one entry per application under test: its name, per-environment URLs, and where
  to find credentials (an env-var pair, a properties file, etc.). Add as many entries as you
  have applications — the execution and impact-analysis agents look up apps by name from this
  list rather than having them hardcoded.
- **`database`** *(optional)* — a connection-string env var, if your pipeline includes a
  DB-verification stage. Omit this block entirely if there's no backend state worth verifying
  directly.

## 3. Set the environment variables your config references

Whatever env-var names you chose above (`TFS_ORG_URL`, `EXAMPLE_APP_USER`, `QA_DB_CONNECTION`,
etc.) need to actually be set in the shell the pipeline runs in. Nothing in this template reads
credentials from the config file itself — the config only names *where* to find them.

## 4. Run it

See `README.md` for the two ways to run the pipeline (interactive playbook vs the headless
`flow.py` script) once your config is in place.

## 5. Inspect a run afterward

The dashboard's **Tickets** tab shows every stage's real output (readiness scores, ship-risk,
regression risks, generated test cases, pass/fail counts with screenshots) for any ticket a
pipeline stage has run against — read straight from the `summary.json`/`testsuite.json` files
each agent writes under `bunker/`. Nothing new to enable; if a stage ran, its output shows up
here.

The **Traces** tab shows a visual waterfall of each headless `flow.py` run — which stage/agent
call ran, how long it took, cost, tool calls, and any errors — read straight from the
`trace.jsonl` files `pipelines/tracing.py` already writes under `artifacts/flow/<ticket_id>/`.
Nothing new to enable there either; if `PIPELINE_TRACING` hasn't been explicitly disabled, a
`flow.py` run already produces one.
