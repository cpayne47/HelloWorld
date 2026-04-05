"""CLI for testing Garmin golf scorecard extraction."""

import argparse
import logging
import sys

from .config import load_config


def cmd_login(args) -> None:
    """Open browser to log into Garmin and capture session tokens."""
    from .browser_login import browser_login
    from .garmin_client import TOKEN_DIR

    config = load_config()
    browser_login(config.garmin.email, config.garmin.password, str(TOKEN_DIR))


def cmd_scorecard(args) -> None:
    """Fetch and display the most recent golf scorecard."""
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
    subparsers.add_parser("login", help="Log into Garmin via browser (required first time)")

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
