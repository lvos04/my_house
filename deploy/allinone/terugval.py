"""Eenmalig startprogramma voor een offline kopie van de Custom App."""

import tempfile
import uuid

from . import supervisor


def main() -> int:
    # Ook na de databasecommit kan de bewaker nog een fout of een stop ontvangen.
    # Maak het nieuwe ID pas zichtbaar nadat ook zijn afsluiting is gelukt.
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as output:
        code = supervisor.main(offline_fallback=True, fallback_output=output)
        if code == 0:
            output.seek(0)
            try:
                regel, = [line for line in output if line.startswith("Terugval-ID: ")]
                receipt = uuid.UUID(regel.removeprefix("Terugval-ID: ").strip())
            except ValueError:
                code = 1
            else:
                supervisor.log(f"Terugval-ID: {receipt}")
                supervisor.log("Terugvalkopie voorbereid. PostgreSQL is gestopt.")
                return 0
    supervisor.log("Terugval geweigerd. De oude worker blijft uit. Bewaar de bron en de snapshot.")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
