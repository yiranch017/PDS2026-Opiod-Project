"""Prepare CDC WONDER county-year drug overdose mortality from the supplied ZIP."""

import argparse
import csv
import io
import json
import re
import zipfile
from pathlib import Path

import pandas as pd


CAUSES = {"D1": "unintentional", "D2": "suicide", "D3": "homicide", "D4": "undetermined"}
YEARS = range(2006, 2016)


def prepare_mortality(zip_path):
    records = []
    excluded = {"footer_or_blank": 0, "outside_geography": 0}
    with zipfile.ZipFile(zip_path) as archive:
        for year in YEARS:
            name = f"Underlying Cause of Death, {year}.txt"
            with archive.open(name) as stream:
                reader = csv.DictReader(io.TextIOWrapper(stream, encoding="utf-8-sig"), delimiter="\t")
                for row in reader:
                    code = str(row.get("County Code") or "").strip()
                    if not re.fullmatch(r"\d{4,5}", code):
                        excluded["footer_or_blank"] += 1
                        continue
                    fips = code.zfill(5)
                    state = int(fips[:2])
                    if state in (2, 0) or state > 56 or state in (3, 7, 14, 43, 52):
                        excluded["outside_geography"] += 1
                        continue
                    raw_year = str(row.get("Year") or "").strip()
                    if raw_year not in (str(year), f"{year}.0"):
                        raise ValueError(f"Unexpected year in {name}: {raw_year!r}")
                    cause = str(row.get("Drug/Alcohol Induced Cause Code") or "").strip()
                    raw_deaths = str(row.get("Deaths") or "").strip()
                    if not raw_deaths:
                        raise ValueError(f"Missing deaths in {name}: {fips}, {cause}")
                    if raw_deaths == "Missing":
                        deaths = None
                    else:
                        value = float(raw_deaths)
                        if not value.is_integer() or value < 0:
                            raise ValueError(f"Invalid death count in {name}: {raw_deaths}")
                        deaths = int(value)
                    records.append((fips, year, row["County"].strip(), cause, deaths))

    raw = pd.DataFrame(records, columns=["county_fips", "year", "county", "cause", "deaths"])
    if raw.duplicated(["county_fips", "year", "cause"]).any():
        raise ValueError("Duplicate county-year-cause rows in mortality source")
    if (raw.deaths.dropna() < 10).any():
        raise ValueError("Source contains a count below the documented suppression threshold")

    base = raw[["county_fips", "year", "county"]].drop_duplicates(["county_fips", "year"])
    if raw.groupby(["county_fips", "year"]).county.nunique().gt(1).any():
        raise ValueError("Conflicting county names for a county-year")
    overdose = raw[raw.cause.isin(CAUSES)].pivot(index=["county_fips", "year"], columns="cause", values="deaths")
    overdose = overdose.reindex(columns=list(CAUSES)).rename(columns={
        code: f"deaths_{label}" for code, label in CAUSES.items()
    }).reset_index()
    result = base.merge(overdose, on=["county_fips", "year"], how="left", validate="one_to_one")
    columns = [f"deaths_{label}" for label in CAUSES.values()]
    explicit_missing = raw.loc[raw.deaths.isna(), ["county_fips", "year", "cause"]]
    keys = pd.MultiIndex.from_frame(result[["county_fips", "year"]])
    coverage = []
    for cause, column in zip(CAUSES, columns):
        result[column] = result[column].astype("Int64")
        result[f"{column}_reported"] = result[column].notna()
        missing_keys = pd.MultiIndex.from_frame(
            explicit_missing.loc[explicit_missing.cause.eq(cause), ["county_fips", "year"]]
        )
        status = result[column].notna().map({True: "reported", False: "not_in_export"})
        status.loc[keys.isin(missing_keys)] = "explicit_missing"
        result[f"{column}_status"] = status
        grouped = result.assign(state_fips=result.county_fips.str[:2], status=status).groupby(
            ["state_fips", "year", "status"]
        ).size().unstack(fill_value=0)
        grouped = grouped.reindex(columns=["reported", "explicit_missing", "not_in_export"], fill_value=0)
        grouped["county_years"] = grouped.sum(axis=1)
        coverage.extend(grouped.reset_index().assign(cause=cause).to_dict("records"))
    result = result.sort_values(["county_fips", "year"]).reset_index(drop=True)
    audit = {
        "source": Path(zip_path).name, "years": [min(YEARS), max(YEARS)],
        "county_years": len(result), "source_rows": len(raw),
        "unintentional_reported": int(result.deaths_unintentional_reported.sum()),
        "reporting_by_cause": {column: int(result[f"{column}_reported"].sum()) for column in columns},
        "missing_unintentional_by_year": {
            str(k): int(v) for k, v in result.groupby("year").deaths_unintentional_reported.apply(lambda s: (~s).sum()).items()
        },
        "excluded_rows": excluded,
        "explicit_missing_source_rows": len(explicit_missing),
        "coverage_by_state_year_cause": coverage,
        "note": "Absent cause rows can be zero or suppressed (1-9); they remain missing, never imputed.",
    }
    return result, audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zip", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    mortality, audit = prepare_mortality(args.zip)
    args.output.mkdir(parents=True, exist_ok=True)
    mortality.to_parquet(args.output / "mortality_county_year.parquet", index=False)
    mortality.to_csv(args.output / "mortality_county_year.csv", index=False, na_rep="")
    (args.output / "mortality_audit.json").write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(audit, indent=2))


if __name__ == "__main__":
    main()
