"""EG4 Electronics API Module."""

import asyncio
import json
import logging
import re
from typing import Any
from urllib.parse import urlencode

import aiohttp

_LOGGER = logging.getLogger(__name__)


class EG4API:
    """API Client for EG4 Electronics."""

    def __init__(
        self,
        username: str,
        password: str,
        session: aiohttp.ClientSession,
    ) -> None:
        """Initialize the API client."""
        self.username = username
        self.password = password
        self.session = session
        self.base_url = "https://monitor.eg4electronics.com/WManage"
        self.logged_in = False
        self.serial_number: str | None = (
            None  # Stores the primarily discovered/used serial number
        )
        self.plant_id: int | None = None  # Stores the discovered plant ID

    async def login(self) -> bool:
        """Login to EG4 portal."""
        try:
            login_url = f"{self.base_url}/web/login"
            _LOGGER.debug("Attempting to login at URL: %s", login_url)

            # First, get the login page to establish session cookies
            async with self.session.get(login_url) as response:
                if response.status != 200:
                    _LOGGER.error(
                        "Failed to access login page. Status: %s", response.status
                    )
                    response_text = await response.text()
                    _LOGGER.debug("Response: %s", response_text)
                    return False
                _LOGGER.debug("Successfully accessed login page")

            login_data = {
                "account": self.username,
                "password": self.password,
            }

            # Match the exact headers from the working curl request
            headers = {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
                "Accept-Language": "en-US,en;q=0.9",
                "Cache-Control": "max-age=0",
                "Connection": "keep-alive",
                "Content-Type": "application/x-www-form-urlencoded",
                "DNT": "1",
                "Origin": "https://monitor.eg4electronics.com",
                "Referer": "https://monitor.eg4electronics.com/WManage/web/login",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-User": "?1",
                "Upgrade-Insecure-Requests": "1",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
                "sec-ch-ua": '"Chromium";v="136", "Google Chrome";v="136", "Not.A/Brand";v="99"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
            }

            encoded_data = urlencode(login_data)
            _LOGGER.debug("Sending login request with data: %s", login_data)
            async with self.session.post(
                login_url, data=encoded_data, headers=headers
            ) as response:
                response_text = await response.text()
                _LOGGER.debug("Login response status: %s", response.status)
                _LOGGER.debug(
                    "Login response: %s", response_text[:500]
                )  # Log first 500 chars

                # Check for redirect or success indicators
                if response.status == 302 or response.status == 200:
                    # Check if we were redirected to a dashboard/monitor page
                    if "monitor" in str(response.url) or "dashboard" in str(
                        response.url
                    ):
                        _LOGGER.debug("Login successful - redirected to monitor page")
                        self.logged_in = True
                        return True

                    # Check response content for success indicators
                    if any(
                        indicator in response_text.lower()
                        for indicator in ["dashboard", "monitor", "logout", "welcome"]
                    ):
                        _LOGGER.debug(
                            "Login successful - found success indicators in response"
                        )
                        self.logged_in = True
                        return True

                # Check for explicit error messages
                if any(
                    error in response_text.lower()
                    for error in ["invalid", "error", "failed", "incorrect"]
                ):
                    _LOGGER.error("Login failed: Found error indicators in response")
                    return False

                _LOGGER.warning(
                    "Login status unclear - assuming success if status is 200"
                )
                if response.status == 200:
                    self.logged_in = True
                    return True

                return False

        except aiohttp.ClientError as err:
            _LOGGER.error("Network error during login: %s", err)
            return False
        except Exception as err:
            _LOGGER.exception("Unexpected error during login: %s", err)
            return False

    async def get_system_data(
        self, serial_to_fetch: str | None = None
    ) -> dict[str, Any] | None:
        """Get current system data for a specific serial number or the default one."""
        if not self.logged_in:
            _LOGGER.debug("Not logged in for get_system_data, attempting login")
            if not await self.login():
                _LOGGER.error("Failed to login during system data request")
                return None

        try:
            cookies_dict: dict[str, str] = {}
            for cookie in self.session.cookie_jar:
                cookies_dict[cookie.key] = cookie.value
            cookies_str = "; ".join([f"{k}={v}" for k, v in cookies_dict.items()])

            headers = {
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Accept-Language": "en-US,en;q=0.9",
                "Connection": "keep-alive",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Cookie": cookies_str,
                "Origin": "https://monitor.eg4electronics.com",
                "Referer": "https://monitor.eg4electronics.com/WManage/web/monitor/inverter",
                "User-Agent": "Mozilla/5.0",
                "X-Requested-With": "XMLHttpRequest",
            }

            current_serial_to_use = (
                serial_to_fetch if serial_to_fetch else self.serial_number
            )

            if not current_serial_to_use:
                _LOGGER.debug(
                    "No specific serial_to_fetch and no default self.serial_number, discovering primary serial..."
                )
                monitor_url = f"{self.base_url}/web/monitor/inverter"
                async with self.session.get(monitor_url) as response:
                    if response.status == 200:
                        html = await response.text()
                        _LOGGER.debug("Got monitor page, searching for serial number")
                        patterns = [
                            r'serialNum["\']?\s*[:=]\s*["\']([^"\']+)["\']',
                            r'serialNumber["\']?\s*[:=]\s*["\']([^"\']+)["\']',
                            r'sn["\']?\s*[:=]\s*["\']([^"\']+)["\']',
                            r'deviceSn["\']?\s*[:=]\s*["\']([^"\']+)["\']',
                        ]
                        for pattern in patterns:
                            sn_matches = re.findall(pattern, html, re.IGNORECASE)
                            if sn_matches:
                                if not serial_to_fetch:
                                    self.serial_number = sn_matches[0]
                                current_serial_to_use = sn_matches[0]
                                _LOGGER.debug(
                                    "Found serial number using pattern %s: %s",
                                    pattern,
                                    current_serial_to_use,
                                )
                                break
                        if not current_serial_to_use:
                            _LOGGER.debug(
                                "Serial number not found in HTML, trying API fallbacks for primary discovery."
                            )
                            # Fallback 1: /api/inverter/getInverterList (often 404s or needs specific params)
                            list_url_1 = f"{self.base_url}/api/inverter/getInverterList"
                            async with self.session.post(
                                list_url_1, headers=headers, data=urlencode({})
                            ) as list_response_1:
                                if list_response_1.status == 200:
                                    try:
                                        data1 = await list_response_1.json()
                                        if (
                                            data1.get("success")
                                            and data1.get("rows")
                                            and data1["rows"]
                                        ):
                                            discovered_sn = data1["rows"][0].get(
                                                "serialNum"
                                            )
                                            if not serial_to_fetch:
                                                self.serial_number = discovered_sn
                                            current_serial_to_use = discovered_sn
                                            _LOGGER.debug(
                                                "Found serial from /api/inverter/getInverterList: %s",
                                                current_serial_to_use,
                                            )
                                    except Exception:
                                        pass  # Ignore parsing errors, try next
                            # Fallback 2: /api/device/list
                            if not current_serial_to_use:
                                list_url_2 = f"{self.base_url}/api/device/list"
                                async with self.session.post(
                                    list_url_2, headers=headers, data=urlencode({})
                                ) as list_response_2:
                                    if list_response_2.status == 200:
                                        try:
                                            data2 = await list_response_2.json()
                                            if (
                                                data2.get("success")
                                                and data2.get("data")
                                                and isinstance(data2["data"], list)
                                                and data2["data"]
                                            ):
                                                discovered_sn = data2["data"][0].get(
                                                    "sn"
                                                ) or data2["data"][0].get("serialNum")
                                                if not serial_to_fetch:
                                                    self.serial_number = discovered_sn
                                                current_serial_to_use = discovered_sn
                                                _LOGGER.debug(
                                                    "Found serial from /api/device/list: %s",
                                                    current_serial_to_use,
                                                )
                                        except Exception:
                                            pass  # Ignore parsing errors

            if not current_serial_to_use:
                _LOGGER.error(
                    "Could not determine a serial number to use for fetching system data."
                )
                return None

            _LOGGER.debug("Fetching runtime data for serial: %s", current_serial_to_use)
            system_data = {}
            async with self.session.post(
                f"{self.base_url}/api/inverter/getInverterRuntime",
                headers=headers,
                data=urlencode({"serialNum": current_serial_to_use}),
            ) as response:
                if response.status == 200:
                    raw_runtime_data = await response.json()
                    _LOGGER.debug(
                        "Raw runtime data for %s from /api/inverter/getInverterRuntime: %s",
                        current_serial_to_use,
                        json.dumps(raw_runtime_data, indent=4),
                    )
                    data = raw_runtime_data
                    if data.get("success"):

                        def to_float(value: Any, default: float = 0.0) -> float:
                            try:
                                return float(value)
                            except (ValueError, TypeError):
                                return default

                        system_data = {
                            "DeviceTime": data.get("deviceTime", "Unknown"),
                            "ServerTime": data.get("serverTime", "Unknown"),
                            "StatusText": data.get("statusText", "Unknown"),
                            "StatusCode": data.get("status"),
                            "Lost": data.get("lost", False),
                            "HasRuntimeData": data.get("hasRuntimeData", False),
                            "SerialNumber": data.get("serialNum"),
                            "FirmwareCode": data.get("fwCode"),
                            "ModelText": data.get("modelText"),
                            "Model": data.get("model"),
                            "PowerRatingText": data.get("powerRatingText"),
                            "PowerFactor": to_float(data.get("pf", "0")),
                            "RemainTime": data.get("remainTime", 0),
                            "Battery": {
                                "SOC": to_float(data.get("soc", 0)),
                                "Power": to_float(data.get("batPower", 0)),
                                "Voltage": to_float(data.get("vBat", 0)) / 10,
                                "Current": to_float(data.get("iBat", 0)) / 10
                                if "iBat" in data
                                else None,
                                "Temperature": to_float(data.get("tBat", 0))
                                if "tBat" in data
                                else None,
                                "Shared": data.get("batShared", False),
                                "Type": data.get("batteryType", "Unknown"),
                                "ParallelNum": int(data.get("batParallelNum", 0))
                                if data.get("batParallelNum", "").isdigit()
                                else 0,
                                "CapacityAh": int(data.get("batCapacity", 0))
                                if data.get("batCapacity", "").isdigit()
                                else 0,
                                "Color": data.get("batteryColor"),
                                "ChargePower": to_float(data.get("pCharge", 0)),
                                "DischargePower": to_float(data.get("pDisCharge", 0)),
                                "BMS": {
                                    "MaxChargeCurrent": to_float(
                                        data.get("maxChgCurrValue", 0)
                                    ),
                                    "MaxDischargeCurrent": to_float(
                                        data.get("maxDischgCurrValue", 0)
                                    ),
                                    "ChargeEnabled": data.get("bmsCharge", False),
                                    "DischargeEnabled": data.get("bmsDischarge", False),
                                    "ForceChargeEnabled": data.get(
                                        "bmsForceCharge", False
                                    ),
                                },
                            },
                            "Solar": {
                                "PV1": {
                                    "Voltage": to_float(data.get("vpv1", 0)) / 10,
                                    "Current": to_float(data.get("ipv1", 0)) / 10
                                    if "ipv1" in data
                                    else None,
                                    "Power": to_float(data.get("ppv1", 0)),
                                },
                                "PV2": {
                                    "Voltage": to_float(data.get("vpv2", 0)) / 10,
                                    "Current": to_float(data.get("ipv2", 0)) / 10
                                    if "ipv2" in data
                                    else None,
                                    "Power": to_float(data.get("ppv2", 0)),
                                },
                                "PV3": {
                                    "Voltage": to_float(data.get("vpv3", 0)) / 10,
                                    "Current": to_float(data.get("ipv3", 0)) / 10
                                    if "ipv3" in data
                                    else None,
                                    "Power": to_float(data.get("ppv3", 0)),
                                },
                                "TotalPower": to_float(data.get("ppv", 0)),
                            },
                            "Grid": {
                                "VoltageR": to_float(data.get("vacr", 0)) / 10,
                                "VoltageS": to_float(data.get("vacs", 0)) / 10,
                                "VoltageT": to_float(data.get("vact", 0)) / 10,
                                "Frequency": to_float(data.get("fac", 0)) / 100
                                if data.get("fac")
                                else None,
                                "PowerImport": to_float(data.get("pToUser", 0)),
                                "PowerExport": to_float(data.get("pToGrid", 0)),
                                "PowerNet": to_float(data.get("pToUser", 0))
                                - to_float(data.get("pToGrid", 0)),
                                "Current": round(
                                    float(data.get("igrid", 0)) / 10
                                    if data.get("igrid")
                                    else (
                                        (
                                            to_float(data.get("pToUser", 0))
                                            - to_float(data.get("pToGrid", 0))
                                        )
                                        / (to_float(data.get("vacr", 0)) / 10)
                                        if data.get("vacr", 0) > 0
                                        else 0.0
                                    ),
                                    1,
                                ),
                            },
                            "Load": {
                                "Power": to_float(data.get("consumptionPower", 0)),
                                "Current": round(
                                    float(data.get("iload", 0)) / 10
                                    if data.get("iload")
                                    else (
                                        to_float(data.get("consumptionPower", 0))
                                        / (to_float(data.get("vacr", 0)) / 10)
                                        if data.get("vacr", 0) > 0
                                        and data.get("consumptionPower") is not None
                                        else 0.0
                                    ),
                                    1,
                                ),
                            },
                            "EPS": {
                                "VoltageR": to_float(data.get("vepsr", 0)) / 10,
                                "VoltageS": to_float(data.get("vepss", 0)) / 10,
                                "VoltageT": to_float(data.get("vepst", 0)) / 10,
                                "Frequency": to_float(data.get("feps", 0)) / 100
                                if data.get("feps")
                                else None,
                                "Power": to_float(data.get("peps", 0)),
                                "ApparentPower": to_float(data.get("seps", 0)),
                                "L1NPower": to_float(data.get("pEpsL1N", 0)),
                                "L2NPower": to_float(data.get("pEpsL2N", 0)),
                                "HasOverloadRecoveryTime": data.get(
                                    "hasEpsOverloadRecoveryTime", False
                                ),
                                "LoadPowerShow": data.get("epsLoadPowerShow", False),
                            },
                            "Temperatures": {
                                "InverterInner": to_float(data.get("tinner", 0))
                                if "tinner" in data
                                else None,
                                "Radiator1": to_float(data.get("tradiator1", 0))
                                if "tradiator1" in data
                                else None,
                                "Radiator2": to_float(data.get("tradiator2", 0))
                                if "tradiator2" in data
                                else None,
                                "Environment": to_float(data.get("envTemp", 0))
                                if "envTemp" in data
                                else None,
                            },
                            "InverterDetails": {
                                "ACPower": to_float(data.get("pinv", 0)),
                                "RectifierPower": to_float(data.get("prec", 0)),
                                "VBus1": to_float(data.get("vBus1", 0)) / 10,
                                "VBus2": to_float(data.get("vBus2", 0)) / 10,
                                "IsParallelEnabled": data.get(
                                    "isParallelEnabled", False
                                ),
                            },
                            "Generator": {
                                "RunStatus": data.get("genDryContact") == "ON",
                                "Voltage": to_float(data.get("genVolt", 0)),
                                "Frequency": to_float(data.get("genFreq", 0)),
                                "Power": to_float(data.get("genPower", 0)),
                                "UsingGeneratorFlag": data.get(
                                    "_12KUsingGenerator", False
                                ),
                            },
                            "ACCouple": {
                                "InverterFlow": data.get(
                                    "_12KAcCoupleInverterFlow", False
                                ),
                                "InverterData": data.get(
                                    "_12KAcCoupleInverterData", False
                                ),
                                "Power": to_float(data.get("acCouplePower", 0)),
                            },
                            "SmartLoad": {
                                "InverterFlow": data.get(
                                    "smartLoadInverterFlow", False
                                ),
                                "Enabled": data.get("smartLoadInverterEnable", False),
                                "Power": to_float(data.get("smartLoadPower", 0)),
                                "ConsumptionPower114": to_float(
                                    data.get("consumptionPower114", 0)
                                ),
                            },
                            "UITags": {
                                "GridLoadPowerShow": data.get(
                                    "gridLoadPowerShow", False
                                ),
                                "PLoadPowerShow": data.get("pLoadPowerShow", False),
                                "HasQuickChargeTask": data.get(
                                    "hasUnclosedQuickChargeTask", False
                                ),
                                "HasQuickDischargeTask": data.get(
                                    "hasUnclosedQuickDischargeTask", False
                                ),
                                "HasPEPSLNValue": data.get("haspEpsLNValue", False),
                            },
                        }
            _LOGGER.debug(
                "Fetching energy statistics for serial: %s", current_serial_to_use
            )
            async with self.session.post(
                f"{self.base_url}/api/inverter/getInverterEnergy",
                headers=headers,
                data=urlencode({"serialNum": current_serial_to_use}),
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get("success"):
                        system_data["Energy"] = {
                            "Today": {
                                "Solar Generation": float(data.get("eToday", 0)) / 10,
                                "Grid Import": float(data.get("eToGridToday", 0)) / 10,
                                "Grid Export": float(data.get("eToUserToday", 0)) / 10,
                                "Battery Charge": float(data.get("eChargeToday", 0))
                                / 10,
                                "Battery Discharge": float(
                                    data.get("eDischargeToday", 0)
                                )
                                / 10,
                            },
                            "Total": {
                                "Solar Generation": float(data.get("eTotal", 0)) / 10,
                                "Grid Import": float(data.get("eToGridTotal", 0)) / 10,
                                "Grid Export": float(data.get("eToUserTotal", 0)) / 10,
                                "Battery Charge": float(data.get("eChargeTotal", 0))
                                / 10,
                                "Battery Discharge": float(
                                    data.get("eDischargeTotal", 0)
                                )
                                / 10,
                            },
                        }
            return system_data
        except aiohttp.ClientError as err:
            _LOGGER.error("Network error getting system data: %s", err)
            return None
        except Exception as err:
            _LOGGER.exception("Unexpected error getting system data: %s", err)
            return None

    async def get_plant_info(self) -> dict[str, Any] | None:
        """Fetch plant information, primarily to get the plantId."""
        if not self.logged_in:
            _LOGGER.debug("Not logged in for get_plant_info, attempting login")
            if not await self.login():
                _LOGGER.error("Failed to login during get_plant_info request")
                return None
        try:
            cookies_dict: dict[str, str] = {}
            for cookie in self.session.cookie_jar:
                cookies_dict[cookie.key] = cookie.value
            cookies_str = "; ".join([f"{k}={v}" for k, v in cookies_dict.items()])
            headers = {
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Accept-Language": "en-US,en;q=0.9",
                "Connection": "keep-alive",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Cookie": cookies_str,
                "DNT": "1",
                "Origin": "https://monitor.eg4electronics.com",
                "Referer": "https://monitor.eg4electronics.com/WManage/web/monitor/inverter",
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-origin",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
                "X-Requested-With": "XMLHttpRequest",
                "sec-ch-ua": '"Chromium";v="136", "Google Chrome";v="136", "Not.A/Brand";v="99"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
            }
            plant_list_url = f"{self.base_url}/web/config/plant/list/viewer"
            form_data = {
                "page": 1,
                "rows": 20,
                "searchText": "",
                "sort": "createDate",
                "order": "desc",
            }
            _LOGGER.debug("Fetching plant list from: %s with POST", plant_list_url)
            async with self.session.post(
                plant_list_url, headers=headers, data=urlencode(form_data)
            ) as response:
                _LOGGER.debug("Plant list response status: %s", response.status)
                response_text = await response.text()
                if response.status == 200:
                    try:
                        data = json.loads(response_text)
                        _LOGGER.info(
                            "Raw JSON response from %s: %s",
                            plant_list_url,
                            json.dumps(data, indent=4),
                        )
                        if "rows" in data and isinstance(data.get("rows"), list):
                            if data["rows"]:
                                first_plant = data["rows"][0]
                                self.plant_id = first_plant.get("id")
                                _LOGGER.info(
                                    "Discovered plantId: %s, Plant Name: %s",
                                    self.plant_id,
                                    first_plant.get("name"),
                                )
                                return first_plant
                            else:
                                _LOGGER.warning(
                                    "No plants found in the 'rows' array from %s",
                                    plant_list_url,
                                )
                                return None
                        else:
                            _LOGGER.warning(
                                "Response from %s did not contain 'rows' or 'rows' was not a list: %s",
                                plant_list_url,
                                data,
                            )
                            return None
                    except json.JSONDecodeError:
                        _LOGGER.error(
                            "Failed to decode JSON from %s. Response: %s",
                            plant_list_url,
                            response_text[:1000],
                        )
                        return None
                else:
                    _LOGGER.error(
                        "Failed to fetch plant list, status: %s, response: %s",
                        response.status,
                        response_text[:500],
                    )
                    return None
        except aiohttp.ClientError as err:
            _LOGGER.error("Network error during get_plant_info: %s", err)
            return None
        except Exception as err:
            _LOGGER.exception("Unexpected error during get_plant_info: %s", err)
            return None

    async def get_all_inverter_serials(
        self, plant_id_to_fetch: int | None
    ) -> list[str] | None:
        """Fetch a list of all inverter serial numbers for the account for a given plant_id."""
        inverters = await self.get_all_inverters(plant_id_to_fetch=plant_id_to_fetch)
        if inverters is None:
            return None
        return [
            str(item["serialNum"])
            for item in inverters
            if isinstance(item.get("serialNum"), (str, int))
        ]

    async def get_all_inverters(
        self, plant_id_to_fetch: int | None
    ) -> list[dict[str, Any]] | None:
        """Fetch all inverter rows for a plant as raw dictionaries."""
        if not self.logged_in:
            _LOGGER.debug("Not logged in for get_all_inverters, attempting login")
            if not await self.login():
                _LOGGER.error("Failed to login during get_all_inverters request")
                return None
        if plant_id_to_fetch is None:
            _LOGGER.error("plant_id_to_fetch is required for get_all_inverters")
            return None
        try:
            cookies_dict: dict[str, str] = {}
            for cookie in self.session.cookie_jar:
                cookies_dict[cookie.key] = cookie.value
            cookies_str = "; ".join([f"{k}={v}" for k, v in cookies_dict.items()])
            headers = {
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Accept-Language": "en-US,en;q=0.9",
                "Connection": "keep-alive",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Cookie": cookies_str,
                "DNT": "1",
                "Origin": "https://monitor.eg4electronics.com",
                "Referer": "https://monitor.eg4electronics.com/WManage/web/monitor/inverter",
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-origin",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
                "X-Requested-With": "XMLHttpRequest",
                "sec-ch-ua": '"Chromium";v="136", "Google Chrome";v="136", "Not.A/Brand";v="99"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
            }
            list_url = f"{self.base_url}/web/config/inverter/list"
            _LOGGER.debug(
                "Fetching all inverter rows from: %s with POST for plantId: %s",
                list_url,
                plant_id_to_fetch,
            )
            form_data = {
                "page": 1,
                "rows": 50,
                "plantId": plant_id_to_fetch,
                "searchText": "",
                "targetSerialNum": "",
            }
            async with self.session.post(
                list_url, headers=headers, data=urlencode(form_data)
            ) as list_response:
                _LOGGER.debug(
                    "Inverter list POST response status: %s", list_response.status
                )
                response_text_content = await list_response.text()
                if list_response.status == 200:
                    try:
                        data = json.loads(response_text_content)
                        _LOGGER.info(
                            "Raw JSON response from %s (for plantId %s): %s",
                            list_url,
                            plant_id_to_fetch,
                            json.dumps(data, indent=4),
                        )
                        if isinstance(data, dict) and data.get("rows"):
                            rows = [
                                row for row in data["rows"] if isinstance(row, dict)
                            ]
                            _LOGGER.info(
                                "Found %s inverter rows for plant %s",
                                len(rows),
                                plant_id_to_fetch,
                            )
                            return rows
                        elif isinstance(data, list):
                            rows = [item for item in data if isinstance(item, dict)]
                            _LOGGER.info(
                                "Found %s inverter rows from direct list for plant %s",
                                len(rows),
                                plant_id_to_fetch,
                            )
                            return rows
                        else:
                            _LOGGER.warning(
                                "Response from %s for plant %s was not in expected list/dict format or no 'rows': %s",
                                list_url,
                                plant_id_to_fetch,
                                data,
                            )
                            return []
                    except json.JSONDecodeError:
                        _LOGGER.error(
                            "Failed to decode JSON from %s for plant %s. Response text: %s",
                            list_url,
                            plant_id_to_fetch,
                            response_text_content[:1000],
                        )
                        return None
                    except Exception as err:
                        _LOGGER.error(
                            "Error processing response from %s for plant %s: %s",
                            list_url,
                            plant_id_to_fetch,
                            err,
                        )
                        return None
                else:
                    _LOGGER.error(
                        "Failed to fetch inverter list from %s for plant %s, status: %s, response: %s",
                        list_url,
                        plant_id_to_fetch,
                        list_response.status,
                        response_text_content[:1000],
                    )
                    return None
        except aiohttp.ClientError as err:
            _LOGGER.error(
                "Network error during get_all_inverters for plant %s: %s",
                plant_id_to_fetch,
                err,
            )
            return None
        except Exception as err:
            _LOGGER.exception(
                "Unexpected error during get_all_inverters for plant %s: %s",
                plant_id_to_fetch,
                err,
            )
            return None

    async def get_parallel_group_details(
        self, serial_num: str
    ) -> dict[str, Any] | None:
        """Fetch parallel group details for a serial number."""
        if not self.logged_in:
            _LOGGER.debug(
                "Not logged in for get_parallel_group_details, attempting login"
            )
            if not await self.login():
                _LOGGER.error("Failed to login during get_parallel_group_details")
                return None

        try:
            cookies_dict: dict[str, str] = {}
            for cookie in self.session.cookie_jar:
                cookies_dict[cookie.key] = cookie.value
            cookies_str = "; ".join([f"{k}={v}" for k, v in cookies_dict.items()])

            headers = {
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Accept-Language": "en-US,en;q=0.9",
                "Connection": "keep-alive",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Cookie": cookies_str,
                "Origin": "https://monitor.eg4electronics.com",
                "Referer": "https://monitor.eg4electronics.com/WManage/web/monitor/inverter",
                "User-Agent": "Mozilla/5.0",
                "X-Requested-With": "XMLHttpRequest",
            }

            url = f"{self.base_url}/api/inverterOverview/getParallelGroupDetails"
            async with self.session.post(
                url,
                headers=headers,
                data=urlencode({"serialNum": serial_num}),
            ) as response:
                if response.status != 200:
                    body = await response.text()
                    _LOGGER.warning(
                        "Parallel details request failed for %s, status=%s body=%s",
                        serial_num,
                        response.status,
                        body[:300],
                    )
                    return None

                data = await response.json()
                if data.get("success"):
                    return data
                _LOGGER.warning(
                    "Parallel details returned unsuccessful response for %s: %s",
                    serial_num,
                    data,
                )
                return None

        except aiohttp.ClientError as err:
            _LOGGER.error(
                "Network error during get_parallel_group_details for %s: %s",
                serial_num,
                err,
            )
            return None

    async def get_day_multiline_parallel(
        self,
        serial_num: str,
        date_text: str,
    ) -> dict[str, Any] | None:
        """Fetch parallel day chart data for a serial/date pair."""
        if not self.logged_in:
            _LOGGER.debug("Not logged in for day multiline, attempting login")
            if not await self.login():
                _LOGGER.error("Failed to login during get_day_multiline_parallel")
                return None

        try:
            cookies_dict: dict[str, str] = {}
            for cookie in self.session.cookie_jar:
                cookies_dict[cookie.key] = cookie.value
            cookies_str = "; ".join([f"{k}={v}" for k, v in cookies_dict.items()])

            headers = {
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Accept-Language": "en-US,en;q=0.9",
                "Connection": "keep-alive",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Cookie": cookies_str,
                "Origin": "https://monitor.eg4electronics.com",
                "Referer": "https://monitor.eg4electronics.com/WManage/web/monitor/inverter",
                "User-Agent": "Mozilla/5.0",
                "X-Requested-With": "XMLHttpRequest",
            }

            url = f"{self.base_url}/api/analyze/chart/dayMultiLineParallel"
            payload = urlencode({"serialNum": serial_num, "dateText": date_text})

            async with self.session.post(
                url, headers=headers, data=payload
            ) as response:
                if response.status != 200:
                    body = await response.text()
                    _LOGGER.warning(
                        "Day multiline request failed for %s (%s), status=%s body=%s",
                        serial_num,
                        date_text,
                        response.status,
                        body[:300],
                    )
                    return None

                data = await response.json()
                if data.get("success"):
                    return data

                _LOGGER.warning(
                    "Day multiline returned unsuccessful response for %s (%s): %s",
                    serial_num,
                    date_text,
                    data,
                )
                return None

        except aiohttp.ClientError as err:
            _LOGGER.error(
                "Network error during get_day_multiline_parallel for %s: %s",
                serial_num,
                err,
            )
            return None

    async def get_midbox_runtime(self, serial_num: str) -> dict[str, Any] | None:
        """Fetch GridBoss midbox runtime payload for a serial number."""
        if not self.logged_in:
            _LOGGER.debug("Not logged in for get_midbox_runtime, attempting login")
            if not await self.login():
                _LOGGER.error("Failed to login during get_midbox_runtime")
                return None

        try:
            cookies_dict: dict[str, str] = {}
            for cookie in self.session.cookie_jar:
                cookies_dict[cookie.key] = cookie.value
            cookies_str = "; ".join([f"{k}={v}" for k, v in cookies_dict.items()])

            headers = {
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Accept-Language": "en-US,en;q=0.9",
                "Connection": "keep-alive",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Cookie": cookies_str,
                "Origin": "https://monitor.eg4electronics.com",
                "Referer": "https://monitor.eg4electronics.com/WManage/web/monitor/inverter",
                "User-Agent": "Mozilla/5.0",
                "X-Requested-With": "XMLHttpRequest",
            }

            url = f"{self.base_url}/api/midbox/getMidboxRuntime"
            async with self.session.post(
                url,
                headers=headers,
                data=urlencode({"serialNum": serial_num}),
            ) as response:
                if response.status != 200:
                    body = await response.text()
                    _LOGGER.warning(
                        "Midbox runtime request failed for %s, status=%s body=%s",
                        serial_num,
                        response.status,
                        body[:300],
                    )
                    return None

                data = await response.json()
                if data.get("success"):
                    return data

                _LOGGER.warning(
                    "Midbox runtime returned unsuccessful response for %s: %s",
                    serial_num,
                    data,
                )
                return None

        except aiohttp.ClientError as err:
            _LOGGER.error(
                "Network error during get_midbox_runtime for %s: %s",
                serial_num,
                err,
            )
            return None

    async def get_event_list(
        self,
        page: int = 1,
        rows: int = 100,
        event_text: str = "_all",
    ) -> dict[str, Any] | None:
        """Fetch portal event list entries."""
        if not self.logged_in:
            _LOGGER.debug("Not logged in for get_event_list, attempting login")
            if not await self.login():
                _LOGGER.error("Failed to login during get_event_list")
                return None

        try:
            cookies_dict: dict[str, str] = {}
            for cookie in self.session.cookie_jar:
                cookies_dict[cookie.key] = cookie.value
            cookies_str = "; ".join([f"{k}={v}" for k, v in cookies_dict.items()])

            headers = {
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Accept-Language": "en-US,en;q=0.9",
                "Connection": "keep-alive",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Cookie": cookies_str,
                "Origin": "https://monitor.eg4electronics.com",
                "Referer": "https://monitor.eg4electronics.com/WManage/web/analyze/event",
                "User-Agent": "Mozilla/5.0",
                "X-Requested-With": "XMLHttpRequest",
            }

            url = f"{self.base_url}/api/analyze/event/list"
            payload = urlencode(
                {
                    "page": page,
                    "rows": rows,
                    "eventText": event_text,
                }
            )

            async with self.session.post(
                url, headers=headers, data=payload
            ) as response:
                if response.status != 200:
                    body = await response.text()
                    _LOGGER.warning(
                        "Event list request failed status=%s body=%s",
                        response.status,
                        body[:300],
                    )
                    return None

                data = await response.json()
                if data.get("success"):
                    return data

                _LOGGER.warning("Event list returned unsuccessful response: %s", data)
                return None

        except aiohttp.ClientError as err:
            _LOGGER.error("Network error during get_event_list: %s", err)
            return None
        except Exception as err:
            _LOGGER.exception("Unexpected error during get_event_list: %s", err)
            return None
        except Exception as err:
            _LOGGER.exception(
                "Unexpected error during get_midbox_runtime for %s: %s",
                serial_num,
                err,
            )
            return None
        except Exception as err:
            _LOGGER.exception(
                "Unexpected error during get_day_multiline_parallel for %s: %s",
                serial_num,
                err,
            )
            return None
        except Exception as err:
            _LOGGER.exception(
                "Unexpected error during get_parallel_group_details for %s: %s",
                serial_num,
                err,
            )
            return None

    async def close(self) -> None:
        """Close the session."""
        self.logged_in = False
        self.serial_number = None
        self.plant_id = None

    async def __aenter__(self):
        """Async enter."""
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        """Async exit."""
        await self.close()


if __name__ == "__main__":

    async def main():
        logging.basicConfig(level=logging.DEBUG)
        from settings import settings

        username = settings.eg4_username
        password = settings.eg4_password

        if not username or not password:
            _LOGGER.error(
                "Missing credentials. Set EG4_PORTAL__USERNAME and EG4_PORTAL__PASSWORD in .env.local"
            )
            return

        async with aiohttp.ClientSession() as session:
            api = EG4API(username, password, session)

            _LOGGER.info("Attempting to login...")
            if not await api.login():
                _LOGGER.error("Login failed. Exiting.")
                return

            _LOGGER.info("Attempting to fetch plant info...")
            plant_info = await api.get_plant_info()

            current_plant_id = None
            if plant_info and plant_info.get("id"):
                current_plant_id = plant_info.get("id")
                _LOGGER.info("Using plantId: %s", current_plant_id)
            else:
                _LOGGER.error(
                    "Could not determine plantId. Cannot proceed to fetch inverter list."
                )
                return  # Exit if no plant_id

            # Ensure self.serial_number is cleared before fetching all so primary discovery doesn't interfere
            # with the loop's explicit fetching, or rely on serial_to_fetch in get_system_data
            # The current get_system_data prioritizes serial_to_fetch, so this is fine.

            _LOGGER.info(
                "Attempting to fetch all inverter serial numbers for plantId: %s...",
                current_plant_id,
            )
            all_serials = await api.get_all_inverter_serials(
                plant_id_to_fetch=current_plant_id
            )

            if all_serials:
                _LOGGER.info(
                    "All discovered inverter serial numbers for plant %s: %s",
                    current_plant_id,
                    all_serials,
                )
                for serial_from_list in all_serials:
                    _LOGGER.info(
                        "Fetching data for serial: %s (plant: %s)",
                        serial_from_list,
                        current_plant_id,
                    )
                    # Explicitly pass the serial from the list to get_system_data
                    inverter_data = await api.get_system_data(
                        serial_to_fetch=serial_from_list
                    )
                    if inverter_data:
                        print(
                            f"\nData for Inverter SN: {serial_from_list} (Plant ID: {current_plant_id})"
                        )
                        print(json.dumps(inverter_data, indent=4))
                        # If this is the first serial and self.serial_number is not set,
                        # get_system_data would have set it.
                        # We can also explicitly set it here if needed for other logic,
                        # but for this loop, serial_to_fetch is key.
                        if not api.serial_number:  # Set the first successfully fetched serial as the "primary" for the instance
                            api.serial_number = serial_from_list
                    else:
                        _LOGGER.error(
                            "Failed to fetch data for serial: %s", serial_from_list
                        )
            else:
                _LOGGER.error(
                    "Failed to fetch all inverter serial numbers for plant %s.",
                    current_plant_id,
                )

    asyncio.run(main())
