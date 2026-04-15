"""Trusted proxy X-Forwarded-For parser.

Walks the chain right-to-left, accepts the first IP not in the trusted
CIDR list. If all IPs are trusted (or header is absent/empty), falls back
to the socket peer address.
"""

from __future__ import annotations

from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network, ip_address

from fastapi import Request


def get_client_ip(
    request: Request,
    trusted_proxies: list[IPv4Network | IPv6Network] | None = None,
) -> str:
    """Extract the real client IP, respecting trusted proxy CIDRs."""
    if not trusted_proxies:
        return _peer_ip(request)

    forwarded = request.headers.get("x-forwarded-for", "")
    if not forwarded:
        return _peer_ip(request)

    chain = [addr.strip() for addr in forwarded.split(",") if addr.strip()]
    # Walk right-to-left: rightmost is closest to us
    for raw_ip in reversed(chain):
        try:
            addr = ip_address(raw_ip)
        except ValueError:
            continue
        if not _is_trusted(addr, trusted_proxies):
            return str(addr)

    return _peer_ip(request)


def _peer_ip(request: Request) -> str:
    if request.client:
        return request.client.host
    return "0.0.0.0"


def _is_trusted(
    addr: IPv4Address | IPv6Address,
    trusted: list[IPv4Network | IPv6Network],
) -> bool:
    for network in trusted:
        if addr in network:
            return True
    return False
