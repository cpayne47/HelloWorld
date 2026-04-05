"""Garmin Connect client using undetected-chromedriver.

Garmin's Cloudflare protection blocks both automated logins and API calls
from non-browser HTTP clients. This module uses undetected-chromedriver
(real Chrome binary, patched to avoid detection) to navigate Garmin Connect
and extract golf scorecard data.
"""

import json
import logging
import sys
import time
from datetime import date, datetime
from pathlib import Path

from .config import GarminConfig
from .scorecard import HoleScore, Scorecard

logger = logging.getLogger(__name__)

GARMIN_SCORECARDS_URL = "https://connect.garmin.com/app/scorecards"
GARMIN_LOGIN_URL = "https://sso.garmin.com/sso/signin"


class GarminClient:
    """Fetches golf data from Garmin Connect using a real Chrome browser."""

    def __init__(self, config: GarminConfig):
        self._config = config
        self._driver = None

    def _ensure_browser(self):
        if self._driver is not None:
            return self._driver

        try:
            import undetected_chromedriver as uc
        except ImportError:
            print(
                "undetected-chromedriver is required:\n\n"
                "  pip install undetected-chromedriver\n",
                file=sys.stderr,
            )
            raise SystemExit(1)

        options = uc.ChromeOptions()
        options.add_argument("--no-first-run")
        options.add_argument("--no-service-autorun")
        options.add_argument("--password-store=basic")

        # Auto-detect Chrome version to avoid driver mismatch
        chrome_version = None
        try:
            import subprocess
            result = subprocess.run(
                ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "--version"],
                capture_output=True, text=True,
            )
            if result.returncode == 0:
                chrome_version = int(result.stdout.strip().split()[-1].split(".")[0])
                logger.info("Detected Chrome version: %d", chrome_version)
        except Exception as e:
            logger.debug("Could not detect Chrome version: %s", e)

        logger.info("Launching Chrome...")
        driver = uc.Chrome(options=options, headless=False, version_main=chrome_version)
        self._driver = driver
        return driver

    def _login(self, driver) -> None:
        """Log into Garmin Connect if not already logged in."""
        driver.get(GARMIN_SCORECARDS_URL)
        time.sleep(5)

        # Check if we landed on the scorecards page or got redirected to SSO
        if "sso.garmin.com" in driver.current_url:
            logger.info("Not logged in, performing login...")
            try:
                email_field = driver.find_element("id", "username")
                email_field.clear()
                email_field.send_keys(self._config.email)

                pw_field = driver.find_element("id", "password")
                pw_field.clear()
                pw_field.send_keys(self._config.password)

                login_btn = driver.find_element("id", "login-btn-signin")
                login_btn.click()

                # Wait for redirect back to connect.garmin.com
                for _ in range(30):
                    time.sleep(2)
                    if "connect.garmin.com" in driver.current_url:
                        break
                else:
                    print("Login timed out. Check browser for MFA or CAPTCHA.", file=sys.stderr)
                    raise SystemExit(1)

                logger.info("Login successful, redirected to: %s", driver.current_url)
                # Navigate to scorecards after login
                driver.get(GARMIN_SCORECARDS_URL)
                time.sleep(5)
            except Exception as e:
                logger.error("Auto-login failed: %s", e)
                print(
                    f"Auto-login failed: {e}\n"
                    "The browser is still open — please log in manually.\n"
                    "Once you're on the scorecards page, press Enter here...",
                    file=sys.stderr,
                )
                input()
        else:
            logger.info("Already logged in at: %s", driver.current_url)

    def get_recent_golf_rounds(self, days_back: int = 30) -> list[dict]:
        """Navigate to scorecards page and extract scorecard list."""
        driver = self._ensure_browser()
        self._login(driver)

        # Make sure we're on the scorecards page
        if "/scorecards" not in driver.current_url:
            driver.get(GARMIN_SCORECARDS_URL)
            time.sleep(5)

        # Dump page source for debugging
        page_text = driver.find_element("tag name", "body").text
        page_html = driver.page_source
        logger.debug("Page URL: %s", driver.current_url)
        logger.debug("Page text (first 2000 chars):\n%s", page_text[:2000])

        # Find all scorecard links on the page
        scorecards = []

        # Try finding links to individual scorecards
        links = driver.find_elements("css selector", "a[href*='scorecard']")
        if not links:
            links = driver.find_elements("css selector", "a[href*='golf']")
        if not links:
            # Broader: any clickable card-like elements
            links = driver.find_elements("css selector", "a")

        logger.info("Found %d links on scorecards page", len(links))

        for link in links:
            href = link.get_attribute("href") or ""
            text = link.text.strip()
            if not text:
                continue

            # Look for scorecard-related links
            if any(kw in href.lower() for kw in ["scorecard", "golf", "activity"]):
                # Extract scorecard ID (UUID or numeric)
                scorecard_id = href.rstrip("/").split("/")[-1]
                scorecards.append({
                    "scorecardId": scorecard_id,
                    "text": text,
                    "href": href,
                })
                logger.debug("Found scorecard link: %s -> %s", text[:50], href)

        if not scorecards:
            logger.warning("No scorecard links found. Page text:\n%s", page_text[:3000])
            # Save page source for debugging
            debug_file = Path("debug_scorecards_page.html")
            debug_file.write_text(page_html)
            print(f"\nNo scorecards found. Page HTML saved to {debug_file} for debugging.")

        return scorecards

    def get_scorecard(self, scorecard_id: str) -> Scorecard:
        """Navigate to a specific scorecard and extract hole-by-hole data."""
        driver = self._ensure_browser()

        # Navigate to the scorecard detail page
        url = f"https://connect.garmin.com/app/scorecards/{scorecard_id}"
        driver.get(url)
        time.sleep(5)

        page_text = driver.find_element("tag name", "body").text
        logger.debug("Scorecard page text:\n%s", page_text[:3000])

        return self._parse_scorecard_page(driver, scorecard_id, page_text)

    def _parse_scorecard_page(self, driver, scorecard_id: str, page_text: str) -> Scorecard:
        """Extract scorecard data from the rendered HTML page."""

        # Extract course name
        course_name = "Unknown Course"
        for selector in ["h1", "h2", "[class*='course']", "[class*='Course']", "[class*='title']"]:
            try:
                el = driver.find_element("css selector", selector)
                if el.text.strip():
                    course_name = el.text.strip()
                    break
            except Exception:
                continue

        # Try to extract date from page text
        date_played = date.today()
        import re
        date_patterns = [
            r'(\w+ \d{1,2}, \d{4})',  # Apr 4, 2026
            r'(\d{1,2}/\d{1,2}/\d{4})',  # 4/4/2026
            r'(\d{4}-\d{2}-\d{2})',  # 2026-04-04
        ]
        for pattern in date_patterns:
            match = re.search(pattern, page_text)
            if match:
                try:
                    for fmt in ["%B %d, %Y", "%b %d, %Y", "%m/%d/%Y", "%Y-%m-%d"]:
                        try:
                            date_played = datetime.strptime(match.group(1), fmt).date()
                            break
                        except ValueError:
                            continue
                except Exception:
                    pass
                if date_played != date.today():
                    break

        scorecard = Scorecard(
            garmin_activity_id=0,
            course_name=course_name,
            date_played=date_played,
        )

        # Strategy 1: Look for table rows with hole data
        try:
            rows = driver.find_elements("css selector", "table tr")
            for row in rows:
                cells = row.find_elements("css selector", "td, th")
                if len(cells) >= 3:
                    try:
                        texts = [c.text.strip() for c in cells]
                        hole_num = int(texts[0])
                        if 1 <= hole_num <= 18:
                            par = int(texts[1]) if texts[1].isdigit() else 4
                            score = int(texts[2]) if texts[2].isdigit() else 0
                            putts = int(texts[3]) if len(texts) > 3 and texts[3].isdigit() else None
                            scorecard.holes.append(HoleScore(
                                hole_number=hole_num,
                                par=par,
                                score=score,
                                putts=putts,
                            ))
                    except (ValueError, IndexError):
                        continue
        except Exception as e:
            logger.debug("Table parsing failed: %s", e)

        # Strategy 2: Parse numbers from page text if no table found
        if not scorecard.holes:
            logger.info("No table found, trying text-based extraction")
            # Look for patterns like hole numbers followed by scores
            lines = page_text.split("\n")
            logger.debug("Page has %d lines of text", len(lines))
            for line in lines:
                logger.debug("  Line: %s", line[:100])

        if not scorecard.holes:
            # Save for debugging
            debug_file = Path("debug_scorecard_detail.html")
            debug_file.write_text(driver.page_source)
            logger.warning("Could not extract hole data. HTML saved to %s", debug_file)
            print(f"\nPage text:\n{page_text[:2000]}")

        return scorecard

    def close(self) -> None:
        if self._driver:
            self._driver.quit()
            self._driver = None
