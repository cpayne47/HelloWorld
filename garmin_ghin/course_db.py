"""Course database for correcting Garmin par data and mapping to GHIN."""

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent / "courses.json"


def _load_db() -> dict:
    if not DB_PATH.exists():
        return {"courses": []}
    with open(DB_PATH) as f:
        return json.load(f)


def find_course(garmin_name: str) -> Optional[dict]:
    """Look up a course by the name Garmin reports.

    Matching is case-insensitive and tolerant of minor differences
    (e.g. "Desert Mountain - No 7" matches "Desert Mountain - No 7").
    """
    db = _load_db()
    normalized = garmin_name.strip().lower()
    for course in db["courses"]:
        if course["garmin_name"].strip().lower() == normalized:
            return course
    return None


def get_correct_pars(garmin_name: str, tee_name: str) -> Optional[list[int]]:
    """Return the correct hole-by-hole pars for a course/tee combo.

    Returns None if the course or tee is not in the database.
    """
    course = find_course(garmin_name)
    if not course:
        return None

    tees = course.get("tees", {})

    # Direct match
    if tee_name in tees:
        return tees[tee_name]["pars"]

    # Case-insensitive match
    for name, data in tees.items():
        if name.strip().lower() == tee_name.strip().lower():
            return data["pars"]

    # Fall back to default tee
    default = course.get("default_tee")
    if default and default in tees:
        logger.info("Tee '%s' not found, using default '%s'", tee_name, default)
        return tees[default]["pars"]

    return None


def get_ghin_name(garmin_name: str) -> Optional[str]:
    """Return the GHIN-compatible course name, if known."""
    course = find_course(garmin_name)
    return course["ghin_name"] if course else None


def get_tee_label(garmin_name: str, tee_name: str) -> Optional[str]:
    """Return a human-readable label for the tee (e.g. 'Par 3s', 'Par 4s')."""
    course = find_course(garmin_name)
    if not course:
        return None
    tees = course.get("tees", {})
    for name, data in tees.items():
        if name.strip().lower() == tee_name.strip().lower():
            return data.get("label")
    return None
