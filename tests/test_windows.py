import unittest
from datetime import date

from fares.windows import clusters, upcoming, windows_for

# Real gazetted dates from data.gov.sg, chosen to cover each awkward shape:
# a Sun+Mon observed pair, a lone Thursday, a lone Wednesday, and a three-day
# CNY run that already spans a weekend.
HOLIDAYS = {
    date(2026, 8, 9): "National Day",
    date(2026, 8, 10): "National Day (Observed)",
    date(2026, 12, 25): "Christmas Day",
    date(2027, 1, 1): "New Year's Day",
    date(2027, 2, 6): "Chinese New Year",
    date(2027, 2, 7): "Chinese New Year",
    date(2027, 2, 8): "Chinese New Year (Observed)",
    date(2027, 3, 10): "Hari Raya Puasa",
    date(2027, 5, 20): "Vesak Day",
}


class TestClusters(unittest.TestCase):
    def test_observed_day_merges_with_its_weekend(self):
        national = next(c for c in clusters(HOLIDAYS) if c.anchor == date(2026, 8, 9))
        self.assertEqual(national.start, date(2026, 8, 8))  # Saturday
        self.assertEqual(national.end, date(2026, 8, 10))  # Monday in lieu
        self.assertEqual(national.name, "National Day")  # "(Observed)" stripped, not duplicated

    def test_cny_run_is_a_single_cluster(self):
        cny = [c for c in clusters(HOLIDAYS) if c.anchor == date(2027, 2, 6)]
        self.assertEqual(len(cny), 1)
        self.assertEqual((cny[0].start, cny[0].end), (date(2027, 2, 6), date(2027, 2, 8)))

    def test_lone_midweek_holiday_stands_alone(self):
        vesak = next(c for c in clusters(HOLIDAYS) if c.anchor == date(2027, 5, 20))
        self.assertEqual((vesak.start, vesak.end), (date(2027, 5, 20), date(2027, 5, 20)))

    def test_every_holiday_lands_in_exactly_one_cluster(self):
        covered = [d for c in clusters(HOLIDAYS) for d in HOLIDAYS if c.start <= d <= c.end]
        self.assertEqual(len(covered), len(HOLIDAYS))


class TestWindows(unittest.TestCase):
    def _windows(self, anchor, **kwargs):
        cluster = next(c for c in clusters(HOLIDAYS) if c.anchor == anchor)
        return windows_for(cluster, HOLIDAYS, **kwargs)

    def test_long_weekend_offers_a_no_leave_option(self):
        windows = self._windows(date(2026, 8, 9))
        free = [w for w in windows if w.leave_days == 0]
        self.assertEqual(len(free), 1)
        self.assertEqual((free[0].depart, free[0].ret), (date(2026, 8, 8), date(2026, 8, 10)))
        self.assertEqual(free[0].nights, 2)

    def test_thursday_holiday_bridges_to_a_four_day_weekend(self):
        # Vesak falls Thu 20 May 2027: one day of leave on the Friday should
        # absorb the weekend and return Sunday.
        windows = self._windows(date(2027, 5, 20), max_leave=1)
        self.assertEqual(len(windows), 1)
        self.assertEqual((windows[0].depart, windows[0].ret), (date(2027, 5, 20), date(2027, 5, 23)))
        self.assertEqual(windows[0].leave_days, 1)

    def test_wednesday_holiday_needs_two_days_to_reach_a_weekend(self):
        # Hari Raya Puasa falls Wed 10 Mar 2027. One day of leave cannot reach a
        # weekend, so nothing qualifies; two days reaches it in both directions.
        self.assertEqual(self._windows(date(2027, 3, 10), max_leave=1), [])

        windows = self._windows(date(2027, 3, 10), max_leave=2)
        spans = {(w.depart, w.ret) for w in windows}
        self.assertIn((date(2027, 3, 6), date(2027, 3, 10)), spans)  # Sat before
        self.assertIn((date(2027, 3, 10), date(2027, 3, 14)), spans)  # Sun after

    def test_night_bounds_are_respected(self):
        windows = self._windows(date(2027, 2, 6), max_leave=2, min_nights=4, max_nights=6)
        self.assertTrue(windows)
        self.assertTrue(all(4 <= w.nights <= 6 for w in windows))

    def test_limit_prefers_cheaper_leave(self):
        windows = self._windows(date(2027, 2, 6), max_leave=2, limit=2)
        self.assertEqual(len(windows), 2)
        self.assertEqual([w.leave_days for w in windows], sorted(w.leave_days for w in windows))

    def test_windows_are_deduplicated(self):
        windows = self._windows(date(2026, 8, 9), max_leave=2, limit=99)
        spans = [(w.depart, w.ret) for w in windows]
        self.assertEqual(len(spans), len(set(spans)))


class TestUpcoming(unittest.TestCase):
    def test_booking_horizon_excludes_imminent_and_distant_trips(self):
        today = date(2026, 8, 2)

        # National Day departs in 6 days — inside the lead time, so not actionable.
        soon = upcoming(HOLIDAYS, today, min_lead_days=14, lookahead_days=180)
        self.assertFalse([w for w in soon if w.anchor == date(2026, 8, 9)])

        # Christmas is 145 days out and should be picked up.
        self.assertTrue([w for w in soon if w.anchor == date(2026, 12, 25)])

        # Vesak 2027 is beyond a 180-day horizon.
        self.assertFalse([w for w in soon if w.anchor == date(2027, 5, 20)])

    def test_dropping_the_lead_time_surfaces_the_imminent_break(self):
        today = date(2026, 8, 2)
        relaxed = upcoming(HOLIDAYS, today, min_lead_days=1, lookahead_days=180)
        self.assertTrue([w for w in relaxed if w.anchor == date(2026, 8, 9)])


if __name__ == "__main__":
    unittest.main()
