"""Append-only price history, kept as JSONL in the repo.

Committing observations to git means the history is versioned, diffable and
needs no database. One line per (run, destination, travel window).
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HISTORY_PATH = REPO_ROOT / "docs" / "data" / "history.jsonl"
DASHBOARD_PATH = REPO_ROOT / "docs" / "data" / "dashboard.json"
STATE_PATH = REPO_ROOT / "data" / "alert_state.json"


def observation_key(dest: str, window_key: str) -> str:
    return f"{dest}|{window_key}"


def load_history(path: Path = HISTORY_PATH) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def append(rows: list[dict], path: Path = HISTORY_PATH) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def price_series(history: list[dict], key: str, before: str | None = None) -> list[int]:
    """Prices for one key in observation order, optionally excluding today's run."""
    series = []
    for row in history:
        if row.get("key") != key or row.get("price") is None:
            continue
        if before is not None and row.get("observed_on", "") >= before:
            continue
        series.append(int(row["price"]))
    return series


def load_state(path: Path = STATE_PATH) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(state: dict, path: Path = STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_dashboard(history: list[dict], generated_at: datetime, today: date) -> dict:
    """Collapse the history into what the dashboard page needs.

    Grouped by holiday, then destination, keeping one entry per travel window
    with its full price series so the page can chart trends without reparsing
    the whole JSONL.
    """
    by_key: dict[str, list[dict]] = defaultdict(list)
    for row in history:
        by_key[row["key"]].append(row)

    holidays: dict[str, dict] = {}
    for key, rows in by_key.items():
        rows.sort(key=lambda r: r["observed_on"])
        latest = rows[-1]
        if latest["depart"] < today.isoformat():
            continue  # the trip has departed; drop it from the live view

        anchor = latest["holiday_date"]
        holiday = holidays.setdefault(
            anchor,
            {"name": latest["holiday"], "date": anchor, "destinations": {}},
        )
        dest = holiday["destinations"].setdefault(
            latest["dest"], {"code": latest["dest"], "name": latest["dest_name"], "windows": []}
        )

        prices = [r["price"] for r in rows if r.get("price") is not None]
        dest["windows"].append(
            {
                "key": key,
                "depart": latest["depart"],
                "return": latest["return"],
                "nights": latest["nights"],
                "leave_days": latest["leave_days"],
                "label": latest["window_label"],
                "current": latest.get("price"),
                "nonstop": latest.get("nonstop_price"),
                "airlines": latest.get("airlines", []),
                "route": latest.get("route"),
                "stops": latest.get("stops"),
                "min": min(prices) if prices else None,
                "max": max(prices) if prices else None,
                "series": [
                    {"on": r["observed_on"], "price": r["price"]}
                    for r in rows
                    if r.get("price") is not None
                ],
            }
        )

    for holiday in holidays.values():
        for dest in holiday["destinations"].values():
            dest["windows"].sort(key=lambda w: (w["leave_days"], w["depart"]))
            live = [w["current"] for w in dest["windows"] if w["current"] is not None]
            dest["cheapest"] = min(live) if live else None
        # Deliberately NOT sorted by price. The dashboard assigns each destination
        # a chart colour by its position here, so this order must stay stable —
        # otherwise re-filtering the page repaints series that did not change.
        holiday["destinations"] = list(holiday["destinations"].values())

    return {
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "currency": history[-1]["currency"] if history else "SGD",
        "holidays": sorted(holidays.values(), key=lambda h: h["date"]),
    }


def write_dashboard(payload: dict, path: Path = DASHBOARD_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
