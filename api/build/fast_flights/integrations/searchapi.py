# [AI NOTICE]
# This file is formerly written by AI and then corrected to behave correctly by a human.
# https://claude.ai was used.
# [/AI NOTICE]

# pyright: reportMissingTypeArgument=false
# pyright: reportUnknownParameterType=false
# pyright: reportUnknownVariableType=false
# pyright: reportUnknownMemberType=false
# pyright: reportUnknownArgumentType=false

from __future__ import annotations

import typing
from dataclasses import dataclass
from typing import Literal

import primp
from typing_extensions import Final, override

from ..pb.flights_pb2 import Passenger
from ..querying import Query
from .base import DataSourceIntegration, get_env

SEAT_MAP: dict[str, str] = {
    "economy": "economy",
    "premium-economy": "premium_economy",
    "business": "business",
    "first": "first_class",
}

BASE_URL = "https://www.searchapi.io/api/v1/search"


@dataclass
class Airport:
    """A departure or arrival airport on a single segment."""

    code: str
    """
    IATA code (e.g., `TPE`).
    """

    name: str
    """
    The name of the airport.
    """

    date: str
    """
    The associated date. Format: `YYYY-MM-DD`
    """

    time: str
    """
    The associated time. Format: `HH:MM`
    """


@dataclass
class Amenities:
    """
    Structured in-flight amenity data (`DetectedExtensions` in SearchApi).
    Maps directly to the `detected_extensions` object on each `FlightSegment`.
    """

    wifi: Literal["available", "free", "for free"] | None
    """Wi-Fi availability."""

    seat_type: str | None
    """The seat type expressed in natural language.

    For example: `"Lie Flat"`, `"Average Legroom"`
    """

    legroom_short: str | None
    """Legroom (short description).

    For example: `"29 in"`
    """

    legroom_long: str | None
    """Legroom (with verbose description)."""

    has_power_usb: bool | None
    """Power USB availability."""

    has_personal_video: bool | None
    """Personal video availability."""

    has_on_demand_video: bool | None
    """On-demand video availability."""

    has_live_tv: bool | None
    """Live TV availability."""

    has_stream_to_device: bool | None
    """Stream-to-device availability."""


@dataclass
class Segment:
    """One flight leg within an itinerary (e.g., TPE -> NRT is one segment; a connection adds more)."""

    departure: Airport
    arrival: Airport
    duration_min: int
    airplane: str
    airline: str
    airline_logo: str
    travel_class: str
    flight_number: str
    is_overnight: bool
    is_often_delayed: bool
    ticket_also_sold_by: list[str]

    extensions: list[str]
    """Extensions expressed in a list of natural language strings.

    For example: `["Wi-Fi for a fee", "USB outlet"]`
    """

    amenities: Amenities | None


@dataclass
class Layover:
    """A connection between two consecutive segments."""

    airport_code: str
    airport_name: str
    duration_min: int
    overnight: bool


@dataclass
class CarbonEmissions:
    """CO2 figures for a whole itinerary."""

    this_flight_kg: int | None
    typical_kg: int | None
    difference_pct: int | None

    lowest_route_kg: int | None
    """The greenest option on this route"""


@dataclass
class BookingRequest:
    """Low-level booking URL + POST payload returned by SearchApi."""

    url: str
    post_data: str | None


@dataclass
class BookingOption:
    """
    One purchasable fare option for an itinerary (data point 6).
    Returned only when the main search is followed up with `booking_token`.
    """

    book_with: str
    """
    For example: `"Delta"`, `"Google Flights"`
    """

    fare_type: str
    """
    For example: `"BASIC ECONOMY"`, `"MAIN CABIN"`, `"FIRST"`
    """

    flight_numbers: list[str]
    price: int | None

    baggage_prices: list[str]
    """
    For example: `["1st bag: $30", "2nd bag: $40"]`
    """

    extensions: list[str]
    """Fare-level notes.

    For example: `"No changes allowed"`
    """

    booking_phone: str | None
    booking_request: BookingRequest | None
    is_split_booking: bool


@dataclass
class AirlineLink:
    """
    A direct link to an airline's official policy page.
    Used for both baggage allowance (data point 6) and
    passenger assistance / accessibility (data point 5).
    """

    airline_code: str
    airline: str
    url: str


@dataclass
class CheaperAlternative:
    """An alternative date pair with a lower price (data point 7)."""

    departure: str
    """
    Departure date. Format: `YYYY-MM-DD`
    """

    ret: str
    """Return date. Format: `YYYY-MM-DD`

    Renamed from `return` to `ret` to align with Python's ecosystem better.
    """

    price: int


@dataclass
class PriceHistory:
    price: int
    iso_date: str


