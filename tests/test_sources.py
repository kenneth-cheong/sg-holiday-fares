import unittest
from datetime import date
from unittest.mock import MagicMock, patch

from fares.sources import GoogleFlightsSource

SEARCH_ARGS = ("SIN", "BKK", date(2026, 11, 6), date(2026, 11, 9), 1)


def fake_leg(price=400, airline="Scoot"):
    leg = MagicMock()
    leg.from_airport.code, leg.to_airport.code = "SIN", "BKK"
    leg.duration = 155
    leg.arrival.date = leg.departure.date = [2026, 11, 6]
    leg.arrival.time = leg.departure.time = [10, 0]
    result = MagicMock()
    result.price, result.flights, result.airlines = price, [leg], (airline,)
    return result


class TestFlightsNotFoundRetry(unittest.TestCase):
    """FlightsNotFound is also what fast-flights raises when Google's payload
    carries errorHasStatus: true — observed in practice on routes that priced
    fine minutes earlier. It must not be trusted as "no service" on the first
    attempt the way a real empty result would be."""

    def test_recovers_if_a_later_attempt_succeeds(self):
        from fast_flights.exceptions import FlightsNotFound

        calls = [FlightsNotFound("errorHasStatus"), [fake_leg(price=455)]]
        with patch("fast_flights.get_flights", side_effect=calls) as mocked, \
             patch("fares.sources.time.sleep"):
            offers = GoogleFlightsSource(retries=2).search(*SEARCH_ARGS)
        self.assertEqual(mocked.call_count, 2)
        self.assertEqual([o.price for o in offers], [455])

    def test_gives_up_as_empty_once_retries_are_exhausted(self):
        from fast_flights.exceptions import FlightsNotFound

        with patch("fast_flights.get_flights", side_effect=FlightsNotFound("x")) as mocked, \
             patch("fares.sources.time.sleep"):
            offers = GoogleFlightsSource(retries=2).search(*SEARCH_ARGS)
        self.assertEqual(mocked.call_count, 2)
        self.assertEqual(offers, [])

    def test_a_single_not_found_no_longer_short_circuits(self):
        # Regression guard: retries=3 must mean three real attempts even when
        # every one of them raises FlightsNotFound, not an immediate bailout
        # on the first.
        from fast_flights.exceptions import FlightsNotFound

        with patch("fast_flights.get_flights", side_effect=FlightsNotFound("x")) as mocked, \
             patch("fares.sources.time.sleep"):
            GoogleFlightsSource(retries=3).search(*SEARCH_ARGS)
        self.assertEqual(mocked.call_count, 3)

    def test_generic_failures_still_raise_after_exhausting_retries(self):
        with patch("fast_flights.get_flights", side_effect=ConnectionError("boom")) as mocked, \
             patch("fares.sources.time.sleep"):
            with self.assertRaises(RuntimeError):
                GoogleFlightsSource(retries=2).search(*SEARCH_ARGS)
        self.assertEqual(mocked.call_count, 2)


if __name__ == "__main__":
    unittest.main()
