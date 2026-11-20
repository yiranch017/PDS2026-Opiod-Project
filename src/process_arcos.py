"""Reduce a zipped ARCOS TSV to buyer-location/year/drug totals using pandas."""

from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
import json
import math
from pathlib import Path
import shutil
import signal
import threading
import time
import zipfile

import numpy as np
import pandas as pd
import psutil


USECOLS = [
    "BUYER_STATE", "BUYER_COUNTY", "TRANSACTION_CODE", "DRUG_CODE",
    "DRUG_NAME", "TRANSACTION_DATE", "CALC_BASE_WT_IN_GM",
]
KEYS = ["buyer_state", "buyer_county", "year", "drug_code"]
VALUES = ["base_weight_g", "shipment_records"]
DTYPES = {column: "string" for column in USECOLS}


def summarize_chunk(chunk, start_year, end_year):
    """Return additive group totals and audit counts; never impute bad weights."""
    dates = pd.to_datetime(chunk["TRANSACTION_DATE"], format="%Y-%m-%d", errors="coerce")
    years = dates.dt.year
    weights = pd.to_numeric(chunk["CALC_BASE_WT_IN_GM"], errors="coerce")
    finite = pd.Series(np.isfinite(weights.to_numpy(dtype=float, na_value=np.nan)), index=chunk.index)
    states = chunk["BUYER_STATE"].str.strip().str.upper()
    counties = chunk["BUYER_COUNTY"].str.strip().str.upper()
    drugs = chunk["DRUG_CODE"].str.strip()
    geography_ok = states.str.fullmatch(r"[A-Z]{2}").fillna(False) & counties.ne("").fillna(False)
    drug_ok = drugs.str.fullmatch(r"\d{4}").fillna(False)
    audits = Counter({
        "rows_read": len(chunk),
        "invalid_dates": int(dates.isna().sum()),
        "invalid_or_nonfinite_weights": int((~finite).sum()),
        "negative_weights": int((finite & weights.lt(0).fillna(False)).sum()),
        "invalid_geography": int((~geography_ok).sum()),
        "invalid_drug_codes": int((~drug_ok).sum()),
    })
    checks = [
        ("excluded_invalid_date", dates.notna()),
        ("excluded_year", years.between(start_year, end_year)),
        ("excluded_alaska", states.ne("AK")),
        ("excluded_non_sale", chunk["TRANSACTION_CODE"].eq("S")),
        ("excluded_geography", geography_ok),
        ("excluded_drug_code", drug_ok),
        ("excluded_invalid_weight", finite),
        ("excluded_negative_weight", weights.ge(0)),
    ]
    keep = pd.Series(True, index=chunk.index)
    for reason, valid in checks:
        valid = valid.fillna(False)
        audits[reason] = int((keep & ~valid).sum())
        keep &= valid
    selected = pd.DataFrame({
        "buyer_state": states[keep], "buyer_county": counties[keep],
        "year": years[keep].astype("int16"), "drug_code": drugs[keep],
        "base_weight_g": weights[keep].astype("float64"),
        "shipment_records": np.int64(1),
    })
    audits["rows_retained"] = len(selected)
    total_weight = math.fsum(selected["base_weight_g"])
    summary = selected.groupby(KEYS, as_index=False, observed=True, sort=False)[VALUES].sum()
    transactions = Counter(chunk["TRANSACTION_CODE"].fillna("<missing>").value_counts().to_dict())
    labels = Counter(
        chunk[["DRUG_CODE", "DRUG_NAME"]].fillna("<missing>").value_counts().to_dict()
    )
    return summary, audits, total_weight, transactions, labels


def merge_summaries(paths, destination):
    """Read two compact summaries, combining keys that cross chunk boundaries."""
    frames = [pd.read_parquet(path, dtype_backend="pyarrow") for path in paths]
    combined = pd.concat(frames, ignore_index=True)
    totals = combined.groupby(KEYS, as_index=False, observed=True, sort=False)[VALUES].sum()
    if not np.isfinite(totals["base_weight_g"].to_numpy(dtype=float)).all():
        raise ValueError("Nonfinite aggregate weight; outputs were not validated.")
    totals.to_parquet(destination, index=False, compression="snappy")
    del frames, combined, totals
    for path in paths:
        path.unlink()


