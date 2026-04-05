"""Scorecard data model and reconstruction logic."""

from dataclasses import dataclass, field
from datetime import date
from typing import Optional


@dataclass
class HoleScore:
    hole_number: int
    par: int
    score: int
    putts: Optional[int] = None
    fairway_hit: Optional[bool] = None
    gir: Optional[bool] = None
    penalties: int = 0


@dataclass
class Scorecard:
    """Reconstructed scorecard from a Garmin golf activity."""

    garmin_activity_id: int
    course_name: str
    date_played: date
    holes: list[HoleScore] = field(default_factory=list)
    total_score: Optional[int] = None
    total_putts: Optional[int] = None
    score_vs_par: Optional[int] = None
    tee_name: Optional[str] = None

    @property
    def num_holes(self) -> int:
        return len(self.holes)

    @property
    def is_18_holes(self) -> bool:
        return self.num_holes == 18

    @property
    def computed_total(self) -> int:
        return sum(h.score for h in self.holes)

    @property
    def computed_par(self) -> int:
        return sum(h.par for h in self.holes)

    def validate(self) -> list[str]:
        """Return a list of issues found with the scorecard."""
        issues = []
        if not self.holes:
            issues.append("No hole data found")
            return issues

        if self.num_holes not in (9, 18):
            issues.append(f"Unexpected number of holes: {self.num_holes}")

        if self.total_score and self.total_score != self.computed_total:
            issues.append(
                f"Total score mismatch: reported {self.total_score}, "
                f"computed {self.computed_total}"
            )

        for h in self.holes:
            if h.score < 1:
                issues.append(f"Hole {h.hole_number}: invalid score {h.score}")
            if h.par not in (3, 4, 5, 6):
                issues.append(f"Hole {h.hole_number}: unusual par {h.par}")

        return issues

    def summary(self) -> str:
        """Human-readable summary for confirmation emails."""
        vs_par = self.score_vs_par if self.score_vs_par is not None else (
            self.computed_total - self.computed_par if self.holes else None
        )
        score_line = f"Score:  {self.computed_total}"
        if vs_par is not None:
            score_line += f" ({vs_par:+d} vs par {self.computed_par})"
        else:
            score_line += f" (par {self.computed_par})"

        lines = [
            f"Course: {self.course_name}",
            f"Date:   {self.date_played.isoformat()}",
            f"Holes:  {self.num_holes}",
            score_line,
        ]
        if self.tee_name:
            lines.append(f"Tees:   {self.tee_name}")
        if self.total_putts:
            lines.append(f"Putts:  {self.total_putts}")

        lines.append("")
        lines.append("Hole  Par  Score  Putts")
        lines.append("-" * 28)
        for h in self.holes:
            putts_str = str(h.putts) if h.putts is not None else "-"
            lines.append(f"{h.hole_number:>4}  {h.par:>3}  {h.score:>5}  {putts_str:>5}")

        issues = self.validate()
        if issues:
            lines.append("")
            lines.append("⚠ Issues detected:")
            for issue in issues:
                lines.append(f"  - {issue}")

        return "\n".join(lines)
