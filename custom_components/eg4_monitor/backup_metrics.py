"""Derived backup-system health metrics for EG4 Monitor."""

from __future__ import annotations

from typing import Any, Mapping

from .const import (
    CONF_BATTERY_CRITICAL_VOLTAGE,
    CONF_BATTERY_HIGH_SOC_MIN_VOLTAGE,
    CONF_BATTERY_HIGH_SOC_THRESHOLD,
    CONF_BATTERY_IMBALANCE_THRESHOLD,
    CONF_BATTERY_RESERVE_PERCENT,
    CONF_DIESEL_FUEL_LEVEL_GALLONS,
    CONF_DIESEL_REORDER_PERCENT,
    CONF_DIESEL_TANK_CAPACITY_GALLONS,
    CONF_GENERATOR_FULL_LOAD_BURN_GPH,
    CONF_GENERATOR_IDLE_BURN_GPH,
    CONF_GENERATOR_MIN_SUPPLY_POWER_W,
    CONF_GENERATOR_RATED_POWER_KW,
    CONF_LOAD_SHED_RUNTIME_HOURS,
)

DEFAULT_BATTERY_HIGH_SOC_THRESHOLD = 90.0
DEFAULT_BATTERY_HIGH_SOC_MIN_VOLTAGE = 52.0
DEFAULT_BATTERY_CRITICAL_VOLTAGE = 48.0
DEFAULT_BATTERY_IMBALANCE_THRESHOLD = 0.5
DEFAULT_BATTERY_RESERVE_PERCENT = 20.0
DEFAULT_DIESEL_REORDER_PERCENT = 25.0
DEFAULT_GENERATOR_IDLE_BURN_GPH = 0.35
DEFAULT_GENERATOR_FULL_LOAD_BURN_GPH = 1.1
DEFAULT_GENERATOR_RATED_POWER_KW = 12.0
DEFAULT_GENERATOR_MIN_SUPPLY_POWER_W = 100.0
DEFAULT_LOAD_SHED_RUNTIME_HOURS = 2.0


def _safe_get(data: dict[str, Any] | None, path: tuple[str, ...]) -> Any:
    current: Any = data
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _option_float(
    options: Mapping[str, Any],
    key: str,
    default: float,
) -> float:
    value = _float_or_none(options.get(key))
    return default if value is None else value


