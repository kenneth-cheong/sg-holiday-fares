"""Decide which observed fares are worth waking someone up for.

Two independent triggers. An absolute target works from the first run — you
already know roughly what a cheap fare to Bali looks like. A relative drop needs
history first, and is the one that actually earns its keep: it knows that SGD
600 to Bali is unremarkable in March and a bargain over Christmas.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from statistics import median

from .sources import Offer
from .windows import TravelWindow


@dataclass
class Alert:
    dest: str
    dest_name: str
    window: TravelWindow
    offer: Offer
    nonstop: Offer | None
    baseline: int | None
    drop_pct: float | None
    reasons: list[str] = field(default_factory=list)
    series: list[int] = field(default_factory=list)


def baseline_for(prior: list[int], min_observations: int, lookback: int) -> int | None:
    """Rolling median of recent observations, or None until there is enough history."""
    if len(prior) < min_observations:
        return None
    return int(median(prior[-lookback:]))


def assess(
    *,
    dest_code: str,
    dest_name: str,
    window: TravelWindow,
    offer: Offer,
    nonstop: Offer | None,
    prior: list[int],
    target: int | None,
    drop_pct: float,
    min_observations: int,
    lookback: int,
) -> Alert | None:
    baseline = baseline_for(prior, min_observations, lookback)
    reasons: list[str] = []

    if target is not None and offer.price <= target:
        reasons.append(f"at or under your {offer.currency} {target:,} target")

    actual_drop: float | None = None
    if baseline:
        actual_drop = (baseline - offer.price) / baseline * 100
        if actual_drop >= drop_pct:
            reasons.append(
                f"{actual_drop:.0f}% below the {offer.currency} {baseline:,} usual price"
            )

    if not reasons:
        return None

    return Alert(
        dest=dest_code,
        dest_name=dest_name,
        window=window,
        offer=offer,
        nonstop=nonstop,
        baseline=baseline,
        drop_pct=actual_drop,
        reasons=reasons,
        series=prior + [offer.price],
    )


def should_send(
    state: dict, key: str, price: int, today: date, *, resend_after_days: int, resend_if_cheaper_pct: float
) -> bool:
    """Suppress repeats of an alert already sent, unless it got materially cheaper.

    Without this a fare that sits below target for three weeks sends twenty-one
    identical messages and trains you to ignore the channel.
    """
    previous = state.get(key)
    if not previous:
        return True

    if price <= previous["price"] * (1 - resend_if_cheaper_pct / 100):
        return True

    sent_on = datetime.strptime(previous["sent_on"], "%Y-%m-%d").date()
    return (today - sent_on).days >= resend_after_days


def record_sent(state: dict, key: str, price: int, today: date) -> None:
    state[key] = {"price": price, "sent_on": today.isoformat()}
