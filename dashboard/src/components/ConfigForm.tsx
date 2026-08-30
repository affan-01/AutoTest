// Ported from pipelines/configure_ui.html (vanilla JS). Same field list, same buildConfig() shape
// (see pipeline.config.example.json), same dynamic add/remove row pattern for repoMap, branch
// mapping, and per-app URL rows - just expressed as React state instead of DOM manipulation.
import { useState } from 'react'
import type { CSSProperties, FormEvent } from 'react'
import type { JsonValue } from '../lib/api'

// ---- Config shape (mirrors pipeline.config.example.json exactly) ----

export interface RepoMapEntry {
  name: string
  path: string
}

export interface AppEntry {
  name: string
  urls: Record<string, string>
  credentialSource: string
}

export interface PipelineConfig {
  project: { name: string }
  backend: {
    type: string
    orgUrlEnv: string
    projectNameEnv: string
    mcpToolPrefix: string
  }
  agents: {
    groom: string
    impact: string
    generate: string
    execute: string
  }
  repos: {
    automationRepoRoot: string
    pageObjectsPath: string
    repoMap: RepoMapEntry[]
  }
  environments: {
    branchMapping: Record<string, string>
    default: string
  }
  apps: AppEntry[]
  database?: {
    connectionStringEnv: string
    notes: string
  }
}

// ---- Editable row types (arrays with stable ids, since object keys are being typed live) ----

interface RepoRow {
  id: number
  name: string
  path: string
}

interface BranchRow {
  id: number
  branch: string
  env: string
}

interface AppUrlRow {
  id: number
  env: string
  url: string
}

interface AppRow {
  id: number
  name: string
  urls: AppUrlRow[]
  credentialSource: string
}

interface FormState {
  projectName: string
  backendType: string
  backendOrgUrlEnv: string
  backendProjectNameEnv: string
  backendMcpToolPrefix: string
  agentsGroom: string
  agentsImpact: string
  agentsGenerate: string
  agentsExecute: string
  automationRepoRoot: string
  pageObjectsPath: string
  repoMap: RepoRow[]
  branchMapping: BranchRow[]
  environmentsDefault: string
  apps: AppRow[]
  dbEnabled: boolean
  dbConnectionStringEnv: string
  dbNotes: string
}

let nextId = 1
function freshId() {
  return nextId++
}

function asRecord(v: JsonValue | undefined): Record<string, JsonValue> {
  return v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, JsonValue>) : {}
}

function asString(v: JsonValue | undefined): string {
  return typeof v === 'string' ? v : ''
}

function asArray(v: JsonValue | undefined): JsonValue[] {
  return Array.isArray(v) ? v : []
}

function initFromConfig(initialConfig: Record<string, JsonValue>): FormState {
  const project = asRecord(initialConfig.project)
  const backend = asRecord(initialConfig.backend)
  const agents = asRecord(initialConfig.agents)
  const repos = asRecord(initialConfig.repos)
  const environments = asRecord(initialConfig.environments)
  const database = initialConfig.database ? asRecord(initialConfig.database) : null

  const repoMapArr = asArray(repos.repoMap).map((r) => {
    const row = asRecord(r)
    return { id: freshId(), name: asString(row.name), path: asString(row.path) }
  })

  const branchMappingObj = asRecord(environments.branchMapping)
  const branchRows: BranchRow[] = Object.keys(branchMappingObj).map((branch) => ({
    id: freshId(),
    branch,
    env: asString(branchMappingObj[branch]),
  }))

  const appsArr = asArray(initialConfig.apps).map((a) => {
    const app = asRecord(a)
    const urlsObj = asRecord(app.urls)
    const urlRows: AppUrlRow[] = Object.keys(urlsObj).map((env) => ({
      id: freshId(),
      env,
      url: asString(urlsObj[env]),
    }))
    return {
      id: freshId(),
      name: asString(app.name),
      urls: urlRows.length ? urlRows : [{ id: freshId(), env: '', url: '' }],
      credentialSource: asString(app.credentialSource),
    }
  })

  return {
    projectName: asString(project.name),
    backendType: asString(backend.type),
    backendOrgUrlEnv: asString(backend.orgUrlEnv),
    backendProjectNameEnv: asString(backend.projectNameEnv),
    backendMcpToolPrefix: asString(backend.mcpToolPrefix),
    agentsGroom: asString(agents.groom),
    agentsImpact: asString(agents.impact),
    agentsGenerate: asString(agents.generate),
    agentsExecute: asString(agents.execute),
    automationRepoRoot: asString(repos.automationRepoRoot),
    pageObjectsPath: asString(repos.pageObjectsPath),
    repoMap: repoMapArr.length ? repoMapArr : [{ id: freshId(), name: '', path: '' }],
    branchMapping: branchRows.length ? branchRows : [{ id: freshId(), branch: '', env: '' }],
    environmentsDefault: asString(environments.default),
    apps: appsArr.length ? appsArr : [{ id: freshId(), name: '', urls: [{ id: freshId(), env: '', url: '' }], credentialSource: '' }],
    dbEnabled: !!database,
    dbConnectionStringEnv: database ? asString(database.connectionStringEnv) : '',
    dbNotes: database ? asString(database.notes) : '',
  }
}

