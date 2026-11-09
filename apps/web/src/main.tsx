import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
// Peil (docs/ontwerp/PEIL_MIGRATIE.md §8): Tailwind 4 + ontwerptaal.css, de oude CSS en de aliaslaag in vaste lagen.
import './peil/peil.css';
import { App } from './App';
import { stripReloadParam } from './api/client';

// After a forced reload for Cloudflare Access (item 38) the cache-busting parameter is not needed.
stripReloadParam();

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
