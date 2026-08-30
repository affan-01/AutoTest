import type { JsonValue, StageName } from '../lib/api'
import JsonRenderer from './JsonRenderer'
import ScreenshotGallery from './ScreenshotGallery'

interface StageTabProps {
  stage: StageName
  data: Record<string, JsonValue> | undefined
  ticketId: string
}

// --- small defensive accessors over the loosely-typed JsonValue tree -------------------------
// Stage schemas are real agent-authored JSON that may add fields at any time; we only reach for
// the handful of fields needed for the "hero" stats here and fall back gracefully otherwise.

function asObj(v: JsonValue | undefined): Record<string, JsonValue> | undefined {
  return typeof v === 'object' && v !== null && !Array.isArray(v) ? v : undefined
}
function asArr(v: JsonValue | undefined): JsonValue[] | undefined {
  return Array.isArray(v) ? v : undefined
}
function asStr(v: JsonValue | undefined): string | undefined {
  return typeof v === 'string' ? v : undefined
}
function asNum(v: JsonValue | undefined): number | undefined {
  return typeof v === 'number' ? v : undefined
}
function asBool(v: JsonValue | undefined): boolean | undefined {
  return typeof v === 'boolean' ? v : undefined
}
function get(obj: Record<string, JsonValue> | undefined, key: string): JsonValue | undefined {
  return obj ? obj[key] : undefined
}

type BadgeClass = 'ok' | 'fail' | 'pending' | 'unknown'

function riskBadgeClass(risk: string | undefined): BadgeClass {
  const r = (risk ?? '').toLowerCase()
  if (r === 'low') return 'ok'
  if (r === 'medium') return 'pending'
  if (r === 'high' || r === 'critical') return 'fail'
  return 'unknown'
}

function boolBadgeClass(b: boolean | undefined, trueClass: BadgeClass = 'ok', falseClass: BadgeClass = 'fail'): BadgeClass {
  if (b === undefined) return 'unknown'
  return b ? trueClass : falseClass
}

function Stat({ value, label }: { value: string; label: string }) {
  return (
    <div className="stat">
      <b>{value}</b>
      <span>{label}</span>
    </div>
  )
}

function StatBadge({ text, cls, label }: { text: string; cls: BadgeClass; label: string }) {
  return (
    <div className="stat">
      <b>
        <span className={`badge ${cls}`}>{text}</span>
      </b>
      <span>{label}</span>
    </div>
  )
}

function GroomHero({ data }: { data: Record<string, JsonValue> }) {
  const plainSummary = asObj(get(data, 'plainSummary'))
  const readiness = asStr(get(plainSummary, 'readiness'))
  const scores = asObj(get(data, 'scores'))
  const categories = asArr(get(scores, 'categories')) ?? []

  const topCategories = categories
    .map((c) => asObj(c))
    .filter((c): c is Record<string, JsonValue> => c !== undefined)
    .slice(0, 3)

  const readinessCls: BadgeClass =
    readiness?.toLowerCase() === 'ready' ? 'ok' : readiness ? 'pending' : 'unknown'

  return (
    <div className="stat-row">
      {readiness && <StatBadge text={readiness} cls={readinessCls} label="Readiness" />}
      {topCategories.map((c, i) => {
        const name = asStr(get(c, 'name')) ?? `Category ${i + 1}`
        const score = asNum(get(c, 'score'))
        return <Stat key={i} value={score !== undefined ? `${score}/100` : '—'} label={name} />
      })}
    </div>
  )
}

function PrImpactHero({ data }: { data: Record<string, JsonValue> }) {
  const shipRisk = asStr(get(data, 'shipRisk'))
  const regressionRisks = asArr(get(data, 'regressionRisks')) ?? []
  const changedFiles = asArr(get(data, 'changedFiles')) ?? []
  const criteriaCounts = asObj(get(data, 'acceptanceCriteria'))
  const counts = asObj(get(criteriaCounts, 'counts'))
  const total = asNum(get(counts, 'total'))
  const met = asNum(get(counts, 'met'))

  return (
    <div className="stat-row">
      {shipRisk && <StatBadge text={shipRisk} cls={riskBadgeClass(shipRisk)} label="Ship Risk" />}
      <Stat value={String(regressionRisks.length)} label="Regression Risks" />
      <Stat value={String(changedFiles.length)} label="Changed Files" />
      {total !== undefined && met !== undefined && (
        <Stat value={`${met}/${total}`} label="Acceptance Criteria Met" />
      )}
    </div>
  )
}

