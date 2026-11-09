/// <reference types="vitest" />
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { VitePWA } from 'vite-plugin-pwa';

/** VITE_APP_VERSION from the image build (contract C9), else the repo's VERSION file. */
function appVersion(): string {
  const fromEnv = process.env.VITE_APP_VERSION?.trim();
  if (fromEnv) return fromEnv;
  try {
    return readFileSync(fileURLToPath(new URL('../../VERSION', import.meta.url)), 'utf8').trim() || '0.0.0-dev';
  } catch {
    return '0.0.0-dev';
  }
}

export default defineConfig({
  define: {
    'import.meta.env.VITE_APP_VERSION': JSON.stringify(appVersion()),
  },
  plugins: [
    react(),
    VitePWA({
      registerType: 'autoUpdate',
      includeAssets: ['icon.svg'],
      manifest: {
        name: 'Huis — Smart Home Control',
        short_name: 'Huis',
        description: 'Lokale bediening van klimaat, verlichting en energie',
        lang: 'nl',
        start_url: '/',
        display: 'standalone',
        // Peil: achtergrond licht (background); donker volgt via de meta theme-color in index.html.
        background_color: '#EEF1F5',
        theme_color: '#EEF1F5',
        icons: [
          { src: 'icon.svg', sizes: 'any', type: 'image/svg+xml', purpose: 'any' },
          { src: 'icon-192.png', sizes: '192x192', type: 'image/png' },
          { src: 'icon-512.png', sizes: '512x512', type: 'image/png' },
          { src: 'icon-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
        ],
      },
      workbox: {
        // Static assets only. /api is never cached, so an offline action can never look executed.
        globPatterns: ['**/*.{js,css,html,svg,png,woff2}'],
        // Pages are loaded network-first (item 38): no cache-first app shell for navigations, and "/"
        // must not be answered from the precache either, otherwise a Cloudflare Access login redirect
        // would never reach the browser.
        navigateFallback: null,
        directoryIndex: '__no_directory_index__',
        cleanupOutdatedCaches: true,
        runtimeCaching: [
          {
            urlPattern: ({ request, url }) =>
              request.mode === 'navigate' && !url.pathname.startsWith('/api/') && !url.pathname.startsWith('/cdn-cgi/'),
            handler: 'NetworkFirst',
            options: {
              cacheName: 'shc-pages',
              networkTimeoutSeconds: 5,
              // Only real pages; redirects (Access login) and error pages are never stored.
              cacheableResponse: { statuses: [200] },
              plugins: [
                {
                  // Every SPA route is the same index.html: keep one copy.
                  cacheKeyWillBeUsed: async () => '/__shc_shell__',
                  // Offline and never visited: fall back to the precached index.html.
                  handlerDidError: async () => (await caches.match('/__shc_shell__')) ?? (await caches.match('index.html', { ignoreSearch: true })) ?? undefined,
                },
              ],
            },
          },
        ],
      },
    }),
  ],
  server: {
    proxy: {
      '/api': { target: 'http://localhost:8000', changeOrigin: false, ws: true },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    css: false,
  },
});
