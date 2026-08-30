import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// During `npm run dev`, the Vite dev server proxies API calls to the Python backend
// (pipelines/api.py) so the dashboard can be developed with hot reload without needing to
// rebuild dist/ on every change. `npm run build` output is served directly by that same
// backend in normal use, so no proxy is needed there.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8765',
      '/config': 'http://127.0.0.1:8765',
      '/save': 'http://127.0.0.1:8765',
    },
  },
})
