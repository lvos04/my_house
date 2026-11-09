# Wijzigingen

Wat er per versie verandert. De uitgebreide uitleg per release staat in `docs/release-notes.md`.

## Unreleased

### Documentatie

- **Omzettingsontwerp naar de ontwerptaal Peil** (alleen ontwerp, de app is niet gewijzigd):
  `docs/ontwerp/PEIL_MIGRATIE.md` vertaalt elk scherm van de web-app en de Android-app naar Peil (tokens, componenten,
  iconen, statusvorm, microcopy), beschrijft fase 1 (aliaslaag) en fase 2 (Tailwind 4 + shadcn/ui in de React
  18-variant), de link "Terug naar de Hub", de themaknop en welke tests geraakt worden. Besluit in
  `docs/adr/ADR-0008-peil-omzetting-twee-fasen.md`.
- Nulmeting: schermafdrukken van de huidige stand (web 222, Android 68) en een axe-meting in
  `docs/screenshots/peil/voor/`.
- Versie-gepinde kopie van Peil 1.1.0 met herkomst en sha256 in `design/ontwerptaal/`.

### Gerepareerd

- **Database-update 0004 hangt niet meer af van de tijdzone van de database.** De vervaldatum van oude
  telefoonsleutels (180 dagen na het laatste gebruik, minstens 30 dagen na de update) is nu precies 180 × 24 uur,
  net als bij een nieuwe koppeling. Voorheen kon die op een database buiten UTC een uur verschuiven rond de
  zomer- en wintertijd. De test draait de update nu in drie tijdzones. Zie `docs/adr/ADR-0007-tijdzone-onafhankelijke-migraties.md`.
