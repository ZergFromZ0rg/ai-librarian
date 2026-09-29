import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (
            /[\\/]node_modules[\\/](react-markdown|remark-gfm|remark-math|rehype-katex|katex)([\\/]|$)/.test(
              id,
            )
          ) {
            return "markdown";
          }
          return undefined;
        },
      },
    },
  },
  server: {
    host: true,
    port: 3000,
    strictPort: false,
    cors: true,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
})
