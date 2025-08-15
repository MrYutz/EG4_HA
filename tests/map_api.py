# map_api.py
import asyncio
import json
import logging
from datetime import datetime

from pyppeteer import launch
from settings import settings

# Set up logging for real-time output
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),  # Console output
    ]
)
logger = logging.getLogger(__name__)

# --- Best Practice: Use Environment Variables for Credentials ---
# In your terminal, run:
# export EG4_USER="your_email@example.com"
# export EG4_PASS="your_secret_password"

LOGIN_URL = 'https://monitor.eg4electronics.com/WManage/web/login'

async def main():
    username = settings.eg4_username
    password = settings.eg4_password

    if not username or not password:
        print("❌ Error: Set EG4_USER and EG4_PASS environment variables.")
        logger.error("Missing credentials")
        return

    print("\n🚀 Starting EG4 API mapping...")
    print(f"📧 Using username: {username}")
    print(f"🔗 Login URL: {LOGIN_URL}")
    print(f"⏰ Starting at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*60)
    logger.info("Starting EG4 API mapping")

    # This list will store all our captured API call data
    api_log = []
    request_log = {}

    print("🔧 Step 1: Setting up event listeners...")
    logger.info("Setting up event listeners")

    def log_request(request):
        """Capture request data"""
        url = request.url
        if "/WManage/web/" in url and "/resources/" not in url:
            timestamp = datetime.now().strftime("%H:%M:%S")
            print(f"\n🔵 [{timestamp}] REQUEST: {request.method} {url}")
            logger.info(f"REQUEST: {request.method} {url}")

            if request.postData:
                print(f"   📤 POST Data: {request.postData}")
                logger.info(f"POST Data: {request.postData}")

            request_log[url] = {
                'method': request.method,
                'headers': dict(request.headers),
                'post_data': request.postData,
                'url': url,
                'timestamp': timestamp
            }

    def log_response(response):
        """Capture response data"""
        url = response.url
        if "/WManage/web/" in url and "/resources/" not in url:
            timestamp = datetime.now().strftime("%H:%M:%S")
            print(f"🔶 [{timestamp}] RESPONSE: {response.status} {url}")
            logger.info(f"RESPONSE: {response.status} {url}")
            # Schedule the async processing
            asyncio.create_task(process_response(response, request_log.get(url, {}), api_log))

    async def process_response(response, request_data, api_log):
        """Process response data asynchronously"""
        try:
            response_body = await response.json()
        except Exception:
            try:
                response_body = await response.text()
            except Exception:
                response_body = "Could not read response body"

        api_log.append({
            'url': response.url,
            'method': request_data.get('method', 'UNKNOWN'),
            'request_headers': request_data.get('headers', {}),
            'request_body': request_data.get('post_data'),
            'response_status': response.status,
            'response_headers': dict(response.headers),
            'response_body': response_body,
        })

    browser = await launch(
        headless=True,  # Must be True in dev container
        args=[
            '--no-sandbox',
            '--disable-setuid-sandbox',
            '--disable-dev-shm-usage',
            '--disable-gpu',
            '--disable-web-security',
            '--disable-extensions',
            '--disable-background-timer-throttling',
            '--disable-backgrounding-occluded-windows',
            '--disable-renderer-backgrounding',
            '--single-process',
            '--no-zygote',
            '--disable-features=VizDisplayCompositor'
        ]
    )
    page = await browser.newPage()

    print("🔧 Step 2: Setting up browser and page...")
    logger.info("Browser launched, setting up page")

    # Enable request interception to capture POST data
    await page.setRequestInterception(True)

    # Step 2: Attach the listeners
    page.on('request', log_request)
    page.on('response', log_response)

    # Allow all requests to continue - fix the async issue
    def continue_request(request):
        asyncio.create_task(request.continue_())
    page.on('request', continue_request)

    print("✅ Event listeners attached - monitoring all requests!")
    logger.info("Event listeners attached")

    print(f"🌐 Step 3: Navigating to login page: {LOGIN_URL}")
    logger.info(f"Navigating to: {LOGIN_URL}")
    await page.goto(LOGIN_URL, {'waitUntil': 'networkidle2'})

    print("Step 4: Filling login credentials...")
    # NOTE: Inspect the login page to get the correct selectors for the inputs
    await page.type('input[name="account"]', username)
    await page.type('input[name="password"]', password)

    print("🔑 Step 5: Clicking login and waiting for navigation...")
    try:
        await asyncio.gather(
            page.waitForNavigation({'waitUntil': 'networkidle2', 'timeout': 10000}),  # Reduced timeout
            page.click('button[type="submit"]')
        )
        print("✅ Login successful!")
    except Exception as e:
        print(f"⚠️  Login navigation issue (continuing anyway): {e}")
        # Continue even if navigation fails - we might still be logged in
    print("Login successful. Now monitoring for API calls...")

    # Step 6: Navigate to the main dashboard or data page after login
    print("Step 6: Navigating to main page to trigger API calls...")
    try:
        # Wait a bit for the page to settle after login
        await asyncio.sleep(3)

        # Try to navigate to the main page or trigger some actions
        current_url = page.url
        print(f"Current page URL: {current_url}")

        # Look for dashboard or main page link
        try:
            await page.waitForSelector('a[href*="dashboard"], a[href*="main"], .nav-item', timeout=5000)
            dashboard_link = await page.querySelector('a[href*="dashboard"], a[href*="main"], .nav-item')
            if dashboard_link:
                print("Found dashboard link, clicking...")
                await dashboard_link.click()
                await page.waitForNavigation({'waitUntil': 'networkidle2'})
        except Exception:
            print("No dashboard link found, staying on current page")

        # Trigger a page reload to capture initial API calls
        print("Reloading page to capture API calls...")
        await page.reload({'waitUntil': 'networkidle2'})

    except Exception as e:
        print(f"Error during navigation: {e}")

    # Step 7: Wait for a while to let the page load all its initial data.
    # The listeners will be firing in the background.
    # Increase this time if the page has a lot to load.
    print("⏳ Waiting for 60 seconds to capture data... (Page should be visible)")
    logger.info("Starting 60-second monitoring period")

    # Wait and show countdown
    for remaining in range(60, 0, -10):
        print(f"⏱️  {remaining} seconds remaining... (watching for API calls)")
        await asyncio.sleep(10)

    print("\n⏹️  Monitoring period complete!")
    logger.info("Monitoring period complete")

    print("\n🔍 Step 8: Halting monitoring. Processing results...")
    await browser.close()

    # Step 9: Save the results to a file
    output_filename = 'api_map.json'
    with open(output_filename, 'w') as f:
        json.dump(api_log, f, indent=2)

    print(f"\n✅ Reverse engineering complete! See '{output_filename}' for the full log.")
    print(f"Found {len(api_log)} API calls.")

    # Print summary of captured calls
    if api_log:
        print("\nCaptured API calls summary:")
        for i, call in enumerate(api_log, 1):
            print(f"{i}. {call['method']} {call['url']} -> {call['response_status']}")
    else:
        print("\n⚠️  No API calls were captured. This could mean:")
        print("   - The login failed")
        print("   - The page doesn't make API calls immediately")
        print("   - The API endpoints don't match our filter criteria")
        print("   - We need to interact with the page more")

if __name__ == '__main__':
    asyncio.run(main())
