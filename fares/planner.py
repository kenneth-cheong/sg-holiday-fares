"""Which holidays are worth spending annual leave on.

Independent of fares — this answers "when should I go", not "how much is it".
For each holiday it reports the longest break each leave budget can buy, so the
Thursday holiday that turns into four days off for one day of leave stands out
against the Saturday one that buys nothing at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .windows import DAY, Cluster, TravelWindow, clusters, is_free, windows_for


def days_in(start: date, end: date) -> list[date]:
    return [start + i * DAY for i in range((end - start).days + 1)]


def leave_dates(window: TravelWindow, holidays: dict[date, str]) -> list[date]:
    """The working days inside a window — exactly what you would book off."""
    return [d for d in days_in(window.depart, window.ret) if not is_free(d, holidays)]


@dataclass(frozen=True)
class Option:
    leave_days: int
    window: TravelWindow

    @property
    def days_off(self) -> int:
        """Calendar days away, counting both the departure and return days."""
        return (self.window.ret - self.window.depart).days + 1

    @property
    def value(self) -> float:
        """Days off bought per day of leave spent. Infinite when nothing is spent."""
        return float("inf") if not self.leave_days else self.days_off / self.leave_days


@dataclass(frozen=True)
class Break:
    cluster: Cluster
    options: list[Option]

    @property
    def best_value(self) -> Option | None:
        """The most efficient option that actually costs leave.

        The zero-leave option is always infinitely efficient and never
        interesting as advice, so it is excluded from the comparison.
        """
        paid = [o for o in self.options if o.leave_days]
        if not paid:
            return None
        return max(paid, key=lambda o: (o.value, o.days_off))


def options_for(cluster: Cluster, holidays: dict[date, str], max_leave: int = 3) -> list[Option]:
    """The longest break each leave budget from 0..max_leave can buy."""
    candidates = windows_for(
        cluster, holidays, max_leave=max_leave, min_nights=0, max_nights=60, limit=10_000
    )

    longest: dict[int, Option] = {}
    for window in candidates:
        option = Option(window.leave_days, window)
        current = longest.get(window.leave_days)
        # Tie-break towards the earlier departure: a break that starts sooner
        # reads as a longer weekend rather than a late return to work.
        if current is None or (option.days_off, -window.depart.toordinal()) > (
            current.days_off,
            -current.window.depart.toordinal(),
        ):
            longest[window.leave_days] = option

    # Drop budgets that buy nothing over a smaller one — spending a day of leave
    # for the same number of days off is never advice worth printing.
    kept: list[Option] = []
    for leave in sorted(longest):
        option = longest[leave]
        if kept and option.days_off <= kept[-1].days_off:
            continue
        kept.append(option)
    return kept


def breaks_between(
    holidays: dict[date, str], start: date, end: date, max_leave: int = 3
) -> list[Break]:
    found = []
    for cluster in clusters(holidays):
        if not start <= cluster.start <= end:
            continue
        found.append(Break(cluster, options_for(cluster, holidays, max_leave)))
    return found


def to_payload(
    holidays: dict[date, str], start: date, end: date, max_leave: int = 3
) -> dict:
    """The calendar view's data: every break, every leave budget, and the exact
    days you would book off for each — so the page can re-mark the calendar as
    the reader changes their leave budget without recomputing anything."""
    upcoming = breaks_between(holidays, start, end, max_leave)

    return {
        "range": {"start": start.isoformat(), "end": end.isoformat()},
        "holidays": {
            day.isoformat(): name
            for day, name in sorted(holidays.items())
            if start.replace(day=1) <= day <= end
        },
        "breaks": [
            {
                "name": brk.cluster.name,
                "start": brk.cluster.start.isoformat(),
                "end": brk.cluster.end.isoformat(),
                "anchor": brk.cluster.anchor.isoformat(),
                "options": [
                    {
                        "leave_days": option.leave_days,
                        "depart": option.window.depart.isoformat(),
                        "return": option.window.ret.isoformat(),
                        "days_off": option.days_off,
                        "value": None if option.leave_days == 0 else round(option.value, 2),
                        "leave_dates": [d.isoformat() for d in leave_dates(option.window, holidays)],
                    }
                    for option in brk.options
                ],
            }
            for brk in upcoming
        ],
    }
