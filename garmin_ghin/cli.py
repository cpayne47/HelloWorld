"""CLI for Garmin golf scorecard extraction."""

import argparse
import logging
import sys
import time


def cmd_scorecard(args) -> None:
    """Fetch and display the most recent golf scorecard."""
    from .config import load_config
    from .garmin_client import GarminClient
    from .scorecard_db import save_scorecard

    config = load_config()
    client = GarminClient(config.garmin)

    try:
        print("Launching Chrome and connecting to Garmin Connect...")
        activities = client.get_recent_golf_rounds(days_back=args.days)

        if not activities:
            print(f"No golf rounds found in the last {args.days} days.")
            sys.exit(0)

        print(f"Found {len(activities)} golf round(s).\n")

        for i, act in enumerate(activities[:5]):
            text = act.get("text", act.get("activityName", ""))
            stats = act.get("stats", [])
            stats_str = f"  [{', '.join(stats)}]" if stats else ""
            print(f"  [{i+1}] {text[:60]}{stats_str}")

        # Fetch the most recent
        latest = activities[0]
        sc_id = latest.get("scorecardId", latest.get("activityId"))
        href = latest.get("href", "")

        if sc_id or href:
            print(f"\nFetching scorecard details...\n")
            scorecard = client.get_scorecard(sc_id or "", href=href)
            print(scorecard.summary())

            # Save to database
            db_id = save_scorecard(scorecard)
            print(f"\nSaved to database (id={db_id})")

            issues = scorecard.validate()
            if not issues:
                print("Scorecard looks good!")
            else:
                print("\nValidation issues:")
                for issue in issues:
                    print(f"  - {issue}")
        else:
            print("\nCould not determine scorecard ID or URL from first result.")
            print(f"Raw data: {latest}")

    finally:
        client.close()


def cmd_bulk(args) -> None:
    """Extract all available scorecards from Garmin and save to database."""
    from .config import load_config
    from .garmin_client import GarminClient
    from .scorecard_db import save_scorecard, scorecard_exists

    config = load_config()
    client = GarminClient(config.garmin)

    try:
        print("Launching Chrome and connecting to Garmin Connect...")
        print("Fetching all available scorecards (scrolling to load history)...\n")

        activities = client.get_recent_golf_rounds(days_back=args.days, scroll_all=True)

        if not activities:
            print("No golf rounds found.")
            sys.exit(0)

        print(f"Found {len(activities)} scorecard(s) on the list page.\n")

        saved = 0
        skipped = 0
        errors = 0

        for i, act in enumerate(activities):
            sc_id = act.get("scorecardId", "")
            href = act.get("href", "")
            text = act.get("text", "")

            # Check if already in database (skip unless --force)
            if sc_id and scorecard_exists(sc_id) and not args.force:
                print(f"  [{i+1}/{len(activities)}] {text[:50]} — already saved, skipping")
                skipped += 1
                continue

            print(f"  [{i+1}/{len(activities)}] {text[:50]} — fetching...")

            try:
                scorecard = client.get_scorecard(sc_id, href=href)

                if not scorecard.holes:
                    print(f"    -> PARSER ERROR: No holes extracted. Stopping.")
                    print(f"    -> Debug files saved. Check debug_scorecard_text.txt")
                    errors += 1
                    break

                if scorecard.played_holes:
                    nine = scorecard.nine_played
                    nine_label = {"front": "F9", "back": "B9", "both": "18"}.get(nine, "?")
                    vs_par = scorecard.computed_total - scorecard.computed_par

                    stepping = args.step and (i + 1) >= args.start
                    if stepping:
                        # Interactive debug mode: show full scorecard
                        print()
                        print("=" * 60)
                        print(scorecard.summary())
                        print("=" * 60)
                        resp = input("\n[p]roceed  [s]kip  [q]uit: ").strip().lower()
                        if resp in ("q", "quit"):
                            print("Stopping.")
                            break
                        elif resp in ("s", "skip"):
                            print("    -> Skipped (not saved)")
                            continue

                    db_id = save_scorecard(scorecard)
                    print(f"    -> {scorecard.course_name} | {scorecard.date_played} | "
                          f"{nine_label} | Score: {scorecard.computed_total} ({vs_par:+d}) | "
                          f"Saved (id={db_id})")
                    saved += 1
                else:
                    print(f"    -> 18 holes parsed but all dashes — no scores recorded")
                    stepping = args.step and (i + 1) >= args.start
                    if stepping:
                        print()
                        print("=" * 60)
                        print(scorecard.summary())
                        print("=" * 60)
                        resp = input("\n[p]roceed  [q]uit: ").strip().lower()
                        if resp in ("q", "quit"):
                            break
                    errors += 1

                # Brief pause between fetches to be polite
                if i < len(activities) - 1:
                    time.sleep(2)

            except Exception as e:
                print(f"    -> Error: {e}")
                errors += 1
                logging.getLogger(__name__).debug("Error fetching scorecard", exc_info=True)

        print(f"\nDone! Saved: {saved}, Skipped (already saved): {skipped}, Errors: {errors}")

    finally:
        client.close()


