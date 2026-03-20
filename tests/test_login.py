"""Test login with basic requests to see if credentials work."""

import logging
from pathlib import Path

import pytest
import requests
from .settings import settings

logger = logging.getLogger(__name__)

pytestmark = pytest.mark.skipif(
    not settings.has_eg4_credentials,
    reason="EG4 credentials not configured in environment/.env",
)


def setup_logging() -> None:
    """Set up logging for real-time output."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler()],
    )


def test_login() -> None:
    """Test login with basic requests to see if credentials work."""
    session = requests.Session()

    # Set headers to mimic a browser
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/91.0.4472.124 Safari/537.36"
        }
    )

    logger.info("Testing EG4 login credentials...")
    logger.info(f"Username: {settings.eg4_username}")
    logger.info(f"Password: {'*' * len(settings.eg4_password)}")

    # First, get the login page to see if there are any hidden fields or tokens
    logger.info("1. Getting login page...")
    login_url = "https://monitor.eg4electronics.com/WManage/web/login"
    response = session.get(login_url)

    logger.info(f"Login page status: {response.status_code}")
    logger.info(f"Login page URL: {response.url}")

    if response.status_code == 200:
        logger.info("Login page accessible")

        # Check if there are any hidden fields or CSRF tokens
        if "csrf" in response.text.lower() or "token" in response.text.lower():
            logger.warning("Detected possible CSRF token requirement")

        # Now try to login
        logger.info("2. Attempting login...")
        login_data = {
            "account": settings.eg4_username,
            "password": settings.eg4_password,
        }

        response = session.post(login_url, data=login_data)

        logger.info(f"Login response status: {response.status_code}")
        logger.info(f"Final URL after login: {response.url}")
        logger.info(f"Response length: {len(response.text)} chars")

        # Check if we were redirected away from login page
        if "login" not in response.url:
            logger.info("Login appears successful - redirected away from login page")

            # Try to access a main page that might trigger API calls
            logger.info("3. Trying to access main/dashboard page...")
            main_urls = [
                "https://monitor.eg4electronics.com/WManage/web/main",
                "https://monitor.eg4electronics.com/WManage/web/dashboard",
                "https://monitor.eg4electronics.com/WManage/web/index",
                "https://monitor.eg4electronics.com/WManage/web/",
            ]

            for url in main_urls:
                try:
                    resp = session.get(url)
                    logger.info(f"  {url} -> {resp.status_code}")
                    if resp.status_code == 200 and "login" not in resp.url:
                        logger.info(f"  Success accessing: {url}")

                        # Save the page content to see what's available
                        Path("main_page.html").write_text(resp.text)
                        logger.info("  Saved page content to main_page.html")
                        break
                except requests.exceptions.RequestException as e:
                    logger.error(f"  Error accessing {url}: {e}")

        else:
            logger.error("Login failed - still on login page")

            # Check for error messages
            if "error" in response.text.lower() or "invalid" in response.text.lower():
                logger.warning("Possible error message in response")

            # Save the response to check for error details
            Path("login_response.html").write_text(response.text)
            logger.info("Saved login response to login_response.html")

    else:
        logger.error(f"Cannot access login page: {response.status_code}")


if __name__ == "__main__":
    setup_logging()
    test_login()
