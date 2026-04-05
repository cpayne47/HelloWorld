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

GARMIN_GOLF_URL = "https://connect.garmin.com/modern/golf"
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

        logger.info("Launching Chrome...")
        driver = uc.Chrome(options=options, headless=False)
        self._driver = driver
        return driver

    def _login(self, driver) -> None:
        """Log into Garmin Connect if not already logged in."""
        driver.get(GARMIN_GOLF_URL)
        time.sleep(3)

        # Check if we landed on the golf page (already logged in) or got redirected to SSO
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
            except Exception as e:
                logger.error("Login failed: %s", e)
                print(
                    f"Login failed: {e}\n"
                    "The browser is still open — you can log in manually.\n"
                    "Press Enter here once you're logged in...",
                    file=sys.stderr,
                )
                input()
        else:
            logger.info("Already logged in")

    def get_recent_golf_rounds(self, days_back: int = 30) -> list[dict]:
        """Navigate to golf page and extract scorecard list."""
        driver = self._ensure_browser()
        self._login(driver)

        # Navigate to golf scorecards page
        driver.get(GARMIN_GOLF_URL)
        time.sleep(5)

        # Try to get data via JavaScript — the page's internal API
        scorecards_json = driver.execute_script("""
            // Try to intercept the page's data store
            try {
                // Try fetching the API from within the browser context
                const resp = await fetch('/proxy/gcs-golfcommunity/api/v2/scorecard/list?limit=50');
                const data = await resp.json();
                return JSON.stringify(data);
            } catch(e) {
                return null;
            }
        """)

        if scorecards_json:
            try:
                data = json.loads(scorecards_json)
                scorecards = data if isinstance(data, list) else data.get("scorecardList", data.get("scorecards", []))
                if scorecards:
                    logger.info("Got %d scorecards via in-browser fetch", len(scorecards))
                    return scorecards
            except Exception as e:
                logger.debug("In-browser fetch parse failed: %s", e)

        # Fallback: parse the HTML page
        logger.info("Falling back to HTML parsing")
        return self._parse_golf_page(driver)

    def _parse_golf_page(self, driver) -> list[dict]:
        """Extract scorecard data from the rendered golf page HTML."""
        time.sleep(3)

        # Try to find scorecard elements on the page
        scorecards = []
        try:
            # Look for scorecard list items — Garmin uses React, elements may vary
            cards = driver.find_elements("css selector",
                "[class*='scorecard'], [class*='Scorecard'], "
                "[class*='golf-score'], [class*='GolfScore'], "
                "[data-testid*='scorecard']"
            )

            if not cards:
                # Try broader selectors
                cards = driver.find_elements("css selector", "a[href*='/golf/scorecard/']")

            logger.info("Found %d scorecard elements on page", len(cards))

            for card in cards:
                href = card.get_attribute("href") or ""
                text = card.text
                # Extract activity ID from href like /golf/scorecard/12345
                activity_id = None
                if "/scorecard/" in href:
                    try:
                        activity_id = int(href.split("/scorecard/")[-1].split("?")[0].split("/")[0])
                    except ValueError:
                        pass

                scorecards.append({
                    "activityId": activity_id,
                    "text": text,
                    "href": href,
                })

        except Exception as e:
            logger.error("Failed to parse golf page: %s", e)

        return scorecards

    def get_scorecard(self, activity_id: int) -> Scorecard:
        """Navigate to a specific scorecard and extract hole-by-hole data."""
        driver = self._ensure_browser()

        url = f"https://connect.garmin.com/modern/golf/scorecard/{activity_id}"
        driver.get(url)
        time.sleep(5)

        # Try in-browser API fetch first
        detail_json = driver.execute_script(f"""
            try {{
                const resp = await fetch('/proxy/gcs-golfcommunity/api/v2/scorecard/{activity_id}/details');
                const data = await resp.json();
                return JSON.stringify(data);
            }} catch(e) {{
                return null;
            }}
        """)

        if detail_json:
            try:
                data = json.loads(detail_json)
                if data and len(str(data)) > 10:
                    logger.info("Got scorecard details via in-browser fetch")
                    return self._build_scorecard_from_api(activity_id, data)
            except Exception as e:
                logger.debug("In-browser scorecard fetch failed: %s", e)

        # Fallback: parse the scorecard page HTML
        return self._parse_scorecard_page(driver, activity_id)

    def _parse_scorecard_page(self, driver, activity_id: int) -> Scorecard:
        """Extract scorecard data from the rendered HTML page."""
        time.sleep(3)
        page_text = driver.find_element("tag name", "body").text

        # Extract course name from page title or header
        course_name = "Unknown Course"
        try:
            header = driver.find_element("css selector",
                "[class*='course-name'], [class*='CourseName'], h1, h2"
            )
            if header.text:
                course_name = header.text.strip()
        except Exception:
            pass

        scorecard = Scorecard(
            garmin_activity_id=activity_id,
            course_name=course_name,
            date_played=date.today(),
        )

        # Try to find hole-by-hole data in table rows
        try:
            rows = driver.find_elements("css selector",
                "table tr, [class*='hole-row'], [class*='HoleRow']"
            )
            for row in rows:
                cells = row.find_elements("css selector", "td, [class*='cell']")
                if len(cells) >= 3:
                    try:
                        texts = [c.text.strip() for c in cells]
                        # Typical layout: hole#, par, score, putts...
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
            logger.error("Failed to parse scorecard table: %s", e)

        if not scorecard.holes:
            logger.warning("Could not extract hole data. Page text:\n%s", page_text[:2000])

        return scorecard

    def _build_scorecard_from_api(self, activity_id: int, data: dict) -> Scorecard:
        """Build scorecard from in-browser API fetch response."""
        course_name = data.get("courseName", data.get("course", {}).get("name", "Unknown Course"))
        date_str = data.get("startTime", data.get("date", ""))
        try:
            date_played = datetime.fromisoformat(date_str.replace("Z", "+00:00")).date()
        except (ValueError, AttributeError):
            date_played = date.today()

        scorecard = Scorecard(
            garmin_activity_id=activity_id,
            course_name=course_name,
            date_played=date_played,
            total_score=data.get("totalScore"),
            total_putts=data.get("totalPutts"),
            score_vs_par=data.get("scoreToPar"),
        )

        for hole_data in data.get("holes", []):
            hole = HoleScore(
                hole_number=hole_data.get("holeNumber", hole_data.get("number", 0)),
                par=hole_data.get("par", 4),
                score=hole_data.get("strokes", hole_data.get("score", 0)),
                putts=hole_data.get("putts"),
                fairway_hit=hole_data.get("fairwayHit"),
                gir=hole_data.get("greenInRegulation", hole_data.get("gir")),
                penalties=hole_data.get("penalties", 0),
            )
            scorecard.holes.append(hole)

        scorecard.holes.sort(key=lambda h: h.hole_number)
        return scorecard

    def close(self) -> None:
        if self._driver:
            self._driver.quit()
            self._driver = None
