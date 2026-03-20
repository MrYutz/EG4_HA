"""Unit tests for EG4 role mapping and aggregation helpers."""

from custom_components.eg4_monitor.__init__ import (
    _apply_event_snapshot,
    _apply_parallel_day_snapshot,
    _apply_midbox_runtime_snapshot,
    _build_aggregate,
    _merge_role_map,
    _pick_role_map,
    _role_map_from_parallel_details,
)


def test_pick_role_map_honors_overrides() -> None:
    inverters = [
        {"serialNum": "SN_MASTER", "modelText": "FlexBOSS21"},
        {"serialNum": "SN_SLAVE", "modelText": "FlexBOSS21"},
        {"serialNum": "SN_GRID", "modelText": "GridBOSS"},
    ]

    role_map = _pick_role_map(
        inverters,
        master_override="SN_MASTER",
        slave_override="SN_SLAVE",
        gridboss_override="SN_GRID",
    )

    assert role_map["SN_MASTER"] == "flexboss_master"
    assert role_map["SN_SLAVE"] == "flexboss_slave"
    assert role_map["SN_GRID"] == "gridboss"


def test_build_aggregate_prefers_role_sources() -> None:
    role_map = {
        "SN_MASTER": "flexboss_master",
        "SN_SLAVE": "flexboss_slave",
        "SN_GRID": "gridboss",
    }

    device_data = {
        "SN_MASTER": {
            "Battery": {"SOC": 77, "Power": -1200, "Voltage": 51.4},
            "Solar": {"TotalPower": 3500},
        },
        "SN_SLAVE": {
            "Solar": {"TotalPower": 3100},
        },
        "SN_GRID": {
            "Grid": {"PowerNet": 900, "VoltageR": 240.2, "Frequency": 60.0},
            "Load": {"Power": 6500},
            "Generator": {"Power": 0, "RunStatus": False},
            "Energy": {
                "Today": {
                    "Solar Generation": 21.2,
                    "Grid Import": 4.1,
                    "Grid Export": 1.7,
                },
                "Total": {
                    "Solar Generation": 12456.0,
                    "Grid Import": 2200.0,
                    "Grid Export": 919.0,
                },
            },
        },
    }

    aggregate = _build_aggregate(device_data, role_map)

    assert aggregate["source_serials"]["battery"] == "SN_MASTER"
    assert aggregate["source_serials"]["site"] == "SN_GRID"
    assert aggregate["Battery"]["SOC"] == 77
    assert aggregate["Grid"]["PowerNet"] == 900
    assert aggregate["Load"]["Power"] == 6500
    assert aggregate["Energy"]["Today"]["Solar Generation"] == 21.2


def test_build_aggregate_solar_fallback_sum() -> None:
    role_map = {
        "A": "flexboss_master",
        "B": "flexboss_slave",
    }
    device_data = {
        "A": {"Solar": {"TotalPower": 1200}},
        "B": {"Solar": {"TotalPower": 1800}},
    }

    aggregate = _build_aggregate(device_data, role_map)

    assert aggregate["Solar"]["TotalPower"] == 3000


def test_build_aggregate_grid_sum_without_gridboss() -> None:
    role_map = {
        "A": "flexboss_master",
        "B": "flexboss_slave",
    }
    device_data = {
        "A": {
            "Grid": {"PowerNet": 2700, "PowerImport": 2700, "PowerExport": 0},
            "Load": {"Power": 2700},
        },
        "B": {
            "Grid": {"PowerNet": 3046, "PowerImport": 3046, "PowerExport": 0},
            "Load": {"Power": 3046},
        },
    }

    aggregate = _build_aggregate(device_data, role_map)

    assert aggregate["Grid"]["PowerNet"] == 5746
    assert aggregate["Grid"]["PowerImport"] == 5746
    assert aggregate["Grid"]["PowerExport"] == 0
    assert aggregate["Load"]["Power"] == 5746


def test_role_map_from_parallel_details() -> None:
    details = {
        "success": True,
        "devices": [
            {"serialNum": "4354850310", "deviceType": 9, "roleText": "Midbox Slave"},
            {"serialNum": "54700E0353", "deviceType": 6, "roleText": "Primary"},
            {"serialNum": "52842P0677", "deviceType": 6, "roleText": "Subordinates"},
        ],
    }

    mapped = _role_map_from_parallel_details(details)

    assert mapped == {
        "4354850310": "gridboss",
        "54700E0353": "flexboss_master",
        "52842P0677": "flexboss_slave",
    }


