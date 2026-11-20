"""Check cross-chunk accounting and corrupt/missing-input behavior."""

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
from process_arcos import KEYS, USECOLS, run  # noqa: E402


def make_archive(path, records):
    text = io.StringIO()
    writer = csv.DictWriter(text, fieldnames=USECOLS, delimiter="\t")
    writer.writeheader()
    writer.writerows(records)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("test.tsv", text.getvalue())


def record(weight="1.5", **updates):
    row = {
        "BUYER_STATE": "FL", "BUYER_COUNTY": "TEST COUNTY",
        "TRANSACTION_CODE": "S", "DRUG_CODE": "9143", "DRUG_NAME": "OXYCODONE",
        "TRANSACTION_DATE": "2010-01-01", "CALC_BASE_WT_IN_GM": weight,
    }
    row.update(updates)
    return row


def options(archive, output, **updates):
    values = dict(
        zip=archive, output=output, member=None, start_year=2006, end_year=2015,
        chunksize=2, pilot_rows=3, full=True, max_memory_mib=1024, min_free_gib=0,
    )
    values.update(updates)
    return argparse.Namespace(**values)


class StreamingTests(unittest.TestCase):
    def test_totals_across_many_chunks_match_known_reference(self):
        # Same key recurs across chunks and reduction levels; WA must stay distinct.
        records = [record(str(value)) for value in range(1, 18)]
        records += [record("100", BUYER_STATE="WA"), record("200", DRUG_CODE="9193")]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive, output = root / "input.zip", root / "result"
            make_archive(archive, records)
            audit = run(options(archive, output))
            result = pd.read_parquet(output / "shipments_county_name_year_drug.parquet")
            self.assertEqual(len(result), 3)
            self.assertFalse(result.duplicated(KEYS).any())
            result = result.set_index(["buyer_state", "drug_code"])
            self.assertEqual(result.loc[("FL", "9143"), "base_weight_g"], 153)
            self.assertEqual(result.loc[("FL", "9143"), "shipment_records"], 17)
            self.assertEqual(result.loc[("WA", "9143"), "base_weight_g"], 100)
            self.assertEqual(result.loc[("FL", "9193"), "base_weight_g"], 200)
            self.assertEqual(audit["aggregate_records"], 19)
            self.assertTrue(audit["validated"])
            self.assertFalse((output / "parts").exists())

    def test_bad_rows_are_audited_and_not_imputed(self):
        records = [
            record("2"), record("0"), record(""), record("nan"), record("inf"),
            record("-5"), record(TRANSACTION_DATE="2010-02-30"),
            record(TRANSACTION_DATE="2019-01-01"), record(BUYER_STATE="AK"),
            record(TRANSACTION_CODE="R"), record(BUYER_COUNTY=" "), record(DRUG_CODE=""),
            record("4", DRUG_CODE="9250B", DRUG_NAME="METHADONE"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive, output = root / "input.zip", root / "result"
            make_archive(archive, records)
            audit = run(options(archive, output))
            self.assertEqual(audit["counts"]["rows_read"], 13)
            self.assertEqual(audit["counts"]["rows_retained"], 3)
            self.assertEqual(audit["counts"]["invalid_or_nonfinite_weights"], 3)
            self.assertEqual(audit["aggregate_weight_g"], 6)
            result = pd.read_parquet(output / "shipments_county_name_year_drug.parquet")
            self.assertIn("9250B", result["drug_code"].tolist())
            self.assertEqual(audit["transaction_codes"]["R"], 1)

    def test_pilot_is_bounded_and_existing_output_is_protected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive, output = root / "input.zip", root / "result"
            make_archive(archive, [record("1") for _ in range(10)])
            audit = run(options(archive, output, full=False))
            self.assertEqual(audit["counts"]["rows_read"], 3)
            self.assertEqual(audit["aggregate_weight_g"], 3)
            self.assertEqual(audit["mode"], "pilot")
            with self.assertRaises(FileExistsError):
                run(options(archive, output))

    def test_no_eligible_rows_fails_without_publishing_final_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive, output = root / "input.zip", root / "result"
            make_archive(archive, [record("invalid")])
            with self.assertRaisesRegex(ValueError, "No eligible"):
                run(options(archive, output))
            audit = json.loads((output / "audit.json").read_text())
            self.assertEqual(audit["status"], "failed")
            self.assertFalse(audit["validated"])
            self.assertFalse((output / "shipments_county_name_year_drug.parquet").exists())


if __name__ == "__main__":
    unittest.main()
