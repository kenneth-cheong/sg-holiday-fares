"""Live fare lookups for the dashboard.

Runs in ap-southeast-1 so Google sees the request from Singapore. That is not
incidental — the daily GitHub Actions sweep runs from a US runner and sees
different inventory (a Jakarta nonstop that exists from Singapore simply was not
returned there), so prices from this endpoint are the ones a Singapore traveller
would actually be quoted.

Routes
    POST /fares    batch lookup   {origin, currency, fresh?, queries:[{dest,depart,ret,maxStops}]}
    GET  /verify   ?dest=HND      confirm a destination code returns itineraries
"""

from __future__ import annotations

import hmac
import json
import os
import time
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
        "writable": bool(EDIT_KEY),
    }


def _handle_destinations(event, method: str) -> dict:
    if method == "GET":
        return _cors({**_read_destinations(), "writable": bool(EDIT_KEY)})

    # Writes are gated because the endpoint is public — the repository is public,
    # so the URL is too. With no key configured the list stays read-only rather
    # than silently accepting anonymous edits.
    if not EDIT_KEY:
        return _cors({"ok": False, "reason": "editing is disabled — no EDIT_KEY is set on the API"}, 503)

    supplied = (event.get("headers") or {}).get("x-edit-key", "")
    if not hmac.compare_digest(supplied, EDIT_KEY):
        return _cors({"ok": False, "reason": "wrong or missing edit key"}, 403)

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
    return _cors({"ok": True, "destinations": cleaned, "updated_at": stamp})


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
