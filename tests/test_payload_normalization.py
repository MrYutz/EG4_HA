"""Tests for verified EG4 portal payload normalization."""

from datetime import UTC, datetime, timedelta

import pytest

from custom_components.eg4_monitor.backup_metrics import (
    build_backup_metrics,
    estimate_battery_runtime_hours,
)
from custom_components.eg4_monitor.data_cache import BoundedPayloadCache
from custom_components.eg4_monitor.eg4_api import (
    EG4API,
    _extract_battery_pack_voltages,
    _normalize_battery_info,
    _normalize_energy_info,
)
from custom_components.eg4_monitor.grid_quality import (
    GRID_STATE_BROWNOUT,
    GRID_STATE_NORMAL,
    GRID_STATE_OUTAGE,
    append_grid_quality_sample,
    build_grid_quality_snapshot,
    classify_grid_voltage,
)


@pytest.mark.asyncio
async def test_post_json_reauthenticates_after_login_page() -> None:
    class Response:
        def __init__(self, body: str, url: str) -> None:
            self.status = 200
            self.url = url
            self._body = body
            self.released = False

        async def text(self) -> str:
            return self._body

        def release(self) -> None:
            self.released = True

    class RequestContext:
        def __init__(self, response: Response) -> None:
            self.response = response

        async def __aenter__(self) -> Response:
            return self.response

        async def __aexit__(self, *args: object) -> None:
            return None

    class Session:
        cookie_jar = ()

        def __init__(self) -> None:
            self.responses = [
                Response(
                    "<html><form>Password</form></html>",
                    "https://monitor.eg4electronics.com/WManage/web/login",
                ),
                Response(
                    '{"success": true, "value": 1}',
                    "https://monitor.eg4electronics.com/WManage/api/test",
                ),
            ]

        def post(self, *args: object, **kwargs: object) -> RequestContext:
            return RequestContext(self.responses.pop(0))

    session = Session()
    api = EG4API("user", "password", session)
    api.logged_in = True
    login_calls = 0

    async def login() -> bool:
        nonlocal login_calls
        login_calls += 1
        api.logged_in = True
        return True

    api.login = login  # type: ignore[method-assign]
    response = await api._post_json("/api/test", "SERIAL", "test")

    assert response == {"success": True, "value": 1}
    assert login_calls == 1
    assert session.responses == []


def test_response_login_detection_catches_redirected_runtime_pages() -> None:
    class Response:
        status = 200
        url = "https://monitor.eg4electronics.com/WManage/web/login"

    assert EG4API._response_indicates_login(Response(), "<html>login password</html>")


def test_normalize_energy_info_uses_verified_tenth_kwh_fields() -> None:
    payload = {
        "success": True,
        "todayYielding": 10,
        "todayCharging": 10,
        "todayDischarging": 2,
        "todayImport": 404,
        "todayExport": 3,
        "todayUsage": 398,
        "totalYielding": 1234,
        "totalCharging": 6236,
        "totalDischarging": 1461,
        "totalImport": 344640,
        "totalExport": 5,
        "totalUsage": 340856,
        "totalCo2ReductionText": "12.5 kG",
        "totalCoalReductionText": "4 kG",
    }

    normalized = _normalize_energy_info(payload)

    assert normalized == {
        "Today": {
            "Solar Generation": 1.0,
            "Grid Import": 40.4,
            "Grid Export": 0.3,
            "Usage": 39.8,
            "Battery Charge": 1.0,
            "Battery Discharge": 0.2,
        },
        "Total": {
            "Solar Generation": 123.4,
            "Grid Import": 34464.0,
            "Grid Export": 0.5,
            "Usage": 34085.6,
            "Battery Charge": 623.6,
            "Battery Discharge": 146.1,
        },
        "Environmental": {
            "CO2 Reduction": 12.5,
            "Coal Reduction": 4.0,
        },
    }


