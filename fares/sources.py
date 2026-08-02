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
from datetime import date
from typing import Protocol


@dataclass(frozen=True)
class Offer:
    price: int
    currency: str
    airlines: tuple[str, ...]
    stops: int
    route: str  # e.g. "SIN-KUL-BKK" for the outbound
    source: str

    @property
    def is_nonstop(self) -> bool:
        return self.stops == 0


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
        from fast_flights import FlightQuery, Passengers, create_query, get_flights
        from fast_flights.exceptions import FlightsNotFound

        query = create_query(
            flights=[
                FlightQuery(
                    date=depart.isoformat(), from_airport=origin, to_airport=dest, max_stops=max_stops
                ),
                FlightQuery(
                    date=ret.isoformat(), from_airport=dest, to_airport=origin, max_stops=max_stops
                ),
            ],
            trip="round-trip",
            seat="economy",
            passengers=Passengers(adults=1),
            currency=self.currency,
            language=self.language,
            max_stops=max_stops,
        )

        last_error: Exception | None = None
        for attempt in range(self.retries):
            try:
                results = list(get_flights(query))
                break
            except FlightsNotFound:
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

            hops = [legs[0].from_airport.code] + [leg.to_airport.code for leg in legs]
            offers.append(
                Offer(
                    price=price,
                    currency=self.currency,
                    airlines=tuple(getattr(result, "airlines", ()) or ()),
                    stops=len(legs) - 1,
                    route="-".join(hops),
                    source=self.name,
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
