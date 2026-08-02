"""Singapore public holidays, from MOM's consolidated dataset on data.gov.sg.

The dataset already contains the "(Observed)" days MOM gazettes when a holiday
falls on a Sunday, so every date returned here can be treated as a non-working
day without further adjustment.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path

RESOURCE_ID = "d_8ef23381f9417e4d4254ee8b4dcdb176"  # Singapore Public Holidays, 2020-2027
API_URL = "https://data.gov.sg/api/action/datastore_search"

REPO_ROOT = Path(__file__).resolve().parent.parent
CACHE_PATH = REPO_ROOT / "data" / "holidays.json"


def _parse(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def fetch(timeout: int = 20) -> dict[date, str]:
    """Pull the holiday list from data.gov.sg. Raises on any network/shape error."""
    url = f"{API_URL}?{urllib.parse.urlencode({'resource_id': RESOURCE_ID, 'limit': 500})}"
    with urllib.request.urlopen(url, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))

    if not payload.get("success"):
        raise RuntimeError("data.gov.sg reported success=false")

    records = payload["result"]["records"]
    if not records:
        raise RuntimeError("data.gov.sg returned no holiday records")

    return {_parse(r["date"]): r["holiday"].strip() for r in records}


def _read_cache() -> dict[date, str]:
    raw = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    return {_parse(k): v for k, v in raw.items()}


def _write_cache(holidays: dict[date, str]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    serialised = {d.isoformat(): name for d, name in sorted(holidays.items())}
    CACHE_PATH.write_text(json.dumps(serialised, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load(refresh: bool = True) -> tuple[dict[date, str], str]:
    """Return (holidays, source) — live data when reachable, else the cached copy.

    The cache is committed to the repo so a data.gov.sg outage degrades the run
    to stale-but-correct dates rather than failing the whole sweep.
    """
    if refresh:
        try:
            holidays = fetch()
            _write_cache(holidays)
            return holidays, "data.gov.sg"
        except (urllib.error.URLError, TimeoutError, KeyError, ValueError, RuntimeError) as exc:
            if not CACHE_PATH.exists():
                raise RuntimeError(f"holiday fetch failed and no cache at {CACHE_PATH}: {exc}") from exc
            print(f"[holidays] live fetch failed ({exc}); falling back to cache")

    return _read_cache(), f"cache:{CACHE_PATH.name}"
