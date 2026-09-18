import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig } from 'vite'

// In dev, API calls are proxied to the FastAPI service; in production the built
// files are served by FastAPI itself, so the UI and API share one origin.
const API = process.env.VITE_API_TARGET ?? 'http://localhost:8000'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: { chunkSizeWarningLimit: 1000 }, // one bundle is fine; recharts dominates it
  server: {
    fs: { allow: ['..'] }, // public sample cases live in ../Problem_doc
    proxy: {
      '/health': API,
      '/optimize-energy': API,
    },
  },
})
