"""Helpers for bounded, read-only EG4 dongle discovery."""

from __future__ import annotations

from ipaddress import IPv4Address, IPv4Network
import re
from typing import Any, Iterable, Mapping

_IPV4_PATTERN = re.compile(r"(?<![0-9.])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![0-9.])")
_DEFAULT_MAX_HOSTS = 254
_LOCAL_NETWORKS = (
    IPv4Network("10.0.0.0/8"),
    IPv4Network("172.16.0.0/12"),
    IPv4Network("192.168.0.0/16"),
)


def extract_ipv4_addresses(value: Any) -> tuple[str, ...]:
    """Extract syntactically valid IPv4 strings from a portal payload.

    Portal schemas have changed over time, so this intentionally walks nested
    dictionaries/lists instead of depending on one field name. Values are
    returned in deterministic order and are not treated as local until the
    caller applies :func:`local_ipv4_addresses`.
    """
    found: set[IPv4Address] = set()

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            for nested in item.values():
                visit(nested)
            return
        if isinstance(item, (list, tuple, set)):
            for nested in item:
                visit(nested)
            return
        if not isinstance(item, str):
            return
        for match in _IPV4_PATTERN.findall(item):
            try:
                found.add(IPv4Address(match))
            except ValueError:
                continue

    visit(value)
    return tuple(str(address) for address in sorted(found))


def local_ipv4_addresses(addresses: Iterable[str]) -> tuple[str, ...]:
    """Keep only addresses safe to probe from the local host.

    Never turn a cloud-reported public address into a TCP scan target. Private,
    link-local, and loopback addresses are the only valid local candidates.
    """
    local: set[IPv4Address] = set()
    for value in addresses:
        try:
            address = IPv4Address(value)
        except (TypeError, ValueError):
            continue
        if (
            address in _LOCAL_NETWORKS[0]
            or address in _LOCAL_NETWORKS[1]
            or address in _LOCAL_NETWORKS[2]
            or address.is_link_local
            or address.is_loopback
        ):
            local.add(address)
    return tuple(str(address) for address in sorted(local))


def _bounded_network(
    address: IPv4Address,
    prefix: int,
    max_hosts: int,
) -> IPv4Network:
    """Return an interface network, narrowing oversized segments to /24."""
    network = IPv4Network(f"{address}/{prefix}", strict=False)
    if network.num_addresses - 2 <= max_hosts:
        return network
    narrowed_prefix = max(prefix, 24)
    return IPv4Network(f"{address}/{narrowed_prefix}", strict=False)


def local_networks_from_adapters(
    adapters: Iterable[Mapping[str, Any]],
    *,
    max_hosts: int = _DEFAULT_MAX_HOSTS,
) -> tuple[IPv4Network, ...]:
    """Build bounded IPv4 networks from Home Assistant network adapters."""
    networks: set[IPv4Network] = set()
    for adapter in adapters:
        if not isinstance(adapter, Mapping) or not adapter.get("enabled", False):
            continue
        for info in adapter.get("ipv4") or ():
            if not isinstance(info, dict):
                continue
            try:
                address = IPv4Address(str(info["address"]))
                prefix = int(info["network_prefix"])
            except (KeyError, TypeError, ValueError):
                continue
            if not (0 <= prefix <= 32):
                continue
            if not local_ipv4_addresses((str(address),)):
                continue
            networks.add(_bounded_network(address, prefix, max_hosts))
    return tuple(sorted(networks, key=lambda network: (int(network.network_address), network.prefixlen)))


def hosts_for_networks(
    networks: Iterable[IPv4Network],
    *,
    max_hosts: int = _DEFAULT_MAX_HOSTS,
) -> tuple[str, ...]:
    """Expand bounded networks into deterministic TCP probe targets."""
    hosts: set[IPv4Address] = set()
    for network in networks:
        network_hosts = 0
        for address in network.hosts():
            hosts.add(address)
            network_hosts += 1
            if network_hosts >= max_hosts:
                break
    return tuple(str(address) for address in sorted(hosts))


__all__ = [
    "extract_ipv4_addresses",
    "hosts_for_networks",
    "local_ipv4_addresses",
    "local_networks_from_adapters",
]
