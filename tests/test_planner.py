import unittest
from datetime import date

from fares.planner import breaks_between, leave_dates, options_for, to_payload
from fares.windows import clusters

from tests.test_windows import HOLIDAYS


def options(anchor, max_leave=3):
    cluster = next(c for c in clusters(HOLIDAYS) if c.anchor == anchor)
    return options_for(cluster, HOLIDAYS, max_leave=max_leave)


class TestOptions(unittest.TestCase):
    def test_zero_leave_option_is_the_holiday_itself(self):
        free = options(date(2026, 8, 9))[0]
        self.assertEqual(free.leave_days, 0)
        self.assertEqual((free.window.depart, free.window.ret), (date(2026, 8, 8), date(2026, 8, 10)))
        self.assertEqual(free.days_off, 3)

    def test_thursday_holiday_is_four_days_for_one(self):
        vesak = next(o for o in options(date(2027, 5, 20)) if o.leave_days == 1)
        self.assertEqual(vesak.days_off, 4)
        self.assertEqual(vesak.value, 4.0)

    def test_budgets_that_buy_nothing_extra_are_dropped(self):
        # Hari Raya Puasa is a lone Wednesday: one day of leave still gives a
        # 2-day break, but two days reaches the weekend for five.
        found = {o.leave_days: o.days_off for o in options(date(2027, 3, 10))}
        self.assertEqual(found[0], 1)
        self.assertEqual(found[2], 5)
        self.assertTrue(all(found[a] < found[b] for a, b in zip(sorted(found), sorted(found)[1:])))

    def test_value_is_days_off_per_leave_day(self):
        for option in options(date(2026, 12, 25)):
            if option.leave_days:
                self.assertAlmostEqual(option.value, option.days_off / option.leave_days)
            else:
                self.assertEqual(option.value, float("inf"))

    def test_free_option_is_never_offered_as_best_value(self):
        brk = breaks_between(HOLIDAYS, date(2026, 1, 1), date(2028, 1, 1))[0]
        self.assertGreater(brk.best_value.leave_days, 0)


class TestLeaveDates(unittest.TestCase):
    def test_only_working_days_count(self):
        option = next(o for o in options(date(2026, 8, 9)) if o.leave_days == 1)
        booked = leave_dates(option.window, HOLIDAYS)
        self.assertEqual(booked, [date(2026, 8, 7)])  # the Friday, not the weekend

    def test_leave_dates_never_include_a_holiday(self):
        for option in options(date(2027, 5, 20)):
            for day in leave_dates(option.window, HOLIDAYS):
                self.assertNotIn(day, HOLIDAYS)
                self.assertLess(day.weekday(), 5)

    def test_count_matches_the_declared_budget(self):
        for option in options(date(2027, 2, 6)):
            self.assertEqual(len(leave_dates(option.window, HOLIDAYS)), option.leave_days)


class TestPayload(unittest.TestCase):
    def setUp(self):
        self.payload = to_payload(HOLIDAYS, date(2026, 8, 1), date(2027, 12, 31))

    def test_shape(self):
        self.assertIn("breaks", self.payload)
        self.assertIn("holidays", self.payload)
        first = self.payload["breaks"][0]
        self.assertEqual(
            set(first), {"name", "start", "end", "anchor", "options"}
        )

    def test_every_option_is_json_safe(self):
        import json

        json.dumps(self.payload)  # infinity would raise on a strict parser downstream
        for brk in self.payload["breaks"]:
            for option in brk["options"]:
                if option["leave_days"] == 0:
                    self.assertIsNone(option["value"])
                else:
                    self.assertIsInstance(option["value"], float)

    def test_out_of_range_breaks_are_excluded(self):
        narrow = to_payload(HOLIDAYS, date(2026, 8, 1), date(2026, 9, 1))
        self.assertEqual([b["name"] for b in narrow["breaks"]], ["National Day"])


if __name__ == "__main__":
    unittest.main()
