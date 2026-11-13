from fastapi import HTTPException


def api_error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def not_found(what: str = "Item") -> HTTPException:
    return api_error(404, "not_found", f"{what} niet gevonden.")


# KLEIN-41/43/44: vaste tekst bij een ontbrekende of te korte SHC_SECRET_KEY(_FILE); nooit (een deel van) de sleutel.
SLEUTEL_ONBRUIKBAAR_TEKST = "De sleutel van deze server ontbreekt of is ongeldig; deze actie is niet mogelijk."


# KLEIN-64: voor ``responses=`` op elke route die secret_key_invalid kan geven, zodat de OpenAPI het noemt.
SECRET_KEY_INVALID_RESPONSE: dict[int | str, dict] = {
    503: {"description": "`secret_key_invalid`: de sleutel van deze server ontbreekt of is ongeldig."}
}


def secret_key_invalid() -> HTTPException:
    return api_error(503, "secret_key_invalid", SLEUTEL_ONBRUIKBAAR_TEKST)


def require_secret_box():
    """SecretBox met de huidige hoofdsleutel, of 503 ``secret_key_invalid``. SHC_SECRET_KEY_FILE wordt bij
    elke aanroep opnieuw gelezen, dus de sleutel kan tijdens het draaien ontbreken of te kort zijn
    (KLEIN-43). De fouttekst van RuntimeError/ValueError wordt niet doorgegeven."""
    from .config import get_settings
    from .security import SecretBox

    try:
        return SecretBox(get_settings().resolved_secret_key())
    except (RuntimeError, ValueError):
        raise secret_key_invalid() from None
