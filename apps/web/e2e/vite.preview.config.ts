// `vite preview` for the browser tests: serves the built app (dist/) and proxies /api (incl. the
// WebSocket) to the API that e2e/run-stack.sh started on a free port (E2E_API_URL).
//
// The app's own vite.config.ts proxies to a fixed localhost:8000, and Vite's proxy answers an
// unreachable API with an empty "500 text/plain", which the app would read as an answer from the API.
// Here an unreachable API gives "502 Bad Gateway" without a JSON body, like a reverse proxy in front
// of a stopped API does, so the app's offline rule (verbeterlijst 4) sees "server niet bereikbaar".
import type { IncomingMessage, ServerResponse } from 'node:http';
import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vite';

const target = process.env.E2E_API_URL;
if (!target) throw new Error('E2E_API_URL ontbreekt (start de tests met e2e/run-stack.sh).');

export default defineConfig({
  root: fileURLToPath(new URL('..', import.meta.url)),
  logLevel: 'warn',
  preview: {
    host: '127.0.0.1',
    strictPort: true,
    proxy: {
      '/api': {
        target,
        changeOrigin: false,
        ws: true,
        configure(proxy) {
          // Registered before Vite's own handler, which then sees headersSent and does nothing.
          proxy.on('error', (_err, _req, res) => {
            // HTTP: a ServerResponse; WebSocket upgrade: the client socket (Vite closes that itself).
            const r = res as unknown as Partial<ServerResponse<IncomingMessage>> | undefined;
            if (r && typeof r.writeHead === 'function' && !r.headersSent && !r.writableEnded) {
              r.writeHead(502, { 'Content-Type': 'text/plain; charset=utf-8', 'Cache-Control': 'no-store' });
              r.end?.('API niet bereikbaar');
            }
          });
        },
      },
    },
  },
});
