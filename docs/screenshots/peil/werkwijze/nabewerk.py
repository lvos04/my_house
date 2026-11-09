"""Nabewerking van de afdrukken (gebruik: python nabewerk.py <ruwe map> <doelmap>; vereist Pillow en numpy):
1. lange Android-afdrukken inkorten (lege ruimte tussen inhoud en ondernavigatie weg);
2. PNG's verkleinen met een palet van 256 kleuren waarin de 96 meest voorkomende kleuren (vlakken,
   kaarten, randen, tekst) EXACT behouden blijven; alleen randpixels van tekst/iconen worden afgerond.
   Controle per bestand: elke kleur die >= 0,05 % van de pixels beslaat moet exact gelijk blijven,
   anders blijft het bestand onverkleind (verliesvrij)."""

import glob
import os
import sys
from collections import Counter

from PIL import Image

KEEP = 96


def rows_equal(im, y, bg):
    px = im.crop((0, y, im.width, y + 1)).get_flattened_data()
    return all(abs(p[0] - bg[0]) + abs(p[1] - bg[1]) + abs(p[2] - bg[2]) < 6 for p in px)


def trim_tall(im):
    H = im.height
    bg = im.getpixel((2, H - 140))
    y = H - 1
    while y > 0 and not rows_equal(im, y, bg):
        y -= 1
    nav_top = y + 1
    while y > 0 and rows_equal(im, y, bg):
        y -= 1
    end = min(y + 24, nav_top)
    if nav_top - end < 40:
        return im
    out = Image.new("RGB", (im.width, end + (H - nav_top)), bg)
    out.paste(im.crop((0, 0, im.width, end)), (0, 0))
    out.paste(im.crop((0, nav_top, im.width, H)), (0, end))
    return out


def reduce(im):
    import numpy as np

    a = np.asarray(im, dtype=np.uint8).reshape(-1, 3)
    uniq, inv, cnt = np.unique(a, axis=0, return_inverse=True, return_counts=True)
    inv = inv.reshape(-1)
    order = np.argsort(-cnt)
    if len(uniq) <= 256:
        pal = uniq[order]
    else:
        keep = uniq[order[:KEEP]]
        rest = im.quantize(colors=256 - KEEP, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
        rp = np.array(rest.getpalette()[: 3 * (256 - KEEP)], dtype=np.uint8).reshape(-1, 3)
        pal = np.vstack([keep, rp])
    # exacte toewijzing: elke unieke kleur naar de dichtstbijzijnde paletkleur (de vlakkleuren zitten er exact in)
    d = ((uniq[:, None, :].astype(np.int32) - pal[None, :, :].astype(np.int32)) ** 2).sum(-1)
    idx_u = d.argmin(1).astype(np.uint8)
    idx = idx_u[inv].reshape(im.height, im.width)
    q = Image.fromarray(idx, mode="P")
    flat = list(pal.reshape(-1).tolist())
    flat += [0] * (768 - len(flat))
    q.putpalette(flat)
    # controle: kleuren met >= 0,05 % van de pixels blijven exact
    total = a.shape[0]
    big = cnt / total >= 0.0005
    mapped = pal[idx_u]
    bad = np.where(big & (mapped != uniq).any(1))[0]
    if len(bad):
        return None, tuple(uniq[bad[0]].tolist())
    return q, None


src, dst = sys.argv[1], sys.argv[2]
os.makedirs(dst, exist_ok=True)
stats = Counter()
for f in sorted(glob.glob(src + "/*.png")):
    im = Image.open(f).convert("RGB")
    if "-volledig-" in f and "/android" in dst:
        im = trim_tall(im)
    q, bad = reduce(im)
    out = dst + "/" + os.path.basename(f)
    if q is None:
        im.save(out, optimize=True)
        stats["verliesvrij"] += 1
        print("verliesvrij", os.path.basename(f), bad)
    else:
        q.save(out, optimize=True)
        stats["palet"] += 1
print(dict(stats))
