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
                    db_id = save_scorecard(scorecard)
                    nine = scorecard.nine_played
                    nine_label = {"front": "F9", "back": "B9", "both": "18"}.get(nine, "?")
                    vs_par = scorecard.computed_total - scorecard.computed_par
                    print(f"    -> {scorecard.course_name} | {scorecard.date_played} | "
                          f"{nine_label} | Score: {scorecard.computed_total} ({vs_par:+d}) | "
                          f"Putts: {scorecard.total_putts or '-'} | "
                          f"Saved (id={db_id})")
                    saved += 1
                else:
                    print(f"    -> 18 holes parsed but all dashes — no scores recorded, skipping")
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

    # fix-names subcommand
    subparsers.add_parser("fix-names",
                          help="Apply course name mappings from courses.json to existing DB records")

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
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
