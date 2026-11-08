import json
from pathlib import Path

import httpx
import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def load(rel: str):
    p = FIXTURES / rel
    return json.loads(p.read_text()) if p.suffix == ".json" else p.read_text()


@pytest.fixture
def plain_factory():
    """Client factory without TLS pinning; respx intercepts the requests."""
    return lambda base, fp: httpx.AsyncClient(base_url=base)


async def no_sleep(_s):
    return None