def test_merge_role_map_respects_locked_serials() -> None:
    base = {
        "4354850310": "gridboss",
        "54700E0353": "flexboss_slave",
        "52842P0677": "flexboss_master",
    }
    discovered = {
        "54700E0353": "flexboss_master",
        "52842P0677": "flexboss_slave",
    }

    merged = _merge_role_map(base, discovered, {"52842P0677"})

    assert merged["54700E0353"] == "flexboss_master"
    assert merged["52842P0677"] == "flexboss_master"


def test_apply_parallel_day_snapshot_updates_site_values() -> None:
    aggregate = {
        "source_serials": {"battery": "A", "site": "A"},
        "Grid": {"PowerImport": 10.0, "PowerExport": 0.0, "PowerNet": 10.0},
        "Load": {"Power": 10.0},
        "Solar": {"TotalPower": 0.0},
        "Battery": {"Power": 0.0, "SOC": 0.0},
    }
    chart = {
        "success": True,
        "data": [
            {
                "time": "2026-03-20 08:55:00",
                "gridPower": -6914,
                "consumption": 6914,
                "batteryDischarging": 0,
                "solarPv": 0,
                "soc": 100,
            }
        ],
    }

    _apply_parallel_day_snapshot(aggregate, chart)

    assert aggregate["Grid"]["PowerImport"] == 6914.0
    assert aggregate["Grid"]["PowerExport"] == 0.0
    assert aggregate["Grid"]["PowerNet"] == 6914.0
    assert aggregate["Load"]["Power"] == 6914.0
    assert aggregate["Battery"]["SOC"] == 100.0
    assert aggregate["chart_latest_time"] == "2026-03-20 08:55:00"


def test_apply_midbox_runtime_snapshot_updates_site_values() -> None:
    aggregate = {
        "source_serials": {"battery": "A", "site": "A"},
        "Grid": {
            "PowerImport": 1.0,
            "PowerExport": 0.0,
            "PowerNet": 1.0,
            "Voltage": 0.0,
            "Frequency": 0.0,
        },
        "EPS": {"Power": 0.0, "Voltage": 0.0, "Frequency": 0.0},
        "Load": {"Power": 1.0},
        "Solar": {"TotalPower": 0.0},
        "Battery": {"Power": 0.0, "SOC": 0.0},
        "Generator": {"Power": 0.0, "RunStatus": False},
    }

    payload = {
        "success": True,
        "midboxData": {
            "deviceTime": "2026-03-20 08:59:01",
            "serverTime": "2026-03-20 12:59:01",
            "gridRmsVolt": 2459,
            "gridFreq": 6000,
            "upsRmsVolt": 2447,
            "eToGridTodayL1": 0,
            "eToGridTodayL2": 0,
            "eToGridTotalL1": 1,
            "eToGridTotalL2": 0,
            "eToUserTodayL1": 706,
            "eToUserTodayL2": 0,
            "eToUserTotalL1": 576698,
            "eToUserTotalL2": 0,
            "eUpsTodayL1": 0,
            "eUpsTodayL2": 0,
            "eUpsTotalL1": 0,
            "eUpsTotalL2": 0,
            "eLoadTodayL1": 0,
            "eLoadTodayL2": 0,
            "eLoadTotalL1": 0,
            "eLoadTotalL2": 0,
        },
        "deviceData": {
            "gridPower": -5896,
            "pLoad": 5896,
            "soc": 100,
            "batCapacity": 1400,
            "genDryContact": "OFF",
            "_12KUsingGenerator": False,
        },
    }

    _apply_midbox_runtime_snapshot(aggregate, payload)

    assert aggregate["Grid"]["PowerImport"] == 5896.0
    assert aggregate["Grid"]["PowerExport"] == 0.0
    assert aggregate["Grid"]["PowerNet"] == 5896.0
    assert aggregate["Grid"]["Voltage"] == 245.9
    assert aggregate["Grid"]["Frequency"] == 60.0
    assert aggregate["EPS"]["Voltage"] == 244.7
    assert aggregate["Load"]["Power"] == 5896.0
    assert aggregate["Battery"]["SOC"] == 100.0
    assert aggregate["Battery"]["TotalCapacityKWh"] == 140.0
    assert aggregate["Battery"]["RemainingKWh"] == 140.0
    assert aggregate["Battery"]["Direction"] == "Idle"
    assert aggregate["Generator"]["RunStatus"] is False
    assert aggregate["Generator"]["SupplyingPower"] is False
    assert aggregate["Generator"]["StatusSource"] == "none"
    assert aggregate["Energy"]["Today"]["Grid Export"] == 0.0
    assert aggregate["Energy"]["Total"]["Grid Export"] == 0.1
    assert aggregate["Energy"]["Today"]["Usage"] == 70.6
    assert aggregate["Energy"]["Total"]["Usage"] == 57669.8
    assert aggregate["Energy"]["Today"]["UPS Usage"] == 0.0
    assert aggregate["Energy"]["Total"]["UPS Usage"] == 0.0
    assert aggregate["Energy"]["Today"]["Grid Import"] == 70.6
    assert aggregate["Energy"]["Total"]["Grid Import"] == 57669.8
    assert aggregate["midbox_device_time"] == "2026-03-20 08:59:01"


