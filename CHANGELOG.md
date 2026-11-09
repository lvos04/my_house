# Wijzigingen

Wat er per versie verandert. De uitgebreide uitleg per release staat in `docs/release-notes.md`.

## Unreleased

### Gerepareerd

- **Database-update 0004 hangt niet meer af van de tijdzone van de database.** De vervaldatum van oude
  telefoonsleutels (180 dagen na het laatste gebruik, minstens 30 dagen na de update) is nu precies 180 × 24 uur,
  net als bij een nieuwe koppeling. Voorheen kon die op een database buiten UTC een uur verschuiven rond de
  zomer- en wintertijd. De test draait de update nu in drie tijdzones. Zie `docs/adr/ADR-0007-tijdzone-onafhankelijke-migraties.md`.
