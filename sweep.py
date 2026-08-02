#!/usr/bin/env python3
"""Daily fare sweep across Singapore public holiday travel windows.

    python sweep.py                 # full run: fetch, record, alert, rebuild dashboard
    python sweep.py --dry-run       # fetch and print, write nothing, send nothing
    python sweep.py --no-telegram   # record and rebuild, but stay quiet
    python sweep.py --limit 4       # only the first N queries, for a quick smoke test

Needs TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in the environment to notify.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from fares import alerts as alerting
from fares import holidays as holiday_data
from fares import notify, planner, store, windows
from fares.sources import GoogleFlightsSource, pick_offers

REPO_ROOT = Path(__file__).resolve().parent


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", type=Path, default=REPO_ROOT / "config.json")
    parser.add_argument("--dry-run", action="store_true", help="print results, write and send nothing")
    parser.add_argument("--no-telegram", action="store_true", help="record results but do not notify")
    parser.add_argument("--limit", type=int, help="cap the number of fare queries")
    parser.add_argument("--min-lead-days", type=int, help="override the booking lead time")
    parser.add_argument("--offline-holidays", action="store_true", help="use the cached holiday list")
    parser.add_argument("--today", type=date.fromisoformat, help="override today's date (testing)")
    return parser.parse_args(argv)


def build_plan(config: dict, today: date, min_lead_override: int | None):
    holidays, source = holiday_data.load(refresh=not config.get("_offline_holidays"))
    search = config["search"]

    candidates = windows.upcoming(
        holidays,
        today,
        min_lead_days=min_lead_override if min_lead_override is not None else search["min_lead_days"],
        lookahead_days=search["lookahead_days"],
        max_leave=search["max_leave_days"],
        min_nights=search["min_nights"],
        max_nights=search["max_nights"],
        limit=search["max_windows_per_holiday"],
    )
    return holidays, source, candidates


def observe(source, origin, dest_cfg, window, currency, today, now):
    """One fare lookup as (history row, best offer, cheapest nonstop).

    Failures are recorded rather than raised, so one bad route cannot abort the
    sweep and the gap stays visible in the history.
    """
    row = {
        "observed_on": today.isoformat(),
        "observed_at": now.isoformat(timespec="seconds"),
        "key": store.observation_key(dest_cfg["code"], window.key),
        "holiday": window.name,
        "holiday_date": window.anchor.isoformat(),
        "origin": origin,
        "dest": dest_cfg["code"],
        "dest_name": dest_cfg["name"],
        "airports": list(dest_cfg.get("airports") or [dest_cfg["code"]]),
        "airport": None,
        "depart": window.depart.isoformat(),
        "return": window.ret.isoformat(),
        "nights": window.nights,
        "leave_days": window.leave_days,
        "window_label": window.label,
        "currency": currency,
        "max_stops": dest_cfg.get("max_stops"),
        "source": source.name,
        "price": None,
        "stops": None,
        "airlines": [],
        "route": None,
        "duration_minutes": None,
        "nonstop_price": None,
        "nonstop_duration_minutes": None,
        "error": None,
    }

    # A destination can span several airports — Bangkok is BKK and DMK, and the
    # low-cost carriers only fly into one of them. Each is queried and the
    # cheapest wins, with the winning airport recorded so the row still says
    # where you actually land.
    max_stops = dest_cfg.get("max_stops")
    best = nonstop = winner = None
    failures = []

    for airport in row["airports"]:
        try:
            offers = source.search(origin, airport, window.depart, window.ret, max_stops)
        except Exception as exc:
            failures.append(f"{airport}: {type(exc).__name__}")
            continue

        candidate, candidate_nonstop = pick_offers(offers, max_stops)
        if candidate and (best is None or candidate.price < best.price):
            best, winner = candidate, airport
        if candidate_nonstop and (nonstop is None or candidate_nonstop.price < nonstop.price):
            nonstop = candidate_nonstop

    if best is None:
        row["error"] = "; ".join(failures)[:300] if failures else "no offers matched the stop limit"
        return row, None, None

    row["airport"] = winner
    row.update(
        price=best.price,
        stops=best.stops,
        airlines=list(best.airlines),
        route=best.route,
        duration_minutes=best.duration_minutes,
        nonstop_price=nonstop.price if nonstop else None,
        nonstop_duration_minutes=nonstop.duration_minutes if nonstop else None,
    )
    return row, best, nonstop


def run(args) -> int:
    config = json.loads(args.config.read_text(encoding="utf-8"))
    config["_offline_holidays"] = args.offline_holidays
    today = args.today or date.today()
    now = datetime.now()

    holidays, holiday_source, candidates = build_plan(config, today, args.min_lead_days)

    print(f"holidays: {len(holidays)} loaded from {holiday_source}")

    # The leave planner is independent of fares, so it is refreshed before any
    # network work and survives a sweep that finds nothing to price.
    if not args.dry_run:
        horizon = min(today + timedelta(days=550), max(holidays))
        store.write_plan(planner.to_payload(holidays, today, horizon))
        print(f"plan:     rebuilt through {horizon}")

    if not candidates:
        print("no travel windows inside the booking horizon — nothing to do")
        return 0

    print(f"windows:  {len(candidates)} inside the booking horizon")
    for window in candidates:
        print(f"  · {window.name}: {window.label}")

    source = GoogleFlightsSource(currency=config["currency"])
    history = store.load_history()
    state = store.load_state()
    alert_cfg = config["alerts"]
    pause = config["search"].get("pause_between_queries", 1.0)

    jobs = [(dest, window) for window in candidates for dest in config["destinations"]]
    if args.limit:
        jobs = jobs[: args.limit]

    print(f"\nquerying {len(jobs)} destination/window pairs\n" + "-" * 78)

    rows: list[dict] = []
    found: list[alerting.Alert] = []
    failures = 0

    for index, (dest_cfg, window) in enumerate(jobs, start=1):
        row, best, nonstop = observe(
            source, config["origin"], dest_cfg, window, config["currency"], today, now
        )
        rows.append(row)

        if row["error"]:
            failures += 1
            print(f"{index:>3}. {dest_cfg['code']} {window.depart}..{window.ret}  FAILED  {row['error'][:60]}")
        else:
            prior = store.price_series(history, row["key"], before=today.isoformat())
            alert = alerting.assess(
                dest_code=dest_cfg["code"],
                dest_name=dest_cfg["name"],
                window=window,
                offer=best,
                nonstop=nonstop,
                prior=prior,
                target=dest_cfg.get("alert_below"),
                drop_pct=dest_cfg.get("drop_pct", alert_cfg["default_drop_pct"]),
                min_observations=alert_cfg["min_observations"],
                lookback=alert_cfg["baseline_lookback"],
            )
            flag = ""
            if alert:
                if alerting.should_send(
                    state,
                    row["key"],
                    best.price,
                    today,
                    resend_after_days=alert_cfg["resend_after_days"],
                    resend_if_cheaper_pct=alert_cfg["resend_if_cheaper_pct"],
                ):
                    found.append(alert)
                    flag = "  ALERT"
                else:
                    flag = "  (alert suppressed — already sent)"

            print(
                f"{index:>3}. {dest_cfg['code']} {window.depart}..{window.ret}  "
                f"{config['currency']} {best.price:<6} {', '.join(best.airlines)[:22]:<22}"
                f" {len(prior):>3} prior{flag}"
            )

        if index < len(jobs):
            time.sleep(pause)

    print("-" * 78)
    print(f"{len(rows) - failures} priced, {failures} failed, {len(found)} alert(s)")

    if args.dry_run:
        if found:
            print("\n--- message that would be sent ---")
            print(notify.compose(found, today))
        print("\ndry run — nothing written, nothing sent")
        return 0

    store.append(rows)
    history.extend(rows)
    store.write_dashboard(store.build_dashboard(history, now, today))
    print(f"wrote {len(rows)} observations; dashboard rebuilt")

    if not found:
        return 0

    message = notify.compose(found, today)
    if args.no_telegram:
        print("\n--- message (telegram disabled) ---")
        print(message)
        return 0

    token, chat_id = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set — printing instead")
        print(message)
        return 0

    if notify.send(token, chat_id, message):
        for alert in found:
            alerting.record_sent(state, store.observation_key(alert.dest, alert.window.key), alert.offer.price, today)
        store.save_state(state)
        print(f"sent {len(found)} alert(s) to Telegram")
    else:
        print("Telegram delivery failed — alert state not advanced, will retry next run")

    return 0


if __name__ == "__main__":
    sys.exit(run(parse_args()))
