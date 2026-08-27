import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    // bind to all addresses so the dev server can be reached from other hosts/containers
    host: true,
    port: 3000,
    strictPort: false,
    cors: true,
  },
})
