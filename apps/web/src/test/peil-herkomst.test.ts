// Drift-controle voor de ontwerptaal Peil (O-14, ONTWERPTAAL §14): de kopieën in de app moeten byte-gelijk
// zijn aan de versie-gepinde kopie in design/ontwerptaal/ (op de herkomstkop van 2 regels na), en die weer aan
// de sha256 uit design/ontwerptaal/HERKOMST.md. Een nieuwe Peil-versie = nieuwe kopie + nieuwe sha256 hier.
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const REPO = join(__dirname, '../../../..');
const read = (p: string) => readFileSync(join(REPO, p), 'utf8');
/** sha256 van de inhoud zonder de herkomstkop (de eerste twee regels). */
const zonderKop = (p: string) => createHash('sha256').update(read(p).split('\n').slice(2).join('\n')).digest('hex');

const GEPIND: { app: string; bron: string; sha256: string }[] = [
  {
    app: 'apps/web/src/peil-alias.css',
    bron: 'design/ontwerptaal/aliaslagen/my_house.css',
    sha256: '18495346cb556e48753c99260424338ce344ef17d92003655c39c69988f8c302',
  },
];

describe('Peil-kopieën (herkomst en drift)', () => {
  const herkomst = read('design/ontwerptaal/HERKOMST.md');

  it.each(GEPIND)('$app is gelijk aan de gepinde Peil 1.1.0-kopie', ({ app, bron, sha256 }) => {
    expect(herkomst).toContain(sha256);
    expect(zonderKop(bron)).toBe(sha256);
    expect(zonderKop(app)).toBe(sha256);
    const kop = read(app).split('\n').slice(0, 2).join('\n');
    expect(kop).toContain('HERKOMST — Peil 1.1.0');
    expect(kop).toContain(`sha256 van het bronbestand (zonder deze kop): ${sha256}`);
  });
});
