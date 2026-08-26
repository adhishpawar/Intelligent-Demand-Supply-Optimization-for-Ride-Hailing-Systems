import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// PLAN §4.1 pivot note (see PROGRESS.md): Node.js was unavailable at session start,
// so the frontend was originally scoped buildless. The user explicitly asked for a
// real React app with role-based login/routing mid-session; a portable Node.js
// distribution was fetched (same pattern as Postgres/Redis/Kafka tonight) so this is
// a genuine Vite dev/build toolchain, not a buildless shim.
export default defineConfig({
  plugins: [react()],
  server: { port: 8080, strictPort: true },
  preview: { port: 8080, strictPort: true },
})
