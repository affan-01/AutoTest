// Thin fetch wrappers for pipelines/api.py's routes. Stage summaries come from agent-authored
// JSON schemas that are rich and evolve independently of this dashboard (see docs/CONFIGURATION.md
// and the schema table in the dashboard build's plan) - typed loosely here on purpose so a field
// the backend adds tomorrow doesn't require a frontend type change to keep compiling. Components
// that care about a specific field read it defensively (optional chaining / fallback), and
// <JsonRenderer> displays whatever else is present without needing to know its shape up front.

export type JsonValue =
  | string
  | number
  | boolean
  | null
  | JsonValue[]
  | { [key: string]: JsonValue }

export type StageName = 'groom' | 'pr_impact' | 'cross_system' | 'generate' | 'execute'

export const STAGE_LABELS: Record<StageName, string> = {
  groom: 'Groom / Evaluate',
  pr_impact: 'PR Impact',
  cross_system: 'Cross-System Impact',
  generate: 'Generate Tests',
  execute: 'Execute',
}

export const STAGE_ORDER: StageName[] = ['groom', 'pr_impact', 'cross_system', 'generate', 'execute']

export type TicketStatus = 'pass' | 'fail' | 'pending' | 'unknown'

export interface TicketListRow {
  ticket_id: string
  type: string | null
  stages: string[]
  mtime: number
  status: TicketStatus
}

export interface TicketDetail {
  ticket_id: string
  stages: Partial<Record<StageName, Record<string, JsonValue>>>
  status: TicketStatus
  qa_report_md: string
}

export interface TraceListRow {
  ticket_id: string
  runs: number
  mtime: number
}

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url)
  if (!res.ok) throw new Error(`${url} -> HTTP ${res.status}`)
  return res.json() as Promise<T>
}

export const api = {
  listTickets: () => getJson<{ tickets: TicketListRow[] }>('/api/tickets').then((d) => d.tickets),

  getTicket: (id: string) => getJson<TicketDetail>(`/api/tickets/${encodeURIComponent(id)}`),

  screenshotUrl: (ticketId: string, filename: string) =>
    `/api/tickets/${encodeURIComponent(ticketId)}/screenshots/${encodeURIComponent(filename)}`,

  listTraces: () => getJson<{ traces: TraceListRow[] }>('/api/traces').then((d) => d.traces),

  getTrace: (ticketId: string, run: number) =>
    getJson<{ trace: JsonValue }>(`/api/trace?ticket=${encodeURIComponent(ticketId)}&run=${run}`).then(
      (d) => d.trace,
    ),

  getConfig: () => getJson<Record<string, JsonValue>>('/config'),

  saveConfig: async (config: Record<string, JsonValue>): Promise<{ ok: true; path: string } | { ok: false; errors: string[] }> => {
    const res = await fetch('/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(config),
    })
    return res.json()
  },
}
