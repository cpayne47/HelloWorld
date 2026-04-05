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
from .course_db import get_correct_pars, get_ghin_name, get_tee_box
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

    def get_recent_golf_rounds(self, days_back: int = 30, scroll_all: bool = False) -> list[dict]:
        """Navigate to scorecards page and extract scorecard list.

        If scroll_all is True, scrolls to the bottom repeatedly to load all
        historical scorecards (for bulk extraction).
        """
        driver = self._ensure_browser()
        self._login(driver)

        # Make sure we're on the scorecards page
        if "/scorecards" not in driver.current_url:
            driver.get(GARMIN_SCORECARDS_URL)
            time.sleep(5)

        # Scroll to load all scorecards if requested (Garmin uses lazy loading)
        if scroll_all:
            logger.info("Scrolling to load all scorecards...")
            prev_count = 0
            no_change_count = 0
            for scroll_attempt in range(100):  # Max 100 scrolls
                items = driver.find_elements("css selector", "[class*='GolfList_listItem']")
                current_count = len(items)
                if current_count == prev_count:
                    no_change_count += 1
                    if no_change_count >= 3:
                        logger.info("No new items after %d scrolls, done loading (%d items)",
                                    scroll_attempt + 1, current_count)
                        break
                else:
                    no_change_count = 0
                    logger.debug("Scroll %d: %d items loaded", scroll_attempt + 1, current_count)
                prev_count = current_count
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(2)

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

        Garmin always lays out 18 holes on every scorecard:
            Hole   1  2 ... 9  Out  10 11 ... 18  In  Total
            Par    3  3 ... 3   27   3  3 ...  3  27    54
            Score  4  — ... —   33   —  5 ...  3  33    33
            GIR    ✓  ✗ ...    6/9   ...           0/0  6/9
            Putts  2  — ... —   21   —  1 ...  2  19    19

        Each cell renders as its own line in page text. We always extract
        all 18 holes — unplayed holes have dashes (score=None). After
        extraction we determine front/back/both from which holes have scores.
        """
        import re

        lines = page_text.split("\n")
        non_empty = [l.strip() for l in lines if l.strip()]

        # Always dump page text for debugging
        debug_text_file = Path("debug_scorecard_text.txt")
        debug_text_file.write_text(page_text)
        logger.debug("Page text saved to %s", debug_text_file)

        # Extract course name from the first few non-empty lines
        course_name = "Unknown Course"
        for i, line in enumerate(non_empty):
            if line.lower() in ("scorecards", "stroke play", "men's tees",
                                "women's tees", "hole", "par", "score", "putts",
                                "gir", "stats", "badges"):
                continue
            if re.match(r'\w+ \d{1,2}, \d{4}', line):
                continue
            if re.match(r'^\d+$', line):
                continue
            course_name = line
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
            if line.lower() in ("men's tees", "women's tees"):
                tee_name = line
                break

        scorecard = Scorecard(
            garmin_activity_id=scorecard_id or "",
            course_name=course_name,
            date_played=date_played,
            garmin_tee_name=tee_name,
            tee_name=tee_name,
        )

        # === Tokenize the scorecard table area ===
        # Find the section between "Hole" and "Stats"/"Badges"/"Eagle or better"
        # and collect every data token in order.
        tokens = []  # list of: int, None (dash), True/False (GIR), or "LABEL:xxx"
        in_table = False
        stop_words = {"stats", "badges", "eagle or better",
                      "exclude this scorecard", "par starter", "break 40",
                      "welcome to the club", "break 50", "break 60 on 9",
                      "penalty-free"}

        for line in lines:
            stripped = line.strip()
            low = stripped.lower()

            if not stripped:
                continue

            if low == "hole" and not in_table:
                in_table = True
                tokens.append("LABEL:hole")
                continue

            if not in_table:
                continue

            # Stop at stats/badges section
            if low in stop_words:
                break

            # Labels
            if low in ("par", "score", "gir", "putts"):
                tokens.append(f"LABEL:{low}")
                continue

            # Summary words — mark but keep (we'll use them for alignment)
            if low in ("out", "in", "total"):
                tokens.append(f"SUMMARY:{low}")
                continue

            # Fraction like "6/9" — skip
            if re.match(r'^\d+/\d+$', stripped):
                continue

            # Dash
            if stripped == "—":
                tokens.append(None)
                continue

            # GIR checkmark/X
            if stripped in ("\u2713", "\u2714", "\u2705") or "✓" in stripped or "✔" in stripped:
                tokens.append(True)
                continue
            if stripped in ("X", "x", "\u2717", "\u2718", "✗", "✘") or "✕" in stripped or "✖" in stripped:
                tokens.append(False)
                continue

            # Number
            if stripped.isdigit():
                tokens.append(int(stripped))
                continue

            # Unknown — skip
            logger.debug("Skipping unknown token: %r", stripped)

        logger.debug("Tokens (%d): %s", len(tokens), tokens[:80])

        # === Parse tokens into rows ===
        # Split tokens at LABEL markers. Each label starts a new row.
        # A row contains the per-hole values plus summary totals.
        rows = {}  # label -> list of values
        current_row_label = None
        current_row_vals = []

        for token in tokens:
            if isinstance(token, str) and token.startswith("LABEL:"):
                # Save previous row
                if current_row_label is not None:
                    rows[current_row_label] = current_row_vals
                current_row_label = token.split(":")[1]
                current_row_vals = []
            elif isinstance(token, str) and token.startswith("SUMMARY:"):
                # Keep summaries as markers for alignment
                current_row_vals.append(token)
            else:
                current_row_vals.append(token)

        # Save last row
        if current_row_label is not None:
            rows[current_row_label] = current_row_vals

        logger.debug("Raw rows:")
        for label, vals in rows.items():
            logger.debug("  %s (%d): %s", label, len(vals), vals[:25])

        # === Extract 18 per-hole values from each row ===
        # Each row has: 9 values, SUMMARY:out, [out_total], 9 values, SUMMARY:in, [in_total], SUMMARY:total, [grand_total]
        # We need to extract just the 18 per-hole values.

        def extract_18(vals):
            """Extract exactly 18 per-hole values from a row with summaries.

            Stat rows have: val*9, SUMMARY:out, total, val*9, SUMMARY:in, total, SUMMARY:total, total
            We collect 9 data values, skip SUMMARY + its total, collect 9 more, skip rest.
            """
            result = []
            i = 0
            while i < len(vals) and len(result) < 18:
                v = vals[i]
                if isinstance(v, str) and v.startswith("SUMMARY:"):
                    # Skip the summary label
                    i += 1
                    # Skip the total value that follows (if it's not another SUMMARY)
                    if i < len(vals) and not (isinstance(vals[i], str) and vals[i].startswith("SUMMARY:")):
                        i += 1
                    continue
                result.append(v)
                i += 1
            return result

        # Holes are always 1-18
        hole_nums = list(range(1, 19))
        par_vals = extract_18(rows.get("par", []))
        score_vals = extract_18(rows.get("score", []))
        gir_vals = extract_18(rows.get("gir", []))
        putts_vals = extract_18(rows.get("putts", []))

        logger.debug("Extracted - holes(%d): %s", len(hole_nums), hole_nums)
        logger.debug("Extracted - par(%d): %s", len(par_vals), par_vals)
        logger.debug("Extracted - score(%d): %s", len(score_vals), score_vals)
        logger.debug("Extracted - gir(%d): %s", len(gir_vals), gir_vals)
        logger.debug("Extracted - putts(%d): %s", len(putts_vals), putts_vals)

        # Validate: we should have 18 par values and 18 score values
        if len(par_vals) != 18 or len(score_vals) != 18:
            debug_file = Path("debug_scorecard_detail.html")
            debug_file.write_text(driver.page_source)
            logger.warning("Expected 18 pars and scores, got par=%d score=%d",
                           len(par_vals), len(score_vals))
            print(f"\nPARSER ERROR: Expected 18 values per row, got par={len(par_vals)} score={len(score_vals)}.")
            print(f"Debug files: {debug_text_file}, {debug_file}")
            print(f"Tokens ({len(tokens)}): {tokens[:60]}")
            return scorecard  # Return empty scorecard — caller should stop

        # Build all 18 holes (unplayed holes get score=None)
        for i in range(18):
            hole_num = hole_nums[i] if i < len(hole_nums) else i + 1
            par = par_vals[i] if i < len(par_vals) and isinstance(par_vals[i], int) else 3
            score = score_vals[i] if i < len(score_vals) and isinstance(score_vals[i], int) else None
            putts = putts_vals[i] if i < len(putts_vals) and isinstance(putts_vals[i], int) else None
            gir = gir_vals[i] if i < len(gir_vals) and isinstance(gir_vals[i], bool) else None

            scorecard.holes.append(HoleScore(
                hole_number=hole_num if isinstance(hole_num, int) else i + 1,
                par=par,
                score=score,
                putts=putts,
                gir=gir,
            ))

        # Extract summary stats from the Stats section of page text
        stats_section = False
        for line in non_empty:
            if line == "Stats":
                stats_section = True
                continue
            if stats_section:
                if line == "Badges":
                    break
                if "Eagle or better" in line:
                    try:
                        idx = non_empty.index(line)
                        if idx > 0 and non_empty[idx - 1].isdigit():
                            scorecard.eagles_or_better = int(non_empty[idx - 1])
                    except (ValueError, IndexError):
                        pass
                elif line == "Birdie":
                    try:
                        idx = non_empty.index(line)
                        if idx > 0 and non_empty[idx - 1].isdigit():
                            scorecard.birdies = int(non_empty[idx - 1])
                    except (ValueError, IndexError):
                        pass
                elif line == "Par":
                    try:
                        idx = non_empty.index(line)
                        if idx > 0 and non_empty[idx - 1].isdigit():
                            scorecard.pars = int(non_empty[idx - 1])
                    except (ValueError, IndexError):
                        pass
                elif line == "Bogey":
                    try:
                        idx = non_empty.index(line)
                        if idx > 0 and non_empty[idx - 1].isdigit():
                            scorecard.bogeys = int(non_empty[idx - 1])
                    except (ValueError, IndexError):
                        pass
                elif "Double Bogey or worse" in line:
                    try:
                        idx = non_empty.index(line)
                        if idx > 0 and non_empty[idx - 1].isdigit():
                            scorecard.double_bogeys_or_worse = int(non_empty[idx - 1])
                    except (ValueError, IndexError):
                        pass
                elif "Fairways Hit" in line:
                    try:
                        idx = non_empty.index(line)
                        if idx > 0 and re.match(r'^\d+/\d+$', non_empty[idx - 1]):
                            scorecard.fairways_hit = non_empty[idx - 1]
                    except (ValueError, IndexError):
                        pass
                elif line == "GIR":
                    try:
                        idx = non_empty.index(line)
                        if idx > 0 and re.match(r'^\d+/\d+$', non_empty[idx - 1]):
                            scorecard.gir_summary = non_empty[idx - 1]
                    except (ValueError, IndexError):
                        pass

        # Apply correct pars from course database (Garmin's pars can be wrong)
        garmin_tee = scorecard.garmin_tee_name or ""
        correct_pars = get_correct_pars(scorecard.course_name)
        if correct_pars:
            logger.info("Applying corrected pars from course database for '%s'",
                        scorecard.course_name)
            for hole in scorecard.holes:
                idx = hole.hole_number - 1
                if idx < len(correct_pars):
                    hole.par = correct_pars[idx % len(correct_pars)]

            tee_box = get_tee_box(scorecard.course_name, garmin_tee)
            if tee_box:
                scorecard.tee_name = tee_box

            ghin_name = get_ghin_name(scorecard.course_name)
            if ghin_name:
                scorecard.course_name = ghin_name
        else:
            logger.info("No course database entry for '%s' — using Garmin pars",
                        scorecard.course_name)

        scorecard.compute_stats()
        return scorecard

    def close(self) -> None:
        if self._driver:
            self._driver.quit()
            self._driver = None
