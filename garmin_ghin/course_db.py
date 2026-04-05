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

    Matching is case-insensitive.
    """
    db = _load_db()
    normalized = garmin_name.strip().lower()
    for course in db["courses"]:
        if course["garmin_name"].strip().lower() == normalized:
            return course
    return None


def get_correct_pars(garmin_name: str) -> Optional[list[int]]:
    """Return the correct hole-by-hole pars for a course.

    Pars are course-level (not tee-dependent).
    Returns None if the course is not in the database.
    """
    course = find_course(garmin_name)
    if not course:
        return None
    return course.get("pars")


def get_tee_box(garmin_name: str, garmin_tee_name: str) -> Optional[str]:
    """Map Garmin's tee name to the actual tee box (e.g. 'T3', 'T4').

    For Desert Mountain No 7:
      Men's Tees -> T3
      Women's Tees -> T4
    """
    course = find_course(garmin_name)
    if not course:
        return None
    tees = course.get("tees", {})
    for name, data in tees.items():
        if name.strip().lower() == garmin_tee_name.strip().lower():
            return data.get("tee_box")
    return None


def get_tee_rating(garmin_name: str, garmin_tee_name: str) -> tuple[Optional[float], Optional[int]]:
    """Return (rating, slope) for a course/tee combo, for GHIN posting."""
    course = find_course(garmin_name)
    if not course:
        return None, None
    tees = course.get("tees", {})
    for name, data in tees.items():
        if name.strip().lower() == garmin_tee_name.strip().lower():
            return data.get("rating"), data.get("slope")
    return None, None


def get_ghin_name(garmin_name: str) -> Optional[str]:
    """Return the GHIN-compatible course name, if known."""
    course = find_course(garmin_name)
    return course["ghin_name"] if course else None
