// Testaccounts voor de screenshot-scripts: dezelfde fictieve testwaarden als de browsertests. De waarden staan op
// één plek, in apps/web/e2e/helpers.ts (ADMIN en VIEWER); dit bestand leest ze daaruit. Alleen voor de wegwerp-stack.
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

// PEIL_REPO zoals in run_shots.sh; standaard vier mappen boven dit bestand.
const REPO = process.env.PEIL_REPO || fileURLToPath(new URL('../../../../', import.meta.url));
const HELPERS = resolve(REPO, 'apps/web/e2e/helpers.ts');
const BRON = readFileSync(HELPERS, 'utf8');

function account(naam) {
  const m = BRON.match(new RegExp(`^export const ${naam} = \\{ username: '([^']+)', password: '([^']+)' \\};$`, 'm'));
  if (!m) throw new Error(`${naam} niet gevonden in ${HELPERS}: verwacht "export const ${naam} = { username: '…', password: '…' };".`);
  return { username: m[1], password: m[2] };
}

export const ADMIN = account('ADMIN');
export const VIEWER = account('VIEWER');
