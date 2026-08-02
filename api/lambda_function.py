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

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from fares.sources import GoogleFlightsSource, booking_url, pick_offers

CACHE_TTL = int(os.environ.get("CACHE_TTL_SECONDS", "900"))  # 15 minutes
MAX_QUERIES = int(os.environ.get("MAX_QUERIES", "60"))
WORKERS = int(os.environ.get("WORKERS", "8"))

# Cache lives in the container, so it is shared across invocations of a warm
# Lambda but not across containers. At this traffic level that is nearly always
# a single container, and the cost of a miss is one extra lookup.
_CACHE: dict[str, tuple[float, dict]] = {}


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