def cmd_history(args) -> None:
    """Show scorecards stored in the local database."""
    from .scorecard_db import list_scorecards, load_scorecard

    scorecards = list_scorecards(limit=args.limit)

    if not scorecards:
        print("No scorecards in database yet.")
        print("Run 'python -m garmin_ghin.cli scorecard' or 'bulk' to fetch some.")
        return

    if args.id:
        # Show detailed view of a specific scorecard
        sc = load_scorecard(args.id)
        if not sc:
            print(f"No scorecard found with id={args.id}")
            return
        print(sc.summary())
        return

    print(f"{'ID':>4}  {'Date':<12} {'Course':<30} {'Par':>4} {'Score':>5} {'Holes':<5}")
    print("-" * 68)

    for sc in scorecards:
        nine = sc.get("nine_played", "")
        nine_label = {"front": "Front 9", "back": "Back 9", "both": "18"}.get(nine, "?")
        total = sc.get("total_score")
        total_str = str(total) if total is not None else "-"
        # Par for holes played
        par = sc.get("total_score", 0) - sc.get("score_vs_par", 0) if sc.get("score_vs_par") is not None else None
        par_str = str(par) if par is not None else "-"

        print(f"{sc['id']:>4}  {sc['date_played']:<12} {sc['course_name']:<30} {par_str:>4} {total_str:>5} {nine_label:<5}")

    print(f"\n{len(scorecards)} round(s). Use 'history --id N' for full scorecard.")


def cmd_sync(args) -> None:
    """Check Garmin for new rounds and add any to the database.

    Walks down the scorecard list from newest to oldest. For each round,
    fetches the full scorecard, then checks the database by date + course + score.
    Stops as soon as it finds a round that's already recorded.
    """
    from .config import load_config
    from .garmin_client import GarminClient
    from .scorecard_db import round_exists, save_scorecard

    config = load_config()
    client = GarminClient(config.garmin)

    try:
        print("Checking Garmin Connect for new rounds...")
        activities = client.get_recent_golf_rounds(days_back=30, scroll_all=False)

        if not activities:
            print("No rounds found on Garmin.")
            return

        print(f"Found {len(activities)} round(s) on Garmin. Checking for new ones...\n")

        saved = 0
        for i, act in enumerate(activities):
            sc_id = act.get("scorecardId", "")
            href = act.get("href", "")
            text = act.get("text", "")

            print(f"  [{i+1}] {text[:50]} — fetching...")

            try:
                scorecard = client.get_scorecard(sc_id, href=href)

                if not scorecard.holes:
                    print(f"    -> PARSER ERROR: No holes extracted.")
                    print(f"    -> Debug: check debug_scorecard_text.txt")
                    continue

                if not scorecard.played_holes:
                    print(f"    -> No scores recorded, skipping")
                    continue

                # Check if this round is already in the database
                if round_exists(
                    scorecard.date_played.isoformat(),
                    scorecard.course_name,
                    scorecard.computed_total,
                ):
                    print(f"    -> {scorecard.course_name} | {scorecard.date_played} | "
                          f"Score: {scorecard.computed_total} — already in database. Done.")
                    break

                # New round — save it
                db_id = save_scorecard(scorecard)
                nine = scorecard.nine_played
                nine_label = {"front": "F9", "back": "B9", "both": "18"}.get(nine, "?")
                vs_par = scorecard.computed_total - scorecard.computed_par
                print(f"    -> NEW: {scorecard.course_name} | {scorecard.date_played} | "
                      f"{nine_label} | Score: {scorecard.computed_total} ({vs_par:+d}) | "
                      f"Tees: {scorecard.garmin_tee_name or '-'} | Saved (id={db_id})")
                saved += 1

                if i < len(activities) - 1:
                    time.sleep(2)

            except Exception as e:
                print(f"    -> Error: {e}")
                logging.getLogger(__name__).debug("Error fetching scorecard", exc_info=True)

        if saved:
            print(f"\nAdded {saved} new round(s) to database.")
        else:
            print("\nNo new rounds.")

    finally:
        client.close()


