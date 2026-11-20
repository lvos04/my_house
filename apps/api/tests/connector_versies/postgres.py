"""Eigen PostgreSQL 16 voor fysieke kopieën. Een gedeeld testcluster wordt nooit gekopieerd."""

import os
import secrets
import socket
import subprocess
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def draai_postgres(data, logboek, env):
    with socket.socket() as lokaal:
        lokaal.bind(("localhost", 0))
        poort = lokaal.getsockname()[1]
    opties = f"-p {poort} -c listen_addresses=localhost -c unix_socket_directories=''"
    pg_ctl = ["pg_ctl", "-D", str(data), "-w"]
    try:
        subprocess.run([*pg_ctl, "-l", str(logboek), "-o", opties, "start"], check=True, env=env)
        yield poort
    finally:
        if (data / "postmaster.pid").is_file():
            subprocess.run([*pg_ctl, "-m", "fast", "stop"], check=True, env=env)


@contextmanager
def eigen_bron(werk: Path):
    data = werk / "bron"
    env = dict(os.environ, TMPDIR=str(werk), PGPASSWORD=secrets.token_urlsafe(32),
               PATH="/usr/lib/postgresql/16/bin" + os.pathsep + os.environ["PATH"])
    toegang = werk / "toegang"
    with open(toegang, "x", opener=lambda pad, flags: os.open(pad, flags, 0o600)) as bestand:
        bestand.write(env["PGPASSWORD"] + "\n")
    try:
        subprocess.run(["initdb", "-D", str(data), "-U", "postgres", "--auth=scram-sha-256",
                        "--pwfile", str(toegang), "--locale=C", "--encoding=UTF8",
                        "-c", "dynamic_shared_memory_type=mmap"], check=True, env=env, stdout=subprocess.DEVNULL)
    finally:
        toegang.unlink()
    assert (data / "PG_VERSION").read_text().strip() == "16"
    with draai_postgres(data, werk / "bron.log", env) as poort:
        adres = f"postgresql://postgres@localhost:{poort}"
        yield dict(env, SHC_TEST_PG=adres, PROEF_OPSLAG_PG=adres)
