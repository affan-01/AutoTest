import { useMemo, useState, type ReactNode } from 'react'
import './Waterfall.css'

// Ported from pipelines/traces_ui.html's <script> block. The trace JSON comes straight from
// pipelines/tracing.py's trace.jsonl (via api.getTrace, already unwrapped from the `.trace`
// envelope) - it's agent/pipeline-authored and not modeled as a TS interface anywhere, so every
// field here is read defensively with optional chaining exactly like the original vanilla JS did.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Span = Record<string, any>

interface LayoutRow {
  span: Span
  start: number
  dur: number
  depth: number
}

function fmtMs(ms: number | undefined | null): string {
  if (!ms) return '-'
  const s = ms / 1000
  return s < 120 ? `${s.toFixed(1)}s` : `${(s / 60).toFixed(1)}m`
}

function spanDuration(span: Span): number {
  const m = span.metadata || {}
  if (m.delegated && m.wall_ms) return m.wall_ms
  return m.duration_ms || m.wall_ms || m.duration_api_ms || 0
}

// Flame-graph layout: no reliable wall-clock timestamps are recorded on spans (see
// pipelines/trace_report.py, which never reads one either) - only per-span durations in
// metadata. Children are laid out sequentially within their parent's span, which matches how
// this pipeline actually runs (one stage after another, not in parallel).
function layout(span: Span, offset: number, depth: number, rows: LayoutRow[]): LayoutRow {
  let dur = spanDuration(span)
  const children: Span[] = span.children || []
  let childOffset = offset
  children.forEach((c) => {
    const cRow = layout(c, childOffset, depth + 1, rows)
    childOffset += cRow.dur
  })
  if (!dur && children.length) dur = childOffset - offset
  const row: LayoutRow = { span, start: offset, dur, depth }
  rows.push(row)
  return row
}

function statusColor(span: Span): string {
  const m = span.metadata || {}
  if (m.ok === false || span.error) return 'var(--bar-fail)'
  if (m.ok === true) return 'var(--bar-ok)'
  if (span.model) return 'var(--bar-llm)'
  return 'var(--bar-neutral)'
}

interface DetailRow {
  key: string
  value: ReactNode
}

function buildDetailRows(span: Span): DetailRow[] {
  const m = span.metadata || {}
  const rows: DetailRow[] = []
  const add = (key: string, value: ReactNode) => {
    if (value === undefined || value === null || value === '') return
    rows.push({ key, value })
  }

  add('status', span.status || '-')
  if ('ok' in m) add('ok', String(m.ok))
  add('duration', fmtMs(m.duration_ms) + (m.wall_ms ? `  (wall: ${fmtMs(m.wall_ms)})` : ''))
  if (span.model) add('model', `${span.model}  in=${span.input_token_count} out=${span.output_token_count}`)
  if (m.num_turns !== undefined) {
    add(
      'turns',
      `${m.num_turns}/${m.max_turns_configured}${m.hit_max_turns ? ' HIT MAX' : ''}`,
    )
  }
  const cost = m.cost_usd ?? m.total_cost_usd
  if (cost !== undefined) add('cost', `$${Number(cost).toFixed(4)}`)
  if (m.delegated) add('delegated', 'yes - SDK-reported turns/duration only cover the parent, not the sub-agent')
  if (m.stop_reason) add('stop reason', `${m.stop_reason}${m.is_error ? ' (error)' : ''}`)
  if (m.tool_calls && m.tool_calls.length) add('tool calls', m.tool_calls.join(', '))
  if (m.artifact_count !== undefined) add('artifacts', String(m.artifact_count))
  if (m.artifact_wait_ms) add('waited for artifact', fmtMs(m.artifact_wait_ms))
  if (m.note) add('note', m.note)
  if (m.permission_denials && m.permission_denials.length) {
    add('permission denials', <pre className="json-block">{JSON.stringify(m.permission_denials, null, 2)}</pre>)
  }
  if (m.errors && m.errors.length) {
    add('errors', <pre className="json-block">{JSON.stringify(m.errors, null, 2)}</pre>)
  }
  if (span.error) {
    add('span error', <pre className="json-block">{JSON.stringify(span.error, null, 2)}</pre>)
  }
  ;['eval_groundedness', 'eval_summarization'].forEach((key) => {
    const ev = m[key]
    if (ev) {
      add(
        key,
        <>
          score={ev.score} threshold={ev.threshold} passed={String(ev.success)}
          <br />
          {ev.reason || ''}
        </>,
      )
    }
  })
  if (m.new_artifacts && m.new_artifacts.length) {
    add('new files', <pre className="json-block">{m.new_artifacts.join('\n')}</pre>)
  }

  return rows
}