class ParquetReducer:
    """Binary reduction: at most one summary file per level, no raw chunk list."""

    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir()
        self.levels = {}
        self.sequence = 0

    def _path(self):
        self.sequence += 1
        return self.directory / f"part_{self.sequence:08d}.parquet"

    def add(self, summary):
        if summary.empty:
            return
        carry = self._path()
        summary.to_parquet(carry, index=False, compression="snappy")
        level = 0
        while level in self.levels:
            previous = self.levels.pop(level)
            merged = self._path()
            merge_summaries([previous, carry], merged)
            carry = merged
            level += 1
        self.levels[level] = carry

    def finish(self, destination):
        paths = [self.levels[level] for level in sorted(self.levels)]
        if not paths:
            raise ValueError("No eligible shipment records; inspect audit.json.")
        carry = paths[0]
        for other in paths[1:]:
            merged = self._path()
            merge_summaries([carry, other], merged)
            carry = merged
        carry.replace(destination)
        self.levels.clear()
        self.directory.rmdir()


@contextmanager
def resource_guard(output, max_memory_mib, min_free_gib):
    """Monitor process RAM and free disk in the background; interrupt on breach."""
    process = psutil.Process()
    stop = threading.Event()
    problem = []
    peak = {"rss_mib": 0.0}

    def check():
        rss = process.memory_info().rss / 1024**2
        peak["rss_mib"] = max(peak["rss_mib"], rss)
        free = shutil.disk_usage(output).free / 1024**3
        if rss > max_memory_mib:
            return f"RAM limit exceeded ({rss:.0f} MiB > {max_memory_mib} MiB)"
        if free < min_free_gib:
            return f"Free disk below safety threshold ({free:.1f} GiB < {min_free_gib} GiB)"
        return None

    def interrupted(signum, frame):
        raise RuntimeError(problem[0] if problem else "Processing interrupted by SIGTERM")

    def monitor():
        while not stop.wait(0.25):
            message = check()
            if message:
                problem.append(message)
                process.send_signal(signal.SIGTERM)
                return

    initial_problem = check()
    if initial_problem:
        raise RuntimeError(initial_problem)
    previous = signal.signal(signal.SIGTERM, interrupted)
    worker = threading.Thread(target=monitor, daemon=True)
    worker.start()
    try:
        yield peak
    finally:
        stop.set()
        worker.join()
        signal.signal(signal.SIGTERM, previous)


