"""Eenmalig startprogramma voor een offline kopie van de Custom App."""

from . import supervisor


def main() -> int:
    code = supervisor.main(offline_fallback=True)
    if code:
        supervisor.log("Terugval geweigerd. De oude worker blijft uit. Bewaar de bron en de snapshot.")
    else:
        supervisor.log("Terugvalkopie voorbereid. PostgreSQL is gestopt.")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