@dataclass
class PriceInsights:
    """Price intelligence block (data point 4)."""

    lowest_price: int | None
    price_level: Literal["low", "typical", "high", ""]

    typical_price_range: tuple[int, int] | None
    """Typical price range.

    Format: `(low: int, high: int)`
    """

    price_history: list[PriceHistory]
    estimated_savings: int | None  # from `cheapest_to_book`


@dataclass
class Flight:
    """
    A complete bookable itinerary. One or more segments from origin to destination.
    """

    is_best: bool
    airlines: str
    """
    For example: `"United"` or `"United · Lufthansa"`
    """

    flight_numbers: list[str]
    """
    For example: `["UA 837", "LH 402"]`
    """

    segments: list[Segment]
    layovers: list[Layover]
    duration_min: int
    stops: int
    travel_class: str
    carbon: CarbonEmissions | None  # data point 3
    price: int | None
    booking_token: str | None
    airline_logo: str
    extensions: list[str]  # itinerary-level labels


@dataclass
class Result:
    """Everything returned for one search. All seven data points live here."""

    price_insights: PriceInsights
    flights: list[Flight]
    passenger_assistance_links: list[AirlineLink]
    baggage_allowance_links: list[AirlineLink]
    booking_options: list[BookingOption]
    cheaper_alternatives: list[CheaperAlternative]


def params_from_query(q: Query, key: str) -> dict:
    flights = q.flight_data
    outbound = flights[0]

    trip = q.get_trip_type()
    seat = q.get_seat_type()
    passengers = q.passengers
    lang = q.language
    curr = q.currency

    params: dict = {
        "engine": "google_flights",
        "api_key": key,
        "departure_id": outbound.from_airport.airport,
        "arrival_id": outbound.to_airport.airport,
        "outbound_date": outbound.date,
        "travel_class": SEAT_MAP.get(seat, "economy"),
        "adults": str(
            sum(1 for passenger in passengers if passenger == Passenger.ADULT)
        ),
        "children": str(
            sum(1 for passenger in passengers if passenger == Passenger.CHILD)
        ),
        "infants_in_seat": str(
            sum(1 for passenger in passengers if passenger == Passenger.INFANT_IN_SEAT)
        ),
        "infants_on_lap": str(
            sum(1 for passenger in passengers if passenger == Passenger.INFANT_ON_LAP)
        ),
        "currency": curr,
        "hl": lang,
    }

    if trip == "round-trip" and len(flights) > 1:
        params["flight_type"] = "round_trip"
        params["return_date"] = flights[1].date
    elif trip == "multi-city":
        params["flight_type"] = "multi_city"
    else:
        params["flight_type"] = "one_way"

    return params


def params_from_url(url: str, key: str) -> dict:
    from urllib.parse import parse_qs, urlparse

    qs = parse_qs(urlparse(url).query)
    tfs = qs.get("tfs", [""])[0]
    hl = qs.get("hl", ["en"])[0]
    curr = qs.get("curr", ["USD"])[0]
    if not tfs:
        raise ValueError(f"No ?tfs= parameter found in URL: {url!r}")
    return {
        "engine": "google_flights",
        "api_key": key,
        "tfs": tfs,
        "hl": hl,
        "curr": curr,
    }


def to_result_model(data: dict) -> Result:
    best = data.get("best_flights", [])
    others = data.get("other_flights", [])
    flights = [_to_flight(it, is_best=True) for it in best] + [
        _to_flight(it, is_best=False) for it in others
    ]

    return Result(
        price_insights=_to_price_insights(data.get("price_insights", {})),
        flights=flights,
        passenger_assistance_links=[
            to_airline_link(link) for link in data.get("passenger_assistance_links", [])
        ],
        baggage_allowance_links=[
            to_airline_link(link) for link in data.get("baggage_allowance_links", [])
        ],
        booking_options=[
            _to_booking_option(o) for o in data.get("booking_options", [])
        ],
        cheaper_alternatives=[
            to_cheaper_alternative(a) for a in data.get("cheaper_alternatives", [])
        ],
    )


def _to_price_insights(pi: dict) -> PriceInsights:
    raw_range = pi.get("typical_price_range", {})
    price_range = (
        (raw_range["low_price"], raw_range["high_price"])
        if isinstance(raw_range, dict) and "low_price" in raw_range
        else None
    )
    return PriceInsights(
        lowest_price=pi.get("lowest_price"),
        price_level=pi.get("price_level", ""),
        typical_price_range=price_range,
        price_history=[
            PriceHistory(price=h["price"], iso_date=h["iso_date"])
            for h in pi.get("price_history", [])
        ],
        estimated_savings=pi.get("cheapest_to_book", {}).get("estimated_savings"),
    )


