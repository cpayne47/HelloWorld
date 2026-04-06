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

        # Extract tee name — appears as "White Tees", "Men's Tees", etc.
        # on its own line, typically after "Stroke Play" and before "Hole"
        tee_name = None
        for line in non_empty:
            low = line.lower()
            if low.endswith(" tees") or low.endswith(" tee"):
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
        #
        # Simplified parser: only extract Hole, Par, and Score.
        # Garmin renders each row with BOTH front and back 9 inline:
        #   Hole: 1 2 ... 9 Out 10 11 ... 18 In Total
        #   Par:  4 4 ... 5  36  4  5 ...  4  36   72
        #   Score: ...
        # Summary words (Out/In/Total) appear between data groups.
        # We split each label's data at summary markers to get front/back.

        ALL_LABELS = {"hole", "par", "score", "fairway", "gir", "putts"}
        SUMMARY_WORDS = {"out", "in", "total"}

        # Tokenize everything between "Hole" and "Stats"
        tokens = []
        in_table = False
        stop_words = {"stats", "badges", "eagle or better",
                      "exclude this scorecard"}

        for line in lines:
            stripped = line.strip()
            low = stripped.lower()

            if not stripped:
                continue
            if low == "hole" and not in_table:
                in_table = True
                tokens.append(("label", "hole"))
                continue
            if not in_table:
                continue
            if low in stop_words:
                break

            if low in ALL_LABELS:
                tokens.append(("label", low))
            elif low in SUMMARY_WORDS:
                tokens.append(("summary", low))
            elif re.match(r'^\d+/\d+$', stripped):
                tokens.append(("frac", stripped))
            elif stripped == "—":
                tokens.append(("dash", None))
            elif stripped.isdigit():
                tokens.append(("num", int(stripped)))
            else:
                tokens.append(("symbol", stripped))

        logger.debug("Tokens (%d): %s", len(tokens),
                      [(t, v) for t, v in tokens[:100]])

        # === Collect all items per label ===
        sections = {}  # label -> list of (ttype, tval)
        current_label = None
        last_label_idx = 0  # token index of the last label we saw
        for i, (ttype, tval) in enumerate(tokens):
            if ttype == "label":
                current_label = tval
                if current_label not in sections:
                    sections[current_label] = []
                last_label_idx = i
                continue
            if current_label is not None:
                sections[current_label].append((ttype, tval))

        logger.debug("Sections found: %s",
                      {k: len(v) for k, v in sections.items()})

        # === Extract front 9 from labeled sections ===
        # Front 9 is always the first 9 num/dash values in each label.
        def get_first_9(items):
            vals = [tval for ttype, tval in items if ttype in ("num", "dash")]
            return vals[:9]

        front_par = get_first_9(sections.get("par", []))
        front_score = get_first_9(sections.get("score", []))

        # === Extract back 9: try two layouts ===
        #
        # Layout B (inline): Each label has both front and back inline.
        #   Par section has 21+ values: front(9) + Out total(1) + back(9) + In(1) + Tot(1)
        #   → back = values[10:19]
        #
        # Layout A (separate block): Front 9 has labels, back 9 is an
        #   unlabeled block after all labels. Each label only has ~10 values.
        #   → Find 10,11,...,18 in token stream, then par(9)+totals(2), score(9)+totals(2)

        par_all = [tval for t, tval in sections.get("par", []) if t in ("num", "dash")]
        score_all = [tval for t, tval in sections.get("score", []) if t in ("num", "dash")]

        back_par = []
        back_score = []

        # Try Layout B first: inline back 9 within labels
        if len(par_all) > 12:
            logger.debug("Layout B detected (par has %d values)", len(par_all))
            back_par = par_all[10:19]
        if len(score_all) > 12:
            back_score = score_all[10:19]

        # Fallback: Layout A — find unlabeled back 9 block in token stream
        if not back_par:
            logger.debug("Layout B back par empty, trying Layout A (unlabeled block)")
            # Search for hole 10 followed by 11 anywhere in the token stream
            back9_token_idx = None
            for i in range(len(tokens) - 1):
                ttype, tval = tokens[i]
                if ttype == "num" and tval == 10:
                    # Verify next numeric token is 11
                    for j in range(i + 1, min(i + 4, len(tokens))):
                        jt, jv = tokens[j]
                        if jt == "num" and jv == 11:
                            back9_token_idx = i
                            break
                        elif jt == "num":
                            break
                    if back9_token_idx is not None:
                        break

            if back9_token_idx is not None:
                # Collect only num/dash values from back 9 onward
                back_nums = [tval for ttype, tval in tokens[back9_token_idx:]
                             if ttype in ("num", "dash")]

                # Skip hole numbers 10-18
                skip = 0
                while skip < len(back_nums) and isinstance(back_nums[skip], int) \
                        and 10 <= back_nums[skip] <= 18:
                    skip += 1

                logger.debug("Layout A: back nums after holes (%d): %s",
                             len(back_nums) - skip, back_nums[skip:skip+30])

                # Stats are in order: par(9 vals + 2 totals), score(9 + 2), ...
                back_par = back_nums[skip:skip + 9]
                back_score = back_nums[skip + 11:skip + 20]

                logger.debug("Layout A back par: %s", back_par)
                logger.debug("Layout A back score: %s", back_score)

        logger.debug("Front par (%d): %s", len(front_par), front_par)
        logger.debug("Back par (%d): %s", len(back_par), back_par)
        logger.debug("Front score (%d): %s", len(front_score), front_score)
        logger.debug("Back score (%d): %s", len(back_score), back_score)

        # === Combine front + back into 18-hole arrays ===
        def pad9(vals):
            """Pad/trim a list to exactly 9 values."""
            return (vals + [None] * 9)[:9]

        par_vals = pad9(front_par) + pad9(back_par)
        score_vals = pad9(front_score) + pad9(back_score)

        logger.debug("Combined 18 - par: %s", par_vals)
        logger.debug("Combined 18 - score: %s", score_vals)

        # Build all 18 holes
        for i in range(18):
            par = par_vals[i] if isinstance(par_vals[i], int) else 3
            score = score_vals[i] if isinstance(score_vals[i], int) else None

            scorecard.holes.append(HoleScore(
                hole_number=i + 1,
                par=par,
                score=score,
            ))

        # Apply course database corrections
        garmin_tee = scorecard.garmin_tee_name or ""
        correct_pars = get_correct_pars(scorecard.course_name)
        if correct_pars:
            logger.info("Applying corrected pars from course database for '%s'",
                        scorecard.course_name)
            for hole in scorecard.holes:
                # Use modulo so 9-hole course pars wrap to cover holes 10-18
                hole.par = correct_pars[(hole.hole_number - 1) % len(correct_pars)]

        # Apply tee box mapping and GHIN name (independent of par corrections)
        tee_box = get_tee_box(scorecard.course_name, garmin_tee)
        if tee_box:
            scorecard.tee_name = tee_box

        ghin_name = get_ghin_name(scorecard.course_name)
        if ghin_name:
            logger.info("Renaming '%s' -> '%s'", scorecard.course_name, ghin_name)
            scorecard.course_name = ghin_name

        if not correct_pars and not ghin_name:
            logger.info("No course database entry for '%s' — using Garmin pars",
                        scorecard.course_name)

        # Drop partial nine if one side is complete and the other isn't
        scorecard.drop_incomplete_nine()

        scorecard.compute_stats()
        return scorecard

    def close(self) -> None:
        if self._driver:
            self._driver.quit()
            self._driver = None
