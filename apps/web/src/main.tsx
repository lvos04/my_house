import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import '@fontsource/inter/400.css';
import '@fontsource/inter/500.css';
import '@fontsource/inter/600.css';
import '@fontsource/space-grotesk/500.css';
import '@fontsource/space-grotesk/600.css';
import './styles.css';
// Peil fase 1 (docs/ontwerp/PEIL_MIGRATIE.md §8.1): de aliaslaag na styles.css zet de oude variabelen op Peil-waarden.
import './peil-alias.css';
import { App } from './App';
import { stripReloadParam } from './api/client';

// After a forced reload for Cloudflare Access (item 38) the cache-busting parameter is not needed.
stripReloadParam();

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
