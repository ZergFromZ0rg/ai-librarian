import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import pdfjsAssets from './vite-pdfjs-assets.js'

export default defineConfig({
  plugins: [react(), pdfjsAssets()],
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
