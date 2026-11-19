"""Doorloop in ketenproeven dezelfde voorafgaande vergelijking als de beheerder."""
import uuid

from shc_api import backup, restore_control


async def reviewed(admin, body):
    response = await admin.post("/system/restore/preview", body)
    assert response.status_code == 200, response.text
    data = response.json()
    return {**body, "rollback_id": data["comparison"]["rollback_id"], "confirmation": data["confirmation"]}


async def cli_reviewed(args, path, passphrase):
    data = await restore_control.prepare(path, passphrase, args.mode, "cli", str(path.resolve()),
                                        backup.verify_file(path, passphrase))
    args.rollback_id = uuid.UUID(data["comparison"]["rollback_id"])
    args.confirmation = data["confirmation"]
    return args


async def verify_three_gates(admin, body):
    from shc_api.config import get_settings

    initial = await admin.post("/system/restore/apply", body)
    assert initial.status_code == 409
    proposal = await reviewed(admin, body)
    file_id = proposal["rollback_id"]
    backup_path = get_settings().backup_dir / "terugval" / f"{file_id}.shcbak"
    assert backup.verify_file(backup_path, body["passphrase"]).manifest["secrets_included"]
    downloaded = await admin.get(f"/system/restore/files/{file_id}")
    assert downloaded.status_code == 200 and downloaded.content == backup_path.read_bytes()
    for path in (backup_path, backup_path.with_suffix(".json")):
        # a: zonder de doelback-up, b: zonder de geregistreerde vergelijking.
        hidden = path.with_suffix(".apart")
        path.rename(hidden)
        try:
            refused = await admin.post("/system/restore/apply", proposal)
            assert refused.status_code == 409, refused.text
        finally:
            hidden.rename(path)
    # c: geen akkoord of het akkoord van een andere vergelijking.
    for wrong in (None, uuid.uuid4().hex):
        refused = await admin.post("/system/restore/apply", {**proposal, "confirmation": wrong})
        assert refused.status_code == 409, refused.text
    return proposal
