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

GARMIN_SCORECARDS_URL = "https://connect.garmin.com/app/my-scorecards"
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

        # Use a persistent profile directory so login sessions survive between runs.
        # This avoids re-entering credentials every time.
        profile_dir = Path.home() / ".garmin_ghin" / "chrome_profile"
        profile_dir.mkdir(parents=True, exist_ok=True)
        options.add_argument(f"--user-data-dir={profile_dir}")

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
            logger.info("Not logged in, redirected to SSO: %s", driver.current_url)
            print("Logging in with credentials from .env...")
            try:
                # Wait for the login form to be ready
                time.sleep(3)

                email_field = driver.find_element("id", "username")
                email_field.clear()
                email_field.send_keys(self._config.email)
                logger.info("Entered email: %s", self._config.email)

                pw_field = driver.find_element("id", "password")
                pw_field.clear()
                pw_field.send_keys(self._config.password)
                logger.info("Entered password (length %d)", len(self._config.password))

                login_btn = driver.find_element("id", "login-btn-signin")
                login_btn.click()
                logger.info("Clicked sign-in button")

                # Wait for redirect back to connect.garmin.com
                for i in range(30):
                    time.sleep(2)
                    current = driver.current_url
                    if "connect.garmin.com" in current:
                        logger.info("Login successful after %ds, at: %s", (i+1)*2, current)
                        break
                    logger.debug("Waiting for redirect... (%ds) at: %s", (i+1)*2, current)
                else:
                    print(
                        "Login redirect timed out after 60s.\n"
                        "Check the browser — there may be a CAPTCHA or MFA prompt.\n"
                        "Once you're logged in, press Enter here...",
                        file=sys.stderr,
                    )
                    input()

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

        # Find scorecard entries using Garmin's React CSS module classes
        scorecards = []

        # Strategy 1: Use the known CSS class for golf list items
        items = driver.find_elements("css selector", "[class*='GolfList_listItem']")
        logger.info("Found %d GolfList items", len(items))

        for item in items:
            try:
                # Extract the link (the item itself or a child <a>)
                link = None
                if item.tag_name == "a":
                    link = item
                else:
                    links_in_item = item.find_elements("css selector", "a")
                    if links_in_item:
                        link = links_in_item[0]

                href = (link.get_attribute("href") if link else "") or ""

                # Extract title and subtitle
                title = ""
                subtitle = ""
                try:
                    title_el = item.find_element("css selector", "[class*='GolfList_title']")
                    title = title_el.text.strip()
                except Exception:
                    pass
                try:
                    sub_el = item.find_element("css selector", "[class*='GolfList_subTitle']")
                    subtitle = sub_el.text.strip()
                except Exception:
                    pass

                # Extract stats (score, putts, etc.)
                stats = []
                try:
                    stat_els = item.find_elements("css selector", "[class*='GolfList_stat']")
                    stats = [s.text.strip() for s in stat_els if s.text.strip()]
                except Exception:
                    pass

                text = f"{title} - {subtitle}" if subtitle else title
                if not text:
                    text = item.text.strip()[:80]

                # Extract scorecard ID from href
                scorecard_id = href.rstrip("/").split("/")[-1] if href else ""

                if text or href:
                    scorecards.append({
                        "scorecardId": scorecard_id,
                        "text": text,
                        "href": href,
                        "stats": stats,
                    })
                    logger.debug("Found scorecard: %s -> %s (stats: %s)", text[:50], href, stats)
            except Exception as e:
                logger.debug("Error parsing list item: %s", e)
                continue

        # Strategy 2: Fall back to finding any scorecard/golf links
        if not scorecards:
            logger.info("No GolfList items found, falling back to link search")
            links = driver.find_elements("css selector", "a[href*='scorecard'], a[href*='golf']")
            logger.info("Found %d scorecard/golf links", len(links))
            for link in links:
                href = link.get_attribute("href") or ""
                text = link.text.strip()
                if text and href:
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

    def get_scorecard(self, scorecard_id: str, href: str = "") -> Scorecard:
        """Navigate to a specific scorecard and extract hole-by-hole data."""
        driver = self._ensure_browser()

        # Navigate to the scorecard detail page using the discovered href if available
        if href and href.startswith("http"):
            url = href
        elif href and href.startswith("/"):
            url = f"https://connect.garmin.com{href}"
        else:
            # Best guess - try the scorecard ID as a path segment
            url = f"https://connect.garmin.com/app/my-scorecards/{scorecard_id}"
        logger.info("Navigating to scorecard: %s", url)
        driver.get(url)
        time.sleep(5)

        page_text = driver.find_element("tag name", "body").text
        logger.debug("Scorecard page text:\n%s", page_text[:3000])

        return self._parse_scorecard_page(driver, scorecard_id, page_text)

    def _parse_scorecard_page(self, driver, scorecard_id: str, page_text: str) -> Scorecard:
        """Extract scorecard data from the rendered HTML page.

        Garmin uses a row-per-stat layout (not row-per-hole):
            Hole   1  2  3 ... 9  Out  10 ... 18  In  Total
            Par    3  3  4 ...
            Score  4  4  5 ...
            Putts  2  1  3 ...
        """
        import re

        lines = page_text.split("\n")

        # Extract course name from the first few non-empty lines
        # Typically: "Scorecards", "Desert Mountain", "No 7", "Apr 4, 2026"
        course_name = "Unknown Course"
        non_empty = [l.strip() for l in lines if l.strip()]
        for i, line in enumerate(non_empty):
            # Skip navigation/header text
            if line.lower() in ("scorecards", "stroke play", "men's tees",
                                "women's tees", "hole", "par", "score", "putts",
                                "gir", "stats", "badges"):
                continue
            # Skip date lines
            if re.match(r'\w+ \d{1,2}, \d{4}', line):
                continue
            # Skip single numbers
            if re.match(r'^\d+$', line):
                continue
            # First meaningful line is likely the course name
            course_name = line
            # Check if next line is a course/tee variant (e.g., "No 7")
            if i + 1 < len(non_empty):
                next_line = non_empty[i + 1]
                if not re.match(r'\w+ \d{1,2}, \d{4}', next_line) and \
                   next_line.lower() not in ("stroke play", "men's tees", "women's tees") and \
                   not re.match(r'^\d+$', next_line) and \
                   len(next_line) < 20:
                    course_name = f"{course_name} - {next_line}"
            break

        # Extract date
        date_played = date.today()
        for pattern, fmts in [
            (r'(\w+ \d{1,2}, \d{4})', ["%B %d, %Y", "%b %d, %Y"]),
            (r'(\d{1,2}/\d{1,2}/\d{4})', ["%m/%d/%Y"]),
            (r'(\d{4}-\d{2}-\d{2})', ["%Y-%m-%d"]),
        ]:
            match = re.search(pattern, page_text)
            if match:
                for fmt in fmts:
                    try:
                        date_played = datetime.strptime(match.group(1), fmt).date()
                        break
                    except ValueError:
                        continue
                if date_played != date.today():
                    break

        # Extract tee name
        tee_name = None
        for line in non_empty:
            if "tees" in line.lower() and line.lower() not in ("men's tees", "women's tees"):
                continue
            if line.lower() in ("men's tees", "women's tees"):
                tee_name = line
                break

        scorecard = Scorecard(
            garmin_activity_id=0,
            course_name=course_name,
            date_played=date_played,
            tee_name=tee_name,
        )

        # Parse the row-per-stat layout from page text.
        # Garmin renders each cell on its own line:
        #   Hole / 1 / 2 / ... / 9 / Out / Par / 3 / 3 / ... / 27 / Score / 4 / ...
        # We need to find each label and collect values up to the next label or summary.
        #
        # The page has two blocks (front 9 and back 9) with the same label sequence.
        # We parse all values per label across both blocks.

        labels = {"hole", "par", "score", "gir", "putts"}
        summary_labels = {"out", "in", "total"}

        # Collect all values grouped by label, in order of appearance
        # Each label can appear twice (front 9 + back 9)
        all_rows = {}  # label -> list of values (combined front+back)
        current_label = None

        for line in lines:
            stripped = line.strip()
            low = stripped.lower()

            if low in labels:
                current_label = low
                if current_label not in all_rows:
                    all_rows[current_label] = []
                continue

            if current_label is None:
                continue

            # Skip summary labels and their totals
            if low in summary_labels:
                # The next number after Out/In/Total is a summary — skip it
                continue

            # Skip GIR-style stats like "6/9"
            if re.match(r'^\d+/\d+$', stripped):
                continue

            # Skip non-data lines (new section labels, badge names, etc.)
            if stripped == "—":
                all_rows[current_label].append(None)
            elif stripped.isdigit():
                val = int(stripped)
                # Filter out summary totals: if this is hole row, only 1-18
                if current_label == "hole":
                    if 1 <= val <= 18:
                        all_rows[current_label].append(val)
                    # else it's a summary total, skip
                else:
                    all_rows[current_label].append(val)
            else:
                # Hit a non-numeric, non-label line — end of this label's data
                # But only if we already have values (avoid stopping on blank lines)
                if all_rows.get(current_label):
                    current_label = None

        hole_nums = all_rows.get("hole", [])
        par_vals = all_rows.get("par", [])
        score_vals = all_rows.get("score", [])
        putts_vals = all_rows.get("putts", [])

        # The par/score/putts rows include summary totals (Out, In, Total sums).
        # Since we skipped summary labels, the totals that follow them should also
        # be skipped. But our skip logic only skips the label, not the number.
        # Fix: trim par/score/putts to match the number of holes.
        # For front 9: 9 holes -> par has 9 values + 1 summary = 10. Back 9 similar.
        # We align by using hole_nums as the reference length.

        # Actually, let's be smarter: pair values positionally with hole numbers.
        # Remove summary values from par/score/putts by keeping only as many
        # values as we have hole numbers, removing every 10th value (the summary).
        def strip_summaries(vals, hole_count_per_nine=9):
            """Remove the summary value that appears after every 9 holes."""
            result = []
            count = 0
            for v in vals:
                count += 1
                if count == hole_count_per_nine + 1:
                    # This is the Out/In/Total summary — skip it
                    count = 0
                    continue
                result.append(v)
            return result

        if len(par_vals) > len(hole_nums):
            par_vals = strip_summaries(par_vals)
        if len(score_vals) > len(hole_nums):
            score_vals = strip_summaries(score_vals)
        if len(putts_vals) > len(hole_nums):
            putts_vals = strip_summaries(putts_vals)

        logger.debug("Parsed rows - holes: %s, par: %s, score: %s, putts: %s",
                      hole_nums, par_vals, score_vals, putts_vals)

        # Build hole scores from the extracted rows
        num_holes = min(len(hole_nums), len(par_vals), len(score_vals)) if hole_nums else 0
        for i in range(num_holes):
            hole_num = hole_nums[i]
            if not isinstance(hole_num, int) or hole_num < 1 or hole_num > 18:
                continue
            par = par_vals[i] if par_vals[i] is not None else 4
            score = score_vals[i] if score_vals[i] is not None else 0
            if score == 0:
                continue  # Skip unplayed holes
            putts = putts_vals[i] if i < len(putts_vals) and putts_vals[i] is not None else None

            scorecard.holes.append(HoleScore(
                hole_number=hole_num,
                par=par,
                score=score,
                putts=putts,
            ))

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
