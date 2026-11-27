/** Adressen voor gesimuleerde apparaten en veldvalidatie in de webproeven. */
export function proefadres(host: number): string {
  return [10, 83, 27, host].join('.');
}

export const linkLokaalProefadres = [169, 254, 27, 8].join('.');