def test_normalize_battery_info_preserves_verified_battery_fields() -> None:
    payload = {
        "success": True,
        "batStatus": "Charging",
        "batPower": 70,
        "pCharge": 70,
        "pDisCharge": 0,
        "soc": 100,
        "vBat": 541,
        "currentText": "-5.1",
        "totalVoltageText": "54.1",
        "totalNumber": 2,
        "bmsCycleCnt": 17,
        "globalMaxCellTemp": "26.5",
        "globalMinCellTemp": "25.9",
    }

    normalized = _normalize_battery_info(payload)

    assert normalized == {
        "Status": "Charging",
        "Power": 70.0,
        "ChargePower": 70.0,
        "DischargePower": 0.0,
        "SOC": 100.0,
        "Voltage": 54.1,
        "Current": -5.1,
        "TotalVoltage": 54.1,
        "TotalNumber": 2.0,
        "CycleCount": 17.0,
        "MaxCellTemperature": 26.5,
        "MinCellTemperature": 25.9,
    }


def test_extract_battery_pack_voltages_only_accepts_voltage_payloads() -> None:
    assert _extract_battery_pack_voltages(
        {
            "deviceArray": [
                {"serialNum": "inverter-1", "batCapacityValue": 1400},
            ]
        }
    ) == []

    packs = _extract_battery_pack_voltages(
        {
            "batteryArray": [
                {"batterySn": "pack-1", "totalVoltage": 5410},
                {"batterySn": "pack-2", "totalVoltageText": "54.2 V"},
            ]
        }
    )

    assert packs == [
        {
            "id": "pack-1",
            "name": "pack-1",
            "serial": "pack-1",
            "voltage": 54.1,
            "source": "batteryArray",
            "source_key": "totalVoltage",
        },
        {
            "id": "pack-2",
            "name": "pack-2",
            "serial": "pack-2",
            "voltage": 54.2,
            "source": "batteryArray",
            "source_key": "totalVoltageText",
        },
    ]

    packs = _extract_battery_pack_voltages(
        {
            "batteryList": [
                {"batterySn": "pack-3", "voltage": 54.1},
                {"batterySn": "pack-4", "batVolt": 542},
            ]
        }
    )

    assert packs == [
        {
            "id": "pack-3",
            "name": "pack-3",
            "serial": "pack-3",
            "voltage": 54.1,
            "source": "batteryList",
            "source_key": "voltage",
        },
        {
            "id": "pack-4",
            "name": "pack-4",
            "serial": "pack-4",
            "voltage": 54.2,
            "source": "batteryList",
            "source_key": "batVolt",
        },
    ]


def test_bounded_payload_cache_expires_stale_values() -> None:
    start = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    cache = BoundedPayloadCache(timedelta(seconds=10))
    cache.put("serial", {"value": 1}, start)

    cached = cache.get("serial", start + timedelta(seconds=5))
    assert cached is not None
    assert cached.value == {"value": 1}
    assert cached.age_seconds == 5.0

    assert cache.get("serial", start + timedelta(seconds=11)) is None


def test_grid_quality_tracks_outages_and_ignores_large_gaps() -> None:
    start = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    samples = []
    append_grid_quality_sample(samples, start, 245.0)
    append_grid_quality_sample(samples, start + timedelta(minutes=1), 190.0)
    append_grid_quality_sample(samples, start + timedelta(minutes=3), 245.0)
    snapshot = build_grid_quality_snapshot(samples, start + timedelta(minutes=3))

    assert classify_grid_voltage(245) == GRID_STATE_NORMAL
    assert classify_grid_voltage(210) == GRID_STATE_BROWNOUT
    assert classify_grid_voltage(190) == GRID_STATE_OUTAGE
    assert snapshot["State"] == GRID_STATE_NORMAL
    assert snapshot["OutageCount24h"] == 1
    assert snapshot["OutageDurationMinutes24h"] == 2.0


def test_backup_metrics_uses_reserve_before_runtime() -> None:
    assert estimate_battery_runtime_hours(10, 2000, 20, 20) == 3.0

    aggregate = {
        "Battery": {"SOC": 95, "Voltage": 47.5, "RemainingKWh": 10, "TotalCapacityKWh": 20},
        "Load": {"Power": 4000},
        "Generator": {"RunStatus": False, "Power": 0},
        "GridQuality": {"State": "outage"},
    }
    metrics = build_backup_metrics(aggregate, {})

    assert metrics["Battery"]["SOCVoltageMismatch"] is True
    assert metrics["Battery"]["CriticalVoltage"] is True
    assert metrics["Battery"]["EstimatedRuntimeHours"] == 1.5
    assert metrics["LoadShedding"]["Recommended"] is True
