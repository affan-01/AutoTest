import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { api, STAGE_LABELS, STAGE_ORDER, type StageName, type TicketDetail } from '../lib/api'
import StageTab from '../components/StageTab'

const STATUS_CLASS: Record<TicketDetail['status'], string> = {
  pass: 'ok',
  fail: 'fail',
  pending: 'pending',
  unknown: 'unknown',
}

type TabKey = StageName | 'report'

export default function TicketPage() {
  const { ticketId } = useParams<{ ticketId: string }>()
  const [ticket, setTicket] = useState<TicketDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState<TabKey | null>(null)

  useEffect(() => {
    if (!ticketId) return
    let cancelled = false
    setTicket(null)
    setError(null)
    setActiveTab(null)
    api
      .getTicket(ticketId)
      .then((detail) => {
        if (cancelled) return
        setTicket(detail)
        const firstStageWithData = STAGE_ORDER.find((s) => detail.stages[s] !== undefined)
        if (firstStageWithData) {
          setActiveTab(firstStageWithData)
        } else if (detail.qa_report_md.trim() !== '') {
          setActiveTab('report')
        } else {
          setActiveTab(STAGE_ORDER[0])
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err))
      })
    return () => {
      cancelled = true
    }
  }, [ticketId])

  if (!ticketId) {
    return (
      <main className="page-main">
        <div className="empty-state">No ticket id provided.</div>
      </main>
    )
  }

  return (
    <>
      <header className="page-header">
        <h1 style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          Ticket {ticketId}
          {ticket && <span className={`badge ${STATUS_CLASS[ticket.status]}`}>{ticket.status}</span>}
        </h1>
      </header>
      <main className="page-main">
        {error && <div className="empty-state">Failed to load ticket: {error}</div>}
        {!error && !ticket && <div className="empty-state">Loading ticket...</div>}
        {!error && ticket && activeTab && (
          <>
            <div className="tabs">
              {STAGE_ORDER.map((stage) => (
                <button
                  key={stage}
                  className={activeTab === stage ? 'active' : ''}
                  disabled={ticket.stages[stage] === undefined}
                  onClick={() => setActiveTab(stage)}
                >
                  {STAGE_LABELS[stage]}
                </button>
              ))}
              <button
                className={activeTab === 'report' ? 'active' : ''}
                disabled={ticket.qa_report_md.trim() === ''}
                onClick={() => setActiveTab('report')}
              >
                Report
              </button>
            </div>

            {activeTab === 'report' ? (
              ticket.qa_report_md.trim() === '' ? (
                <div className="empty-state">No QA report has been generated for this ticket yet.</div>
              ) : (
                <pre className="json-block">{ticket.qa_report_md}</pre>
              )
            ) : (
              <StageTab stage={activeTab} data={ticket.stages[activeTab]} ticketId={ticket.ticket_id} />
            )}
          </>
        )}
      </main>
    </>
  )
}
