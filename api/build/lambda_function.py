"""Live fare lookups for the dashboard.

Runs in ap-southeast-1 so Google sees the request from Singapore. That is not
incidental — the daily GitHub Actions sweep runs from a US runner and sees
different inventory (a Jakarta nonstop that exists from Singapore simply was not
returned there), so prices from this endpoint are the ones a Singapore traveller
would actually be quoted.

Routes
    POST /fares    batch lookup   {origin, currency, fresh?, queries:[{dest,depart,ret,maxStops}]}
    GET  /verify   ?dest=HND      confirm a destination code returns itineraries
    GET  /destinations            the shared tracked list (PUT to replace it)
    GET  /local-holidays ?feeds=china,th&from=&to=
                                  public holidays and festivals at the destinations
"""

from __future__ import annotations

import hmac
import json
import os
import re
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

from fares.sources import GoogleFlightsSource, booking_url, pick_offers

CACHE_TTL = int(os.environ.get("CACHE_TTL_SECONDS", "900"))  # 15 minutes
MAX_QUERIES = int(os.environ.get("MAX_QUERIES", "60"))
WORKERS = int(os.environ.get("WORKERS", "8"))

# Cache lives in the container, so it is shared across invocations of a warm
# Lambda but not across containers. At this traffic level that is nearly always
# a single container, and the cost of a miss is one extra lookup.
_CACHE: dict[str, tuple[float, dict]] = {}


CONFIG_TABLE = os.environ.get("CONFIG_TABLE", "sg-holiday-fares-config")
EDIT_KEY = os.environ.get("EDIT_KEY", "")
# Editing from the page is a Google sign-in: the access token must have been
# issued to the dashboard's own OAuth client, for one of these addresses.
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
EDITOR_EMAILS = {e.strip().lower() for e in os.environ.get("EDITOR_EMAILS", "").split(",") if e.strip()}
WRITABLE = bool(EDIT_KEY or (GOOGLE_CLIENT_ID and EDITOR_EMAILS))


def _cors(body: dict, status: int = 200) -> dict:
    return {
        "statusCode": status,
        "headers": {
            "content-type": "application/json",
            "access-control-allow-origin": "*",
            "cache-control": "no-store",
        },
        "body": json.dumps(body),
    }


def _config_table():
    import boto3

    return boto3.resource("dynamodb").Table(CONFIG_TABLE)


def _read_destinations() -> dict:
    try:
        item = _config_table().get_item(Key={"id": "destinations"}).get("Item")
    except Exception as exc:
        return {"destinations": None, "error": f"{type(exc).__name__}: {exc}"[:160]}
    if not item:
        return {"destinations": None, "updated_at": None}
    return {
        "destinations": json.loads(item["payload"]),
        "updated_at": item.get("updated_at"),
    }


def _google_editor(token: str) -> tuple[bool, str]:
    """Check a Google access token: issued to this app, for an allowed address."""
    url = "https://oauth2.googleapis.com/tokeninfo?" + urllib.parse.urlencode({"access_token": token})
    try:
        with urllib.request.urlopen(url, timeout=8) as response:
            info = json.loads(response.read())
    except Exception:
        return False, "Google sign-in expired — sign in again"
    if info.get("aud") != GOOGLE_CLIENT_ID and info.get("azp") != GOOGLE_CLIENT_ID:
        return False, "that sign-in was not for this app"
    email = str(info.get("email", "")).lower()
    if str(info.get("email_verified")).lower() != "true" or email not in EDITOR_EMAILS:
        return False, f"{email or 'this account'} cannot edit the list"
    return True, email


def _authorise(event) -> tuple[bool, str]:
    headers = event.get("headers") or {}
    supplied = headers.get("x-edit-key", "")
    if EDIT_KEY and supplied and hmac.compare_digest(supplied, EDIT_KEY):
        return True, "edit key"
    bearer = headers.get("authorization", "")
    if GOOGLE_CLIENT_ID and EDITOR_EMAILS and bearer.lower().startswith("bearer "):
        return _google_editor(bearer[7:].strip())
    return False, "sign in to edit the list"


