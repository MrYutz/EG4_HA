"""Map the API calls of the EG4 website using pyppeteer."""

import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Dict, List

from pyppeteer import launch
from pyppeteer.browser import Browser
from pyppeteer.page import Page, Request, Response
from settings import settings

# --- Constants ---
LOGIN_URL = "https://monitor.eg4electronics.com/WManage/web/login"
OUTPUT_FILENAME = "api_map.json"
MONITORING_DURATION = 60  # in seconds

# --- Logger Setup ---
logger = logging.getLogger(__name__)


def setup_logging() -> None:
    """Set up logging for real-time output."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler()],
    )


async def launch_browser() -> Browser:
    """Launch the pyppeteer browser with the necessary arguments."""
    logger.info("Launching browser...")
    return await launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-web-security",
            "--disable-extensions",
            "--disable-background-timer-throttling",
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
            "--single-process",
            "--no-zygote",
            "--disable-features=VizDisplayCompositor",
        ],
    )


async def login(page: Page, username: str, password: str) -> None:
    """Log in to the EG4 website."""
    logger.info(f"Navigating to login page: {LOGIN_URL}")
    await page.goto(LOGIN_URL, {"waitUntil": "networkidle2"})

    logger.info("Filling login credentials...")
    await page.type('input[name="account"]', username)
    await page.type('input[name="password"]', password)

    logger.info("Clicking login and waiting for navigation...")
    try:
        await asyncio.gather(
            page.waitForNavigation({"waitUntil": "networkidle2", "timeout": 10000}),
            page.click('button[type="submit"]'),
        )
        logger.info("Login successful!")
    except Exception as e:
        logger.warning(f"Login navigation issue (continuing anyway): {e}")


async def monitor_requests(page: Page, duration: int) -> List[Dict[str, Any]]:
    """Monitor network requests for a given duration."""
    api_log: List[Dict[str, Any]] = []
    request_log: Dict[str, Dict[str, Any]] = {}

    def log_request(request: Request) -> None:
        """Capture request data."""
        url = request.url
        if "/WManage/web/" in url and "/resources/" not in url:
            timestamp = datetime.now().strftime("%H:%M:%S")
            logger.info(f"REQUEST: {request.method} {url}")
            if request.postData:
                logger.info(f"POST Data: {request.postData}")

            request_log[url] = {
                "method": request.method,
                "headers": dict(request.headers),
                "post_data": request.postData,
                "url": url,
                "timestamp": timestamp,
            }

    def log_response(response: Response) -> None:
        """Capture response data."""
        url = response.url
        if "/WManage/web/" in url and "/resources/" not in url:
            logger.info(f"RESPONSE: {response.status} {url}")
            asyncio.create_task(
                process_response(response, request_log.get(url, {}), api_log)
            )

    async def process_response(
        response: Response, request_data: Dict[str, Any], log: List[Dict[str, Any]]
    ) -> None:
        """Process response data asynchronously."""
        try:
            response_body = await response.json()
        except Exception:
            try:
                response_body = await response.text()
            except Exception:
                response_body = "Could not read response body"

        log.append(
            {
                "url": response.url,
                "method": request_data.get("method", "UNKNOWN"),
                "request_headers": request_data.get("headers", {}),
                "request_body": request_data.get("post_data"),
                "response_status": response.status,
                "response_headers": dict(response.headers),
                "response_body": response_body,
            }
        )

    await page.setRequestInterception(True)
    page.on("request", log_request)
    page.on("response", log_response)

    def continue_request(request: Request) -> None:
        """Continue all requests."""
        asyncio.create_task(request.continue_())

    page.on("request", continue_request)

    logger.info("Event listeners attached - monitoring all requests!")

    logger.info(f"Waiting for {duration} seconds to capture data...")
    for remaining in range(duration, 0, -10):
        logger.info(f"{remaining} seconds remaining...")
        await asyncio.sleep(10)

    logger.info("Monitoring period complete!")
    return api_log


def save_results(api_log: List[Dict[str, Any]], filename: str) -> None:
    """Save the captured API calls to a file."""
    with open(filename, "w") as f:
        json.dump(api_log, f, indent=2)

    logger.info(f"Reverse engineering complete! See '{filename}' for the full log.")
    logger.info(f"Found {len(api_log)} API calls.")

    if api_log:
        logger.info("Captured API calls summary:")
        for i, call in enumerate(api_log, 1):
            logger.info(
                f"{i}. {call['method']} {call['url']} -> {call['response_status']}"
            )
    else:
        logger.warning("No API calls were captured.")


async def main() -> None:
    """Run the API mapping script."""
    setup_logging()

    username = settings.eg4_username
    password = settings.eg4_password

    if not username or not password:
        logger.error(
            "Missing credentials. Set EG4_PORTAL__USERNAME and EG4_PORTAL__PASSWORD."
        )
        return

    logger.info("Starting EG4 API mapping...")
    logger.info(f"Using username: {username}")

    browser = await launch_browser()
    page = await browser.newPage()

    await login(page, username, password)

    api_log = await monitor_requests(page, MONITORING_DURATION)

    await browser.close()

    save_results(api_log, OUTPUT_FILENAME)


if __name__ == "__main__":
    asyncio.run(main())