function CrossSystemHero({ data }: { data: Record<string, JsonValue> }) {
  const verdict = asObj(get(data, 'verdict'))
  const safeToRelease = asBool(get(verdict, 'safeToRelease'))
  const workflows = asArr(get(data, 'impactedWorkflows')) ?? []
  const blockingGaps = asArr(get(verdict, 'blockingGaps')) ?? []

  return (
    <div className="stat-row">
      {safeToRelease !== undefined && (
        <StatBadge
          text={safeToRelease ? 'Safe to Release' : 'Not Safe'}
          cls={boolBadgeClass(safeToRelease)}
          label="Verdict"
        />
      )}
      <Stat value={String(workflows.length)} label="Impacted Workflows" />
      <Stat value={String(blockingGaps.length)} label="Blocking Gaps" />
    </div>
  )
}

function GenerateHero({ data }: { data: Record<string, JsonValue> }) {
  const testCases = asArr(get(data, 'testCases')) ?? []
  const acCoverage = asArr(get(data, 'acCoverage')) ?? []
  const covered = acCoverage.filter((ac) => asStr(get(asObj(ac), 'status'))?.toLowerCase() === 'covered').length
  const automatable = testCases.filter((tc) => asBool(get(asObj(tc), 'automatable')) === true).length

  return (
    <div className="stat-row">
      <Stat value={String(testCases.length)} label="Test Cases" />
      {acCoverage.length > 0 && (
        <Stat value={`${covered}/${acCoverage.length}`} label="AC Coverage" />
      )}
      <Stat value={String(automatable)} label="Automatable" />
    </div>
  )
}

function ExecuteHero({ data }: { data: Record<string, JsonValue> }) {
  const summary = asObj(get(data, 'summary'))
  const passed = asNum(get(summary, 'passed'))
  const failed = asNum(get(summary, 'failed'))
  const blocked = asNum(get(summary, 'blocked'))
  const skipped = asNum(get(summary, 'skipped'))
  const passRate = asNum(get(summary, 'passRate'))

  return (
    <div className="stat-row">
      {passed !== undefined && <Stat value={String(passed)} label="Passed" />}
      {failed !== undefined && <Stat value={String(failed)} label="Failed" />}
      {blocked !== undefined && <Stat value={String(blocked)} label="Blocked" />}
      {skipped !== undefined && <Stat value={String(skipped)} label="Skipped" />}
      {passRate !== undefined && <Stat value={`${Math.round(passRate * 100)}%`} label="Pass Rate" />}
    </div>
  )
}

const RESULT_BADGE_CLASS: Record<string, BadgeClass> = {
  PASS: 'ok',
  FAIL: 'fail',
  BLOCKED: 'pending',
  SKIP: 'unknown',
}

function ExecuteTestCaseTable({ data, ticketId }: { data: Record<string, JsonValue>; ticketId: string }) {
  const testCases = asArr(get(data, 'testCases')) ?? []
  if (testCases.length === 0) return null

  return (
    <div style={{ overflowX: 'auto' }}>
      <table className="data-table">
        <thead>
          <tr>
            <th>Title</th>
            <th>Result</th>
            <th>Steps</th>
            <th>Screenshots</th>
          </tr>
        </thead>
        <tbody>
          {testCases.map((tcRaw, i) => {
            const tc = asObj(tcRaw)
            const title = asStr(get(tc, 'title')) ?? asStr(get(tc, 'id')) ?? `Test Case ${i + 1}`
            const result = asStr(get(tc, 'result'))
            const steps = asArr(get(tc, 'steps')) ?? []
            const screenshotFiles = steps
              .map((s) => asStr(get(asObj(s), 'screenshotFile')))
              .filter((f): f is string => !!f)

            return (
              <tr key={i}>
                <td>{title}</td>
                <td>
                  {result && (
                    <span className={`badge ${RESULT_BADGE_CLASS[result] ?? 'unknown'}`}>{result}</span>
                  )}
                </td>
                <td>{steps.length}</td>
                <td>
                  {screenshotFiles.length > 0 ? (
                    <ScreenshotGallery ticketId={ticketId} filenames={screenshotFiles} />
                  ) : (
                    <span className="hint">—</span>
                  )}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

export default function StageTab({ stage, data, ticketId }: StageTabProps) {
  if (!data) {
    return <div className="empty-state">This stage hasn't run for this ticket yet.</div>
  }

  return (
    <div>
      {stage === 'groom' && <GroomHero data={data} />}
      {stage === 'pr_impact' && <PrImpactHero data={data} />}
      {stage === 'cross_system' && <CrossSystemHero data={data} />}
      {stage === 'generate' && <GenerateHero data={data} />}
      {stage === 'execute' && <ExecuteHero data={data} />}

      {stage === 'execute' && (
        <div className="card">
          <h2>Test Cases</h2>
          <ExecuteTestCaseTable data={data} ticketId={ticketId} />
        </div>
      )}

      <div className="card">
        <h2>Full Details</h2>
        <JsonRenderer value={data} />
      </div>
    </div>
  )
}
