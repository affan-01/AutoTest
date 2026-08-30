import { useState } from 'react'
import { api } from '../lib/api'

interface ScreenshotGalleryProps {
  ticketId: string
  filenames: string[]
}

export default function ScreenshotGallery({ ticketId, filenames }: ScreenshotGalleryProps) {
  const [openFile, setOpenFile] = useState<string | null>(null)

  if (filenames.length === 0) return null

  return (
    <>
      <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
        {filenames.map((filename) => (
          <img
            key={filename}
            src={api.screenshotUrl(ticketId, filename)}
            alt={filename}
            title={filename}
            onClick={() => setOpenFile(filename)}
            style={{
              width: 60,
              height: 60,
              objectFit: 'cover',
              borderRadius: 4,
              border: '1px solid var(--border)',
              cursor: 'pointer',
            }}
          />
        ))}
      </div>
      {openFile && (
        <div
          onClick={() => setOpenFile(null)}
          style={{
            position: 'fixed',
            inset: 0,
            background: 'rgba(0, 0, 0, 0.8)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 1000,
            cursor: 'zoom-out',
            padding: 24,
          }}
        >
          <img
            src={api.screenshotUrl(ticketId, openFile)}
            alt={openFile}
            style={{
              maxWidth: '100%',
              maxHeight: '100%',
              borderRadius: 6,
              boxShadow: '0 8px 32px rgba(0, 0, 0, 0.5)',
            }}
          />
        </div>
      )}
    </>
  )
}
