import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  base: './',
  plugins: [react(), tailwindcss()],
  cacheDir: '.cache/vite',
  server: {
    host: '127.0.0.1',
    strictPort: true,
    cors: { origin: /^http:\/\/(127\.0\.0\.1|localhost)(:\d+)?$/ },
  },
  build: {
    manifest: true,
    license: true,
    rolldownOptions: { input: 'src/main.tsx' },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
    clearMocks: true,
    restoreMocks: true,
  },
});
