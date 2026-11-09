/* Thema vóór de eerste weergave (Peil; docs/ontwerp/PEIL_MIGRATIE.md §9.2, ADR-0008 punt 4).
   Extern bestand omdat de CSP alleen script-src 'self' toestaat (geen inline script).
   Keuze op dit apparaat in localStorage "shc.thema": "systeem" (standaard) | "licht" | "donker".
   Zet ALTIJD het opgeloste thema op <html>: data-theme="dark|light" en de klasse "dark" bij donker,
   ook bij "systeem" (volgt prefers-color-scheme en luistert naar wijzigingen). Peil kent geen
   prefers-color-scheme-terugval in ontwerptaal.css: zonder dit script is de app altijd licht.
   Dezelfde regels staan in src/theme.ts (de knop in de app); src/test/thema.test.tsx houdt ze gelijk. */
(function () {
  var KEY = 'shc.thema';
  var KLEUR = { light: '#EEF1F5', dark: '#0A0D12' };
  var mq = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;
  function keuze() {
    try {
      var v = window.localStorage.getItem(KEY);
      return v === 'licht' || v === 'donker' ? v : 'systeem';
    } catch (e) {
      return 'systeem';
    }
  }
  function toepassen() {
    var k = keuze();
    var donker = k === 'donker' || (k === 'systeem' && !!(mq && mq.matches));
    var thema = donker ? 'dark' : 'light';
    var root = document.documentElement;
    root.setAttribute('data-theme', thema);
    if (donker) root.classList.add('dark');
    else root.classList.remove('dark');
    root.style.colorScheme = thema;
    var metas = document.querySelectorAll('meta[name="theme-color"]');
    for (var i = 0; i < metas.length; i++) metas[i].setAttribute('content', KLEUR[thema]);
  }
  toepassen();
  if (mq) {
    if (mq.addEventListener) mq.addEventListener('change', toepassen);
    else if (mq.addListener) mq.addListener(toepassen);
  }
  // Een keuze in een ander tabblad volgt hier direct.
  window.addEventListener('storage', function (e) {
    if (e.key === KEY) toepassen();
  });
})();