export default function Waterfall({ trace }: { trace: Span }) {
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null)

  const rows = useMemo(() => {
    if (!trace || !trace.root_spans) return []
    const out: LayoutRow[] = []
    let offset = 0
    ;(trace.root_spans || []).forEach((root: Span) => {
      const r = layout(root, offset, 0, out)
      offset += r.dur
    })
    return out
  }, [trace])

  if (!trace || !trace.root_spans) {
    return <span className="hint">No trace data for this run.</span>
  }

  const md = trace.metadata || {}
  const total = Math.max(rows.reduce((max, r) => Math.max(max, r.start + r.dur), 0), 1)

  let totalCost = 0
  rows.forEach((r) => {
    const c = (r.span.metadata || {}).cost_usd ?? (r.span.metadata || {}).total_cost_usd
    if (c) totalCost += c
  })

  const outcomeOk = md.outcome === 'ok' || md.exit_code === 0
  const selectedSpan = selectedIndex !== null ? rows[selectedIndex]?.span : null
  const detailRows = selectedSpan ? buildDetailRows(selectedSpan) : null

  return (
    <div>
      <div className="stat-row">
        <div className="stat">
          <b>{md.ticket_id || trace.thread_id || '-'}</b>
          <span>ticket</span>
        </div>
        <div className="stat">
          <b>
            <span className={`badge ${outcomeOk ? 'ok' : 'fail'}`}>{md.outcome || trace.status || '-'}</span>
          </b>
          <span>outcome</span>
        </div>
        <div className="stat">
          <b>{fmtMs(total)}</b>
          <span>total time (stacked)</span>
        </div>
        <div className="stat">
          <b>${totalCost.toFixed(4)}</b>
          <span>total cost</span>
        </div>
        {md.stages_run && (
          <div className="stat">
            <b>{md.stages_run.join(', ')}</b>
            <span>stages run</span>
          </div>
        )}
        {md.stages_skipped && md.stages_skipped.length > 0 && (
          <div className="stat">
            <b>{md.stages_skipped.join(', ')}</b>
            <span>stages skipped</span>
          </div>
        )}
      </div>

      <div className="legend">
        <span>
          <i style={{ background: 'var(--bar-ok)' }} />
          ok
        </span>
        <span>
          <i style={{ background: 'var(--bar-fail)' }} />
          failed
        </span>
        <span>
          <i style={{ background: 'var(--bar-llm)' }} />
          model call
        </span>
        <span>
          <i style={{ background: 'var(--bar-neutral)' }} />
          other
        </span>
      </div>

      <div className="waterfall">
        {rows.map((r, i) => {
          const left = (r.start / total) * 100
          const width = Math.max((r.dur / total) * 100, 0.3)
          const label = r.span.name || '(unnamed)'
          return (
            <div className="wf-row" key={i}>
              <button
                type="button"
                className={`wf-bar${selectedIndex === i ? ' selected' : ''}`}
                style={{
                  left: `${left}%`,
                  width: `${width}%`,
                  marginLeft: r.depth * 2,
                  background: statusColor(r.span),
                }}
                onClick={() => setSelectedIndex(i)}
              >
                {label}
                {r.dur ? ` · ${fmtMs(r.dur)}` : ''}
              </button>
            </div>
          )
        })}
      </div>

      <div className="wf-detail">
        {!selectedSpan && <span className="hint">Click a bar for details.</span>}
        {selectedSpan && detailRows && (
          <>
            <h3>{selectedSpan.name || '(unnamed)'}</h3>
            <table className="data-table">
              <tbody>
                {detailRows.map((row, i) => (
                  <tr key={i}>
                    <td className="hint" style={{ whiteSpace: 'nowrap', width: '1%' }}>
                      {row.key}
                    </td>
                    <td>{row.value}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </div>
    </div>
  )
}
