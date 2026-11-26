import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from build_county_panel import align_mortality, build_panel
from clean_mortality import CAUSES


def mortality_row(fips, year, value=None, status="not_in_export"):
    row = {"county_fips": fips, "year": year}
    for cause in CAUSES.values():
        row[f"deaths_{cause}"] = value
        row[f"deaths_{cause}_reported"] = value is not None
        row[f"deaths_{cause}_status"] = status
    return row


def fixtures():
    population, shipments, deaths, drugs = [], [], [], []
    for fips in ["01001", "01003"]:
        for year in range(2006, 2016):
            population.append(dict(county_fips=fips, year=year, population=1000))
            observed = fips == "01001"
            shipments.append(dict(county_fips=fips, year=year,
                base_weight_g=100 if observed else None, shipment_records=20 if observed else None,
                largest_shipment_g=5 if observed else None,
                hydrocodone_oxycodone_g=100 if observed else None,
                hydrocodone_oxycodone_records=20 if observed else None, has_shipment_record=observed))
            if observed:
                deaths.append(mortality_row(fips, year, 10 if year == 2006 else None,
                                           "reported" if year == 2006 else "explicit_missing" if year == 2007 else "not_in_export"))
                drugs.append(dict(county_fips=fips, year=year, drug_code="9143", base_weight_g=100,
                                  upper_1pct_within_drug_year=True, dominant_single_shipment=False))
    return tuple(pd.DataFrame(rows) for rows in [population, shipments, deaths, drugs])


class PanelTests(unittest.TestCase):
    def test_missingness_rates_and_suppression_bounds_survive_merge(self):
        panel, drugs = build_panel(*fixtures())
        self.assertEqual(len(panel), 20)
        index = panel.set_index(["county_fips", "year"])
        self.assertEqual(index.loc[("01001", 2006), "deaths_unintentional_per_100k"], 1000)
        self.assertEqual(index.loc[("01001", 2006), "hydrocodone_oxycodone_g_per_person"], .1)
        self.assertTrue(pd.isna(index.loc[("01001", 2007), "deaths_unintentional_upper"]))
        self.assertEqual(index.loc[("01001", 2008), "deaths_unintentional_upper"], 9)
        self.assertTrue(pd.isna(index.loc[("01001", 2008), "deaths_unintentional"]))
        self.assertFalse(index.loc[("01003", 2006), "has_mortality_source_row"])
        self.assertTrue(pd.isna(index.loc[("01003", 2006), "base_weight_g"]))
        self.assertFalse(panel.joint_complete_2006_2015.any())
        self.assertTrue(drugs.grams_per_person.eq(.1).all())

    def test_duplicate_merge_keys_fail(self):
        population, shipments, deaths, drugs = fixtures()
        shipments = pd.concat([shipments, shipments.iloc[[0]]])
        with self.assertRaisesRegex(ValueError, "duplicate"):
            build_panel(population, shipments, deaths, drugs)

    def test_partial_bedford_total_is_not_used_as_complete_mortality(self):
        population = pd.DataFrame([dict(county_fips="51019", year=2010)])
        deaths = pd.DataFrame([mortality_row("51019", 2010, 20, "reported"),
                               mortality_row("51515", 2010)])
        panel = align_mortality(deaths, population)
        self.assertEqual(panel.deaths_unintentional_reported_subtotal.iloc[0], 20)
        self.assertTrue(pd.isna(panel.deaths_unintentional.iloc[0]))
        self.assertEqual(panel.deaths_unintentional_status.iloc[0], "boundary_not_comparable")
        self.assertTrue(pd.isna(panel.deaths_unintentional_upper.iloc[0]))

    def test_unexpected_mortality_geography_is_not_dropped(self):
        population = pd.DataFrame([dict(county_fips="01001", year=2010)])
        with self.assertRaisesRegex(ValueError, "outside"):
            align_mortality(pd.DataFrame([mortality_row("99999", 2010)]), population)


if __name__ == "__main__":
    unittest.main()
