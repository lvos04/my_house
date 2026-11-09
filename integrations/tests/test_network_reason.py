"""Plain Dutch reasons for network errors in messages shown to the user (technical text in diagnostics)."""

import ssl

import httpx
import pytest
from fake_http import closed_tcp_port

from shc_integrations.base import DeviceOffline, network_reason
from shc_integrations.homewizard import HomeWizardAdapter


@pytest.mark.parametrize(
    ("exc", "text"),
    [
        (httpx.ConnectError("All connection attempts failed"), "geen verbinding"),
        (httpx.ConnectTimeout("timed out"), "geen antwoord binnen de tijd"),
        (TimeoutError(), "geen antwoord binnen de tijd"),
        (httpx.RemoteProtocolError("Server disconnected"), "onverwacht verbroken"),
        (ssl.SSLError("bad handshake"), "beveiligde verbinding mislukt"),
        (OSError("weird"), "netwerkfout"),
    ],
)
def test_network_reason(exc, text):
    assert text in network_reason(exc)


async def test_homewizard_offline_message_is_dutch_with_details_in_diagnostics():
    port = closed_tcp_port()
    a = HomeWizardAdapter(
        {"host": f"127.0.0.1:{port}", "serial": "x", "product_type": "HWE-P1", "min_poll_interval_s": 0}
    )
    with pytest.raises(DeviceOffline) as e:
        await a.poll()
    assert (
        str(e.value)
        == f"HomeWizard 127.0.0.1:{port} niet bereikbaar: geen verbinding (staat het apparaat aan en klopt het adres?)"
    )
    assert "ConnectError" in e.value.diagnostics["error"]
    await a.close()
