"""Turn public holidays into candidate travel windows.

A *cluster* is a maximal run of consecutive non-working days (weekends plus
gazetted holidays) containing at least one holiday. Each cluster expands into a
handful of candidate trips: the break itself, plus variants that spend annual
leave to bridge onto an adjacent weekend. The bridge cases are the point — a
lone Thursday holiday becomes a four-day weekend for one day of leave, and a
lone Wednesday becomes five days for two.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

DAY = timedelta(days=1)


@dataclass(frozen=True)
class TravelWindow:
    name: str
    anchor: date  # first gazetted holiday in the cluster; groups windows together
    depart: date
    ret: date
    leave_days: int

    @property
    def nights(self) -> int:
        return (self.ret - self.depart).days

    @property
    def key(self) -> str:
        return f"{self.anchor.isoformat()}|{self.depart.isoformat()}|{self.ret.isoformat()}"

    @property
    def label(self) -> str:
        leave = "no leave" if not self.leave_days else f"{self.leave_days}d leave"
        return (
            f"{self.depart:%a %-d %b}–{self.ret:%a %-d %b} "
            f"({self.nights} nights, {leave})"
        )


@dataclass(frozen=True)
class Cluster:
    name: str
    anchor: date
    start: date
    end: date


def is_free(day: date, holidays: dict[date, str]) -> bool:
    return day.weekday() >= 5 or day in holidays


def _base_name(name: str) -> str:
    return name.replace("(Observed)", "").strip()


def clusters(holidays: dict[date, str]) -> list[Cluster]:
    """Group holidays into maximal runs of consecutive non-working days."""
    found: list[Cluster] = []
    consumed: set[date] = set()

    for holiday in sorted(holidays):
        if holiday in consumed:
            continue

        start = holiday
        while is_free(start - DAY, holidays):
            start -= DAY
        end = holiday
        while is_free(end + DAY, holidays):
            end += DAY

        span = [start + i * DAY for i in range((end - start).days + 1)]
        in_span = [d for d in span if d in holidays]
        consumed.update(in_span)

        names: list[str] = []
        for d in in_span:
            base = _base_name(holidays[d])
            if base not in names:
                names.append(base)

        found.append(Cluster(" + ".join(names), in_span[0], start, end))

    return sorted(found, key=lambda c: c.start)


def _bridges(boundary: date, step: timedelta, holidays: dict[date, str], max_leave: int) -> list[tuple[date, int]]:
    """Options for extending past a cluster boundary by spending annual leave.

    Immediately outside a maximal run is always a working day, so each step
    costs exactly one leave day — after which any further free days it connects
    to come along at no extra cost. That absorption is where the value is.
    """
    options = [(boundary, 0)]
    cursor, spent = boundary, 0

    while spent < max_leave:
        cursor += step
        spent += 1
        while is_free(cursor + step, holidays):
            cursor += step
        options.append((cursor, spent))

    return options


def windows_for(
    cluster: Cluster,
    holidays: dict[date, str],
    max_leave: int = 2,
    min_nights: int = 2,
    max_nights: int = 10,
    limit: int = 4,
) -> list[TravelWindow]:
    befores = _bridges(cluster.start, -DAY, holidays, max_leave)
    afters = _bridges(cluster.end, DAY, holidays, max_leave)

    unique: dict[tuple[date, date], TravelWindow] = {}
    for depart, leave_before in befores:
        for ret, leave_after in afters:
            leave = leave_before + leave_after
            if leave > max_leave:
                continue
            nights = (ret - depart).days
            if not min_nights <= nights <= max_nights:
                continue
            candidate = TravelWindow(cluster.name, cluster.anchor, depart, ret, leave)
            unique.setdefault((depart, ret), candidate)

    # Cheapest leave first, then longest trip — the best value per day off.
    ranked = sorted(unique.values(), key=lambda w: (w.leave_days, -w.nights, w.depart))
    return ranked[:limit]


def upcoming(
    holidays: dict[date, str],
    today: date,
    min_lead_days: int = 14,
    lookahead_days: int = 180,
    **kwargs,
) -> list[TravelWindow]:
    """Every candidate window departing inside the realistic booking horizon."""
    out: list[TravelWindow] = []
    for cluster in clusters(holidays):
        for window in windows_for(cluster, holidays, **kwargs):
            lead = (window.depart - today).days
            if min_lead_days <= lead <= lookahead_days:
                out.append(window)
    return sorted(out, key=lambda w: (w.depart, w.ret))