def _handle_destinations(event, method: str) -> dict:
    if method == "GET":
        return _cors({**_read_destinations(), "writable": WRITABLE})

    # Writes are gated because the endpoint is public — the repository is public,
    # so the URL is too. With no gate configured the list stays read-only rather
    # than silently accepting anonymous edits.
    if not WRITABLE:
        return _cors({"ok": False, "reason": "editing is disabled on the API"}, 503)

    allowed, who = _authorise(event)
    if not allowed:
        return _cors({"ok": False, "reason": who}, 401)

    body = json.loads(event.get("body") or "{}")
    destinations = body.get("destinations")
    if not isinstance(destinations, list) or not destinations:
        return _cors({"ok": False, "reason": "destinations must be a non-empty list"}, 400)
    if len(destinations) > 40:
        return _cors({"ok": False, "reason": "at most 40 destinations"}, 400)

    cleaned = []
    for entry in destinations:
        code = str(entry.get("code", "")).upper()
        if len(code) != 3 or not code.isalpha():
            return _cors({"ok": False, "reason": f"bad airport code: {code or '(blank)'}"}, 400)
        airports = [str(a).upper() for a in (entry.get("airports") or [code])]
        if any(len(a) != 3 or not a.isalpha() for a in airports):
            return _cors({"ok": False, "reason": f"bad airport list for {code}"}, 400)
        cleaned.append({
            "code": code,
            "name": str(entry.get("name") or code)[:40],
            "airports": airports,
            "max_stops": entry.get("max_stops") if entry.get("max_stops") in (0, 1, 2, 3, None) else 1,
            "alert_below": entry.get("alert_below") if isinstance(entry.get("alert_below"), int) else None,
        })

    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _config_table().put_item(Item={
        "id": "destinations",
        "payload": json.dumps(cleaned),
        "updated_at": stamp,
    })
    print(f"[destinations] {len(cleaned)} saved by {who}")
    return _cors({"ok": True, "destinations": cleaned, "updated_at": stamp})


# Google's public holiday calendars, one per country ("china", "th", "japanese"
# — the page maps country to id). The full feed, not ".official", because it
# carries festivals (Lantern Festival, Setsubun) as well as days off.
HOLIDAY_FEED = "https://calendar.google.com/calendar/ical/en.{}%23holiday%40group.v.calendar.google.com/public/basic.ics"
HOLIDAY_TTL = 12 * 3600
_HOLIDAYS: dict[str, tuple[float, list]] = {}
# Observances every feed carries that say nothing about a destination.
GENERIC_OBSERVANCE = re.compile(
    r"valentine|christmas|halloween|mother'?s day|father'?s day|april fool|new year'?s eve|"
    r"international|daylight saving|world |earth day|children'?s day|teacher'?s'? day|"
    r"march equinox|june solstice|september equinox|december solstice", re.I)


def _unfold(text: str) -> str:
    return text.replace("\r\n ", "").replace("\n ", "")


def _holiday_feed(feed: str) -> list:
    hit = _HOLIDAYS.get(feed)
    if hit and time.time() - hit[0] < HOLIDAY_TTL:
        return hit[1]
    with urllib.request.urlopen(HOLIDAY_FEED.format(feed), timeout=10) as response:
        text = _unfold(response.read().decode("utf-8", "replace"))
    days = []
    for block in text.split("BEGIN:VEVENT")[1:]:
        start = re.search(r"DTSTART;VALUE=DATE:(\d{8})", block)
        end = re.search(r"DTEND;VALUE=DATE:(\d{8})", block)
        name = re.search(r"\nSUMMARY:(.*)", block)
        if not (start and name):
            continue
        name = name.group(1).strip().replace("\\,", ",")
        description = (re.search(r"\nDESCRIPTION:(.*)", block) or [None, ""])[1]
        kind = "holiday" if description.lower().startswith("public holiday") else "festival"
        if kind == "festival" and GENERIC_OBSERVANCE.search(name):
            continue
        if "half-day" in name.lower():  # a half day off moves nobody
            continue
        first = datetime.strptime(start.group(1), "%Y%m%d").date()
        last = datetime.strptime(end.group(1), "%Y%m%d").date() - timedelta(days=1) if end else first
        day = first
        while day <= max(first, last):  # a multi-day event becomes one row per day
            days.append({"date": day.isoformat(), "name": name, "kind": kind})
            day += timedelta(days=1)
    days.sort(key=lambda d: d["date"])
    _HOLIDAYS[feed] = (time.time(), days)
    return days


def _handle_local_holidays(params: dict) -> dict:
    feeds = [f for f in str(params.get("feeds", "")).lower().split(",") if f][:20]
    if not feeds or any(not re.fullmatch(r"[a-z_]{2,24}", f) for f in feeds):
        return _cors({"ok": False, "reason": "feeds must be calendar ids like china,th"}, 400)
    lo = str(params.get("from") or date.today().isoformat())[:10]
    hi = str(params.get("to") or (date.today() + timedelta(days=400)).isoformat())[:10]

    def one(feed):
        try:
            return feed, [d for d in _holiday_feed(feed) if lo <= d["date"] <= hi]
        except Exception as exc:
            print(f"[local-holidays] {feed}: {type(exc).__name__}: {exc}")
            return feed, None

    with ThreadPoolExecutor(max_workers=min(8, len(feeds))) as pool:
        result = dict(pool.map(one, feeds))
    return _cors({"ok": True, "feeds": result})


def _offer_json(offer) -> dict | None:
    if offer is None:
        return None
    return {
        "price": offer.price,
        "currency": offer.currency,
        "airlines": list(offer.airlines),
        "stops": offer.stops,
        "route": offer.route,
        "duration": offer.duration_minutes,
    }