def battery_voltage_stats(packs: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Summarize individual battery pack voltages."""
    voltages = []
    if isinstance(packs, list):
        for pack in packs:
            if not isinstance(pack, dict):
                continue
            voltage = _float_or_none(pack.get("voltage"))
            if voltage is not None:
                voltages.append(voltage)

    if not voltages:
        return {
            "PackCount": 0,
            "LowestVoltage": None,
            "HighestVoltage": None,
            "VoltageSpread": None,
        }

    low = min(voltages)
    high = max(voltages)
    return {
        "PackCount": len(voltages),
        "LowestVoltage": round(low, 2),
        "HighestVoltage": round(high, 2),
        "VoltageSpread": round(high - low, 2),
    }


def estimate_battery_runtime_hours(
    remaining_kwh: Any,
    load_w: Any,
    total_capacity_kwh: Any = None,
    reserve_percent: float = DEFAULT_BATTERY_RESERVE_PERCENT,
) -> float | None:
    """Estimate usable battery runtime from remaining energy and current load."""
    remaining = _float_or_none(remaining_kwh)
    load = _float_or_none(load_w)
    if remaining is None or load is None or load <= 0:
        return None

    capacity = _float_or_none(total_capacity_kwh)
    reserve_kwh = 0.0
    if capacity is not None and capacity > 0:
        reserve_kwh = capacity * max(reserve_percent, 0.0) / 100.0

    usable_kwh = max(remaining - reserve_kwh, 0.0)
    return round(usable_kwh / (load / 1000.0), 2)


def estimate_generator_fuel(
    generator_running: Any,
    generator_power_w: Any,
    options: Mapping[str, Any],
) -> dict[str, Any]:
    """Estimate diesel fuel status from manual tank level and generator load."""
    tank_capacity = _float_or_none(options.get(CONF_DIESEL_TANK_CAPACITY_GALLONS))
    fuel_level = _float_or_none(options.get(CONF_DIESEL_FUEL_LEVEL_GALLONS))
    reorder_percent = _option_float(
        options, CONF_DIESEL_REORDER_PERCENT, DEFAULT_DIESEL_REORDER_PERCENT
    )
    idle_burn = _option_float(
        options, CONF_GENERATOR_IDLE_BURN_GPH, DEFAULT_GENERATOR_IDLE_BURN_GPH
    )
    full_burn = _option_float(
        options, CONF_GENERATOR_FULL_LOAD_BURN_GPH, DEFAULT_GENERATOR_FULL_LOAD_BURN_GPH
    )
    rated_kw = _option_float(
        options, CONF_GENERATOR_RATED_POWER_KW, DEFAULT_GENERATOR_RATED_POWER_KW
    )
    power_w = max(_float_or_none(generator_power_w) or 0.0, 0.0)

    fuel_configured = bool(tank_capacity and tank_capacity > 0 and fuel_level is not None)
    fuel_percent = None
    reorder_needed = None
    if fuel_configured and tank_capacity is not None:
        fuel_level = max(min(fuel_level or 0.0, tank_capacity or 0.0), 0.0)
        fuel_percent = round((fuel_level / tank_capacity) * 100.0, 1)
        reorder_needed = fuel_percent <= reorder_percent

    if rated_kw <= 0:
        load_fraction = 0.0
    else:
        load_fraction = min(power_w / (rated_kw * 1000.0), 1.0)
    burn_rate = idle_burn + (max(full_burn - idle_burn, 0.0) * load_fraction)
    if not bool(generator_running):
        burn_rate = 0.0

    hours_remaining = None
    if fuel_configured:
        active_burn_rate = burn_rate if burn_rate > 0 else idle_burn
        if active_burn_rate > 0:
            hours_remaining = round((fuel_level or 0.0) / active_burn_rate, 1)

    return {
        "FuelConfigured": fuel_configured,
        "FuelLevelGallons": round(fuel_level, 2) if fuel_configured and fuel_level is not None else None,
        "FuelPercent": fuel_percent,
        "FuelReorderNeeded": reorder_needed,
        "BurnRateGPH": round(burn_rate, 3),
        "HoursRemaining": hours_remaining,
        "TankCapacityGallons": tank_capacity,
        "ReorderPercent": reorder_percent,
    }


def build_backup_metrics(
    aggregate: dict[str, Any],
    options: Mapping[str, Any],
) -> dict[str, Any]:
    """Build derived metrics that span battery, load, grid, and generator data."""
    battery = _safe_get(aggregate, ("Battery",))
    generator = _safe_get(aggregate, ("Generator",))
    grid_quality = _safe_get(aggregate, ("GridQuality",))
    if not isinstance(battery, dict):
        battery = {}
    if not isinstance(generator, dict):
        generator = {}
    if not isinstance(grid_quality, dict):
        grid_quality = {}

    voltage = _float_or_none(battery.get("Voltage"))
    soc = _float_or_none(battery.get("SOC"))
    pack_stats = battery_voltage_stats(battery.get("PackVoltages"))
    high_soc_threshold = _option_float(
        options, CONF_BATTERY_HIGH_SOC_THRESHOLD, DEFAULT_BATTERY_HIGH_SOC_THRESHOLD
    )
    high_soc_min_voltage = _option_float(
        options, CONF_BATTERY_HIGH_SOC_MIN_VOLTAGE, DEFAULT_BATTERY_HIGH_SOC_MIN_VOLTAGE
    )
    critical_voltage = _option_float(
        options, CONF_BATTERY_CRITICAL_VOLTAGE, DEFAULT_BATTERY_CRITICAL_VOLTAGE
    )
    imbalance_threshold = _option_float(
        options, CONF_BATTERY_IMBALANCE_THRESHOLD, DEFAULT_BATTERY_IMBALANCE_THRESHOLD
    )
    reserve_percent = _option_float(
        options, CONF_BATTERY_RESERVE_PERCENT, DEFAULT_BATTERY_RESERVE_PERCENT
    )

    soc_voltage_mismatch = (
        soc is not None
        and voltage is not None
        and soc >= high_soc_threshold
        and voltage < high_soc_min_voltage
    )
    battery_critical = voltage is not None and voltage < critical_voltage
    spread = pack_stats["VoltageSpread"]
    imbalance_detected = spread is not None and spread >= imbalance_threshold
    health_confidence = "unknown"
    if soc_voltage_mismatch:
        health_confidence = "suspect_soc"
    elif imbalance_detected:
        health_confidence = "pack_imbalance"
    elif battery_critical:
        health_confidence = "critical_voltage"
    elif voltage is not None and soc is not None:
        health_confidence = "ok"

    runtime_hours = estimate_battery_runtime_hours(
        battery.get("RemainingKWh"),
        _safe_get(aggregate, ("Load", "Power")),
        battery.get("TotalCapacityKWh"),
        reserve_percent,
    )

    gen_running = bool(generator.get("RunStatus"))
    gen_power = _float_or_none(generator.get("Power")) or 0.0
    min_supply_power = _option_float(
        options,
        CONF_GENERATOR_MIN_SUPPLY_POWER_W,
        DEFAULT_GENERATOR_MIN_SUPPLY_POWER_W,
    )
    generator_running_without_load = gen_running and gen_power < min_supply_power
    fuel = estimate_generator_fuel(gen_running, gen_power, options)
    load_shed_runtime = _option_float(
        options, CONF_LOAD_SHED_RUNTIME_HOURS, DEFAULT_LOAD_SHED_RUNTIME_HOURS
    )
    grid_down = grid_quality.get("State") in {"outage", "brownout"}
    load_shedding_recommended = bool(
        grid_down
        and not gen_running
        and runtime_hours is not None
        and runtime_hours < load_shed_runtime
    )

    return {
        "Battery": {
            **pack_stats,
            "EstimatedRuntimeHours": runtime_hours,
            "SOCVoltageMismatch": soc_voltage_mismatch,
            "CriticalVoltage": battery_critical,
            "ImbalanceDetected": imbalance_detected,
            "HealthConfidence": health_confidence,
            "HighSOCThreshold": high_soc_threshold,
            "HighSOCMinVoltage": high_soc_min_voltage,
            "CriticalVoltageThreshold": critical_voltage,
            "ImbalanceThreshold": imbalance_threshold,
            "ReservePercent": reserve_percent,
        },
        "Generator": {
            "RunningWithoutLoad": generator_running_without_load,
            "MinSupplyPowerW": min_supply_power,
        },
        "Fuel": fuel,
        "LoadShedding": {
            "Recommended": load_shedding_recommended,
            "RuntimeThresholdHours": load_shed_runtime,
        },
    }
