// Drift-controle voor de ontwerptaal Peil (O-14, ONTWERPTAAL §14, pin- en driftregel E08). De pin staat in
// design/ontwerptaal/HERKOMST.json (versie, bron-commit in ai-server-manager, sha256 per bronbestand). De kopieën in de
// app moeten byte-gelijk zijn aan de gepinde kopie in design/ontwerptaal/ (op de herkomstkop van 2 regels na), en die
// weer aan de sha256 in de pin. Nieuwe Peil-versie = `python3 tools/peil_kopie.py bijwerken` (schrijft kopieën en pin)
// + deze tests + de browsertests; de vergelijking met de bron zelf doet tools/peil_kopie.py in CI.
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const REPO = join(__dirname, '../../../..');
const read = (p: string) => readFileSync(join(REPO, p), 'utf8');
const sha = (s: string) => createHash('sha256').update(s).digest('hex');
/** De inhoud zonder de herkomstkop (de eerste twee regels). */
const zonderKop = (p: string) => read(p).split('\n').slice(2).join('\n');

type Herkomst = {
  versie: string;
  bron_repo: string;
  bron_commit: string;
  bestanden: Record<string, { bron_pad: string; sha256: string }>;
  app_kopieen: Record<string, string>;
};
const herkomst = JSON.parse(read('design/ontwerptaal/HERKOMST.json')) as Herkomst;

const APP: { app: string; kopie: string }[] = [
  { app: 'apps/web/src/peil-alias.css', kopie: 'aliaslagen/my_house.css' },
  { app: 'apps/web/src/peil/ontwerptaal.css', kopie: 'ontwerptaal.css' },
];

describe('Peil-kopieën (herkomst en drift)', () => {
  it('de pin is Peil 1.1.1 uit ai-server-manager op een volledige commit', () => {
    expect(herkomst.versie).toBe('1.1.1');
    expect(herkomst.bron_repo).toBe('lvos04/ai-server-manager');
    expect(herkomst.bron_commit).toMatch(/^[0-9a-f]{40}$/);
    expect(Object.values(herkomst.app_kopieen).sort()).toEqual(APP.map((a) => `design/ontwerptaal/${a.kopie}`).sort());
  });

  it.each(APP)('$app is gelijk aan de gepinde kopie $kopie', ({ app, kopie }) => {
    const pin = herkomst.bestanden[kopie];
    expect(sha(zonderKop(`design/ontwerptaal/${kopie}`))).toBe(pin.sha256);
    expect(sha(zonderKop(app))).toBe(pin.sha256);
    const kop = read(app).split('\n')[0];
    expect(kop).toContain(`HERKOMST — Peil ${herkomst.versie} · bron ${herkomst.bron_repo}@${herkomst.bron_commit} ${pin.bron_pad}`);
    expect(kop).toContain(`sha256 van het bronbestand (zonder deze kop): ${pin.sha256}`);
    // De inhoud noemt zelf dezelfde versie (een kopie van een andere versie met een aangepaste kop valt zo op).
    expect(zonderKop(app).slice(0, 400)).toContain(`Peil ${herkomst.versie} —`);
  });

  it('tokens.json (bron van de Android-kleuren) is byte-gelijk aan de pin', () => {
    expect(sha(read('design/ontwerptaal/tokens.json'))).toBe(herkomst.bestanden['tokens.json'].sha256);
  });

  it('1.1.1 zit echt in de app-kopieën: -soft-banden ≥ 7:1 met -ink en de my_house-correcties in de aliaslaag', () => {
    const tw = zonderKop('apps/web/src/peil/ontwerptaal.css');
    expect(tw).toContain('--domain-heat-soft: #FFC3A4;');
    expect(tw).toContain('--domain-cool-soft: #AAD2FF;');
    const alias = zonderKop('apps/web/src/peil-alias.css');
    expect(alias).toContain('.light-tile { border-color: var(--border-strong); }');
    expect(alias).toContain('--heat-soft: #FFC3A4;');
  });
});
