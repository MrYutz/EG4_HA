"""Unit tests for EG4 role mapping and aggregation helpers."""

from datetime import UTC, datetime

import pytest

from custom_components.eg4_monitor.local_dongle import (
    EG4LocalDongleIdentity,
    EG4LocalTelemetry,
)
from custom_components.eg4_monitor.__init__ import (
    _add_discovered_local_dongles,
    _apply_battery_data_quality_guards,
    _apply_local_telemetry_snapshot,
    _apply_battery_info_snapshot,
    _apply_event_snapshot,
    _apply_grid_quality_snapshot,
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


def test_build_aggregate_fills_energy_gaps_from_battery_source() -> None:
    role_map = {"MASTER": "flexboss_master", "GRID": "gridboss"}
    device_data = {
        "MASTER": {
            "Battery": {"SOC": 80},
            "Energy": {
                "Today": {
                    "Battery Charge": 2.5,
                    "Battery Discharge": 1.25,
                },
                "Total": {"Battery Charge": 125.0},
            },
        },
        "GRID": {
            "Grid": {"VoltageR": 240.0},
            "Energy": {
                "Today": {"Solar Generation": 4.2},
                "Total": {"Solar Generation": 400.0},
            }
        },
    }

    aggregate = _build_aggregate(device_data, role_map)

    assert aggregate["Energy"]["Today"] == {
        "Solar Generation": 4.2,
        "Usage": None,
        "Grid Import": None,
        "Grid Export": None,
        "Battery Charge": 2.5,
        "Battery Discharge": 1.25,
    }
    assert aggregate["Energy"]["Total"]["Battery Charge"] == 125.0


def test_apply_battery_info_snapshot_keeps_verified_fields_and_pack_rows() -> None:
    aggregate = {"Battery": {"Power": -800.0}}
    battery_info = {
        "success": True,
        "batStatus": "Discharging",
        "batPower": -900,
        "vBat": 531,
        "currentText": "-17.0",
        "totalVoltageText": "53.1",
        "totalNumber": 2,
        "bmsCycleCnt": 42,
        "globalMaxCellTemp": "31.5",
        "globalMinCellTemp": "29.0",
        "batteryList": [
            {"batterySn": "PACK-A", "batVolt": 530},
            {"batterySn": "PACK-B", "batVolt": 532},
        ],
    }

    _apply_battery_info_snapshot(aggregate, battery_info)

    assert aggregate["Battery"]["Status"] == "Discharging"
    assert aggregate["Battery"]["Current"] == -17.0
    assert aggregate["Battery"]["TotalVoltage"] == 53.1
    assert aggregate["Battery"]["CycleCount"] == 42.0
    assert aggregate["Battery"]["PortalPower"] == -900.0
    assert aggregate["Battery"]["PackVoltages"][1]["voltage"] == 53.2
    assert aggregate["Battery"]["Power"] == -800.0


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


def test_apply_battery_info_snapshot_keeps_parallel_rows_without_summary() -> None:
    aggregate = {"Battery": {}}
    parallel_info = {
        "success": True,
        "batteryArray": [
            {"batterySn": "PACK-A", "totalVoltage": 5410},
        ],
    }

    _apply_battery_info_snapshot(aggregate, None, parallel_info)

    assert aggregate["Battery"]["ParallelDeviceCount"] == 1
    assert aggregate["Battery"]["ParallelDevices"] == [
        {"batterySn": "PACK-A", "totalVoltage": 5410}
    ]


def test_grid_quality_does_not_record_stale_runtime_sample() -> None:
    aggregate = {"Grid": {"Voltage": 190.0}}
    samples = []

    _apply_grid_quality_snapshot(
        aggregate,
        samples,
        datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
        record_sample=False,
    )

    assert samples == []
    assert aggregate["GridQuality"]["SampleRecorded"] is False
    assert aggregate["GridQuality"]["State"] == "unavailable"


def test_discovered_local_dongle_is_assigned_by_inverter_serial() -> None:
    local_dongles = {}
    discovered: list[dict[str, object]] = []
    _add_discovered_local_dongles(
        local_dongles,
        (
            EG4LocalDongleIdentity(
                host="192.168.3.14",
                port=8000,
                dongle_serial="DJ12345678",
                inverter_serial="INV_MASTER",
            ),
            EG4LocalDongleIdentity(
                host="192.168.3.241",
                port=8000,
                dongle_serial="GRID123456",
                inverter_serial="INV_GRID",
            ),
        ),
        {
            "INV_MASTER": "flexboss_master",
            "INV_GRID": "gridboss",
        },
        port=8000,
        timeout=5.0,
        source="portal_reported_ip",
        discovered=discovered,
    )

    assert set(local_dongles) == {"flexboss_master"}
    assert local_dongles["flexboss_master"].host == "192.168.3.14"
    assert discovered[0]["Role"] == "flexboss_master"


def test_local_bms_soc_authorizes_canonical_lead_acid_soc() -> None:
    aggregate = {
        "Battery": {"Type": "LEAD_ACID", "SOC": None, "PortalSOC": 85.0},
    }
    telemetry = EG4LocalTelemetry(
        host="192.168.3.14",
        port=8000,
        dongle_serial="DJ12345678",
        inverter_serial="INV1234567",
        battery_voltage=52.3,
        battery_soc=45.0,
        battery_soh=100.0,
        charge_power=6725.0,
        discharge_power=0.0,
        bms_parallel_count=5,
        bms_capacity_ah=1400.0,
        bms_current=25.0,
        bms_max_cell_voltage=3.297,
        bms_min_cell_voltage=3.278,
        bms_max_cell_temperature=31.0,
        bms_min_cell_temperature=29.0,
        bms_cycle_count=18.0,
        bms_available=True,
    )

    _apply_local_telemetry_snapshot(
        aggregate, {"flexboss_master": telemetry}, configured_roles={"flexboss_master"}
    )
    _apply_battery_data_quality_guards(aggregate)

    battery = aggregate["Battery"]
    assert battery["SOC"] == 45.0
    assert battery["SOCSource"] == "local_bms"
    assert battery["SOCQuality"] == "bms_reported"
    assert battery["LocalSOC"] == 45.0
    assert battery["LocalSOCSource"] == "local_inverter_unverified"
    assert battery["LocalSOCQuality"] == "diagnostic_only"
    assert battery["LocalBMSSOC"] == 45.0
    assert battery["LocalBMSSource"] == "local_bms"
    assert battery["LocalBMSQuality"] == "bms_reported"
    assert battery["LocalDongleSerial"] == "DJ12345678"
    assert battery["LocalVoltage"] == 52.3
    assert battery["LocalPower"] == 6725.0
    assert battery["LocalBMSCurrent"] == 25.0
    assert battery["LocalBMSAvailable"] is True
    assert aggregate["LocalTelemetry"]["ByRole"]["flexboss_master"]["DongleSerial"] == (
        "DJ12345678"
    )
    assert aggregate["LocalTelemetry"]["BMSRole"] == "flexboss_master"


def test_local_inverter_soc_stays_unverified_without_bms() -> None:
    aggregate = {
        "Battery": {"Type": "LEAD_ACID", "SOC": 85.0},
    }
    telemetry = EG4LocalTelemetry(
        host="192.168.3.162",
        port=8000,
        dongle_serial="BJ44300373",
        inverter_serial="INV1234567",
        battery_voltage=52.3,
        battery_soc=85.0,
        battery_soh=100.0,
        charge_power=0.0,
        discharge_power=2500.0,
        bms_parallel_count=0,
        bms_capacity_ah=0.0,
        bms_current=0.0,
        bms_max_cell_voltage=None,
        bms_min_cell_voltage=None,
        bms_max_cell_temperature=0.0,
        bms_min_cell_temperature=0.0,
        bms_cycle_count=0.0,
        bms_available=False,
    )

    _apply_local_telemetry_snapshot(
        aggregate, {"flexboss_slave": telemetry}, configured_roles={"flexboss_slave"}
    )
    _apply_battery_data_quality_guards(aggregate)

    battery = aggregate["Battery"]
    assert battery["SOC"] is None
    assert battery["SOCSource"] == "cloud_unverified"
    assert battery["LocalSOC"] == 85.0
    assert battery["LocalSOCSource"] == "local_inverter_unverified"
    assert battery["LocalBMSSOC"] is None
    assert battery["LocalBMSAvailable"] is False


@pytest.mark.parametrize(
    ("charge_by_role", "expected_charge", "missing_roles"),
    [
        ({}, None, ["flexboss_master", "flexboss_slave"]),
        ({"flexboss_slave": 0.0}, None, ["flexboss_master"]),
        ({"flexboss_master": None, "flexboss_slave": 0.0}, None, []),
        ({"flexboss_master": float("nan"), "flexboss_slave": 0.0}, None, []),
        ({"flexboss_master": 1500.0, "flexboss_slave": 0.0}, 1500.0, []),
        ({"flexboss_master": 0.0, "flexboss_slave": 0.0}, 0.0, []),
    ],
)
def test_local_charge_total_requires_every_configured_role(
    charge_by_role: dict[str, float | None],
    expected_charge: float | None,
    missing_roles: list[str],
) -> None:
    aggregate = {"Battery": {}}
    snapshots = {
        role: EG4LocalTelemetry(
            host="127.0.0.1", port=8000,
            dongle_serial="DJ12345678", inverter_serial=role,
            battery_voltage=52.0, battery_soc=None, battery_soh=None,
            charge_power=power, discharge_power=0.0,
            bms_parallel_count=None, bms_capacity_ah=None, bms_current=None,
            bms_max_cell_voltage=None, bms_min_cell_voltage=None,
            bms_max_cell_temperature=None, bms_min_cell_temperature=None,
            bms_cycle_count=None, bms_available=False,
        )
        for role, power in charge_by_role.items()
    }

    _apply_local_telemetry_snapshot(
        aggregate, snapshots,
        configured_roles={"flexboss_master", "flexboss_slave"},
    )

    assert aggregate["Battery"]["LocalChargePower"] == expected_charge
    assert aggregate["LocalTelemetry"]["MissingRoles"] == missing_roles
    assert set(aggregate["LocalTelemetry"]["ByRole"]) == set(charge_by_role)
    if "flexboss_slave" in charge_by_role:
        assert aggregate["LocalTelemetry"]["ByRole"]["flexboss_slave"]["ChargePower"] == 0.0


def test_lead_acid_portal_soc_is_not_authoritative() -> None:
    aggregate = {
        "Battery": {
            "Type": "LEAD_ACID",
            "SOC": 94.0,
            "RemainingKWh": 131.6,
            "Power": -9000.0,
            "Voltage": 51.5,
            "Current": 0.8,
        }
    }

    _apply_battery_data_quality_guards(aggregate)

    battery = aggregate["Battery"]
    assert battery["SOC"] is None
    assert battery["RemainingKWh"] is None
    assert battery["PortalSOC"] == 94.0
    assert battery["SOCSource"] == "cloud_unverified"
    assert battery["SOCQuality"] == "unavailable"
    assert battery["PortalSOCSource"] == "cloud_unverified"
    assert battery["PortalSOCQuality"] == "diagnostic_only"
    assert battery["Current"] == round(-9000.0 / 51.5, 2)
    assert battery["PortalCurrent"] == 0.8
    assert battery["CurrentSource"] == "power_voltage"


def test_apply_parallel_day_snapshot_uses_negative_discharge_power() -> None:
    aggregate = {
        "source_serials": {},
        "Grid": {},
        "Load": {},
        "Solar": {},
        "Battery": {},
    }
    chart = {
        "data": [
            {
                "batteryDischarging": 2500,
            }
        ]
    }

    _apply_parallel_day_snapshot(aggregate, chart)

    assert aggregate["Battery"]["Power"] == -2500.0
    assert aggregate["Battery"]["ChargePower"] == 0.0
    assert aggregate["Battery"]["DischargePower"] == 2500.0
    assert aggregate["Battery"]["Direction"] == "Discharging"


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
