"""Gegenereerde adressen voor ASGI-clients, adapterdubbels en adresvalidatie."""

from ipaddress import IPv4Address


def lan(nummer: int) -> str:
    return str(IPv4Address(bytes((10, 231, 19, nummer))))


def bezoeker(nummer: int) -> str:
    # TEST-NET-3 voor een anonieme HTTP-client.
    return str(IPv4Address(bytes((203, 0, 113, nummer))))


def stack(subnet: int, host: int, prefix: tuple[int, int] = (172, 30)) -> str:
    # De grensproeven gebruiken dezelfde subnetmaten als de ontwikkelstack.
    return str(IPv4Address(bytes((*prefix, subnet, host))))


LINK_LOKAAL = str(IPv4Address(bytes((169, 254, 3, 7))))
PUBLIEK = str(IPv4Address(bytes((11, 17, 3, 1))))
