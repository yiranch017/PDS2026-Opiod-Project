import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from clean_population import prepare_population


class PopulationTests(unittest.TestCase):
    def sources(self, root, invalid=False, wrong_total=False):
        paths = []
        for early, years in [(True, range(2006, 2010)), (False, range(2010, 2016))]:
            rows = [("40", "51", "0", "Virginia", "Virginia", 1100),
                    ("50", "51", "19", "Virginia", "Bedford County", 1000),
                    ("50", "51", "515", "Virginia", "Bedford city", 100)] if early else [
                        ("040", "51", "000", "Virginia", "Virginia", 1500),
                        ("050", "51", "019", "Virginia", "Bedford County", 1500)]
            rows += [("40", "46", "0", "South Dakota", "South Dakota", 200),
                     ("50", "46", "113" if early else "102", "South Dakota",
                      "Shannon County" if early else "Oglala Lakota County", 200)]
            records = []
            for level, state, county, state_name, name, pop in rows:
                row = dict(SUMLEV=level, STATE=state, COUNTY=county, STNAME=state_name, CTYNAME=name)
                for year in years:
                    row[f"POPESTIMATE{year}"] = pop
                if invalid and county in ["19", "019"]:
                    row[f"POPESTIMATE{min(years)}"] = 0
                if wrong_total and level in ["40", "040"]:
                    row[f"POPESTIMATE{min(years)}"] += 1
                records.append(row)
            path = root / ("early.csv" if early else "late.csv")
            pd.DataFrame(records).to_csv(path, index=False)
            paths.append(path)
        return paths

    def test_annual_population_preserves_totals_and_harmonizes_codes(self):
        with tempfile.TemporaryDirectory() as temp:
            panel, audit = prepare_population(*self.sources(Path(temp)))
        self.assertEqual(len(panel), 20)
        self.assertEqual(set(panel.county_fips), {"51019", "46102"})
        bedford = panel[panel.county_fips.eq("51019")].set_index("year")
        self.assertEqual(bedford.loc[2006, "population"], 1100)
        self.assertEqual(bedford.loc[2010, "population"], 1500)
        self.assertEqual(bedford.loc[2006, "population_components"], 2)
        self.assertEqual(bedford.loc[2010, "population_components"], 1)
        self.assertTrue(audit["validated"])

    def test_zero_population_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, "positive"):
                prepare_population(*self.sources(Path(temp), invalid=True))

    def test_state_total_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, "state totals"):
                prepare_population(*self.sources(Path(temp), wrong_total=True))


if __name__ == "__main__":
    unittest.main()
