import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from clean_mortality import prepare_mortality


class MortalityCleaningTests(unittest.TestCase):
    def test_suppressed_and_explicit_missing_are_not_zeros(self):
        with tempfile.TemporaryDirectory() as temp:
            archive_path = Path(temp) / "mortality.zip"
            header = "Notes\tCounty\tCounty Code\tYear\tYear Code\tDrug/Alcohol Induced Cause\tDrug/Alcohol Induced Cause Code\tDeaths\n"
            with zipfile.ZipFile(archive_path, "w") as archive:
                for year in range(2006, 2016):
                    rows = [
                        f"\tAutauga County, AL\t1001\t{year}.0\t{year}.0\tOther\tO9\t100\n",
                        f"\tBaldwin County, AL\t1003\t{year}.0\t{year}.0\tOther\tO9\t200\n",
                        f"\tAutauga County, AL\t1001\t{year}.0\t{year}.0\tOverdose\tD1\t10\n",
                        f"\tBaldwin County, AL\t1003\t{year}.0\t{year}.0\tOverdose\tD1\tMissing\n",
                        f"\tAlaska County, AK\t2001\t{year}.0\t{year}.0\tOther\tO9\t100\n",
                    ]
                    archive.writestr(f"Underlying Cause of Death, {year}.txt", header + "".join(rows))
            result, audit = prepare_mortality(archive_path)
            self.assertEqual(len(result), 20)
            self.assertEqual(result.loc[result.county_fips.eq("01001"), "deaths_unintentional"].dropna().tolist(), [10] * 10)
            self.assertTrue(result.loc[result.county_fips.eq("01003"), "deaths_unintentional"].isna().all())
            self.assertFalse(result.county_fips.eq("02001").any())
            self.assertEqual(audit["explicit_missing_source_rows"], 10)
            self.assertTrue(result.loc[result.county_fips.eq("01003"), "deaths_unintentional_status"].eq("explicit_missing").all())
            self.assertTrue(result["deaths_undetermined_status"].eq("not_in_export").all())
            coverage = next(row for row in audit["coverage_by_state_year_cause"]
                            if row["year"] == 2006 and row["cause"] == "D1")
            self.assertEqual(coverage, {"state_fips": "01", "year": 2006, "cause": "D1",
                                        "reported": 1, "explicit_missing": 1, "not_in_export": 0,
                                        "county_years": 2})


if __name__ == "__main__":
    unittest.main()
