import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import path from 'path';
import {defineConfig} from 'vite';

const API_HOST = process.env.API_HOST || '127.0.0.1';
const API_PORT = Number(process.env.API_PORT || 3001);
const API_TARGET = `http://${API_HOST}:${API_PORT}`;

export default defineConfig({
  base: './', // Required for Electron file:// protocol
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, '.'),
    },
  },
  server: {
    hmr: process.env.DISABLE_HMR !== 'true',
    proxy: {
      // dev:stack runs the Vite dev server on 3000 and the local API on 3001;
      // proxy /api so the React app's `${origin}/api` URLs reach the backend
      // without a separate VITE_API_BASE override.
      '/api': {
        target: API_TARGET,
        changeOrigin: false,
        secure: false,
      },
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
});
