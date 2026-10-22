import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath, URL } from 'node:url'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    // Bind IPv4 explicitly: Vite otherwise listens on [::1] only, which makes
    // http://127.0.0.1:5173 unreachable and breaks tooling that assumes IPv4.
    host: '127.0.0.1',
    port: 5173,
    // The API is same-origin through this proxy in development, so the session
    // cookie is first-party and CORS is not involved.
    // Only /api is proxied. Proxying /health or /metrics would shadow the
    // dashboard's own routes of the same name on a direct load or refresh —
    // the SPA reads everything it needs through /api/v1.
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
    rollupOptions: {
      output: {
        // Split the heavy, rarely-changing dependencies into their own chunks so a
        // change to application code does not invalidate the whole bundle.
        manualChunks: {
          react: ['react', 'react-dom', 'react-router-dom'],
          charts: ['recharts'],
          query: ['@tanstack/react-query'],
        },
      },
    },
  },
})
