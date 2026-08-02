#!/usr/bin/env python3
"""Recommended Singapore travel windows — public holidays joined with weekends.

    python plan.py                    # next 12 months
    python plan.py --months 18        # further out (dataset runs to Dec 2027)
    python plan.py --max-leave 4      # allow bigger leave budgets
    python plan.py --no-calendar      # just the table

Answers "when should I go", independent of any fare data.
"""

from __future__ import annotations

import argparse
import calendar
import sys
from datetime import date, timedelta

from fares import holidays as holiday_data
from fares.planner import breaks_between, days_in, leave_dates

DAY = timedelta(days=1)
HOLIDAY, LEAVE, WEEKEND, PLAIN = "H", "L", "·", " "


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--months", type=int, default=12, help="horizon in months (default 12)")
    parser.add_argument("--max-leave", type=int, default=3, help="largest leave budget to consider")
    parser.add_argument("--from", dest="start", type=date.fromisoformat, help="start date")
    parser.add_argument("--no-calendar", action="store_true", help="skip the month grids")
    parser.add_argument("--offline", action="store_true", help="use the cached holiday list")
    return parser.parse_args(argv)


def month_grid(year: int, month: int, marks: dict[date, str]) -> list[str]:
    """One month as fixed-width rows, each day suffixed with its marker."""
    lines = [f"{calendar.month_name[month]} {year}".center(21), "Mo Tu We Th Fr Sa Su"]
    for week in calendar.Calendar(firstweekday=0).monthdatescalendar(year, month):
        cells = []
        for day in week:
            if day.month != month:
                cells.append("   ")
                continue
            cells.append(f"{day.day:>2}{marks.get(day, PLAIN)}")
        lines.append("".join(cells).rstrip())
    return lines


def side_by_side(blocks: list[list[str]], per_row: int = 3, gap: str = "    ") -> str:
    out = []
    for start in range(0, len(blocks), per_row):
        group = blocks[start : start + per_row]
        height = max(len(b) for b in group)
        for block in group:
            block.extend([""] * (height - len(block)))
        for row in range(height):
            out.append(gap.join(block[row].ljust(21) for block in group).rstrip())
        out.append("")
    return "\n".join(out)


def main(argv=None) -> int:
    args = parse_args(argv)
    holidays, source = holiday_data.load(refresh=not args.offline)

    start = args.start or date.today()
    end = start + timedelta(days=int(args.months * 30.44))
    latest = max(holidays)
    if end > latest:
        end = latest

    upcoming = breaks_between(holidays, start, end, max_leave=args.max_leave)
    if not upcoming:
        print(f"No public holidays between {start} and {end}.")
        return 0

    print(f"Singapore public holidays joined with weekends — {start:%d %b %Y} to {end:%d %b %Y}")
    print(f"holidays from {source}; {len(upcoming)} breaks\n")

    marks: dict[date, str] = {}
    ranked: list[tuple[float, str, object]] = []

    for brk in upcoming:
        span = f"{brk.cluster.start:%a %-d %b}" + (
            "" if brk.cluster.start == brk.cluster.end else f" – {brk.cluster.end:%a %-d %b}"
        )
        print(f"{brk.cluster.name}  ({span})")

        for option in brk.options:
            window = option.window
            cost = "no leave" if not option.leave_days else (
                f"{option.leave_days} day{'s' if option.leave_days > 1 else ''} leave"
            )
            value = "" if not option.leave_days else f"   {option.value:.1f} days off per day of leave"
            print(
                f"    {cost:<13} → {window.depart:%a %-d %b} – {window.ret:%a %-d %b}"
                f"   {option.days_off} day{'s' if option.days_off != 1 else ''} off{value}"
            )

        best = brk.best_value
        if best:
            ranked.append((best.value, brk.cluster.name, best))
            for day in leave_dates(best.window, holidays):
                marks[day] = LEAVE
        print()

    # Marks are drawn a month either side so a break straddling the horizon edge
    # still renders a complete month grid.
    view_start, view_end = start - timedelta(days=31), end + timedelta(days=31)
    for day in holidays:
        if view_start <= day <= view_end:
            marks[day] = HOLIDAY

    def summarise(title, rows):
        print(title)
        for value, name, option in rows:
            window = option.window
            print(
                f"    {value:>4.1f}x  {name:<28} {window.depart:%a %-d %b} – {window.ret:%a %-d %b}"
                f"  ({option.leave_days}d leave → {option.days_off} days off)"
            )
        print()

    summarise(
        "Best value for a day off",
        sorted(ranked, key=lambda r: (-r[0], -r[2].days_off, r[2].window.depart))[:5],
    )

    # Every long weekend ties at 4.0x, so value alone hides the genuinely big
    # breaks. Rank the same options by length to surface them.
    longest = []
    for brk in upcoming:
        paid = [o for o in brk.options if o.leave_days]
        if paid:
            best = max(paid, key=lambda o: (o.days_off, -o.leave_days))
            longest.append((best.value, brk.cluster.name, best))
    summarise(
        "Longest breaks available",
        sorted(longest, key=lambda r: (-r[2].days_off, r[2].leave_days))[:5],
    )

    if args.no_calendar:
        return 0

    print(f"\nCalendar   {HOLIDAY} public holiday   {LEAVE} suggested leave   {WEEKEND} weekend\n")

    for day in days_in(view_start, view_end):
        if day.weekday() >= 5 and day not in marks:
            marks[day] = WEEKEND

    months, cursor = [], date(start.year, start.month, 1)
    while cursor <= end:
        if any(d.month == cursor.month and d.year == cursor.year and marks.get(d) in (HOLIDAY, LEAVE)
               for d in marks):
            months.append(month_grid(cursor.year, cursor.month, marks))
        cursor = date(cursor.year + (cursor.month == 12), (cursor.month % 12) + 1, 1)

    print(side_by_side(months))
    return 0


if __name__ == "__main__":
    sys.exit(main())
