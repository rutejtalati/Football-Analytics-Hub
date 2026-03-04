from __future__ import annotations

import unittest

from backend.services.providers.apifootball_provider import ApiFootballProvider


class PredictionMathTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = ApiFootballProvider()

    def test_poisson_distribution_mass_is_close_to_one(self) -> None:
        probs = self.provider._score_probs(1.55, 1.12, rho=-0.05, max_goals=10)
        total = sum(p for _h, _a, p in probs)
        self.assertAlmostEqual(total, 1.0, places=6)

    def test_outcome_probabilities_sum_to_one(self) -> None:
        probs = self.provider._score_probs(1.91, 0.88, rho=-0.04, max_goals=10)
        p_home = sum(p for h, a, p in probs if h > a)
        p_draw = sum(p for h, a, p in probs if h == a)
        p_away = sum(p for h, a, p in probs if h < a)
        self.assertAlmostEqual(p_home + p_draw + p_away, 1.0, places=6)


if __name__ == "__main__":
    unittest.main()
