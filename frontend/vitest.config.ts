import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import path from 'path';

/**
 * Kept out of `vite.config.ts` so the app build never carries test-only aliases.
 *
 * `@/api/client` is redirected at resolution time rather than mocked per file: a unit test
 * that quietly reaches `registry`, `localhost:8888` or anywhere else is a test whose result
 * depends on what is running on the machine, and that is not a test. Components under test
 * get their data as props or through `setQueryData`.
 */
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: [
      { find: /^@\/api\/client$/, replacement: path.resolve(__dirname, './src/test/apiStub.ts') },
      { find: /^@\//, replacement: path.resolve(__dirname, './src') + '/' },
    ],
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
    restoreMocks: true,
  },
});