def test_apply_midbox_runtime_snapshot_detects_generator_running() -> None:
    aggregate = {
        "source_serials": {"battery": "A", "site": "A"},
        "Grid": {
            "PowerImport": 1.0,
            "PowerExport": 0.0,
            "PowerNet": 1.0,
            "Voltage": 0.0,
            "Frequency": 0.0,
        },
        "Load": {"Power": 1.0},
        "Solar": {"TotalPower": 0.0},
        "Battery": {"Power": 0.0, "SOC": 0.0},
        "Generator": {"Power": 0.0, "RunStatus": False},
    }

    payload = {
        "success": True,
        "midboxData": {
            "genRmsVolt": 2390,
            "genFreq": 5998,
            "genL1ActivePower": 1300,
            "genL2ActivePower": 900,
        },
        "deviceData": {
            "genDryContact": "ON",
            "_12KUsingGenerator": True,
        },
    }

    _apply_midbox_runtime_snapshot(aggregate, payload)

    assert aggregate["Generator"]["Power"] == 2200.0
    assert aggregate["Generator"]["Voltage"] == 239.0
    assert aggregate["Generator"]["Frequency"] == 59.98
    assert aggregate["Generator"]["RunStatus"] is True
    assert aggregate["Generator"]["SupplyingPower"] is True
    assert aggregate["Generator"]["StatusSource"] == "using_generator_flag"


def test_build_aggregate_battery_direction_from_power() -> None:
    role_map = {"A": "flexboss_master"}
    device_data = {"A": {"Battery": {"Power": -1234}}}

    aggregate = _build_aggregate(device_data, role_map)

    assert aggregate["Battery"]["Direction"] == "Discharging"
    assert aggregate["Battery"]["ChargePower"] == 0.0
    assert aggregate["Battery"]["DischargePower"] == 1234.0


def test_apply_event_snapshot_open_events() -> None:
    aggregate = {}
    payload = {
        "success": True,
        "rows": [
            {
                "recordId": 1,
                "serialNum": "52842P0677",
                "event": "E030",
                "eventText": "Grid Boss Breaker Open",
                "eventType": "FAULT",
                "startTime": "2026-03-20 09:00:00",
                "status": "OPEN",
            },
            {
                "recordId": 2,
                "serialNum": "54700E0353",
                "event": "W030",
                "eventText": "Grid Boss CAN Loss",
                "eventType": "WARNING",
                "startTime": "2026-03-20 08:00:00",
                "status": "CLOSE",
            },
        ],
    }

    _apply_event_snapshot(aggregate, payload, {"52842P0677", "54700E0353"})

    assert aggregate["Events"]["HasOpen"] is True
    assert aggregate["Events"]["OpenCount"] == 1
    assert aggregate["Events"]["OpenSummary"] == "Grid Boss Breaker Open (E030)"
    assert len(aggregate["Events"]["OpenTop"]) == 1


def test_apply_event_snapshot_no_open_events() -> None:
    aggregate = {}
    payload = {
        "success": True,
        "rows": [
            {
                "recordId": 3,
                "eventText": "Grid Boss CAN Loss",
                "event": "W030",
                "status": "CLOSE",
            }
        ],
    }

    _apply_event_snapshot(aggregate, payload, {"52842P0677", "54700E0353"})

    assert aggregate["Events"]["HasOpen"] is False
    assert aggregate["Events"]["OpenCount"] == 0
    assert aggregate["Events"]["OpenSummary"] == "none"


def test_apply_event_snapshot_filters_other_serials() -> None:
    aggregate = {}
    payload = {
        "success": True,
        "rows": [
            {
                "recordId": 99,
                "serialNum": "UNRELATED",
                "event": "E999",
                "eventText": "Other site fault",
                "status": "OPEN",
            }
        ],
    }

    _apply_event_snapshot(
        aggregate, payload, {"52842P0677", "54700E0353", "4354850310"}
    )

    assert aggregate["Events"]["HasOpen"] is False
    assert aggregate["Events"]["OpenCount"] == 0
