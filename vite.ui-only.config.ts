import { fileURLToPath } from 'node:url'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig } from 'vite'


export default defineConfig({
  base: './',
  plugins: [react(), tailwindcss()],
  build: {
    outDir: 'dist-ui',
    emptyOutDir: true,
    rollupOptions: {
      input: fileURLToPath(new URL('./ui-only.html', import.meta.url)),
    },
  },
})
