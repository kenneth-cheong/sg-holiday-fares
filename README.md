# Singapore holiday fares

Checks air fares out of Singapore for every public holiday travel window, records
what it sees, and messages Telegram when something is genuinely cheap rather than
merely available.

Runs entirely on free infrastructure: a GitHub Actions cron does the sweep, the
price history is committed to this repo as JSONL, and the dashboard is a static
page on GitHub Pages.

## What it actually does

1. **Holidays** come from [MOM's consolidated dataset on data.gov.sg][dataset],
   which already includes the "(Observed)" days gazetted when a holiday falls on
   a Sunday. A copy is cached in `data/holidays.json` so an outage degrades to
   stale-but-correct dates instead of a failed run.
2. **Travel windows** are derived, not listed. Each holiday expands into a
   maximal run of consecutive non-working days, plus variants that spend annual
   leave to bridge onto an adjacent weekend — Vesak 2027 falls on a Thursday, so
   one day of leave buys a four-day weekend, and that is the window worth
   pricing.
3. **Fares** come from Google Flights' internal endpoint via `fast-flights`.
   Chosen over Amadeus specifically because it sees the low-cost carriers that
   matter out of Singapore: Scoot, AirAsia, Jetstar, VietJet.
4. **History** accumulates one JSONL row per destination, window and day.
5. **Alerts** fire on either an absolute target you set per destination, or a
   drop against that window's own rolling median. The second is the one that
   matters — it knows SGD 600 to Bali is ordinary in March and a bargain at
   Christmas.

## Setup

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python sweep.py --dry-run --limit 5
```

### Telegram

1. Message [@BotFather](https://t.me/BotFather), send `/newbot`, follow the
   prompts, and copy the token it gives you.
2. Send your new bot any message (a bot cannot open a conversation with you).
3. Get your chat id:
   ```bash
   curl -s "https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates" | grep -o '"id":[0-9-]*' | head -1
   ```
4. In the repo: **Settings → Secrets and variables → Actions**, add
   `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.

The token is a bearer credential — anyone holding it can post as your bot. It
belongs in Actions secrets and never in the repo.

### Dashboard

Enable **Settings → Pages → Deploy from branch**, folder `/docs`. The page reads
`docs/data/dashboard.json`, which the sweep rewrites on every run. Locally:

```bash
python3 -m http.server -d docs 8000
```

## Configuration

Everything lives in `config.json`.

| Key | Meaning |
|---|---|
| `search.min_lead_days` | Ignore trips departing sooner than this — too late to act on |
| `search.lookahead_days` | Booking horizon; fares beyond ~6 months are not yet meaningful |
| `search.max_leave_days` | Annual leave the generator may spend bridging to a weekend |
| `search.max_windows_per_holiday` | Cap on candidate windows, cheapest-leave first |
| `alerts.min_observations` | Observations required before a relative alert can fire |
| `alerts.default_drop_pct` | How far below the median counts as a deal |
| `alerts.resend_after_days` | Cooldown before re-alerting an unchanged fare |
| `destinations[].max_stops` | `0` for nonstop only, `1` to allow a connection |
| `destinations[].alert_below` | Absolute price target, in the configured currency |

**Tune `alert_below` after a week of data.** The shipped values are guesses; on
the first run most destinations tripped their target, which is noise rather than
signal. The percentage rule needs `min_observations` days before it can help.

`max_stops` deserves a thought per destination. Over National Day 2026 the
cheapest Bangkok round trip was SGD 455 routed through Kuala Lumpur, against
SGD 726 for the cheapest nonstop — a real saving, and a genuinely worse weekend
if the trip is only three days.

## Layout

```
sweep.py              orchestrator and CLI
fares/holidays.py     data.gov.sg fetch + cached fallback
fares/windows.py      holidays -> candidate (depart, return) pairs
fares/sources.py      FareSource interface + Google Flights implementation
fares/store.py        JSONL history, alert state, dashboard payload
fares/alerts.py       baseline, thresholds, re-alert suppression
fares/notify.py       Telegram delivery
docs/index.html       dashboard (GitHub Pages)
docs/data/            history.jsonl + dashboard.json, written by the sweep
```

```bash
python -m unittest discover -s tests -t . -v
```

## Known limitations

**The fare source is unofficial.** `fast-flights` calls the endpoint Google
Flights' own frontend uses, so Google can change it without notice and the sweep
will start failing. Everything downstream is written against the `FareSource`
interface, so replacing it with SerpAPI or Amadeus means implementing one method.
`fast-flights` is pinned for the same reason — an unattended upgrade is a
plausible way for this to break quietly.

**A round-trip result describes only the outbound.** Google returns the total
round-trip price alongside the outbound itinerary; the return legs are not in the
payload. So the stop count and carrier shown describe the outbound journey, while
the price is for both. Fine for tracking prices, wrong if you want itinerary
detail.

**Results depend on where the query comes from.** Google Flights returns
different inventory by point of sale, and GitHub's runners are not in Singapore.
Measured on the first run: the Jakarta window of 7–9 Nov 2026 returned a nonstop
TransNusa at SGD 227 from a Singapore connection, and *no nonstop at all* from
the US-based runner minutes later. Currency is pinned to SGD so the numbers stay
comparable, but expect the history to drift from what you see booking at home,
and expect occasional "no offers matched the stop limit" gaps on nonstop-only
destinations. Trends stay valid because every observation comes from the same
place; absolute prices are indicative. A self-hosted in-region runner is the
only real fix.

**Daily sampling smooths over intraday volatility.** Fares move hour to hour. A
single observation is weak evidence; the trend is the signal.

**Prices are for one adult in economy**, and are what Google displays rather than
a guaranteed bookable fare. Always confirm with the airline.

[dataset]: https://data.gov.sg/datasets/d_8ef23381f9417e4d4254ee8b4dcdb176/view
