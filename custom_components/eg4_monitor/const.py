"""Constants for the EG4 Monitor integration."""

DOMAIN = "eg4_monitor"
LOCAL_DONGLE_TRACE_SCHEMA = "eg4_local_dongle_trace.v2"

CONF_MASTER_SERIAL = "master_serial"
CONF_SLAVE_SERIAL = "slave_serial"
CONF_GRIDBOSS_SERIAL = "gridboss_serial"
CONF_BATTERY_DETAIL_INTERVAL = "battery_detail_interval"
CONF_BATTERY_HIGH_SOC_THRESHOLD = "battery_high_soc_threshold"
CONF_BATTERY_HIGH_SOC_MIN_VOLTAGE = "battery_high_soc_min_voltage"
CONF_BATTERY_CRITICAL_VOLTAGE = "battery_critical_voltage"
CONF_BATTERY_IMBALANCE_THRESHOLD = "battery_imbalance_threshold"
CONF_BATTERY_RESERVE_PERCENT = "battery_reserve_percent"
CONF_DIESEL_TANK_CAPACITY_GALLONS = "diesel_tank_capacity_gallons"
CONF_DIESEL_FUEL_LEVEL_GALLONS = "diesel_fuel_level_gallons"
CONF_DIESEL_REORDER_PERCENT = "diesel_reorder_percent"
CONF_GENERATOR_IDLE_BURN_GPH = "generator_idle_burn_gph"
CONF_GENERATOR_FULL_LOAD_BURN_GPH = "generator_full_load_burn_gph"
CONF_GENERATOR_RATED_POWER_KW = "generator_rated_power_kw"
CONF_GENERATOR_MIN_SUPPLY_POWER_W = "generator_min_supply_power_w"
CONF_LOAD_SHED_RUNTIME_HOURS = "load_shed_runtime_hours"
CONF_LOCAL_MASTER_HOST = "local_master_host"
CONF_LOCAL_SLAVE_HOST = "local_slave_host"
CONF_LOCAL_GRIDBOSS_HOST = "local_gridboss_host"
CONF_LOCAL_DONGLE_PORT = "local_dongle_port"
CONF_LOCAL_AUTO_DISCOVERY = "local_auto_discovery"
CONF_LOCAL_TRACE_ENABLED = "local_trace_enabled"
CONF_LOCAL_TRACE_INTERVAL = "local_trace_interval"
CONF_LOCAL_TRACE_BMS_INTERVAL = "local_trace_bms_interval"

# Base URLs
BASE_URL = "https://monitor.eg4electronics.com/WManage"
LOGIN_URL = f"{BASE_URL}/web/login"
MONITOR_URL = f"{BASE_URL}/web/monitor/inverter"

# Update interval in seconds (5 minutes)
UPDATE_INTERVAL = 300
DEFAULT_BATTERY_DETAIL_INTERVAL = 600
DEFAULT_LOCAL_DONGLE_PORT = 8000
DEFAULT_LOCAL_DONGLE_TIMEOUT = 5.0
DEFAULT_LOCAL_GRIDBOSS_HOST = ""
DEFAULT_LOCAL_AUTO_DISCOVERY = True
DEFAULT_LOCAL_DISCOVERY_TIMEOUT = 0.5
DEFAULT_LOCAL_DISCOVERY_MAX_HOSTS = 254
DEFAULT_LOCAL_DISCOVERY_CONCURRENCY = 8
DEFAULT_LOCAL_TRACE_ENABLED = True
DEFAULT_LOCAL_TRACE_INTERVAL = 1.0
DEFAULT_LOCAL_TRACE_BMS_INTERVAL = 5.0

# Read-only, zero-based profiles. The recorder owns scheduling; shared shapes
# (notably inverter holding110/125) have exactly one maintenance deadline.
LOCAL_INVERTER_PRIMARY_BLOCKS = ((4, 0, 40),)
LOCAL_INVERTER_DYNAMIC_BLOCKS = ((4, 40, 40), (4, 120, 26))
LOCAL_GRIDBOSS_PRIMARY_BLOCKS = ((4, 0, 40), (4, 120, 12))
LOCAL_GRIDBOSS_DYNAMIC_BLOCKS = ((4, 500, 100),)
LOCAL_INVERTER_MAINTENANCE = (
    (4, 80, 40, None),  # Configured BMS interval.
    (3, 110, 125, 60.0),
    (3, 0, 20, 300.0),
    (3, 20, 90, 300.0),
    (3, 235, 102, 300.0),
)
LOCAL_GRIDBOSS_MAINTENANCE = (
    (3, 4500, 10, 5.0),
    (3, 0, 20, 300.0),
    (3, 20, 90, 300.0),
    (3, 110, 125, 300.0),
    (3, 235, 88, 300.0),
    (3, 2000, 108, 300.0),
)
