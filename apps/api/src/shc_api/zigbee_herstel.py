"""Publiceer ontvangen Zigbee2MQTT-bestanden uitsluitend in een eigen, ongekoppelde herstelmap."""

import fcntl
import hashlib
import os
from pathlib import Path

from .backup import Z2M_CONFIG, Z2M_SECRET_FILES, BackupError
from .restore_control import sync_directory

BESTANDEN = frozenset({Z2M_CONFIG, *Z2M_SECRET_FILES, 'database.db', 'coordinator_backup.json',
                       'state.json', 'devices.yaml', 'groups.yaml'})


def herstelmap(data: Path, zigbee: Path | None) -> Path:
    root = data / 'zigbee-herstel'
    actief = (zigbee or data / 'zigbee2mqtt').resolve()
    if root.is_symlink() or root.resolve().is_relative_to(actief) or actief.is_relative_to(root.resolve()):
        raise BackupError('De herstelmap overlapt de actieve Zigbee2MQTT-opslag.')
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    if root.stat().st_mode & 0o022:
        raise BackupError('De herstelmap is beschrijfbaar voor andere gebruikers.')
    return root


def _inhoud(map_: Path) -> dict[str, str]:
    if map_.is_symlink() or not map_.is_dir():
        raise BackupError('De herstelmap is ongeldig.')
    inhoud = {}
    for pad in sorted(map_.iterdir()):
        if pad.name not in BESTANDEN or pad.is_symlink() or not pad.is_file():
            raise BackupError('De selectie bevat een onbekend Zigbee2MQTT-bestand.')
        with pad.open('rb') as bestand:
            inhoud[pad.name] = hashlib.file_digest(bestand, 'sha256').hexdigest()
    if not inhoud:
        raise BackupError('Kies minstens één Zigbee2MQTT-bestand.')
    return inhoud


def publiceer(bestanden: Path, root: Path) -> dict:
    inhoud = _inhoud(bestanden)
    digest = hashlib.sha256()
    for naam, vingerafdruk in inhoud.items():
        digest.update(naam.encode() + b'\0' + bytes.fromhex(vingerafdruk))
    doel = root / digest.hexdigest()[:32]
    # Een verloren HTTP-antwoord of een gelijktijdige herhaling levert dezelfde complete kopie op.
    slot = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fcntl.flock(slot, fcntl.LOCK_EX)
        if doel.exists() or doel.is_symlink():
            if _inhoud(doel) != inhoud:
                raise BackupError('De bestaande herstelkopie is gewijzigd. Bewaar die kopie afzonderlijk.')
        else:
            sync_directory(bestanden)
            bestanden.rename(doel)
            os.fsync(slot)
    finally:
        os.close(slot)
    return {'map': f'{root.name}/{doel.name}', 'bestanden': list(inhoud)}
