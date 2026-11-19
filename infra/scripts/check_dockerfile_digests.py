"""Weiger externe Dockerfile-basisimages zonder vaste SHA-256-digest."""

import argparse
import re
import shlex
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = re.compile(r"(?:Dockerfile(?:\..+)?|.+\.Dockerfile)", re.IGNORECASE)
FROM = re.compile(r"FROM\s+(?:--platform=\S+\s+)?(\S+)(?:\s+AS\s+(\S+))?", re.IGNORECASE)
PIN = re.compile(r"[^@$]+@sha256:[0-9a-f]{64}")
ESCAPE = re.compile(r"#\s*escape\s*=\s*([\\\x60])", re.IGNORECASE)
HEREDOC = re.compile(r"""<<(-?)(["']?)([\w.-]+)\2""")


def instructions(source: str) -> Iterator[tuple[int, str]]:
    """Lees Dockerfile-instructies met commentaar, vervolgregels en heredoc-tekst."""
    escape = "\\"
    directives = True
    pending = None
    start = 0
    heredocs: list[tuple[str, bool]] = []
    for number, physical in enumerate(source.removeprefix("\ufeff").splitlines(), 1):
        if heredocs:
            end, strip_tabs = heredocs[0]
            if (physical.lstrip("\t") if strip_tabs else physical) == end:
                heredocs.pop(0)
            continue
        line = physical.lstrip()
        if directives and (directive := ESCAPE.fullmatch(line)):
            escape = directive[1]
        if not line or line.startswith("#"):
            continue
        directives = False
        if pending is None:
            start = number
            pending = ""
        else:
            # De inspringing kan de enige scheiding tussen twee woorden zijn.
            line = physical
        line = line.rstrip()
        if line.endswith(escape):
            pending += line[:-1]
            continue
        line = (pending + line).lstrip()
        pending = None
        if "<<" in line and line.split(maxsplit=1)[0].upper() in {"RUN", "COPY", "ADD"}:
            heredocs = [
                (match[3], bool(match[1]))
                for word in shlex.split(line, posix=False)
                if (match := HEREDOC.fullmatch(word))
            ]
        yield start, line


def violations(source: str) -> list[tuple[int, str]]:
    stages: set[str] = set()
    errors = []
    for number, line in instructions(source):
        if line.split(maxsplit=1)[0].upper() != "FROM":
            continue
        match = FROM.fullmatch(line)
        if not match:
            errors.append((number, line))
            continue
        image, stage = match.groups()
        if image.lower() not in stages | {"scratch"} and not PIN.fullmatch(image):
            errors.append((number, image))
        if stage:
            stages.add(stage.lower())
    return errors


def dockerfiles() -> list[Path]:
    result = subprocess.run(  # noqa: S603 - vaste git-aanroep binnen deze repo
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],  # noqa: S607
        cwd=ROOT, capture_output=True, check=True,
    )
    paths = {Path(name) for name in result.stdout.decode().split("\0") if name}
    return sorted(
        path for path in paths
        if DOCKERFILE.fullmatch(path.name) and not path.name.endswith(".dockerignore") and (ROOT / path).is_file()
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dockerfiles", nargs="*", type=Path)
    args = parser.parse_args()
    paths = args.dockerfiles or [ROOT / path for path in dockerfiles()]
    if not paths:
        parser.error("Geen Dockerfiles gevonden.")
    failed = False
    for path in paths:
        for number, image in violations(path.read_text(encoding="utf-8")):
            print(f"FOUT {path}:{number}: {image!r} vereist een vaste externe @sha256-digest.", file=sys.stderr)
            failed = True
    if failed:
        return 1
    print(f"Digestcontrole: {len(paths)} Dockerfiles gecontroleerd.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
