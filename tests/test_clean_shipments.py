"""Check geographic matching, buyer selection, and cleaning accounting."""

import argparse
import csv
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from clean_shipments import buyer_group, clean, match_counties, normalize_county, summarize_main
from test_process_arcos import make_archive, options, record
from process_arcos import run


class CleaningTests(unittest.TestCase):
    def test_name_variants_match_without_collapsing_cities(self):
        self.assertEqual(normalize_county("Saint Johns"), normalize_county("St. Johns County"))
        self.assertEqual(normalize_county("DE SOTO"), normalize_county("DeSoto County"))
        self.assertEqual(normalize_county("DONA ANA"), normalize_county("Doña Ana County"))
        self.assertNotEqual(normalize_county("Fairfax city"), normalize_county("Fairfax County"))

    def test_city_alias_must_be_unambiguous_and_year_specific(self):
        reference = pd.DataFrame([
            {"state": "VA", "state_fips": "51", "county_name": "Fairfax County", "county_fips": "51059", "year": 2010},
            {"state": "VA", "state_fips": "51", "county_name": "Fairfax city", "county_fips": "51600", "year": 2010},
            {"state": "VA", "state_fips": "51", "county_name": "Alexandria city", "county_fips": "51510", "year": 2010},
        ])
        reference["county_key"] = reference.county_name.map(normalize_county)
        shipments = pd.DataFrame([
            {"buyer_state": "VA", "buyer_county": "FAIRFAX", "year": 2010},
            {"buyer_state": "VA", "buyer_county": "FAIRFAX CITY", "year": 2010},
            {"buyer_state": "VA", "buyer_county": "ALEXANDRIA", "year": 2010},
            {"buyer_state": "VA", "buyer_county": "ALEXANDRIA", "year": 2011},
        ])
        result = match_counties(shipments, reference)
        self.assertEqual(result.county_fips.iloc[:3].tolist(), ["51059", "51600", "51510"])
        self.assertTrue(pd.isna(result.county_fips.iloc[3]))
        self.assertEqual(result.match_method.iloc[2], "unique_city_alias")

    def test_direct_care_does_not_include_upstream_or_animal_buyers(self):
        for label in ["RETAIL PHARMACY", "CHAIN PHARMACY"]:
            self.assertEqual(buyer_group(label), "pharmacy")
        for label in ["PRACTITIONER", "PRACTITIONER-DW/100", "MLP-NURSE PRACTITIONER-DW/30SW"]:
            self.assertEqual(buyer_group(label), "practitioner")
        for label in ["DISTRIBUTOR", "REVERSE DISTRIB", "MANUFACTURER", "MLP-ANIMAL SHELTER", "HOSPITAL/CLINIC", "M/O PHARMACY"]:
            self.assertNotIn(buyer_group(label), {"pharmacy", "practitioner"})

    def test_documented_geography_changes_match_census_targets(self):
        reference = pd.DataFrame([
            {"state": "SD", "state_fips": "46", "county_name": "Oglala Lakota County", "county_fips": "46102", "year": 2010},
            {"state": "VA", "state_fips": "51", "county_name": "Bedford County", "county_fips": "51019", "year": 2010},
        ])
        reference["county_key"] = reference.county_name.map(normalize_county)
        shipments = pd.DataFrame([
            {"buyer_state": "SD", "buyer_county": "SHANNON", "year": 2010},
            {"buyer_state": "VA", "buyer_county": "BEDFORD CITY", "year": 2010},
            {"buyer_state": "CT", "buyer_county": "ANDERSON", "year": 2010},
        ])
        result = match_counties(shipments, reference)
        self.assertEqual(result.county_fips.iloc[:2].tolist(), ["46102", "51019"])
        self.assertTrue(result.match_method.iloc[:2].eq("documented_geography_change").all())
        self.assertTrue(pd.isna(result.county_fips.iloc[2]))

    def test_large_shipments_are_flagged_without_removing_their_weight(self):
        matched = pd.DataFrame([
            {"county_fips": "12027", "year": 2010, "drug_code": "9143", "drug_name": "OXYCODONE", "buyer_group": "pharmacy", "base_weight_g": 1009.0, "shipment_records": 10, "largest_shipment_g": 1000.0},
            {"county_fips": "12043", "year": 2010, "drug_code": "9143", "drug_name": "OXYCODONE", "buyer_group": "pharmacy", "base_weight_g": 10.0, "shipment_records": 10, "largest_shipment_g": 1.0},
        ])
        by_drug, county_year = summarize_main(matched)
        self.assertEqual(county_year.base_weight_g.sum(), 1019.0)
        row = by_drug.set_index("county_fips").loc["12027"]
        self.assertTrue(row.dominant_single_shipment)
        self.assertTrue(row.upper_1pct_within_drug_year)
        self.assertEqual(row.base_weight_g, 1009.0)

    def test_cleaning_reconciles_exclusions_and_keeps_missing_counties_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "shipments.zip"
            records = [
                record("2", BUYER_COUNTY="DE SOTO"),
                record("3", BUYER_COUNTY="DESOTO", BUYER_BUS_ACT="PRACTITIONER"),
                record("1000", BUYER_COUNTY="DE SOTO", BUYER_BUS_ACT="DISTRIBUTOR"),
                record("20", BUYER_STATE="PR", BUYER_COUNTY="SAN JUAN"),
                record("7", BUYER_COUNTY="UNKNOWN COUNTY"),
            ]
            make_archive(archive, records)
            source = root / "source"
            run(options(archive, source, start_year=2010, end_year=2010))
            mortality = root / "mortality.zip"
            text = io.StringIO()
            writer = csv.writer(text, delimiter="\t")
            writer.writerow(["County", "County Code", "Year"])
            writer.writerow(["DeSoto County, FL", "12027", "2010.0"])
            writer.writerow(["Glades County, FL", "12043", "2010.0"])
            writer.writerow(["Autauga County, AL", "1001", "2010.0"])
            with zipfile.ZipFile(mortality, "w") as zip_file:
                zip_file.writestr("Underlying Cause of Death, 2010.txt", text.getvalue())
            output = root / "clean"
            audit = clean(argparse.Namespace(
                shipments=source / "shipments_by_buyer_type.parquet", mortality_zip=mortality,
                output=output, allow_pilot=False, max_memory_mib=1024, min_free_gib=0,
            ))
            self.assertTrue(audit["validated"])
            self.assertEqual(audit["main_weight_g"], 5)
            self.assertEqual(audit["main_records"], 2)
            self.assertEqual(audit["excluded_buyer_records"], 1)
            self.assertEqual(audit["outside_scope_records"], 1)
            self.assertEqual(audit["unmatched_direct_buyer_records"], 1)
            panel = pd.read_parquet(output / "shipments_county_year.parquet").set_index("county_fips")
            self.assertEqual(panel.loc["12027", "base_weight_g"], 5)
            self.assertEqual(panel.loc["12027", "largest_shipment_g"], 3)
            self.assertTrue(pd.isna(panel.loc["12043", "base_weight_g"]))
            self.assertFalse(panel.loc["12043", "has_shipment_record"])
            self.assertIn("01001", panel.index)
            self.assertEqual(json.loads((output / "cleaning_audit.json").read_text())["status"], "complete")


if __name__ == "__main__":
    unittest.main()
