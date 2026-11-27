const proefadres = [192, 168, 1, 20].join('.');
export const proefwoningUrl = `http://${proefadres}:8088`;

export async function maskLan(page) {
  return page.evaluate((adressen) => {
    const toegestaan = new Set(adressen);
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let aantal = 0;
    for (let tekst = walker.nextNode(); tekst; tekst = walker.nextNode()) {
      const gevonden = tekst.textContent.match(/\b(?:\d{1,3}\.){3}\d{1,3}\b/g) ?? [];
      if (gevonden.some((adres) => !toegestaan.has(adres))) {
        tekst.parentElement.setAttribute('data-peil-mask', '');
        aantal++;
      }
    }
    return aantal;
  }, [proefadres, [127, 0, 0, 1].join('.')]);
}
