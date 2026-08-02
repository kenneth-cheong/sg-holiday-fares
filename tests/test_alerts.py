import unittest
from datetime import date

from fares.alerts import assess, baseline_for, record_sent, should_send
from fares.notify import sparkline
from fares.sources import Offer, pick_offers
from fares.windows import TravelWindow

WINDOW = TravelWindow("Deepavali", date(2026, 11, 8), date(2026, 11, 7), date(2026, 11, 9), 0)


def offer(price, stops=0, airlines=("Scoot",)):
    return Offer(price, "SGD", airlines, stops, "SIN-BKK", "google-flights")


def assess_with(price, prior, target=None, drop_pct=20, min_observations=5, lookback=30):
    return assess(
        dest_code="BKK",
        dest_name="Bangkok",
        window=WINDOW,
        offer=offer(price),
        nonstop=None,
        prior=prior,
        target=target,
        drop_pct=drop_pct,
        min_observations=min_observations,
        lookback=lookback,
    )


class TestBaseline(unittest.TestCase):
    def test_withholds_a_baseline_until_there_is_enough_history(self):
        self.assertIsNone(baseline_for([500, 510, 490], min_observations=5, lookback=30))

    def test_uses_the_median_not_the_mean(self):
        # One 3000 outlier must not drag the baseline up; the median ignores it.
        self.assertEqual(baseline_for([500, 510, 490, 505, 3000], 5, 30), 505)

    def test_lookback_limits_how_far_back_it_reaches(self):
        prior = [1000] * 10 + [400, 402, 404, 406, 408]
        self.assertEqual(baseline_for(prior, 5, 5), 404)


class TestAssess(unittest.TestCase):
    def test_absolute_target_fires_with_no_history(self):
        alert = assess_with(300, prior=[], target=350)
        self.assertIsNotNone(alert)
        self.assertIn("target", alert.reasons[0])

    def test_no_alert_when_price_is_ordinary(self):
        self.assertIsNone(assess_with(500, prior=[490, 500, 510, 495, 505], target=350))

    def test_relative_drop_fires_against_the_baseline(self):
        alert = assess_with(400, prior=[500, 500, 500, 500, 500], target=None)
        self.assertIsNotNone(alert)
        self.assertEqual(alert.baseline, 500)
        self.assertAlmostEqual(alert.drop_pct, 20.0)

    def test_drop_just_under_the_threshold_stays_quiet(self):
        self.assertIsNone(assess_with(401, prior=[500] * 5, target=None, drop_pct=20))

    def test_series_includes_the_new_observation(self):
        alert = assess_with(300, prior=[500, 400], target=350)
        self.assertEqual(alert.series, [500, 400, 300])


class TestSuppression(unittest.TestCase):
    def setUp(self):
        self.state = {}
        record_sent(self.state, "BKK|x", 400, date(2026, 8, 1))

    def _should(self, price, today, after=7, cheaper=5):
        return should_send(
            self.state, "BKK|x", price, today, resend_after_days=after, resend_if_cheaper_pct=cheaper
        )

    def test_first_alert_always_sends(self):
        self.assertTrue(should_send({}, "new", 400, date(2026, 8, 1), resend_after_days=7, resend_if_cheaper_pct=5))

    def test_same_price_next_day_is_suppressed(self):
        self.assertFalse(self._should(400, date(2026, 8, 2)))

    def test_materially_cheaper_breaks_through(self):
        self.assertTrue(self._should(370, date(2026, 8, 2)))  # 7.5% cheaper

    def test_marginally_cheaper_does_not(self):
        self.assertFalse(self._should(395, date(2026, 8, 2)))  # 1.3% cheaper

    def test_cooldown_expiry_allows_a_reminder(self):
        self.assertFalse(self._should(400, date(2026, 8, 7)))
        self.assertTrue(self._should(400, date(2026, 8, 8)))


class TestPickOffers(unittest.TestCase):
    def test_stop_limit_is_enforced_locally(self):
        offers = [offer(300, stops=1), offer(500, stops=0)]
        best, nonstop = pick_offers(offers, max_stops=0)
        self.assertEqual(best.price, 500)  # the 1-stop is excluded despite being cheaper
        self.assertEqual(nonstop.price, 500)

    def test_nonstop_is_reported_alongside_a_cheaper_connection(self):
        offers = [offer(300, stops=1), offer(500, stops=0)]
        best, nonstop = pick_offers(offers, max_stops=1)
        self.assertEqual((best.price, nonstop.price), (300, 500))

    def test_no_nonstop_available(self):
        best, nonstop = pick_offers([offer(300, stops=1)], max_stops=1)
        self.assertEqual(best.price, 300)
        self.assertIsNone(nonstop)

    def test_empty_input(self):
        self.assertEqual(pick_offers([], max_stops=1), (None, None))


class TestSparkline(unittest.TestCase):
    def test_too_short_to_plot(self):
        self.assertEqual(sparkline([100]), "")

    def test_flat_series_renders_flat(self):
        self.assertEqual(sparkline([100, 100, 100]), "▁▁▁")

    def test_endpoints_span_the_block_range(self):
        line = sparkline([100, 200, 300])
        self.assertTrue(line.startswith("▁"))
        self.assertTrue(line.endswith("█"))

    def test_long_series_is_truncated_to_the_recent_tail(self):
        self.assertEqual(len(sparkline(list(range(100)))), 24)


if __name__ == "__main__":
    unittest.main()
