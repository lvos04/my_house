"""KLEIN-64 (A-25): de OpenAPI noemt 503 ``secret_key_invalid`` op elke route die dat antwoord kan geven
(KLEIN-41, 43, 45; docs/api.md). Andere routes krijgen het niet."""

from __future__ import annotations

import pytest

from shc_api.main import create_app

ROUTES = [
    ("post", "/api/v1/system/restore/verify"),
    ("put", "/api/v1/system/backup-settings"),
    ("get", "/api/v1/system/notify"),
    ("put", "/api/v1/system/notify"),
    ("post", "/api/v1/system/notify/test"),
    ("post", "/api/v1/devices/{device_id}/test"),
    ("post", "/api/v1/integrations/{provider}/pair"),
    ("post", "/api/v1/pairing/codes"),
    ("post", "/api/v1/pairing/redeem"),
]


@pytest.fixture(scope="module")
def spec() -> dict:
    return create_app().openapi()


@pytest.mark.parametrize(("method", "path"), ROUTES)
def test_route_noemt_503_secret_key_invalid(spec: dict, method: str, path: str) -> None:
    responses = spec["paths"][path][method]["responses"]
    assert "503" in responses, f"{method.upper()} {path} noemt 503 niet"
    assert "secret_key_invalid" in responses["503"]["description"]


def test_alleen_deze_routes_noemen_503(spec: dict) -> None:
    met_503 = {
        (method, path)
        for path, ops in spec["paths"].items()
        for method, op in ops.items()
        if "503" in op.get("responses", {})
    }
    assert met_503 == set(ROUTES)
