"""Tests for the EG4API client."""

import logging
from typing import AsyncGenerator

import aiohttp
import pytest
import pytest_asyncio

from custom_components.eg4_monitor.eg4_api import EG4API
from tests.settings import settings

pytestmark = pytest.mark.skipif(
    not settings.has_eg4_credentials,
    reason="EG4 credentials not configured in environment/.env",
)

# Configure basic logging for tests
logging.basicConfig(level=logging.DEBUG)
_LOGGER = logging.getLogger(__name__)


@pytest_asyncio.fixture
async def http_session() -> AsyncGenerator[aiohttp.ClientSession, None]:
    """Provide an aiohttp.ClientSession for each test function."""
    async with aiohttp.ClientSession() as session:
        yield session


@pytest_asyncio.fixture
async def api_client(http_session: aiohttp.ClientSession) -> EG4API:
    """Create an EG4API client fixture."""
    return EG4API(
        username=settings.eg4_username,
        password=settings.eg4_password,
        session=http_session,
    )


@pytest.mark.asyncio
async def test_login_success(api_client: EG4API) -> None:
    """Test successful login to the EG4 portal."""
    _LOGGER.info("Attempting login with provided credentials...")
    success = await api_client.login()
    assert success, "Login failed with provided credentials."
    _LOGGER.info("Login successful.")
    assert api_client.logged_in, (
        "API client 'logged_in' flag not set after successful login."
    )


@pytest.mark.asyncio
async def test_get_system_data_retrieves_data(api_client: EG4API) -> None:
    """Test that get_system_data retrieves a non-empty dictionary."""
    _LOGGER.info("Attempting to fetch system data...")
    # Ensure login first (though get_system_data should handle it)
    if not api_client.logged_in:
        login_success = await api_client.login()
        assert login_success, "Pre-requisite login failed for get_system_data test."

    system_data = await api_client.get_system_data()
    assert system_data is not None, "get_system_data returned None."
    assert isinstance(system_data, dict), "get_system_data did not return a dictionary."
    assert len(system_data) > 0, "get_system_data returned an empty dictionary."
    _LOGGER.info(f"Successfully retrieved system data. Keys: {system_data.keys()}")

    # Log some key data points for manual verification by the user
    if "Battery" in system_data and "SOC" in system_data["Battery"]:
        _LOGGER.info(f"Battery SOC: {system_data['Battery']['SOC']}%")
    if "Solar" in system_data and "Total Power" in system_data["Solar"]:
        _LOGGER.info(f"Solar Total Power: {system_data['Solar']['Total Power']}W")
    if "Grid" in system_data and "Power" in system_data["Grid"]:
        _LOGGER.info(f"Grid Power: {system_data['Grid']['Power']}W")
    if (
        "Energy" in system_data
        and "Today" in system_data["Energy"]
        and "Solar Generation" in system_data["Energy"]["Today"]
    ):
        _LOGGER.info(
            f"Today's Solar Generation: "
            f"{system_data['Energy']['Today']['Solar Generation']} kWh"
        )

    # Check for serial number presence
    assert api_client.serial_number is not None, "Serial number was not discovered."
    _LOGGER.info(f"Discovered Serial Number: {api_client.serial_number}")