def cmd_ghin_login(args) -> None:
    """Test GHIN login — just log in and report status."""
    from .config import load_config
    from .ghin_client import GHINClient

    config = load_config()
    if not config.ghin:
        print("GHIN credentials not configured. Add GHIN_NUMBER and GHIN_PASSWORD to .env")
        sys.exit(1)

    client = GHINClient(config.ghin)
    try:
        client.login()
        print("\nGHIN login test complete. Page text (first 300 chars):")
        print(client.get_page_text()[:300])
    finally:
        client.close()


def cmd_ghin_post(args) -> None:
    """Post a scorecard to GHIN (hole-by-hole).

    By default runs in dry-run mode: fills the form but does NOT click POST SCORE.
    """
    from .config import load_config
    from .ghin_client import GHINClient
    from .scorecard_db import load_scorecard, list_scorecards

    config = load_config()
    if not config.ghin:
        print("GHIN credentials not configured. Add GHIN_NUMBER and GHIN_PASSWORD to .env")
        sys.exit(1)

    # Load the scorecard
    if args.id:
        scorecard = load_scorecard(args.id)
        if not scorecard:
            print(f"No scorecard found with id={args.id}")
            sys.exit(1)
    else:
        # Default: most recent scorecard
        recent = list_scorecards(limit=1)
        if not recent:
            print("No scorecards in database. Run 'sync' or 'bulk' first.")
            sys.exit(1)
        scorecard = load_scorecard(recent[0]["id"])

    # Print what we're about to post
    print("=" * 60)
    print("SCORECARD TO POST TO GHIN")
    print("=" * 60)
    print(scorecard.summary())
    print("=" * 60)
    print()

    if not scorecard.played_holes:
        print("No played holes in this scorecard — nothing to post.")
        sys.exit(1)

    # Confirm before proceeding
    if not args.yes:
        resp = input("Proceed with GHIN form fill? [y/n]: ").strip().lower()
        if resp not in ("y", "yes"):
            print("Aborted.")
            return

    client = GHINClient(config.ghin)
    try:
        report = client.post_score(scorecard, dry_run=not args.post)

        # Print report
        print()
        print("=" * 60)
        print("GHIN POST SCORE REPORT")
        print("=" * 60)
        print(f"  Course:        {report['course']}")
        print(f"  Date:          {report['date']}")
        print(f"  Holes:         {report['holes_count']} ({report['nine_played']})")
        print(f"  Total Score:   {report['total_score']}")
        print(f"  Garmin Tee:    {report.get('garmin_tee', '-')}")
        print()

        sels = report.get("selections", {})
        print("  SELECTIONS MADE:")
        print(f"    Course:      {sels.get('course', 'NOT SET')}")
        print(f"    Holes:       {sels.get('holes', 'NOT SET')}")
        print(f"    Tees:        {sels.get('tee_selected', 'NOT SET')}")
        if sels.get("tee_options_available"):
            print(f"    Tee options:  {sels['tee_options_available']}")
        print(f"    Score Type:  {sels.get('score_type', 'NOT SET')}")
        print(f"    Date:        {sels.get('date', 'NOT SET')}")
        print()

        scores = sels.get("scores_entered", {})
        if scores:
            print(f"  SCORES ENTERED ({len(scores)} holes):")
            # Display in a grid
            front = [scores.get(h, "-") for h in range(1, 10)]
            back = [scores.get(h, "-") for h in range(10, 19)]
            print("    Front: " + "  ".join(f"{s:>3}" for s in front))
            print("    Back:  " + "  ".join(f"{s:>3}" for s in back))
            print(f"    Total: {sum(v for v in scores.values() if isinstance(v, int))}")

        print()
        status = report.get("status", "UNKNOWN")
        msg = report.get("message", "")
        print(f"  STATUS: {status}")
        if msg:
            print(f"  {msg}")

        issues = report.get("issues", [])
        if issues:
            print()
            print("  ISSUES / NOTES:")
            for issue in issues:
                print(f"    - {issue}")

        print()
        print("=" * 60)

        if status == "READY_FOR_REVIEW":
            print("\nReview the form in the browser.")
            print("When ready, you can:")
            print("  - Click POST SCORE manually in the browser")
            print("  - Or re-run with --post flag to auto-submit")
            input("\nPress Enter to close the browser...")

    finally:
        client.close()


