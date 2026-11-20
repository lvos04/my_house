"""Draai de historische fixtures met de destijds uitgebrachte routes, migraties en worker."""

import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
VERSIES = {
    "schema0006": "fbaebb377dd391497dcf4f4dea3da8c9cfa1a3da",
    "schema0007a": "299cb5b1f3f0449d6b2748c764cba1fea8818c28",
    "schema0007b": "44486bf8d66be0c2f2e42d8150304f03f83d764b",
    "schema0008": "000a036f6fb1cbb00c63c377b1d5e76fb5e5f44d",
    "voorgaand": "5079599361142df293db06eb16578d3621fd0dc3",
    "basis": "140d39df39b670b064e663a920a650f03bd9c9d0",
    "voor_herstel": "e22286cd3620b5e9d31e8a0acf0841892b9ab04b",
}


@pytest.fixture(scope="module")
def versies(tmp_path_factory):
    root = tmp_path_factory.mktemp("connectorversies")
    shutil.copytree(Path(__file__).with_name("connector_versies"), root / "proeven")
    for naam, commit in VERSIES.items():
        # Dezelfde bron in CI en lokaal. Iedere proef gebruikt een eigen PostgreSQL-database.
        args = ["git", "archive", commit, "VERSION", "apps/api", "integrations"]
        with subprocess.Popen(args, cwd=REPO, stdout=subprocess.PIPE) as proc:
            with tarfile.open(fileobj=proc.stdout, mode="r|") as archive:
                archive.extractall(root / naam, filter="data")
            assert proc.wait() == 0
    return root


@pytest.mark.parametrize(("versie", "bestand", "selectie"), [
    ("schema0007a", "annulering", "schema_annulering"),
    ("schema0007b", "annulering", "schema_annulering or dezelfde_unit"),
    ("voorgaand", "annulering", "toekomstige_reservering"),
    ("nieuw", "annulering", "reeds_actieve_worker"),
    ("nieuw", "overgangen", "not basis and not vorige_installatie and not apply_bevestigd"),
    ("schema0007a", "overgangen", "basis_open_herstel"),
    ("schema0007a", "overgangen", "basis_oud_archief or basis_eerder_teruggezette"),
    ("schema0007a", "overgangen", "apply_bevestigd"),
    ("schema0007b", "overgangen", "apply_bevestigd"),
    ("schema0007a", "overgangen", "vorige_installatie"),
    ("schema0007b", "overgangen", "vorige_installatie"),
    ("schema0007b", "herstelbewijs", "oude_stille and met_opdracht"),
    ("schema0007b", "herstelbewijs", "oude_stille and enkel_voorstel"),
    ("schema0007b", "herstelbewijs", "oude_afgehandelde"),
    ("schema0007a", "herstelbewijs", "oude_vervallen"),
    ("nieuw", "herstelbewijs", "dump_weigert or vorige_worker or oude_0007"),
    ("schema0007b", "overdracht", "oude_meting_na_wijziging_buitengroep"),
    ("schema0007a", "overdracht", "oude_drieveldmeting_na_groepswissel"),
    ("voorgaand", "overdracht", "terugvalkopie and automatisch"),
    ("voorgaand", "overdracht", "terugvalkopie and handmatig"),
    ("voorgaand", "overdracht", "terugvalkopie and opslagkopie"),
    ("voorgaand", "overdracht", "zelfde_schema_rollback_in_voorgaande_software"),
    ("nieuw", "overdracht", "onuitgevoerde_historie or meetidentiteit or hetzelfde_schema or alleen_lezen_terugval"),
])
def test_versiewissel(versies, versie, bestand, selectie):
    bron = REPO if versie == "nieuw" else versies / versie
    env = dict(os.environ, PROEF_VERSIES=str(versies), PROEF_EINDVERSIE=str(REPO),
               PYTHONPATH=os.pathsep.join(str(bron / deel) for deel in ("apps/api/src", "integrations/src", "apps/api")))
    subprocess.run([
        sys.executable, "-m", "pytest", "-p", "tests.conftest", "-p", "no:cacheprovider", "-c", "pyproject.toml",
        "-qs", "--tb=short", str(versies / "proeven" / f"{bestand}.py"), "-k", selectie,
    ], cwd=bron / "apps/api", env=env, check=True)
