"""Fare lookup, isolated behind a small interface.

The Google Flights source talks to the same unofficial endpoint the Google
Flights web app uses. It is fast, free, and covers the low-cost carriers that
matter out of Singapore (Scoot, AirAsia, Jetstar, VietJet) — but it is
unofficial, so it can break without notice. Everything downstream of
`FareSource` is written against the interface, not the library, so a
replacement (SerpAPI, Amadeus) only has to produce `Offer`s.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol


@dataclass(frozen=True)
class Offer:
    price: int
    currency: str
    airlines: tuple[str, ...]
    stops: int
    route: str  # e.g. "SIN-KUL-BKK" for the outbound
    source: str
    duration_minutes: int | None = None  # door-to-door for the outbound, layovers included

    @property
    def is_nonstop(self) -> bool:
        return self.stops == 0

    @property
    def duration_label(self) -> str:
        if not self.duration_minutes:
            return "—"
        hours, minutes = divmod(self.duration_minutes, 60)
        return f"{hours}h {minutes:02d}m" if hours else f"{minutes}m"


def _leg_time(part) -> datetime | None:
    """A leg endpoint as a naive local datetime. Times arrive as [h] or [h, m].

    Upstream occasionally supplies a None inside those lists, so every component
    is validated rather than trusted — building a datetime from one raises, and
    losing a fare over a missing minute field would be a poor trade.
    """
    day, clock = getattr(part, "date", None), getattr(part, "time", None)
    if not day or not clock or len(day) < 3:
        return None
    minute = clock[1] if len(clock) > 1 else 0
    parts = [day[0], day[1], day[2], clock[0], minute]
    if any(not isinstance(value, int) for value in parts):
        return None
    return datetime(*parts)


def journey_minutes(legs) -> int | None:
    """Total outbound journey time, flying plus layovers.

    Leg durations are already timezone-correct — a 155-minute SIN-BKK hop shows
    local clock times only 95 minutes apart — so they are summed as given.
    Layovers are the only part needing arithmetic, and both ends of a layover
    sit at the same airport, so naive local times are safe there.
    """
    if not legs:
        return None

    total = sum(leg.duration or 0 for leg in legs if isinstance(getattr(leg, "duration", None), int))
    for previous, following in zip(legs, legs[1:]):
        landed, leaves = _leg_time(previous.arrival), _leg_time(following.departure)
        if landed is None or leaves is None:
            return None
        gap = (leaves - landed).total_seconds() / 60
        if gap < 0:  # defensive: a malformed overnight connection
            gap += 24 * 60
        total += gap
    return int(total)


class FareSource(Protocol):
    name: str

    def search(
        self, origin: str, dest: str, depart: date, ret: date, max_stops: int | None
    ) -> list[Offer]:
        """Offers for one round trip, cheapest first. Empty list means no result."""


def _to_int(price) -> int | None:
    if isinstance(price, (int, float)):
        return int(price)
    digits = re.sub(r"[^0-9]", "", str(price or ""))
    return int(digits) if digits else None


def _query(origin, dest, depart, ret, max_stops, currency, language):
    from fast_flights import FlightQuery, Passengers, create_query

    return create_query(
        flights=[
            FlightQuery(date=depart.isoformat(), from_airport=origin, to_airport=dest, max_stops=max_stops),
            FlightQuery(date=ret.isoformat(), from_airport=dest, to_airport=origin, max_stops=max_stops),
        ],
        trip="round-trip",
        seat="economy",
        passengers=Passengers(adults=1),
        currency=currency,
        language=language,
        max_stops=max_stops,
    )


def booking_url(
    origin: str,
    dest: str,
    depart: date,
    ret: date,
    max_stops: int | None = None,
    currency: str = "SGD",
    language: str = "en-US",
) -> str | None:
    """A Google Flights link reproducing the exact search a price came from.

    Built from the same encoded query the fetch uses, so the dates, stop filter
    and currency all carry over. It lands on the search rather than one specific
    itinerary — the fare data has no per-itinerary booking link — but the price
    shown on arrival is the one that was recorded.

    Derived rather than stored: at ~160 characters per window this would add
    megabytes to the committed history over a year for no new information.
    """
    try:
        return _query(origin, dest, depart, ret, max_stops, currency, language).url()
    except Exception:
        return None


class GoogleFlightsSource:
    """Round-trip fares via the `fast-flights` client.

    Two quirks of the round-trip response drive the parsing below, both verified
    against live data:

    * `result.flights` holds the legs of the OUTBOUND journey only. The return
      itinerary is not in this payload at all — pulling it needs a second call
      with a chosen outbound. So stop counts here describe the outbound.
    * `result.price` is nonetheless the TOTAL round-trip fare, which is the
      number worth tracking.
    """

    name = "google-flights"

    def __init__(self, currency: str = "SGD", language: str = "en-US", retries: int = 2):
        self.currency = currency
        self.language = language
        self.retries = retries

    def search(
        self, origin: str, dest: str, depart: date, ret: date, max_stops: int | None = None
    ) -> list[Offer]:
        from fast_flights import get_flights
        from fast_flights.exceptions import FlightsNotFound

        query = _query(origin, dest, depart, ret, max_stops, self.currency, self.language)

        # FlightsNotFound is not reliable evidence a route has no service. It is
        # also what fast-flights raises when Google's own JSON payload carries
        # `errorHasStatus: true` — observed in practice on routes that priced
        # fine minutes earlier, in a pattern (fails from the first query,
        # spread evenly across every destination) that looks like transient
        # rate-limiting rather than a real absence of flights. Retried the same
        # as any other failure rather than accepted on the first attempt.
        last_error: Exception | None = None
        for attempt in range(self.retries):
            try:
                results = list(get_flights(query))
                break
            except FlightsNotFound as exc:
                last_error = exc
                if attempt < self.retries - 1:
                    time.sleep(2 * (attempt + 1))
                else:
                    return []
            except Exception as exc:  # network blips, upstream shape changes
                last_error = exc
                if attempt < self.retries - 1:
                    time.sleep(2 * (attempt + 1))
        else:
            raise RuntimeError(f"{self.name} failed after {self.retries} attempts: {last_error}")

        offers: list[Offer] = []
        for result in results:
            price = _to_int(getattr(result, "price", None))
            legs = list(getattr(result, "flights", []) or [])
            if price is None or not legs:
                continue

            # The price is the point; journey time is context. A parsing fault in
            # the timestamps must never cost us the fare.
            try:
                duration = journey_minutes(legs)
            except Exception:
                duration = None

            hops = [legs[0].from_airport.code] + [leg.to_airport.code for leg in legs]
            offers.append(
                Offer(
                    price=price,
                    currency=self.currency,
                    airlines=tuple(getattr(result, "airlines", ()) or ()),
                    stops=len(legs) - 1,
                    route="-".join(hops),
                    source=self.name,
                    duration_minutes=duration,
                )
            )

        return sorted(offers, key=lambda o: o.price)


def pick_offers(offers: list[Offer], max_stops: int | None) -> tuple[Offer | None, Offer | None]:
    """Return (best matching the stop limit, best nonstop) — the second for context.

    Google's own stop filter is applied server-side, but it is advisory enough
    that a cheap connection occasionally slips through; filtering again here
    means an alert configured for nonstop only can never quote a 1-stop fare.
    """
    if not offers:
        return None, None

    eligible = offers if max_stops is None else [o for o in offers if o.stops <= max_stops]
    nonstop = [o for o in offers if o.is_nonstop]
    return (eligible[0] if eligible else None), (nonstop[0] if nonstop else None)
