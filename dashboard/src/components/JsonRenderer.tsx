import type { JsonValue } from '../lib/api'

// Generic, defensive renderer for arbitrary agent-authored JSON. Must never throw regardless of
// shape/depth - this is the fallback that shows "everything else" a StageTab doesn't hand-render.

const MAX_ARRAY_ROWS = 50
const MAX_INLINE_ITEMS = 3

function isPlainObject(v: JsonValue): v is { [key: string]: JsonValue } {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

function isEmptyValue(v: JsonValue | undefined): boolean {
  if (v === undefined || v === null) return true
  if (typeof v === 'string' && v.trim() === '') return true
  if (Array.isArray(v) && v.length === 0) return true
  return false
}

function humanizeKey(key: string): string {
  const spaced = key
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .replace(/[_-]+/g, ' ')
    .trim()
  return spaced.replace(/\w\S*/g, (w) => w.charAt(0).toUpperCase() + w.slice(1))
}

function isComplex(v: JsonValue): boolean {
  if (Array.isArray(v)) return v.length > 0
  if (isPlainObject(v)) return Object.keys(v).length > 0
  return false
}

function primitiveToText(v: JsonValue): string {
  if (v === null || v === undefined) return '—'
  if (typeof v === 'boolean') return v ? 'Yes' : 'No'
  return String(v)
}

export default function JsonRenderer({ value }: { value: JsonValue | undefined }) {
  if (value === null || value === undefined) {
    return <span className="hint">—</span>
  }

  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    return <>{primitiveToText(value)}</>
  }

  if (Array.isArray(value)) {
    if (value.length === 0) return <span className="hint">—</span>

    const allPrimitive = value.every(
      (item) => item === null || typeof item === 'string' || typeof item === 'number' || typeof item === 'boolean',
    )

    if (allPrimitive) {
      if (value.length > MAX_INLINE_ITEMS) {
        return (
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            {value.map((item, i) => (
              <li key={i}>{primitiveToText(item)}</li>
            ))}
          </ul>
        )
      }
      return <>{value.map((item) => primitiveToText(item)).join(', ')}</>
    }

    // Array of objects (or mixed) -> table with union of keys.
    const rows = value.slice(0, MAX_ARRAY_ROWS)
    const truncated = value.length > MAX_ARRAY_ROWS

    const objectRows = rows.map((item) => (isPlainObject(item) ? item : { value: item }))
    const columns: string[] = []
    for (const row of objectRows) {
      for (const key of Object.keys(row)) {
        if (!columns.includes(key)) columns.push(key)
      }
    }

    // A column is "always complex" if every row where it's present holds a non-empty
    // object/array - those cells recurse one level rather than dumping raw structure inline.
    const complexColumns = new Set(
      columns.filter((col) => {
        const present = objectRows.filter((row) => col in row)
        if (present.length === 0) return false
        return present.every((row) => isComplex(row[col]))
      }),
    )

    return (
      <>
        <div style={{ overflowX: 'auto' }}>
          <table className="data-table">
            <thead>
              <tr>
                {columns.map((col) => (
                  <th key={col}>{humanizeKey(col)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {objectRows.map((row, i) => (
                <tr key={i}>
                  {columns.map((col) => (
                    <td key={col}>
                      {complexColumns.has(col) ? (
                        isEmptyValue(row[col]) ? (
                          <span className="hint">—</span>
                        ) : (
                          <JsonRenderer value={row[col]} />
                        )
                      ) : (
                        <JsonRenderer value={row[col]} />
                      )}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {truncated && <div className="hint">showing first {MAX_ARRAY_ROWS} of {value.length}</div>}
      </>
    )
  }

  // Plain object.
  const entries = Object.entries(value).filter(([, v]) => !isEmptyValue(v))
  if (entries.length === 0) return <span className="hint">—</span>

  return (
    <div>
      {entries.map(([key, v]) => (
        <div key={key} style={{ marginBottom: 6 }}>
          <div style={{ fontWeight: 600, fontSize: 12.5 }}>{humanizeKey(key)}</div>
          <div>
            <JsonRenderer value={v} />
          </div>
        </div>
      ))}
    </div>
  )
}