def _to_flight(it: dict, *, is_best: bool) -> Flight:
    segments = [_to_segment(s) for s in it.get("flights", [])]

    # deduplicate, preserve order, join with " · "
    seen: dict[str, None] = {}
    for s in segments:
        if s.airline:
            seen[s.airline] = None
    airlines = " · ".join(seen)

    flight_numbers = [s.flight_number for s in segments if s.flight_number]

    # travel_class at itinerary level, use first segment's value
    travel_class = segments[0].travel_class if segments else ""

    co2_raw = it.get("carbon_emissions")
    carbon = (
        CarbonEmissions(
            this_flight_kg=co2_raw.get("this_flight"),
            typical_kg=co2_raw.get("typical_for_this_route"),
            difference_pct=co2_raw.get("difference_percent"),
            lowest_route_kg=co2_raw.get("lowest_route"),
        )
        if co2_raw
        else None
    )

    layovers = [_to_layover(lv) for lv in it.get("layovers", [])]

    return Flight(
        is_best=is_best,
        airlines=airlines,
        flight_numbers=flight_numbers,
        segments=segments,
        layovers=layovers,
        duration_min=it.get("total_duration", 0),
        stops=len(layovers),
        travel_class=travel_class,
        carbon=carbon,
        price=it.get("price"),
        booking_token=it.get("booking_token"),
        airline_logo=it.get("airline_logo", ""),
        extensions=it.get("extensions", []),
    )


def _to_segment(s: dict) -> Segment:
    def _airport(a: dict) -> Airport:
        return Airport(
            code=a.get("id", ""),
            name=a.get("name", ""),
            date=a.get("date", ""),
            time=a.get("time", ""),
        )

    de = s.get("detected_extensions", {})
    amenities = (
        Amenities(
            wifi=de.get("wifi"),
            seat_type=de.get("seat_type"),
            legroom_short=de.get("legroom_short"),
            legroom_long=de.get("legroom_long"),
            has_power_usb=de.get("has_power_and_usb_outlets"),
            has_personal_video=de.get("has_personal_video_screen"),
            has_on_demand_video=de.get("has_on_demand_video"),
            has_live_tv=de.get("has_live_tv"),
            has_stream_to_device=de.get("has_stream_video_to_own_device"),
        )
        if de
        else None
    )

    return Segment(
        departure=_airport(s.get("departure_airport", {})),
        arrival=_airport(s.get("arrival_airport", {})),
        duration_min=s.get("duration", 0),
        airplane=s.get("airplane", ""),
        airline=s.get("airline", ""),
        airline_logo=s.get("airline_logo", ""),
        travel_class=s.get("travel_class", ""),
        flight_number=s.get("flight_number", ""),
        is_overnight=s.get("is_overnight", False),
        is_often_delayed=s.get("is_often_delayed", False),
        ticket_also_sold_by=s.get("ticket_also_sold_by", []),
        extensions=s.get("extensions", []),
        amenities=amenities,
    )


def _to_layover(lv: dict) -> Layover:
    return Layover(
        airport_code=lv.get("id", ""),
        airport_name=lv.get("name", ""),
        duration_min=lv.get("duration", 0),
        overnight=lv.get("is_overnight", False),
    )


def _to_booking_option(o: dict) -> BookingOption:
    br = o.get("booking_request", {})
    return BookingOption(
        book_with=o.get("book_with", ""),
        fare_type=o.get("fare_type", ""),
        flight_numbers=o.get("flight_numbers", []),
        price=o.get("price"),
        baggage_prices=o.get("baggage_prices", []),
        extensions=o.get("extensions", []),
        booking_phone=o.get("booking_phone"),
        booking_request=BookingRequest(
            url=br.get("url", ""),
            post_data=br.get("post_data"),
        )
        if br
        else None,
        is_split_booking=o.get("is_split_booking", False),
    )


def to_airline_link(link_obj: dict) -> AirlineLink:
    return AirlineLink(
        airline_code=link_obj.get("airline_code", ""),
        airline=link_obj.get("airline", ""),
        url=link_obj.get("url", ""),
    )


def to_cheaper_alternative(a: dict) -> CheaperAlternative:
    return CheaperAlternative(
        departure=a.get("departure", ""),
        ret=a.get("return", ""),
        price=a.get("price", 0),
    )


class SearchApi(DataSourceIntegration[Result]):
    """The [SearchApi](https://searchapi.io) integration.

    Args:
        api_key (str, optional): The API key (or env variable `SEARCHAPI_KEY`).
    """

    __slots__: Final = ("key",)

    key: str

    def __init__(self, *, api_key: str | None = None):
        self.key = api_key or get_env("SEARCHAPI_KEY")

    @override
    def fetch(self, q: Query | str) -> Result:
        params = (
            params_from_url(q, self.key)
            if isinstance(q, str)
            else params_from_query(q, self.key)
        )

        resp = primp.get(BASE_URL, params=params)
        resp.raise_for_status()

        return to_result_model(typing.cast(dict, resp.json()))
