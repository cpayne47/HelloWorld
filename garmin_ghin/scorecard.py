"""Scorecard data model and reconstruction logic."""

from dataclasses import dataclass, field
from datetime import date
from typing import Optional


@dataclass
class HoleScore:
    hole_number: int  # 1-18
    par: int
    score: Optional[int] = None  # None = not played
    putts: Optional[int] = None
    fairway_hit: Optional[bool] = None
    gir: Optional[bool] = None
    penalties: int = 0

    @property
    def played(self) -> bool:
        return self.score is not None

    @property
    def score_vs_par(self) -> Optional[int]:
        if self.score is None:
            return None
        return self.score - self.par


@dataclass
class Scorecard:
    """Reconstructed scorecard from a Garmin golf activity.

    Always stored as 18 holes. Unplayed holes have score=None.
    """

    garmin_activity_id: str
    course_name: str
    date_played: date
    holes: list[HoleScore] = field(default_factory=list)
    tee_name: Optional[str] = None
    garmin_tee_name: Optional[str] = None  # Original Garmin tee name before mapping

    # Summary stats from Garmin
    eagles_or_better: int = 0
    birdies: int = 0
    pars: int = 0
    bogeys: int = 0
    double_bogeys_or_worse: int = 0
    fairways_hit: Optional[str] = None  # e.g. "0/0" or "7/14"
    gir_summary: Optional[str] = None   # e.g. "6/9"

    @property
    def played_holes(self) -> list[HoleScore]:
        return [h for h in self.holes if h.played]

    @property
    def num_holes_played(self) -> int:
        return len(self.played_holes)

    @property
    def nine_played(self) -> Optional[str]:
        """Return 'front', 'back', 'both', or None."""
        played = {h.hole_number for h in self.played_holes}
        front = played & set(range(1, 10))
        back = played & set(range(10, 19))
        if front and back:
            return "both"
        elif front:
            return "front"
        elif back:
            return "back"
        return None

    @property
    def computed_total(self) -> int:
        return sum(h.score for h in self.played_holes)

    @property
    def computed_par(self) -> int:
        return sum(h.par for h in self.played_holes)

    @property
    def total_putts(self) -> Optional[int]:
        putts = [h.putts for h in self.played_holes if h.putts is not None]
        return sum(putts) if putts else None

    def ensure_18_holes(self, course_pars: Optional[list[int]] = None) -> None:
        """Ensure the scorecard has entries for all 18 holes.

        Unplayed holes get score=None. If course_pars is provided (length 9 or 18),
        it's used for par values; otherwise defaults to par 3.
        """
        existing = {h.hole_number: h for h in self.holes}
        full = []
        for n in range(1, 19):
            if n in existing:
                full.append(existing[n])
            else:
                # Determine par for this hole
                if course_pars:
                    if len(course_pars) == 18:
                        par = course_pars[n - 1]
                    elif len(course_pars) == 9:
                        par = course_pars[(n - 1) % 9]
                    else:
                        par = 3
                else:
                    par = 3
                full.append(HoleScore(hole_number=n, par=par, score=None))
        self.holes = full

    def compute_stats(self) -> None:
        """Recompute scoring stats from hole data."""
        self.eagles_or_better = 0
        self.birdies = 0
        self.pars = 0
        self.bogeys = 0
        self.double_bogeys_or_worse = 0
        for h in self.played_holes:
            diff = h.score - h.par
            if diff <= -2:
                self.eagles_or_better += 1
            elif diff == -1:
                self.birdies += 1
            elif diff == 0:
                self.pars += 1
            elif diff == 1:
                self.bogeys += 1
            else:
                self.double_bogeys_or_worse += 1

        gir_count = sum(1 for h in self.played_holes if h.gir is True)
        gir_total = sum(1 for h in self.played_holes if h.gir is not None)
        if gir_total:
            self.gir_summary = f"{gir_count}/{gir_total}"

    def validate(self) -> list[str]:
        """Return a list of issues found with the scorecard."""
        issues = []
        played = self.played_holes
        if not played:
            issues.append("No hole data found")
            return issues

        if self.num_holes_played not in (9, 18):
            issues.append(f"Unexpected number of holes played: {self.num_holes_played}")

        for h in played:
            if h.score < 1:
                issues.append(f"Hole {h.hole_number}: invalid score {h.score}")
            if h.par not in (3, 4, 5, 6):
                issues.append(f"Hole {h.hole_number}: unusual par {h.par}")

        return issues

    def summary(self) -> str:
        """Human-readable summary with full 18-hole layout."""
        played = self.played_holes
        vs_par = self.computed_total - self.computed_par if played else 0

        nine = self.nine_played
        nine_label = {"front": "Front 9", "back": "Back 9", "both": "18 holes"}.get(nine, "")

        lines = [
            f"Course: {self.course_name}",
            f"Date:   {self.date_played.isoformat()}",
            f"Played: {self.num_holes_played} holes ({nine_label})",
            f"Score:  {self.computed_total} ({vs_par:+d} vs par {self.computed_par})",
        ]
        if self.tee_name:
            lines.append(f"Tees:   {self.tee_name}")
        if self.total_putts is not None:
            lines.append(f"Putts:  {self.total_putts}")

        # Scoring distribution
        lines.append("")
        dist_parts = []
        if self.eagles_or_better:
            dist_parts.append(f"Eagles+: {self.eagles_or_better}")
        dist_parts.append(f"Birdies: {self.birdies}")
        dist_parts.append(f"Pars: {self.pars}")
        dist_parts.append(f"Bogeys: {self.bogeys}")
        if self.double_bogeys_or_worse:
            dist_parts.append(f"Dbl+: {self.double_bogeys_or_worse}")
        lines.append("  ".join(dist_parts))

        if self.fairways_hit:
            lines.append(f"Fairways: {self.fairways_hit}  GIR: {self.gir_summary or '-'}")
        elif self.gir_summary:
            lines.append(f"GIR: {self.gir_summary}")

        # Full 18-hole scorecard
        lines.append("")
        lines.append("       " + "".join(f"{n:>4}" for n in range(1, 10)) + "   Out")
        lines.append("  Par  " + "".join(f"{self.holes[n-1].par:>4}" for n in range(1, 10))
                      + f"  {sum(self.holes[n-1].par for n in range(1, 10)):>4}")

        front_scores = []
        front_total = 0
        for n in range(1, 10):
            h = self.holes[n - 1]
            if h.played:
                front_scores.append(f"{h.score:>4}")
                front_total += h.score
            else:
                front_scores.append("   -")
        lines.append("Score  " + "".join(front_scores)
                      + (f"  {front_total:>4}" if any(self.holes[n-1].played for n in range(1, 10)) else "     -"))

        front_gir = []
        for n in range(1, 10):
            h = self.holes[n - 1]
            if not h.played:
                front_gir.append("   -")
            elif h.gir is True:
                front_gir.append("   Y")
            elif h.gir is False:
                front_gir.append("   X")
            else:
                front_gir.append("    ")
        lines.append("  GIR  " + "".join(front_gir))

        front_putts = []
        front_putts_total = 0
        has_front_putts = False
        for n in range(1, 10):
            h = self.holes[n - 1]
            if h.played and h.putts is not None:
                front_putts.append(f"{h.putts:>4}")
                front_putts_total += h.putts
                has_front_putts = True
            elif h.played:
                front_putts.append("    ")
            else:
                front_putts.append("   -")
        lines.append("Putts  " + "".join(front_putts)
                      + (f"  {front_putts_total:>4}" if has_front_putts else "     -"))

        # Back 9
        lines.append("")
        lines.append("       " + "".join(f"{n:>4}" for n in range(10, 19)) + "    In  Tot")
        lines.append("  Par  " + "".join(f"{self.holes[n-1].par:>4}" for n in range(10, 19))
                      + f"  {sum(self.holes[n-1].par for n in range(10, 19)):>4}"
                      + f"  {sum(h.par for h in self.holes):>4}")

        back_scores = []
        back_total = 0
        for n in range(10, 19):
            h = self.holes[n - 1]
            if h.played:
                back_scores.append(f"{h.score:>4}")
                back_total += h.score
            else:
                back_scores.append("   -")
        total_score = front_total + back_total
        lines.append("Score  " + "".join(back_scores)
                      + (f"  {back_total:>4}" if any(self.holes[n-1].played for n in range(10, 19)) else "     -")
                      + f"  {total_score:>4}")

        back_gir = []
        for n in range(10, 19):
            h = self.holes[n - 1]
            if not h.played:
                back_gir.append("   -")
            elif h.gir is True:
                back_gir.append("   Y")
            elif h.gir is False:
                back_gir.append("   X")
            else:
                back_gir.append("    ")
        lines.append("  GIR  " + "".join(back_gir))

        back_putts = []
        back_putts_total = 0
        has_back_putts = False
        for n in range(10, 19):
            h = self.holes[n - 1]
            if h.played and h.putts is not None:
                back_putts.append(f"{h.putts:>4}")
                back_putts_total += h.putts
                has_back_putts = True
            elif h.played:
                back_putts.append("    ")
            else:
                back_putts.append("   -")
        total_putts_display = front_putts_total + back_putts_total
        lines.append("Putts  " + "".join(back_putts)
                      + (f"  {back_putts_total:>4}" if has_back_putts else "     -")
                      + (f"  {total_putts_display:>4}" if has_front_putts or has_back_putts else "     -"))

        issues = self.validate()
        if issues:
            lines.append("")
            lines.append("Issues:")
            for issue in issues:
                lines.append(f"  - {issue}")

        return "\n".join(lines)
