"""Afzonderlijke loopbackhosts voor de HTTP-dubbels van de adapterproeven."""

from ipaddress import IPv4Address

P1_HOST, STEKKER_HOST, KWH_HOST, HUE_HOST = (
    str(IPv4Address(bytes((127, 0, 0, nummer)))) for nummer in range(2, 6)
)
