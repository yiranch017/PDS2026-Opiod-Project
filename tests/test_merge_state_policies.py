import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from merge_state_policies import POLICIES, prepare_state_policies


class StatePolicyTests(unittest.TestCase):
    def test_focal_adoption_and_unknown_coverage(self):
        rows = [
            {"state_fips": fips, "year": year, **dict.fromkeys(POLICIES, 0)}
            for fips in (1, 12)
            for year in range(2005, 2016)
        ]
        source = pd.DataFrame(rows)
        source.loc[source.state_fips.eq(12) & source.year.ge(2010), "pillmill"] = 1
        source.loc[source.state_fips.eq(1) & source.year.ge(2006), "pdmp_access"] = 1
        result = prepare_state_policies(source, {"01", "11", "12"}).set_index(["state_fips", "year"])

        self.assertTrue(result.loc[("12", 2010), "focal_pain_clinic_adoption"])
        self.assertFalse(result.loc[("12", 2010), "state_regulation_change"])
        self.assertTrue(result.loc[("01", 2006), "state_regulation_change"])
        self.assertFalse(result.loc[("01", 2007), "state_regulation_change"])
        self.assertTrue(result.loc[("01", 2015), "state_regulation_change_during_study"])
        self.assertTrue(pd.isna(result.loc[("11", 2015), "state_regulation_change_during_study"]))
