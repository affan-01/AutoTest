import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import type { JsonValue } from '../lib/api'
import ConfigForm from '../components/ConfigForm'

type SaveResult = { ok: true; path: string } | { ok: false; errors: string[] } | null

export default function ConfigPage() {
  const [initialConfig, setInitialConfig] = useState<Record<string, JsonValue> | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [saveResult, setSaveResult] = useState<SaveResult>(null)

  useEffect(() => {
    let cancelled = false
    api
      .getConfig()
      .then((config) => {
        if (!cancelled) setInitialConfig(config)
      })
      .catch((err: unknown) => {
        if (!cancelled) setLoadError(err instanceof Error ? err.message : String(err))
      })
    return () => {
      cancelled = true
    }
  }, [])

  async function handleSave(config: Record<string, JsonValue>) {
    setSaveResult(null)
    try {
      const result = await api.saveConfig(config)
      setSaveResult(result)
    } catch (err: unknown) {
      setSaveResult({ ok: false, errors: [`Could not reach the local server: ${err instanceof Error ? err.message : String(err)}`] })
    }
  }

  return (
    <>
      <header className="page-header">
        <h1>Configure</h1>
        <p>
          Fill in your project's applications, environments and PM tool below. This writes{' '}
          <code>pipeline.config.json</code> at the repo root - nothing here is committed to git.
        </p>
      </header>
      <main className="page-main">
        {saveResult && saveResult.ok && (
          <span className="badge ok">Saved to {saveResult.path}</span>
        )}
        {saveResult && !saveResult.ok && (
          <div
            style={{
              background: 'var(--danger-bg)',
              color: 'var(--danger)',
              borderRadius: 8,
              padding: '10px 14px',
              margin: '10px 0',
              fontSize: 13,
              whiteSpace: 'pre-line',
            }}
          >
            {saveResult.errors.map((e) => `• ${e}`).join('\n')}
          </div>
        )}

        {loadError && (
          <div
            style={{
              background: 'var(--danger-bg)',
              color: 'var(--danger)',
              borderRadius: 8,
              padding: '10px 14px',
              margin: '10px 0',
              fontSize: 13,
            }}
          >
            Could not load current config: {loadError}
          </div>
        )}

        {!initialConfig && !loadError && <p className="hint">Loading current config...</p>}

        {initialConfig && <ConfigForm initialConfig={initialConfig} onSave={handleSave} />}
      </main>
    </>
  )
}