function buildConfig(state: FormState): PipelineConfig {
  const repoMap: RepoMapEntry[] = state.repoMap
    .filter((r) => r.name.trim())
    .map((r) => ({ name: r.name.trim(), path: r.path.trim() }))

  const branchMapping: Record<string, string> = {}
  state.branchMapping.forEach((b) => {
    const branch = b.branch.trim()
    if (branch) branchMapping[branch] = b.env.trim()
  })

  const apps: AppEntry[] = state.apps
    .filter((a) => a.name.trim() || a.credentialSource.trim() || a.urls.some((u) => u.env.trim()))
    .map((a) => {
      const urls: Record<string, string> = {}
      a.urls.forEach((u) => {
        const env = u.env.trim()
        if (env) urls[env] = u.url.trim()
      })
      return { name: a.name.trim(), urls, credentialSource: a.credentialSource.trim() }
    })

  const config: PipelineConfig = {
    project: { name: state.projectName.trim() },
    backend: {
      type: state.backendType.trim(),
      orgUrlEnv: state.backendOrgUrlEnv.trim(),
      projectNameEnv: state.backendProjectNameEnv.trim(),
      mcpToolPrefix: state.backendMcpToolPrefix.trim(),
    },
    agents: {
      groom: state.agentsGroom.trim(),
      impact: state.agentsImpact.trim(),
      generate: state.agentsGenerate.trim(),
      execute: state.agentsExecute.trim(),
    },
    repos: {
      automationRepoRoot: state.automationRepoRoot.trim(),
      pageObjectsPath: state.pageObjectsPath.trim(),
      repoMap,
    },
    environments: {
      branchMapping,
      default: state.environmentsDefault.trim(),
    },
    apps,
  }

  if (state.dbEnabled) {
    config.database = {
      connectionStringEnv: state.dbConnectionStringEnv.trim(),
      notes: state.dbNotes.trim(),
    }
  }

  return config
}

// ---- Small shared bits ----

const appCardStyle: CSSProperties = {
  border: '1px dashed var(--border)',
  borderRadius: 8,
  padding: 12,
  marginBottom: 10,
}
const appCardHeadStyle: CSSProperties = {
  display: 'flex',
  justifyContent: 'space-between',
  alignItems: 'center',
  gap: 10,
}
const checkboxRowStyle: CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: 8,
  margin: '10px 0 4px',
}

function RemoveButton({ onClick, label = '✕' }: { onClick: () => void; label?: string }) {
  return (
    <button type="button" className="btn danger" onClick={onClick}>
      {label}
    </button>
  )
}

export interface ConfigFormProps {
  initialConfig: Record<string, JsonValue>
  onSave: (config: Record<string, JsonValue>) => Promise<void>
}

