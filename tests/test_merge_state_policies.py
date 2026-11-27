import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from merge_state_policies import POLICIES, prepare_state_policies


class StatePolicyTests(unittest.TestCase):
    def test_cli_merges_policy_by_state_year_with_retained_parquet_index(self):
        panel = pd.DataFrame(
            {"county_fips": ["53001", "12001"], "state_fips": ["53", "12"], "year": [2011, 2010]},
            index=[10, 20],
        )
        source = pd.DataFrame([
            {"state_fips": fips, "year": year, **dict.fromkeys(POLICIES, 0)}
            for fips in (12, 53)
            for year in (2009, 2010, 2011)
        ])
        source.loc[source.state_fips.eq(53) & source.year.eq(2011), "pdmp_access"] = 1
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source.to_stata(root / "source.dta", write_index=False)
            panel.to_parquet(root / "panel.parquet", index=True)
            subprocess.run([
                sys.executable, "-B", str(Path(__file__).resolve().parents[1] / "src" / "merge_state_policies.py"),
                "--sppd", str(root / "source.dta"), "--county-panel", str(root / "panel.parquet"),
                "--output", str(root / "output"),
            ], check=True, capture_output=True, text=True)
            output = pd.read_parquet(root / "output" / "county_year_with_policies.parquet")

        pd.testing.assert_frame_equal(output[panel.columns], panel.reset_index(drop=True))
        self.assertEqual(output.state_regulation_change.tolist(), [True, False])

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
