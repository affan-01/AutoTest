import { useEffect, useState, type CSSProperties } from 'react'
import { api, type JsonValue, type TraceListRow } from '../lib/api'
import Waterfall from '../components/Waterfall'

// Ported from pipelines/traces_ui.html - a two-column layout (ticket list + trace viewer) that
// isn't shared with any other page, so its grid is scoped here rather than added to styles.css.
const gridStyle: CSSProperties = {
  display: 'grid',
  gridTemplateColumns: '220px 1fr',
  gap: 18,
}

export default function TracesPage() {
  const [traces, setTraces] = useState<TraceListRow[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  const [selectedTicket, setSelectedTicket] = useState<string | null>(null)
  const [selectedRun, setSelectedRun] = useState<number>(-1)
  const [currentTrace, setCurrentTrace] = useState<JsonValue | null>(null)
  const [traceError, setTraceError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    api
      .listTraces()
      .then((rows) => {
        if (cancelled) return
        setTraces(rows)
        if (rows.length > 0) {
          setSelectedTicket(rows[0].ticket_id)
          setSelectedRun(-1)
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err))
      })
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    if (!selectedTicket) {
      setCurrentTrace(null)
      return
    }
    let cancelled = false
    setTraceError(null)
    api
      .getTrace(selectedTicket, selectedRun)
      .then((trace) => {
        if (!cancelled) setCurrentTrace(trace)
      })
      .catch((err: unknown) => {
        if (!cancelled) setTraceError(err instanceof Error ? err.message : String(err))
      })
    return () => {
      cancelled = true
    }
  }, [selectedTicket, selectedRun])

  const selectedEntry = traces?.find((t) => t.ticket_id === selectedTicket) ?? null
  const runCount = selectedEntry ? selectedEntry.runs : 0

  function selectTicket(ticketId: string) {
    setSelectedTicket(ticketId)
    setSelectedRun(-1)
  }

  return (
    <>
      <header className="page-header">
        <h1>Pipeline Traces</h1>
        <p>
          Reads the trace.jsonl files pipelines/tracing.py writes per run under
          artifacts/flow/&lt;ticket_id&gt;/ - no data leaves this machine.
        </p>
      </header>
      <main className="page-main" style={gridStyle}>
        <div className="card">
          {error && <div className="empty-state">Failed to load traces: {error}</div>}
          {!error && traces === null && <div className="empty-state">Loading...</div>}
          {!error && traces !== null && traces.length === 0 && (
            <div className="empty-state">
              No runs yet. Run <code>python3 -m pipelines.flow --ticket-id &lt;id&gt;</code> first.
            </div>
          )}
          {!error && traces !== null && traces.length > 0 && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              {traces.map((t) => (
                <button
                  key={t.ticket_id}
                  type="button"
                  onClick={() => selectTicket(t.ticket_id)}
                  style={{
                    textAlign: 'left',
                    background: t.ticket_id === selectedTicket ? 'var(--accent)' : 'transparent',
                    color: t.ticket_id === selectedTicket ? 'var(--accent-text)' : 'var(--text)',
                    border: '1px solid transparent',
                    borderColor: t.ticket_id === selectedTicket ? 'var(--accent)' : 'transparent',
                    borderRadius: 6,
                    padding: '8px 10px',
                    cursor: 'pointer',
                    font: 'inherit',
                    fontSize: 13,
                  }}
                >
                  {t.ticket_id}
                  <span
                    style={{
                      display: 'block',
                      fontSize: 11,
                      marginTop: 2,
                      color: t.ticket_id === selectedTicket ? 'var(--accent-text)' : 'var(--muted)',
                      opacity: t.ticket_id === selectedTicket ? 0.85 : 1,
                    }}
                  >
                    {t.runs} run(s)
                  </span>
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="card">
          {runCount > 1 && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12, flexWrap: 'wrap' }}>
              <span>Run:</span>
              <select value={selectedRun} onChange={(e) => setSelectedRun(parseInt(e.target.value, 10))}>
                {Array.from({ length: runCount }, (_, i) => {
                  const idx = -1 - i // -1 = latest, -2 = one before, ...
                  return (
                    <option key={idx} value={idx}>
                      {i === 0 ? 'latest' : `${i + 1} runs ago`}
                    </option>
                  )
                })}
              </select>
            </div>
          )}

          {!selectedTicket && <span className="hint">Select a ticket on the left to view its trace.</span>}
          {selectedTicket && traceError && (
            <span className="hint">Failed to load trace: {traceError}</span>
          )}
          {selectedTicket && !traceError && currentTrace === null && <span className="hint">Loading trace...</span>}
          {selectedTicket && !traceError && currentTrace !== null && (
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            <Waterfall trace={currentTrace as any} />
          )}
        </div>
      </main>
    </>
  )
}