def _lookup(source, origin, currency, spec) -> dict:
    dest = str(spec["dest"]).upper()
    depart = date.fromisoformat(spec["depart"])
    ret = date.fromisoformat(spec["ret"])
    max_stops = spec.get("maxStops")
    key = f"{origin}|{dest}|{depart}|{ret}|{max_stops}|{currency}"

    entry = _CACHE.get(key)
    if entry and time.time() - entry[0] < CACHE_TTL:
        return {**entry[1], "cached": True, "age": int(time.time() - entry[0])}

    result = {
        "dest": dest,
        "depart": depart.isoformat(),
        "return": ret.isoformat(),
        "maxStops": max_stops,
        "currency": currency,
        "best": None,
        "nonstop": None,
        "book": booking_url(origin, dest, depart, ret, max_stops, currency),
        "error": None,
    }

    try:
        offers = source.search(origin, dest, depart, ret, max_stops)
        best, nonstop = pick_offers(offers, max_stops)
        result["best"] = _offer_json(best)
        result["nonstop"] = _offer_json(nonstop)
        if best is None:
            result["error"] = "no offers matched the stop limit"
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"[:200]

    # Only successful lookups are cached; a transient failure should not be
    # served for the next fifteen minutes.
    if result["best"] is not None:
        _CACHE[key] = (time.time(), result)
    return {**result, "cached": False, "age": 0}


def _handle_fares(payload: dict) -> dict:
    queries = payload.get("queries") or []
    if not isinstance(queries, list) or not queries:
        return _cors({"error": "queries must be a non-empty list"}, 400)
    if len(queries) > MAX_QUERIES:
        return _cors({"error": f"at most {MAX_QUERIES} queries per request"}, 400)

    origin = str(payload.get("origin", "SIN")).upper()
    currency = str(payload.get("currency", "SGD")).upper()
    if payload.get("fresh"):
        _CACHE.clear()

    source = GoogleFlightsSource(currency=currency)
    started = time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = list(pool.map(lambda spec: _lookup(source, origin, currency, spec), queries))

    return _cors({
        "origin": origin,
        "currency": currency,
        "results": results,
        "took_ms": int((time.time() - started) * 1000),
        "cache_ttl": CACHE_TTL,
    })


def _handle_verify(params: dict) -> dict:
    dest = str(params.get("dest", "")).upper()
    if len(dest) != 3 or not dest.isalpha():
        return _cors({"ok": False, "reason": "not a 3-letter IATA code"}, 400)

    origin = str(params.get("origin", "SIN")).upper()
    if dest == origin:
        return _cors({"ok": False, "reason": "that is the origin"}, 400)

    depart = date.today() + timedelta(days=45)
    depart += timedelta(days=(5 - depart.weekday()) % 7)  # the next Saturday
    outcome = _lookup(
        GoogleFlightsSource(currency="SGD"),
        origin,
        "SGD",
        {"dest": dest, "depart": depart.isoformat(), "ret": (depart + timedelta(days=2)).isoformat()},
    )

    if outcome["best"] is None:
        # "no offers" means the route genuinely returned nothing; anything else
        # is an upstream failure and must not be reported as an absent route.
        # Some real destinations (SIN-PVG, SIN-CTU) currently trip a parser bug
        # in fast-flights, and calling those "no service" would be wrong.
        detail = outcome.get("error") or ""
        if detail.startswith("no offers"):
            return _cors({"ok": False, "reason": f"no {origin}-{dest} itineraries returned"})
        return _cors({
            "ok": False,
            "reason": f"lookup failed for {origin}-{dest} — this may be a route the fare source cannot parse",
            "detail": detail[:160],
        })
    return _cors({
        "ok": True,
        "dest": dest,
        "sample_price": outcome["best"]["price"],
        "airlines": outcome["best"]["airlines"],
        "nonstop_available": outcome["nonstop"] is not None,
    })


def lambda_handler(event, context):
    request = (event.get("requestContext") or {}).get("http") or {}
    method = request.get("method", "GET").upper()
    path = request.get("path", "/")

    if method == "OPTIONS":
        return _cors({"ok": True})

    try:
        if path.endswith("/destinations"):
            return _handle_destinations(event, method)

        if path.endswith("/local-holidays"):
            return _handle_local_holidays(event.get("queryStringParameters") or {})

        if path.endswith("/verify"):
            return _handle_verify(event.get("queryStringParameters") or {})

        if path.endswith("/fares") and method == "POST":
            return _handle_fares(json.loads(event.get("body") or "{}"))

        return _cors({"error": f"no route for {method} {path}"}, 404)
    except json.JSONDecodeError:
        return _cors({"error": "body must be JSON"}, 400)
    except Exception as exc:
        print(f"[error] {type(exc).__name__}: {exc}")
        return _cors({"error": "internal error"}, 500)
