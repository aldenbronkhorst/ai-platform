"""Aggregate per-scenario results into one pass-rate scorecard."""
from __future__ import annotations

from score import ScenarioResult

AXES = ["routing", "read_only_first", "robustness", "conclusion"]
_LABEL = {
    "routing": "Routing",
    "read_only_first": "Read-only-first",
    "robustness": "Robustness",
    "conclusion": "Conclusion",
}


def build_scorecard(results: list[ScenarioResult]) -> dict:
    per_axis: dict[str, dict] = {}
    for axis in AXES:
        applicable = []  # True/False/None for scenarios where the axis was present
        for r in results:
            a = r.by_axis().get(axis)
            if a is not None:
                applicable.append(a.passed)
        scored = [v for v in applicable if v is not None]
        per_axis[axis] = {
            "pass": sum(1 for v in scored if v),
            "scored": len(scored),
            "skipped": len(applicable) - len(scored),
        }
    return {
        "scenarios": len(results),
        "overall_pass": sum(1 for r in results if r.passed()),
        "per_axis": per_axis,
    }


def _rate(pass_n: int, scored_n: int) -> str:
    if scored_n == 0:
        return "  n/a"
    return f"{100 * pass_n / scored_n:5.0f}%"


def render_scorecard(results: list[ScenarioResult], card: dict | None = None) -> str:
    card = card or build_scorecard(results)
    lines: list[str] = []
    lines.append("=" * 60)
    lines.append("  AI-platform troubleshooting eval scorecard")
    lines.append("=" * 60)
    n = card["scenarios"]
    op = card["overall_pass"]
    lines.append(f"  Overall: {op}/{n} scenarios passed"
                 f"{'' if n == 0 else f'  ({100 * op / n:.0f}%)'}")
    lines.append("-" * 60)
    lines.append(f"  {'Axis':<18}{'pass':>8}{'scored':>9}{'rate':>9}{'skipped':>10}")
    for axis in AXES:
        s = card["per_axis"][axis]
        lines.append(
            f"  {_LABEL[axis]:<18}{s['pass']:>8}{s['scored']:>9}"
            f"{_rate(s['pass'], s['scored']):>9}{s['skipped']:>10}"
        )
    lines.append("-" * 60)
    lines.append("  (skipped = axis not applicable / needed a judge that was off /")
    lines.append("   could not be decided deterministically - never counts as a fail)")
    lines.append("=" * 60)
    # per-scenario failures for quick triage
    fails = [r for r in results if not r.passed()]
    if fails:
        lines.append("  Failing scenarios:")
        for r in fails:
            bad = [a for a in r.axes if a.passed is False]
            for a in bad:
                lines.append(f"    - {r.scenario_id}: {a.axis} - {a.detail}")
    return "\n".join(lines)
