import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from analyze_policy import YEARS, comparison, contrasts, state_rates


class PolicyAnalysisTests(unittest.TestCase):
    def test_did_weights_control_states_equally_and_excludes_transition(self):
        rows = []
        for year in YEARS:
            post = year > 2010
            rows.extend([
                ("12", year, 10 - 2 * post + 90 * (year == 2010), .1),
                ("10", year, 3 + post, .1),
                ("26", year, 9 + 3 * post, .1),
            ])
        states = pd.DataFrame(rows, columns=["state_fips", "year", "rate", "county_jackknife_se"])
        annual = comparison(states, "12", ["10", "26"])
        results = contrasts(annual, 2010)
        self.assertEqual(results["treated_change"], -2)
        self.assertEqual(results["control_change"], 2)
        self.assertEqual(results["did"], -4)
        self.assertNotEqual(contrasts(annual, 2010, include_transition=True)["did"], -4)

    def test_rate_uses_observed_county_population_and_keeps_coverage_denominator(self):
        panel = pd.DataFrame({
            "state_fips": ["12"] * 3, "year": [2010] * 3,
            "county_fips": ["12001", "12003", "12005"],
            "population": [100, 300, 100], "outcome": [1, 3, np.nan],
        })
        rates = state_rates(panel, "outcome", panel.outcome.notna()).iloc[0]
        self.assertAlmostEqual(rates.rate, 2.5)
        self.assertAlmostEqual(rates.population_share, .8)
        self.assertEqual(rates.counties, 2)
        self.assertAlmostEqual(rates.county_jackknife_se, 1)