export default function ConfigForm({ initialConfig, onSave }: ConfigFormProps) {
  const [state, setState] = useState<FormState>(() => initFromConfig(initialConfig))
  const [submitting, setSubmitting] = useState(false)

  function set<K extends keyof FormState>(key: K, value: FormState[K]) {
    setState((s) => ({ ...s, [key]: value }))
  }

  // ---- Repo map rows ----
  function updateRepoRow(id: number, field: 'name' | 'path', value: string) {
    setState((s) => ({
      ...s,
      repoMap: s.repoMap.map((r) => (r.id === id ? { ...r, [field]: value } : r)),
    }))
  }
  function addRepoRow() {
    setState((s) => ({ ...s, repoMap: [...s.repoMap, { id: freshId(), name: '', path: '' }] }))
  }
  function removeRepoRow(id: number) {
    setState((s) => ({ ...s, repoMap: s.repoMap.filter((r) => r.id !== id) }))
  }

  // ---- Branch mapping rows ----
  function updateBranchRow(id: number, field: 'branch' | 'env', value: string) {
    setState((s) => ({
      ...s,
      branchMapping: s.branchMapping.map((b) => (b.id === id ? { ...b, [field]: value } : b)),
    }))
  }
  function addBranchRow() {
    setState((s) => ({ ...s, branchMapping: [...s.branchMapping, { id: freshId(), branch: '', env: '' }] }))
  }
  function removeBranchRow(id: number) {
    setState((s) => ({ ...s, branchMapping: s.branchMapping.filter((b) => b.id !== id) }))
  }

  // ---- Applications ----
  function updateApp(id: number, field: 'name' | 'credentialSource', value: string) {
    setState((s) => ({
      ...s,
      apps: s.apps.map((a) => (a.id === id ? { ...a, [field]: value } : a)),
    }))
  }
  function addApp() {
    setState((s) => ({
      ...s,
      apps: [...s.apps, { id: freshId(), name: '', urls: [{ id: freshId(), env: '', url: '' }], credentialSource: '' }],
    }))
  }
  function removeApp(id: number) {
    setState((s) => ({ ...s, apps: s.apps.filter((a) => a.id !== id) }))
  }
  function updateAppUrlRow(appId: number, rowId: number, field: 'env' | 'url', value: string) {
    setState((s) => ({
      ...s,
      apps: s.apps.map((a) =>
        a.id === appId
          ? { ...a, urls: a.urls.map((u) => (u.id === rowId ? { ...u, [field]: value } : u)) }
          : a,
      ),
    }))
  }
  function addAppUrlRow(appId: number) {
    setState((s) => ({
      ...s,
      apps: s.apps.map((a) => (a.id === appId ? { ...a, urls: [...a.urls, { id: freshId(), env: '', url: '' }] } : a)),
    }))
  }
  function removeAppUrlRow(appId: number, rowId: number) {
    setState((s) => ({
      ...s,
      apps: s.apps.map((a) => (a.id === appId ? { ...a, urls: a.urls.filter((u) => u.id !== rowId) } : a)),
    }))
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    const config = buildConfig(state)
    setSubmitting(true)
    try {
      await onSave(config as unknown as Record<string, JsonValue>)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form onSubmit={handleSubmit}>
      <section className="card">
        <h2>Project</h2>
        <p className="hint">Identifies your project to the agents wherever they previously assumed one hardcoded name.</p>
        <label>
          Project name
          <span className="field-hint">Your work-item-tracking project's name (e.g. the Jira/TFS project key).</span>
        </label>
        <input
          type="text"
          placeholder="your-project"
          value={state.projectName}
          onChange={(e) => set('projectName', e.target.value)}
        />
      </section>

      <section className="card">
        <h2>PM tool / backend</h2>
        <p className="hint">
          This template ships one working reference adapter for TFS/Azure DevOps (see docs/adapters/tfs.md). Using a
          different tool still works for the parts of the pipeline that don't write back to it - just fill in what
          applies and leave the rest at its default.
        </p>
        <label>
          Backend type
          <span className="field-hint">e.g. "tfs", "jira", "github" - used to pick which adapter doc/notes apply.</span>
        </label>
        <input type="text" placeholder="tfs" value={state.backendType} onChange={(e) => set('backendType', e.target.value)} />
        <label>
          Org URL env var
          <span className="field-hint">Name of the environment variable holding your PM tool's base URL.</span>
        </label>
        <input
          type="text"
          placeholder="TFS_ORG_URL"
          value={state.backendOrgUrlEnv}
          onChange={(e) => set('backendOrgUrlEnv', e.target.value)}
        />
        <label>
          Project name env var
          <span className="field-hint">Name of the environment variable holding your PM tool's project name.</span>
        </label>
        <input
          type="text"
          placeholder="TFS_PROJECT"
          value={state.backendProjectNameEnv}
          onChange={(e) => set('backendProjectNameEnv', e.target.value)}
        />
        <label>
          MCP tool prefix
          <span className="field-hint">The MCP server namespace exposing your PM tool's tools to the agents.</span>
        </label>
        <input
          type="text"
          placeholder="mcp__your-pm-tool__"
          value={state.backendMcpToolPrefix}
          onChange={(e) => set('backendMcpToolPrefix', e.target.value)}
        />
      </section>

      <section className="card">
        <h2>Agent names</h2>
        <p className="hint">
          Only change these if you renamed one of the agent files under .claude/agents/. Leave the defaults if you're
          using this template as shipped.
        </p>
        <label>Groom / evaluate agent</label>
        <input type="text" placeholder="us-eval" value={state.agentsGroom} onChange={(e) => set('agentsGroom', e.target.value)} />
        <label>PR impact agent</label>
        <input
          type="text"
          placeholder="pr-impact-analyzer"
          value={state.agentsImpact}
          onChange={(e) => set('agentsImpact', e.target.value)}
        />
        <label>Test-case generation agent</label>
        <input
          type="text"
          placeholder="test-case-generation-agent"
          value={state.agentsGenerate}
          onChange={(e) => set('agentsGenerate', e.target.value)}
        />
        <label>Test execution agent</label>
        <input
          type="text"
          placeholder="manual-test-execution-agent"
          value={state.agentsExecute}
          onChange={(e) => set('agentsExecute', e.target.value)}
        />
      </section>

      <section className="card">
        <h2>Repos</h2>
        <p className="hint">
          Where your test-automation code lives, so agents ground locators in real page objects instead of inventing
          them.
        </p>
        <label>
          Automation repo root
          <span className="field-hint">Local path to your test-automation repo.</span>
        </label>
        <input
          type="text"
          placeholder="/path/to/your/automation/repo"
          value={state.automationRepoRoot}
          onChange={(e) => set('automationRepoRoot', e.target.value)}
        />
        <label>
          Page objects path
          <span className="field-hint">Path to your page-object classes, relative to the repo root above.</span>
        </label>
        <input
          type="text"
          placeholder="src/test/java/com/yourorg/pages/"
          value={state.pageObjectsPath}
          onChange={(e) => set('pageObjectsPath', e.target.value)}
        />
        <label>
          Repo map (optional)
          <span className="field-hint">Other repos worth knowing about - a name and a path, one row each.</span>
        </label>
        {state.repoMap.map((row) => (
          <div className="form-row" key={row.id}>
            <div>
              <input
                type="text"
                placeholder="name (e.g. acme-web-ui)"
                value={row.name}
                onChange={(e) => updateRepoRow(row.id, 'name', e.target.value)}
              />
            </div>
            <div>
              <input
                type="text"
                placeholder="path"
                value={row.path}
                onChange={(e) => updateRepoRow(row.id, 'path', e.target.value)}
              />
            </div>
            <RemoveButton onClick={() => removeRepoRow(row.id)} />
          </div>
        ))}
        <button type="button" className="btn" onClick={addRepoRow}>
          + Add repo
        </button>
      </section>

      <section className="card">
        <h2>Environments</h2>
        <p className="hint">Which environment a linked PR's source branch implies, so the execution agent can auto-detect it.</p>
        <label>Branch → environment mapping</label>
        {state.branchMapping.map((row) => (
          <div className="form-row" key={row.id}>
            <div>
              <input
                type="text"
                placeholder="branch (e.g. develop)"
                value={row.branch}
                onChange={(e) => updateBranchRow(row.id, 'branch', e.target.value)}
              />
            </div>
            <div>
              <input
                type="text"
                placeholder="environment (e.g. staging)"
                value={row.env}
                onChange={(e) => updateBranchRow(row.id, 'env', e.target.value)}
              />
            </div>
            <RemoveButton onClick={() => removeBranchRow(row.id)} />
          </div>
        ))}
        <button type="button" className="btn" onClick={addBranchRow}>
          + Add branch mapping
        </button>
        <label style={{ marginTop: 14 }}>
          Default environment
          <span className="field-hint">Used when there's no linked PR or the branch is ambiguous.</span>
        </label>
        <input
          type="text"
          placeholder="staging"
          value={state.environmentsDefault}
          onChange={(e) => set('environmentsDefault', e.target.value)}
        />
      </section>

      <section className="card">
        <h2>Applications</h2>
        <p className="hint">
          One entry per application under test. The execution and impact-analysis agents look these up by name
          instead of having them hardcoded.
        </p>
        {state.apps.map((app) => (
          <div className="app-card" style={appCardStyle} key={app.id}>
            <div className="app-card-head" style={appCardHeadStyle}>
              <div>
                <input
                  type="text"
                  placeholder="Application name (e.g. ExampleApp)"
                  value={app.name}
                  onChange={(e) => updateApp(app.id, 'name', e.target.value)}
                />
              </div>
              <RemoveButton onClick={() => removeApp(app.id)} label="Remove app" />
            </div>

            <label>Per-environment URLs</label>
            {app.urls.map((row) => (
              <div className="form-row" key={row.id}>
                <div>
                  <input
                    type="text"
                    placeholder="environment (e.g. staging)"
                    value={row.env}
                    onChange={(e) => updateAppUrlRow(app.id, row.id, 'env', e.target.value)}
                  />
                </div>
                <div>
                  <input
                    type="text"
                    placeholder="https://..."
                    value={row.url}
                    onChange={(e) => updateAppUrlRow(app.id, row.id, 'url', e.target.value)}
                  />
                </div>
                <RemoveButton onClick={() => removeAppUrlRow(app.id, row.id)} />
              </div>
            ))}
            <button type="button" className="btn" onClick={() => addAppUrlRow(app.id)}>
              + Add environment URL
            </button>

            <label>
              Credential source
              <span className="field-hint">Where to find credentials, e.g. env:APP_USER,APP_PASS</span>
            </label>
            <input
              type="text"
              placeholder="env:EXAMPLE_APP_USER,EXAMPLE_APP_PASS"
              value={app.credentialSource}
              onChange={(e) => updateApp(app.id, 'credentialSource', e.target.value)}
            />
          </div>
        ))}
        <button type="button" className="btn" onClick={addApp}>
          + Add application
        </button>
      </section>

      <section className="card">
        <div className="checkbox-row" style={checkboxRowStyle}>
          <input
            type="checkbox"
            id="db_enabled"
            checked={state.dbEnabled}
            onChange={(e) => set('dbEnabled', e.target.checked)}
          />
          <label style={{ margin: 0 }} htmlFor="db_enabled">
            This project has a backend/DB verification stage
          </label>
        </div>
        <p className="hint">Optional - only needed if the pipeline should verify backend state directly (Stage 6).</p>
        {state.dbEnabled && (
          <div>
            <label>
              Connection string env var
              <span className="field-hint">Name of the environment variable holding the DB connection string.</span>
            </label>
            <input
              type="text"
              placeholder="QA_DB_CONNECTION"
              value={state.dbConnectionStringEnv}
              onChange={(e) => set('dbConnectionStringEnv', e.target.value)}
            />
            <label>Notes (optional)</label>
            <input
              type="text"
              placeholder="e.g. read-only reporting replica"
              value={state.dbNotes}
              onChange={(e) => set('dbNotes', e.target.value)}
            />
          </div>
        )}
      </section>

      <footer
        style={{
          position: 'sticky',
          bottom: 0,
          background: 'var(--bg)',
          borderTop: '1px solid var(--border)',
          padding: '14px 0',
          display: 'flex',
          alignItems: 'center',
          gap: 14,
        }}
      >
        <button type="submit" className="btn primary" disabled={submitting}>
          {submitting ? 'Saving...' : 'Save pipeline.config.json'}
        </button>
      </footer>
    </form>
  )
}
