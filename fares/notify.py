"""Telegram delivery. Standard library only — no dependency worth adding for one POST."""

from __future__ import annotations

import html
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import date

from .alerts import Alert

API = "https://api.telegram.org/bot{token}/sendMessage"
MAX_LEN = 3800  # Telegram's hard limit is 4096; leave room for the chunk suffix
BLOCKS = "▁▂▃▄▅▆▇█"


def sparkline(values: list[int]) -> str:
    """A price trend small enough to sit inline in a chat message."""
    values = [v for v in values if v is not None]
    if len(values) < 2:
        return ""
    low, high = min(values), max(values)
    if high == low:
        return BLOCKS[0] * len(values)
    span = high - low
    return "".join(BLOCKS[min(int((v - low) / span * (len(BLOCKS) - 1)), len(BLOCKS) - 1)] for v in values[-24:])


def _stops_label(stops: int) -> str:
    return "nonstop" if stops == 0 else f"{stops} stop" if stops == 1 else f"{stops} stops"


def format_alert(alert: Alert) -> str:
    offer = alert.offer
    esc = html.escape
    airlines = ", ".join(offer.airlines) or "unknown carrier"

    lines = [
        f"<b>{esc(alert.dest_name)} ({esc(alert.dest)})</b> — "
        f"<b>{esc(offer.currency)} {offer.price:,}</b>",
        f"{esc(alert.window.label)}",
        f"{esc(airlines)} · {_stops_label(offer.stops)} · <code>{esc(offer.route)}</code>",
        "· " + "; ".join(esc(r) for r in alert.reasons),
    ]

    if alert.nonstop and alert.nonstop.price != offer.price:
        lines.append(f"cheapest nonstop: {esc(offer.currency)} {alert.nonstop.price:,}")

    trend = sparkline(alert.series)
    if trend:
        lines.append(f"<code>{trend}</code> last {min(len(alert.series), 24)} checks")

    return "\n".join(lines)


def compose(alerts: list[Alert], today: date) -> str:
    """Group alerts by holiday so the message reads as a digest, not a stream."""
    by_holiday: dict[tuple[str, str], list[Alert]] = {}
    for alert in alerts:
        by_holiday.setdefault((alert.window.anchor.isoformat(), alert.window.name), []).append(alert)

    count = len(alerts)
    header = f"✈️ <b>{count} fare alert{'s' if count != 1 else ''}</b> — {today:%a %-d %b %Y}"
    blocks = [header]

    for (anchor, name), group in sorted(by_holiday.items()):
        when = date.fromisoformat(anchor)
        blocks.append(f"\n<b>━ {html.escape(name)} · {when:%-d %b %Y}</b>")
        for alert in sorted(group, key=lambda a: a.offer.price):
            blocks.append("\n" + format_alert(alert))

    return "\n".join(blocks)


def _chunks(text: str) -> list[str]:
    if len(text) <= MAX_LEN:
        return [text]

    parts, current = [], ""
    for block in text.split("\n\n"):
        if len(current) + len(block) + 2 > MAX_LEN and current:
            parts.append(current)
            current = block
        else:
            current = f"{current}\n\n{block}" if current else block
    if current:
        parts.append(current)
    return parts


def send(token: str, chat_id: str, text: str, timeout: int = 20) -> bool:
    """Post to Telegram. Never raises — a failed notification must not fail the sweep."""
    ok = True
    for chunk in _chunks(text):
        payload = urllib.parse.urlencode(
            {
                "chat_id": chat_id,
                "text": chunk,
                "parse_mode": "HTML",
                "disable_web_page_preview": "true",
            }
        ).encode()

        request = urllib.request.Request(API.format(token=token), data=payload)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
                if not body.get("ok"):
                    print(f"[telegram] rejected: {body}")
                    ok = False
        except urllib.error.HTTPError as exc:
            print(f"[telegram] HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:200]}")
            ok = False
        except Exception as exc:
            print(f"[telegram] send failed: {exc}")
            ok = False

    return ok
