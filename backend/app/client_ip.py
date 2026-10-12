"""Client IP for guest quotas and rate limits, safe behind trusted reverse proxies.

X-Forwarded-For is client-controlled: anyone can send `X-Forwarded-For: 1.2.3.4`.
Only the entries appended by proxies we operate are trustworthy, so:

* TRUSTED_PROXIES empty (default): the header is ignored; the TCP peer is the client.
* Otherwise the header is only read when the TCP peer is a trusted proxy, and it is
  walked right to left, skipping trusted proxies; the first other address is the
  client. Anything a client prepended sits further left and is never reached.
* A malformed entry stops the walk; the last trusted hop's view (the peer, or the
  last address already accepted) is used instead of guessing.

Uvicorn should not rewrite the peer itself (run it with --no-proxy-headers), so this
is the single place forwarding headers are interpreted.
"""
import ipaddress
from functools import lru_cache

from starlette.requests import Request

from app.config import settings

_Network = ipaddress.IPv4Network | ipaddress.IPv6Network


@lru_cache(maxsize=8)
def _parse_trusted(raw: str) -> tuple[_Network, ...]:
    networks = []
    for item in raw.split(","):
        item = item.strip()
        if item:
            networks.append(ipaddress.ip_network(item, strict=False))  # fail loudly on a typo
    return tuple(networks)


def trusted_networks() -> tuple[_Network, ...]:
    return _parse_trusted(settings.trusted_proxies)


def _parse_ip(value: str):
    try:
        return ipaddress.ip_address(value.strip())
    except ValueError:
        return None


def _is_trusted(addr, networks) -> bool:
    return any(addr in net for net in networks)


def resolve_client_ip(peer: str | None, forwarded_for: list[str], networks) -> str | None:
    peer_addr = _parse_ip(peer or "")
    if peer_addr is None or not networks or not _is_trusted(peer_addr, networks):
        return peer
    client = peer_addr
    for header in reversed(forwarded_for):
        for entry in reversed(header.split(",")):
            addr = _parse_ip(entry)
            if addr is None:
                return str(client)
            client = addr
            if not _is_trusted(addr, networks):
                return str(addr)
    # Every hop was a trusted proxy: the left-most one is the best we know.
    return str(client)


def client_ip(request: Request) -> str | None:
    peer = request.client.host if request.client else None
    return resolve_client_ip(peer, request.headers.getlist("x-forwarded-for"), trusted_networks())


def rate_limit_key(request: Request) -> str:
    """slowapi key function (same identity as the quota's)."""
    return client_ip(request) or "unknown"
