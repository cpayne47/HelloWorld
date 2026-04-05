"""CLI for Garmin golf scorecard extraction."""

import argparse
import logging
import sys


def cmd_login(args) -> None:
    """Import Garmin session cookies."""
    from .auth import import_from_chrome, save_cookie_string

    if args.chrome:
        import_from_chrome()
    elif args.file:
        from pathlib import Path
        cookie_string = Path(args.file).read_text().strip()
        if not cookie_string:
            print("File is empty.")
            sys.exit(1)
        # Strip "Cookie: " prefix if present
        if cookie_string.lower().startswith("cookie:"):
            cookie_string = cookie_string.split(":", 1)[1].strip()
        save_cookie_string(cookie_string)
    elif args.paste:
        print("Paste your Cookie header value from browser dev tools,")
        print("then press Enter:\n")
        cookie_string = input("> ").strip()
        if not cookie_string:
            print("No cookies provided.")
            sys.exit(1)
        save_cookie_string(cookie_string)
    else:
        print("Specify a method:\n")
        print("  python -m garmin_ghin.cli login --file cookie.txt  (read from file)")
        print("  python -m garmin_ghin.cli login --chrome            (auto-read from Chrome)")
        print("  python -m garmin_ghin.cli login --paste             (paste interactively)")


def cmd_scorecard(args) -> None:
    """Fetch and display the most recent golf scorecard."""
    from .config import load_config
    from .garmin_client import GarminClient

    config = load_config()
    client = GarminClient(config.garmin)

    print("Connecting to Garmin Connect...")
    activities = client.get_recent_golf_rounds(days_back=args.days)

    if not activities:
        print(f"No golf rounds found in the last {args.days} days.")
        sys.exit(0)

    print(f"Found {len(activities)} golf round(s).\n")

    latest = activities[0]
    activity_id = latest.get("activityId")
    print(f"Fetching scorecard for activity {activity_id}...\n")

    scorecard = client.get_scorecard(activity_id)
    print(scorecard.summary())

    issues = scorecard.validate()
    if not issues:
        print("\nScorecard looks good!")


def main() -> None:
    parser = argparse.ArgumentParser(description="Garmin golf scorecard tools")
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Enable debug logging",
    )
    subparsers = parser.add_subparsers(dest="command")

    # login subcommand
    login_parser = subparsers.add_parser("login", help="Import Garmin session cookies")
    login_parser.add_argument("--file", type=str, help="Read cookies from a text file")
    login_parser.add_argument("--paste", action="store_true", help="Paste cookie string interactively")
    login_parser.add_argument("--chrome", action="store_true", help="Auto-read from Chrome browser")

    # scorecard subcommand
    sc_parser = subparsers.add_parser("scorecard", help="Fetch and display most recent scorecard")
    sc_parser.add_argument(
        "-n", "--days", type=int, default=30,
        help="Look back this many days for rounds (default: 30)",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if args.command == "login":
        cmd_login(args)
    elif args.command == "scorecard":
        cmd_scorecard(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
