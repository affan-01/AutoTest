# Backend adapter: TFS / Azure DevOps (on-prem)

This pipeline ships **one working reference adapter** — TFS / Azure DevOps Server (on-prem).
Everything TFS-specific that the agents need (REST auth, API-version quirks, work-item field
names, write mechanics) lives here instead of scattered across the agent prompts, so the core
agents stay backend-agnostic and a future Jira/GitHub/Linear adapter can be added by writing an
equivalent doc + filling in `pipeline.config.json`'s `backend` block, without touching the
agents themselves.

Set `backend.type: "tfs"` in `pipeline.config.json` to use this adapter.

## Config

```json
"backend": {
  "type": "tfs",
  "orgUrlEnv": "TFS_ORG_URL",
  "projectNameEnv": "TFS_PROJECT",
  "mcpToolPrefix": "mcp__your-pm-tool__",
  "authNotes": "see docs/adapters/tfs.md"
}
```

- `orgUrlEnv` / `projectNameEnv` name the environment variables that hold your org's TFS base
  URL (e.g. `https://tfs.yourcompany.com/tfs/YourCollection`) and project name. Agents read
  these instead of any hardcoded value.
- `mcpToolPrefix` is the MCP server namespace exposing your TFS/Azure DevOps tools (e.g.
  `mcp__azure-devops__`). Swap it for whatever your MCP config actually names the server.

## REST auth

- Use **Basic auth via a base64-encoded header**, not `curl -u :$PAT` — on-prem TFS has been
  observed to reject or intermittently mishandle `-u`-style auth. Build the header explicitly:
  `Authorization: Basic <base64(":" + PAT)>`.
- If your instance uses **Windows Integrated Auth** instead of a PAT (common when the PAT has
  been disabled by policy), use `-UseDefaultCredentials` in PowerShell / `Invoke-RestMethod`
  rather than a bearer/basic token.
- **Execute real writes as inline REST/PowerShell calls, not saved `.ps1` script files.**
  Script-file invocation against on-prem TFS has been observed to intermittently return
  malformed-HTML "Page not found" responses that succeed when the same call is issued inline —
  this smells like an NTLM/`-UseDefaultCredentials` auth-context quirk tied to how the process
  was launched, not a generic bug. Draft the logic in a script file first if useful, but run the
  actual create/PATCH/attachment calls as one focused inline command per write.
- Watch for `ConvertTo-Json` silently collapsing a single-element PATCH array into a bare
  object — TFS rejects that shape with `VssPropertyValidationException`. Build the JSON array
  string manually for single-item patches.

## API version quirks

- The Test Plans API is inconsistent across surfaces on some on-prem versions: listing suites
  may need `api-version=7.0`, while `test/plans/{planId}/suites/{suiteId}/testcases` on the
  same instance can 404 on `7.0` and requires `api-version=5.0`. If a Test Plans call 404s
  unexpectedly, try stepping the api-version down before assuming the endpoint is wrong.

## Work-item field names (this adapter's schema)

These are Azure DevOps / TFS field names — a Jira or GitHub Issues adapter would use entirely
different fields and this table would not apply:

| Purpose | Field |
|---|---|
| Acceptance criteria | `Microsoft.VSTS.Common.AcceptanceCriteria` |
| Repro steps | `Microsoft.VSTS.TCM.ReproSteps` |
| Test steps | `Microsoft.VSTS.TCM.Steps` |
| Area path | `System.AreaPath` |
| Priority | `Microsoft.VSTS.Common.Priority` |
| History / comments | `System.History` |

Custom fields are project-specific — define your own mapping in `pipeline.config.json` (or a
project-level extension of it) rather than hardcoding field names like `Custom.AutomationType`
in an agent prompt. Setting a Test Case to an "automatable" state on this adapter typically
requires several custom picklists to be set in the same PATCH — inspect an existing work item in
that state on your instance and mirror its field set; there is no universal list since custom
fields vary per TFS project template.

## Write-back mechanics (publish stage)

- Link test cases to their parent work item via a **Tested By (reverse)** relation.
- Post evidence as a `System.History` comment — embed images by uploading them as work-item
  attachments first, then referencing the attachment URL in an `<img>` tag inside the comment
  body. If you shell out to a helper script to build/post that comment, make sure it prints
  the attachment URL with `Write-Host`, not `Write-Output` — piping `Write-Output` through
  further processing has been observed to corrupt the URL.
- Azure DevOps Test Case work items generally **cannot be deleted** via the REST API — treat
  test-case creation as append-only, and never overwrite a populated test-case ID/link.
- Always show the full write plan (what will be created/linked/commented/state-changed) and
  wait for one explicit confirmation before executing any batch of writes — this is a pipeline-
  level rule (see `PIPELINE.md`), not specific to TFS, but it matters most here because TFS test
  cases can't be deleted if you get it wrong.

## Adding a different backend

To add a Jira, GitHub Issues, or other adapter: write an equivalent `docs/adapters/<name>.md`
covering that system's auth, field-name mapping, and write mechanics; add a `backend.type`
value for it; and update the agents' generic "resolve from `pipeline.config.json`" instructions
to branch on it if the mechanics differ enough to need agent-level awareness (most day-to-day
agent logic — fetch, analyze, report — should not need to change per backend).