def cmd_fix_names() -> None:
    """Apply course name mappings from courses.json to existing DB records."""
    from .course_db import _load_db
    from .scorecard_db import rename_course

    db = _load_db()
    total = 0
    for course in db.get("courses", []):
        garmin_name = course.get("garmin_name", "")
        ghin_name = course.get("ghin_name", "")
        if garmin_name and ghin_name and garmin_name != ghin_name:
            count = rename_course(garmin_name, ghin_name)
            if count:
                print(f"  Renamed '{garmin_name}' -> '{ghin_name}' ({count} records)")
                total += count

    if total:
        print(f"\nUpdated {total} record(s).")
    else:
        print("All course names already up to date.")


def cmd_tees(args) -> None:
    """Show tee info for all rounds in the database."""
    from .scorecard_db import list_scorecards

    scorecards = list_scorecards(limit=args.limit)
    if not scorecards:
        print("No scorecards in database.")
        return

    print(f"{'ID':>4}  {'Date':<12} {'Course':<32} {'Score':>5} {'Tees':<20} {'Garmin Tees'}")
    print("-" * 95)

    for sc in scorecards:
        total = sc.get("total_score")
        total_str = str(total) if total is not None else "-"
        tee = sc.get("tee_name") or "-"
        garmin_tee = sc.get("garmin_tee_name") or "-"
        print(f"{sc['id']:>4}  {sc['date_played']:<12} {sc['course_name']:<32} {total_str:>5} {tee:<20} {garmin_tee}")

    print(f"\n{len(scorecards)} round(s).")


def main() -> None:
    parser = argparse.ArgumentParser(description="Garmin golf scorecard tools")
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Enable debug logging",
    )
    subparsers = parser.add_subparsers(dest="command")

    # scorecard subcommand
    sc_parser = subparsers.add_parser("scorecard", help="Fetch and display most recent scorecard")
    sc_parser.add_argument(
        "-n", "--days", type=int, default=30,
        help="Look back this many days for rounds (default: 30)",
    )

    # bulk subcommand
    bulk_parser = subparsers.add_parser("bulk", help="Extract all available scorecards from Garmin")
    bulk_parser.add_argument(
        "-n", "--days", type=int, default=3650,
        help="Look back this many days (default: 3650 = ~10 years)",
    )
    bulk_parser.add_argument(
        "--force", action="store_true",
        help="Re-extract and overwrite all scorecards, even ones already saved",
    )
    bulk_parser.add_argument(
        "--step", action="store_true",
        help="Interactive debug mode: show full scorecard and wait before continuing",
    )
    bulk_parser.add_argument(
        "--start", type=int, default=1,
        help="Start stepping from this scorecard number (1-based). Earlier ones auto-save.",
    )

    # history subcommand
    hist_parser = subparsers.add_parser("history", help="Show scorecards from local database")
    hist_parser.add_argument(
        "--id", type=int, default=None,
        help="Show detailed view of a specific scorecard by ID",
    )
    hist_parser.add_argument(
        "--limit", type=int, default=50,
        help="Max scorecards to show (default: 50)",
    )

    # sync subcommand
    subparsers.add_parser("sync", help="Check Garmin for new rounds and add to database")

    # ghin-login subcommand
    subparsers.add_parser("ghin-login", help="Test GHIN login")

    # ghin-post subcommand
    gp_parser = subparsers.add_parser("ghin-post",
                                       help="Post a scorecard to GHIN (hole-by-hole)")
    gp_parser.add_argument(
        "--id", type=int, default=None,
        help="Database ID of scorecard to post (default: most recent)",
    )
    gp_parser.add_argument(
        "--post", action="store_true",
        help="Actually click POST SCORE (default is dry-run: fill form only)",
    )
    gp_parser.add_argument(
        "-y", "--yes", action="store_true",
        help="Skip confirmation prompt",
    )

    # fix-names subcommand
    subparsers.add_parser("fix-names",
                          help="Apply course name mappings from courses.json to existing DB records")

    # tees subcommand
    tees_parser = subparsers.add_parser("tees", help="Show tee info for all rounds")
    tees_parser.add_argument(
        "--limit", type=int, default=100,
        help="Max rounds to show (default: 100)",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if args.command == "scorecard":
        cmd_scorecard(args)
    elif args.command == "bulk":
        cmd_bulk(args)
    elif args.command == "history":
        cmd_history(args)
    elif args.command == "fix-names":
        cmd_fix_names()
    elif args.command == "tees":
        cmd_tees(args)
    elif args.command == "sync":
        cmd_sync(args)
    elif args.command == "ghin-login":
        cmd_ghin_login(args)
    elif args.command == "ghin-post":
        cmd_ghin_post(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
