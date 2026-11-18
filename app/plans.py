"""Training plans: phases with a weekly template, plus changes to single days.

A day's plan is, in order of precedence:
  0. race day (settings.race_day) is always the race; nothing is planned after it, else
  1. a change saved for that exact day (PlannedRun), else
  2. the weekday's entry in the weekly template of the phase in effect that day
     (the phase with the latest start on or before the day), else
  3. nothing (before the first phase, or the template says "unplanned").

Plans are worked out on request, never copied into each day, so editing a template updates
every day that hasn't been changed by hand.
"""

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import PlannedRun, PlanPhase, PlanTemplateDay

KINDS = ("run", "rest", "unplanned")
LENGTHS = ("short", "medium", "long")
EFFORTS = ("easy", "race_pace", "tempo")  # labels: Easy · Race pace · Tempo


def _plan(kind, length, effort, miles, note, source) -> dict | None:
    if kind == "unplanned":
        return None
    out = {"kind": kind, "source": source, "note": note}
    if kind == "run":
        out.update(length=length, effort=effort, distance_mi=miles)
    return out


def plans_between(session: Session, first: date, last: date) -> dict[date, dict | None]:
    """Each day's effective plan from first to last (inclusive). Value None = nothing planned.

    Each plan carries "source": "day" (changed for that day) or "template" (from the weekly plan).
    """
    phases = session.scalars(select(PlanPhase).where(PlanPhase.starts_on <= last).order_by(PlanPhase.starts_on)).all()
    templates: dict[int, dict[int, PlanTemplateDay]] = {}
    if phases:
        for t in session.scalars(select(PlanTemplateDay).where(PlanTemplateDay.phase_id.in_([p.id for p in phases]))):
            templates.setdefault(t.phase_id, {})[t.weekday] = t
    overrides = {p.day: p for p in session.scalars(select(PlannedRun).where(PlannedRun.day.between(first, last)))}

    settings = get_settings()
    out: dict[date, dict | None] = {}
    day = first
    phase_i = -1
    while day <= last:
        while phase_i + 1 < len(phases) and phases[phase_i + 1].starts_on <= day:
            phase_i += 1
        o = overrides.get(day)
        if day == settings.race_day:
            out[day] = {"kind": "race", "source": "race", "note": settings.race_name,
                        "length": None, "effort": None, "distance_mi": settings.race_distance_mi}
        elif day > settings.race_day:
            out[day] = None  # training plans end at the race
        elif o is not None:
            out[day] = _plan(o.kind, o.length, o.effort, o.distance_mi, o.note, "day")
        elif phase_i >= 0 and (t := templates.get(phases[phase_i].id, {}).get(day.weekday())) is not None:
            out[day] = _plan(t.kind, t.length, t.effort, t.default_miles, None, "template")
        else:
            out[day] = None
        day = date.fromordinal(day.toordinal() + 1)
    return out


def phase_dict(session: Session, phase: PlanPhase) -> dict:
    days = {t.weekday: t for t in session.scalars(select(PlanTemplateDay).where(PlanTemplateDay.phase_id == phase.id))}
    return {
        "starts_on": phase.starts_on.isoformat(),
        "name": phase.name,
        "days": [
            {"weekday": wd, "kind": days[wd].kind if wd in days else "unplanned",
             "length": days[wd].length if wd in days else None,
             "effort": days[wd].effort if wd in days else None,
             "default_miles": days[wd].default_miles if wd in days else None}
            for wd in range(7)
        ],
    }
