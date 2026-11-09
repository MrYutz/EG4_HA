"""Tests for bounded automatic local EG4 discovery helpers."""

from __future__ import annotations

from ipaddress import IPv4Network

from custom_components.eg4_monitor.local_discovery import (
    extract_ipv4_addresses,
    hosts_for_networks,
    local_ipv4_addresses,
    local_networks_from_adapters,
)


def test_extract_ipv4_addresses_walks_nested_portal_payload() -> None:
    payload = {
        "plant": {"reportedIp": "192.168.3.14"},
        "rows": [
            {"ipAddress": "192.168.3.162:8000"},
            {"nested": ["public endpoint 203.0.113.10", "bad 999.1.1.1"]},
        ],
    }

    assert extract_ipv4_addresses(payload) == (
        "192.168.3.14",
        "192.168.3.162",
        "203.0.113.10",
    )


def test_local_ipv4_addresses_reject_public_targets() -> None:
    assert local_ipv4_addresses(
        ("192.168.3.14", "10.1.2.3", "169.254.10.2", "203.0.113.10")
    ) == ("10.1.2.3", "169.254.10.2", "192.168.3.14")


def test_networks_are_bounded_and_deduplicated() -> None:
    adapters = [
        {
            "enabled": True,
            "ipv4": [{"address": "192.168.3.2", "network_prefix": 16}],
        },
        {
            "enabled": True,
            "ipv4": [{"address": "192.168.3.3", "network_prefix": 24}],
        },
        {
            "enabled": False,
            "ipv4": [{"address": "10.0.0.2", "network_prefix": 24}],
        },
    ]

    networks = local_networks_from_adapters(adapters)

    assert networks == (IPv4Network("192.168.3.0/24"),)
    assert len(hosts_for_networks(networks)) == 254
