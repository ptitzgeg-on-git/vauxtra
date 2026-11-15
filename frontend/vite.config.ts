import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  build: {
    chunkSizeWarningLimit: 650,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes('node_modules')) return undefined;
          // react-router-dom is a thin re-export of react-router, and scheduler is react-dom's
          // own dependency: both load with React on every page, so they ship with it.
          if (
            id.includes('react-router-dom') ||
            id.includes('/react-router/') ||
            id.includes('/react/') ||
            id.includes('/react-dom/') ||
            id.includes('/scheduler/')
          ) {
            return 'vendor-react';
          }
          if (id.includes('@tanstack/react-query')) return 'vendor-query';
          if (id.includes('lucide-react')) return 'vendor-icons';
          if (id.includes('/axios/')) return 'vendor-http';
          if (id.includes('react-hot-toast')) return 'vendor-toast';
          return 'vendor-misc';
        },
      },
    },
  },
  resolve: {
    alias: {
      "@": path.resolve(import.meta.dirname, "./src"),
    },
  },
  server: {
    host: '0.0.0.0', // Exposes the Vite dev server on the local network
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8888',
        changeOrigin: true,
      }
    }
  }
})
