#!/usr/bin/env python3
"""Edit the tracked destinations without hand-editing config.json.

    python manage.py list
    python manage.py add HND Tokyo --max-stops 1 --target 800
    python manage.py add CTS Sapporo --verify          # confirm the route exists first
    python manage.py set BKK --target 400 --max-stops 0
    python manage.py remove HAN

Writes config.json back one destination per line, so a change is a one-line diff.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

CONFIG = Path(__file__).resolve().parent / "config.json"
CODE = re.compile(r"^[A-Z]{3}$")


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def dump(config: dict, path: Path) -> None:
    """Serialise with each destination on its own line, columns aligned.

    json.dump would either inline the whole list or explode every key onto its
    own line; neither diffs well when you change one target.
    """
    destinations = config.pop("destinations")
    head = json.dumps(config, indent=2, ensure_ascii=False)[:-2].rstrip()

    width_code = max((len(d["code"]) for d in destinations), default=3) + 2
    width_name = max((len(d["name"]) for d in destinations), default=6) + 2

    lines = []
    for dest in destinations:
        code = f'"{dest["code"]}",'.ljust(width_code + 1)
        name = f'"{dest["name"]}",'.ljust(width_name + 1)
        stops = dest.get("max_stops")
        target = dest.get("alert_below")
        lines.append(
            f'    {{ "code": {code} "name": {name} '
            f'"max_stops": {json.dumps(stops)}, "alert_below": {json.dumps(target)} }}'
        )

    config["destinations"] = destinations  # restore, callers may still hold it
    body = ",\n".join(lines)
    path.write_text(f'{head},\n\n  "destinations": [\n{body}\n  ]\n}}\n', encoding="utf-8")


def find(destinations: list[dict], code: str) -> dict | None:
    return next((d for d in destinations if d["code"] == code), None)


def verify_route(origin: str, code: str, currency: str) -> tuple[bool, str]:
    """One live query to confirm the route exists at all before tracking it."""
    from fares.sources import GoogleFlightsSource

    depart = date.today() + timedelta(days=45)
    depart += timedelta(days=(5 - depart.weekday()) % 7)  # next Saturday
    try:
        offers = GoogleFlightsSource(currency=currency).search(
            origin, code, depart, depart + timedelta(days=2), None
        )
    except Exception as exc:
        return False, f"lookup failed: {type(exc).__name__}: {exc}"
    if not offers:
        return False, f"no {origin}-{code} itineraries returned for {depart}"
    best = offers[0]
    return True, f"{origin}-{code} looks fine — cheapest {currency} {best.price:,} on {', '.join(best.airlines)}"


def cmd_list(config: dict, args) -> int:
    print(f"origin {config['origin']} · prices in {config['currency']}\n")
    print(f"{'code':<6}{'destination':<20}{'stops':<8}{'alert below':>12}")
    for dest in config["destinations"]:
        stops = dest.get("max_stops")
        label = "nonstop" if stops == 0 else "any" if stops is None else f"<={stops}"
        target = dest.get("alert_below")
        print(f"{dest['code']:<6}{dest['name']:<20}{label:<8}{(target or '—'):>12}")
    print(f"\n{len(config['destinations'])} tracked")
    return 0


def cmd_add(config: dict, args) -> int:
    code = args.code.upper()
    if not CODE.match(code):
        print(f"'{args.code}' is not a 3-letter IATA airport code")
        return 1
    if find(config["destinations"], code):
        print(f"{code} is already tracked — use 'set' to change it")
        return 1

    if args.verify:
        ok, message = verify_route(config["origin"], code, config["currency"])
        print(message)
        if not ok:
            return 1

    config["destinations"].append(
        {"code": code, "name": args.name, "max_stops": args.max_stops, "alert_below": args.target}
    )
    dump(config, args.config)
    print(f"added {code} ({args.name})")
    if len(config["destinations"]) > 8:
        print(
            f"note: {len(config['destinations'])} destinations — the dashboard charts the first 8 "
            "and lists the rest in the table. Reorder config.json to change which are charted."
        )
    return 0


def cmd_remove(config: dict, args) -> int:
    code = args.code.upper()
    dest = find(config["destinations"], code)
    if not dest:
        print(f"{code} is not tracked")
        return 1
    config["destinations"].remove(dest)
    dump(config, args.config)
    print(f"removed {code} ({dest['name']}) — its past observations stay in the history")
    return 0


def cmd_set(config: dict, args) -> int:
    code = args.code.upper()
    dest = find(config["destinations"], code)
    if not dest:
        print(f"{code} is not tracked — use 'add'")
        return 1

    changes = []
    if args.target is not None:
        dest["alert_below"] = None if args.target < 0 else args.target
        changes.append(f"target={dest['alert_below']}")
    if args.max_stops is not None:
        dest["max_stops"] = None if args.max_stops < 0 else args.max_stops
        changes.append(f"max_stops={dest['max_stops']}")
    if args.name:
        dest["name"] = args.name
        changes.append(f"name={args.name}")

    if not changes:
        print("nothing to change — pass --target, --max-stops or --name")
        return 1

    dump(config, args.config)
    print(f"updated {code}: {', '.join(changes)}")
    return 0


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--config", type=Path, default=CONFIG)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list", help="show tracked destinations").set_defaults(run=cmd_list)

    add = subparsers.add_parser("add", help="track a new destination")
    add.add_argument("code", help="3-letter IATA airport code, e.g. HND")
    add.add_argument("name", help="display name, e.g. Tokyo")
    add.add_argument("--max-stops", type=int, default=1, help="0 for nonstop only (default 1)")
    add.add_argument("--target", type=int, help="alert at or below this price")
    add.add_argument("--verify", action="store_true", help="check the route with one live query")
    add.set_defaults(run=cmd_add)

    remove = subparsers.add_parser("remove", help="stop tracking a destination")
    remove.add_argument("code")
    remove.set_defaults(run=cmd_remove)

    change = subparsers.add_parser("set", help="change an existing destination")
    change.add_argument("code")
    change.add_argument("--max-stops", type=int, help="0 nonstop, 1 one stop, -1 for no limit")
    change.add_argument("--target", type=int, help="alert price, or -1 to clear it")
    change.add_argument("--name")
    change.set_defaults(run=cmd_set)

    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    return args.run(load(args.config), args)


if __name__ == "__main__":
    sys.exit(main())
