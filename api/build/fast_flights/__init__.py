"""fast-flights is a simple Google Flights scraper.

GitHub: https://github.com/AWeirdDev/fast-flights
Docs: https://flights.aweird.me/
"""

from . import integrations
from .exceptions import FlightsNotFound
from .fetcher import fetch_flights_html, get_flights
from .parser import ResultList
from .querying import (
    FlightQuery,
    Passengers,
    Query,
    create_query,
)
from .querying import (
    create_query as create_filter,  # alias
)

__all__ = [
    "FlightQuery",
    "Query",
    "Passengers",
    "create_query",
    "create_filter",
    "get_flights",
    "fetch_flights_html",
    "integrations",
    "FlightsNotFound",
    "ResultList",
]
