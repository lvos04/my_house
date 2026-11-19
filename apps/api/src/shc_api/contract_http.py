"""HTTP-foutvorm van platformcontract 1.3 voor nieuwe routes."""

import uuid

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel
from starlette.exceptions import HTTPException

from .db import sessionmaker
from .deps import current_principal, require_admin


class ContractProblem(BaseModel):
    code: str
    message: str
    details: dict
    trace_id: uuid.UUID


def problem(code: str, message: str, **details) -> dict:
    return ContractProblem(code=code, message=message, details=details, trace_id=uuid.uuid4()).model_dump(mode="json")


def contract_error(
    status: int, code: str, message: str, *, headers: dict[str, str] | None = None, **details
) -> HTTPException:
    return HTTPException(status, detail={"code": code, "message": message, "details": details}, headers=headers)


def _http_error(exc: HTTPException) -> JSONResponse:
    code, message = exc.detail["code"], exc.detail["message"]
    details = exc.detail.get("details", {})
    if exc.status_code == 401:
        details = {"reason": {"token_expired": "token_expired", "token_revoked": "revoked"}.get(code, "invalid_token")}
        code = "unauthorized"
    elif exc.status_code == 403:
        details = {"reason": code if code != "forbidden" else "admin_required"}
        code = "forbidden"
    return JSONResponse(problem(code, message, **details), status_code=exc.status_code, headers=exc.headers)


async def _validation_error(request: Request, errors: list[dict], *, admin: bool = True) -> JSONResponse:
    if not getattr(request.state, "principal", None):
        # Een JSON-leesfout kan vóór FastAPI's dependencycontrole optreden.
        try:
            async with sessionmaker()() as db:
                principal = await current_principal(request, db)
                if admin:
                    await require_admin(principal)
        except HTTPException as auth_error:
            return _http_error(auth_error)
    return JSONResponse(problem("validation_error", "Controleer de invoer.", fouten=errors), status_code=422)


class AdminContractRoute(APIRoute):
    """Pas het foutformaat toe terwijl de bestaande sessie-, telefoon- en CSRF-controles gelden."""

    requires_admin = True

    def get_route_handler(self):
        route_handler = super().get_route_handler()

        async def handler(request: Request):
            try:
                return await route_handler(request)
            except HTTPException as exc:
                if not isinstance(exc.detail, dict):
                    return await _validation_error(
                        request, [{"loc": ["body"], "type": "body_invalid"}], admin=self.requires_admin,
                    )
                return _http_error(exc)
            except RequestValidationError as exc:
                errors = [{"loc": list(e["loc"]), "type": e["type"]} for e in exc.errors()]
                return await _validation_error(request, errors, admin=self.requires_admin)

        return handler


class ConnectorContractRoute(AdminContractRoute):
    """Gebruikt gewone aanmelding; mutaties vereisen aanvullend een ondertekend Hub-verzoek."""

    requires_admin = False
