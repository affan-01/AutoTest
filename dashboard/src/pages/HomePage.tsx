import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, STAGE_LABELS, STAGE_ORDER, type TicketListRow } from '../lib/api'

const STATUS_CLASS: Record<TicketListRow['status'], string> = {
  pass: 'ok',
  fail: 'fail',
  pending: 'pending',
  unknown: 'unknown',
}

export default function HomePage() {
  const navigate = useNavigate()
  const [tickets, setTickets] = useState<TicketListRow[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    api
      .listTickets()
      .then((rows) => {
        if (!cancelled) setTickets(rows)
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err))
      })
    return () => {
      cancelled = true
    }
  }, [])

  const sortedTickets = tickets ? [...tickets].sort((a, b) => b.mtime - a.mtime) : null

  return (
    <>
      <header className="page-header">
        <h1>Tickets</h1>
        <p>Work items that have had at least one pipeline stage run against them.</p>
      </header>
      <main className="page-main">
        {error && <div className="empty-state">Failed to load tickets: {error}</div>}
        {!error && sortedTickets === null && <div className="empty-state">Loading tickets...</div>}
        {!error && sortedTickets !== null && sortedTickets.length === 0 && (
          <div className="empty-state">
            No pipeline runs yet. Run a pipeline stage against a ticket to see it here, e.g.{' '}
            <code>python3 -m pipelines.flow --ticket-id &lt;id&gt;</code>.
          </div>
        )}
        {!error && sortedTickets !== null && sortedTickets.length > 0 && (
          <table className="data-table">
            <thead>
              <tr>
                <th>Ticket</th>
                <th>Stages</th>
                <th>Status</th>
                <th>Last run</th>
              </tr>
            </thead>
            <tbody>
              {sortedTickets.map((row) => (
                <tr
                  key={row.ticket_id}
                  className="clickable"
                  onClick={() => navigate(`/tickets/${row.ticket_id}`)}
                >
                  <td>{row.type ? `${row.type} ${row.ticket_id}` : row.ticket_id}</td>
                  <td>
                    <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                      {STAGE_ORDER.filter((s) => row.stages.includes(s)).map((s) => (
                        <span key={s} className="badge stage">
                          {STAGE_LABELS[s] ?? s}
                        </span>
                      ))}
                    </div>
                  </td>
                  <td>
                    <span className={`badge ${STATUS_CLASS[row.status]}`}>{row.status}</span>
                  </td>
                  <td>{new Date(row.mtime * 1000).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </main>
    </>
  )
}
