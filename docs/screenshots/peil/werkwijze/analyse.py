"""Vat review.json samen: analyse.py <review.json> [--json uit.json]"""

import json
import re
import sys
from collections import Counter, defaultdict

d = json.load(open(sys.argv[1]))
runs = [r for r in d["runs"] if "axe" in r]
som = {
    "metingen": len(runs),
    "schermen": sorted({r["scherm"] for r in runs}),
    "breedtes": sorted({r["breedte"] for r in runs}),
    "axe_overtredingen": sum(v["n"] for r in runs for v in r["axe"]),
    "tekst_onder_4_5": sum(len(r["tekstFout"]) for r in runs),
    "veldrand_onder_3": sum(len(r["veldFout"]) for r in runs),
    "knopgrens_onder_3": sum(len(r["knopGrensFout"]) for r in runs),
    "stippelrand_onder_3": sum(len(r["stippelFout"]) for r in runs),
    "doelen_onder_44": sum(len(r["doelen"]) for r in runs),
    "overloop_max_px": max(r["overloop"] for r in runs),
    "opacity_met_tekst": sum(len(r["opacityTekst"]) for r in runs),
    "afgekapt": sum(len(r["afgekapt"]) for r in runs),
    "inhoud_buiten_element": sorted(
        {f"{r['scherm']} {r['breedte']}: {x}" for r in runs for x in r.get("inhoudUit", [])}
    ),
    "min_tekstcontrast": min(r["minTekst"] for r in runs),
    "min_lettergrootte_px": min(r["minFont"] for r in runs),
    "min_knopgrens": min(r["knopGrensMin"] for r in runs),
    "h1_niet_1": [
        f"{r['scherm']} {r['thema']} {r['breedte']}: {r['structuur']['h1']}" for r in runs if r["structuur"]["h1"] != 1
    ],
    "kopsprongen": sorted({f"{r['scherm']}: {s}" for r in runs for s in r["structuur"]["sprong"]}),
    "dubbele_ids": sorted({f"{r['scherm']}: {s}" for r in runs for s in r["structuur"]["dubbel"]}),
    "kapotte_aria": sorted({f"{r['scherm']}: {s}" for r in runs for s in r["structuur"]["kapot"]}),
}
f = [r for r in runs if "focus" in r]
som["focus_doorlopen"] = len(f)
som["focus_stops_totaal"] = sum(r["focus"]["aantal"] for r in f)
som["focus_ring_min"] = min(r["focus"]["ringMin"] for r in f)
som["focus_zonder_ring"] = sorted({f"{r['scherm']} {r['breedte']}: {x}" for r in f for x in r["focus"]["zonderRing"]})
som["focus_zwak"] = sorted({f"{r['scherm']} {r['breedte']}: {x}" for r in f for x in r["focus"]["zwak"]})
som["focus_bedekt"] = sorted(
    {f"{r['scherm']} {r['thema']} {r['breedte']}: {x}" for r in f for x in r["focus"]["bedekt"]}
)
som["focus_deels_bedekt"] = sorted({f"{r['scherm']} {r['breedte']}: {x}" for r in f for x in r["focus"]["deelsBedekt"]})
som["focus_eerste_niet_skiplink"] = sorted(
    {
        f"{r['scherm']} {r['breedte']}: {r['focus'].get('eerste')}"
        for r in f
        if r["focus"].get("eerste") and "skip-link" not in r["focus"]["eerste"]
    }
)

for k in ["tekstFout", "veldFout", "knopGrensFout", "stippelFout", "doelen", "afgekapt", "opacityTekst"]:
    items = Counter()
    for r in runs:
        for x in r[k]:
            items[json.dumps(x, ensure_ascii=False)[:220] if not isinstance(x, str) else x[:220]] += 1
    som[f"detail_{k}"] = items.most_common(25)

onder_peil = Counter(re.sub(r" \d+\.\d+ \(", " (", x) for r in runs for x in r["tekstOnderPeil"])
som["tekst_4_5_tot_7"] = onder_peil.most_common(40)

groen = defaultdict(set)
for r in runs:
    for g in r["groen"]:
        groen[(g["token"], g["context"][:70])].add(r["scherm"])
som["groen"] = sorted(f"{t} | {c} | {', '.join(sorted(s))}" for (t, c), s in groen.items())
fout_groen = [
    x
    for x in som["groen"]
    if re.search(
        r"Onbekend|Verouderd|Laatst bekend|geen meting|niet bereikbaar|offline|Geen verbinding", x.split(" | ")[1], re.I
    )
    and x.startswith("healthy")
]
som["groen_bij_onbekend_of_verouderd"] = fout_groen

ka = defaultdict(set)
for r in runs:
    for g in r["kleurAlleen"]:
        ka[(g["token"], g["el"][:40], g["context"][:70])].add(r["scherm"])
som["kleur_alleen_kandidaten"] = sorted(f"{t} | {e} | {c} | {', '.join(sorted(s))}" for (t, e, c), s in ka.items())

WOORDEN = {
    r"\bIn orde\b": "In orde → Gezond",
    r"\bActueel\b": "Actueel (versheid is nooit een badge)",
    r"\bLet op\b": "Let op (statuswoord → Beperkt / Wat actie nodig heeft)",
    r"\bProbleem\b": "Probleem → Storing",
    r"\bVerminderd\b|\bVerstoord\b": "Verminderd/Verstoord → Beperkt",
    r"\bGelukt\b|\bKlaar\b": "Gelukt/Klaar (uitvoering → Uitgevoerd en gecontroleerd)",
    r"\bOK\b|\bOkÉ?\b": "OK als knop",
    r"\bDoorgaan\b|Weet je het zeker": "Doorgaan / Weet je het zeker",
    r"snooz|safe mode|blast radius|runbook|healthcheck|allowlist": "Engelse term",
    r"\bVerstuurd\b": "Verstuurd (my_house: Verzonden)",
    r"\bontbreekt\b": "ontbreekt (meetkwaliteit → geen meting)",
    r"\bWaarschuwing\b": "Waarschuwing (ernst → Info/Middel/Hoog)",
    r"\bOffline\b": "Offline als statuswoord",
    r"Laden…": "Laden… (onbekend is geen laden)",
    r"!": "uitroepteken",
    r"\bStoring\b": "(controle) Storing",
    r"\bOnbekend\b": "(controle) Onbekend",
}
woorden = defaultdict(lambda: defaultdict(set))
for r in runs:
    t = r.get("zichtbareTekst", "")
    for pat, uitleg in WOORDEN.items():
        for m in re.finditer(pat, t):
            ctx = t[max(0, m.start() - 45) : m.end() + 45].replace("\n", " ⏎ ")
            woorden[uitleg][ctx].add(r["scherm"])
som["woordenlijst"] = {
    u: sorted(f"…{c}… [{', '.join(sorted(s))}]" for c, s in v.items())[:20] for u, v in woorden.items()
}
fc = [r for r in d["runs"] if r.get("scherm") == "forced-colors"]
som["forced_colors_stippel"] = fc[0]["stippel"] if fc else None

out = json.dumps(som, ensure_ascii=False, indent=1)
if "--json" in sys.argv:
    open(sys.argv[sys.argv.index("--json") + 1], "w").write(out + "\n")
print(out)
