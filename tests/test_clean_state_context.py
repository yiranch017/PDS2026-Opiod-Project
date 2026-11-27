import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from clean_state_context import physician_context


class StateContextTests(unittest.TestCase):
    def test_physician_state_total_requires_all_county_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            raw = Path(directory)
            with ZipFile(raw / "layout.zip", "w") as archive:
                archive.writestr("layout.sas", "@00001 f00002 $05.\n@00006 f0885710 05.\n")
            # Alabama has one missing county count. Alaska has a reported zero.
            (raw / "county.asc").write_text(
                "01001   12\n01003    .\n02013    0\n02016    7\n", encoding="ascii"
            )
            population = pd.DataFrame({
                "county_fips": ["01001", "01003", "02013", "02016"],
                "state_fips": ["01", "01", "02", "02"],
                "year": [2010] * 4,
                "population": [1000, 9000, 700, 300],
            })
            with patch("clean_state_context.HEALTH_FILES", (("county.asc", "layout.zip", (2010,)),)):
                totals = physician_context(raw, population).set_index(["state_fips", "year"])

        self.assertTrue(pd.isna(totals.loc[("01", 2010), "active_nonfederal_md"]))
        self.assertEqual(totals.loc[("02", 2010), "active_nonfederal_md"], 7)
        self.assertEqual(totals.loc[("01", 2010), "physician_observed_population"], 1000)
        self.assertEqual(totals.loc[("01", 2010), "active_nonfederal_md_observed_per_10000"], 120)
        self.assertEqual(totals.loc[("02", 2010), "physician_observed_population"], 1000)
        self.assertEqual(totals.loc[("02", 2010), "active_nonfederal_md_observed_per_10000"], 70)