def write_json(path, value):
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def run(args):
    if args.start_year > args.end_year:
        raise ValueError("start-year must be <= end-year")
    if args.chunksize <= 0 or args.pilot_rows <= 0:
        raise ValueError("Chunk and pilot sizes must be positive")
    if args.max_memory_mib <= 0 or args.min_free_gib < 0:
        raise ValueError("Resource limits must be positive (disk threshold may be zero)")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    start = time.monotonic()
    audit = {
        "status": "running", "mode": "full" if args.full else "pilot",
        "start_year": args.start_year, "end_year": args.end_year,
        "chunksize": args.chunksize, "sale_code": "S", "excluded_states": ["AK"],
        "weight_policy": "finite nonnegative CALC_BASE_WT_IN_GM; units grams",
        "counts": {}, "transaction_codes": {}, "chunks_processed": 0,
        "accepted_weight_g": 0.0, "validated": False,
    }
    counts, transactions, labels = Counter(), Counter(), Counter()
    chunk_weights = []  # One scalar per chunk, not raw records or DataFrames.
    try:
        with resource_guard(output, args.max_memory_mib, args.min_free_gib) as peak:
            reducer = ParquetReducer(output / "parts")
            with zipfile.ZipFile(args.zip) as archive:
                candidates = [name for name in archive.namelist() if name.lower().endswith(".tsv")]
                member = args.member
                if member is None:
                    if len(candidates) != 1:
                        raise ValueError("Specify --member when the ZIP has multiple or no TSV members")
                    member = candidates[0]
                with archive.open(member) as stream, pd.read_csv(
                    stream, sep="\t", usecols=USECOLS, dtype=DTYPES,
                    keep_default_na=False, chunksize=args.chunksize,
                    nrows=None if args.full else args.pilot_rows,
                ) as reader:
                    for number, chunk in enumerate(reader, 1):
                        summary, chunk_counts, weight, codes, names = summarize_chunk(
                            chunk, args.start_year, args.end_year
                        )
                        counts.update(chunk_counts)
                        transactions.update(codes)
                        labels.update(names)
                        chunk_weights.append(weight)
                        del chunk
                        reducer.add(summary)
                        del summary
                        audit.update(
                            counts=dict(counts), transaction_codes=dict(transactions),
                            chunks_processed=number, accepted_weight_g=math.fsum(chunk_weights),
                            elapsed_seconds=round(time.monotonic() - start, 2),
                            sampled_peak_rss_mib=round(peak["rss_mib"], 2),
                        )
                        if number == 1 or number % 20 == 0:
                            write_json(output / "audit.json", audit)
                            print(
                                f"chunks={number:,} rows={counts['rows_read']:,} "
                                f"retained={counts['rows_retained']:,} "
                                f"RAM={psutil.Process().memory_info().rss / 1024**2:.0f} MiB "
                                f"elapsed={time.monotonic() - start:.0f}s", flush=True,
                            )
            provisional = output / "shipments.partial.parquet"
            reducer.finish(provisional)
            totals = pd.read_parquet(provisional, dtype_backend="pyarrow")
            observed_weight = math.fsum(totals["base_weight_g"])
            observed_records = int(totals["shipment_records"].sum())
            excluded = sum(value for key, value in counts.items() if key.startswith("excluded_"))
            if counts["rows_read"] != counts["rows_retained"] + excluded:
                raise ValueError("Retained and excluded records do not reconcile")
            if observed_records != counts["rows_retained"] or totals.duplicated(KEYS).any():
                raise ValueError("Output records or uniqueness check failed")
            if not math.isclose(observed_weight, math.fsum(chunk_weights), rel_tol=1e-10, abs_tol=1e-7):
                raise ValueError("Accepted and aggregated shipment weights do not reconcile")
            audit.update(
                status="complete", validated=True, aggregate_rows=len(totals),
                aggregate_weight_g=observed_weight, aggregate_records=observed_records,
                counts=dict(counts), transaction_codes=dict(transactions),
                chunks_processed=audit["chunks_processed"],
                accepted_weight_g=math.fsum(chunk_weights),
                sampled_peak_rss_mib=round(peak["rss_mib"], 2),
                elapsed_seconds=round(time.monotonic() - start, 2),
            )
            label_rows = [
                {"drug_code": code, "drug_name": name, "source_records": count}
                for (code, name), count in sorted(labels.items())
            ]
            pd.DataFrame(label_rows).to_csv(output / "drug_labels.csv", index=False)
            provisional.replace(output / "shipments_county_name_year_drug.parquet")
            write_json(output / "audit.json", audit)
            print(json.dumps(audit, indent=2), flush=True)
            return audit
    except BaseException as error:
        audit.update(
            status="failed", validated=False, error=str(error), counts=dict(counts),
            transaction_codes=dict(transactions), elapsed_seconds=round(time.monotonic() - start, 2),
        )
        write_json(output / "audit.json", audit)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zip", type=Path, required=True, help="Existing ARCOS ZIP; never extracted")
    parser.add_argument("--output", type=Path, required=True, help="New directory; existing paths are refused")
    parser.add_argument("--member", help="TSV member name; autodetected when there is exactly one")
    parser.add_argument("--chunksize", type=int, default=50_000)
    parser.add_argument("--pilot-rows", type=int, default=100_000)
    parser.add_argument("--full", action="store_true", help="Read the entire archive instead of a bounded pilot")
    parser.add_argument("--start-year", type=int, default=2006)
    parser.add_argument("--end-year", type=int, default=2015)
    parser.add_argument("--max-memory-mib", type=int, default=1024)
    parser.add_argument("--min-free-gib", type=float, default=10)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
