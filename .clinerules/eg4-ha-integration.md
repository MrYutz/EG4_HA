## Brief overview
Project-specific guidelines for the EG4 Home Assistant integration development. This integration connects to the EG4 web portal to fetch solar inverter data and present it as Home Assistant sensors. These rules capture established patterns, architectural decisions, and DRY principles specific to this codebase.

## Communication style
- Use detailed debug logging throughout all modules with consistent `_LOGGER` patterns
- Log API responses with `json.dumps(data, indent=4)` for debugging complex data structures
- Include status codes, URLs, and error context in all error messages
- Use descriptive variable names that reflect the EG4 domain (e.g., `plant_id`, `serial_number`, `inverter_data`)

## Development workflow
- Always test API changes with the standalone `if __name__ == "__main__":` block in `eg4_api.py`
- Use multi-coordinator architecture - one `DataUpdateCoordinator` per inverter serial number
- Maintain the memory-bank documentation when making significant architectural changes
- Follow the established async/await patterns throughout the codebase
- Use proper Home Assistant entity inheritance (`CoordinatorEntity`, `SensorEntity`)

## Coding best practices
- **DRY Violations to Fix**: Eliminate duplicated `SENSOR_TYPES` between `sensor.py` and `config_flow.py`
- **Extract Common Patterns**: Create helper functions for repeated header/cookie construction in `eg4_api.py`
- **Single Responsibility**: Break down the massive `get_system_data()` method into smaller, focused functions
- **Centralize Constants**: Move hardcoded URLs, timeouts, and magic numbers to `const.py`
- **Type Hints**: Use modern Python typing (`str | None`, `dict[str, Any]`) consistently
- **Error Handling**: Use specific exception types with proper fallback logic and user-friendly messages

## Home Assistant integration patterns
- Use `async_get_clientsession(hass)` for all HTTP requests to leverage HA's session management
- Store multiple coordinators in `hass.data[DOMAIN][entry.entry_id]["coordinators"]` keyed by serial number
- Create unique entity IDs using format: `{serial_number}_{sensor_key}`
- Populate device info with: identifiers, name, manufacturer ("EG4 Electronics"), model, sw_version
- Use proper sensor device classes and state classes for energy dashboard integration
- Handle coordinator data being None gracefully in sensor entities

## API client architecture
- Maintain session state with `logged_in`, `serial_number`, and `plant_id` instance variables
- Use browser-like headers to avoid detection as automated requests
- Implement robust login verification by accessing monitor page after login POST
- Use dynamic discovery: `get_plant_info()` → `get_all_inverter_serials()` → `get_system_data(serial)`
- Handle individual inverter failures without affecting others in multi-inverter setups
- Parse API responses with helper functions like `to_float()` for consistent data conversion

## Data transformation patterns
- Structure API responses into nested dictionaries with consistent naming (Battery, Solar, Grid, Load, EPS, Energy)
- Convert raw values using established patterns: voltages `/10`, frequencies `/100`, energies `/10`
- Use lambda functions in sensor descriptions for clean data extraction: `value_fn=lambda data: data["Battery"]["SOC"]`
- Handle missing or invalid data gracefully with fallback values and None checks

## Testing and validation
- Mock `aiohttp.ClientSession` for unit tests to avoid actual API calls
- Test multi-inverter scenarios with different serial numbers
- Validate that device info is correctly populated for each inverter
- Ensure sensor entities handle coordinator data being None or missing keys
- Test config flow validation with both valid and invalid credentials

## Project-specific constraints
- Integration depends on reverse-engineered EG4 portal API endpoints that may change
- Must handle rate limiting and session timeouts gracefully
- Support both single and multi-inverter installations
- Maintain compatibility with HACS installation and updates
- Keep scan intervals reasonable (30s default) to avoid overwhelming the EG4 portal
