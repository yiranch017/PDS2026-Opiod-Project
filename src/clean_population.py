"""Read annual Census population estimates on a consistent county geography."""

import argparse
import hashlib
import json
from pathlib import Path
import urllib.request

import numpy as np
import pandas as pd


SOURCES = {
    "co-est00int-tot.csv": "https://www2.census.gov/programs-surveys/popest/datasets/2000-2010/intercensal/county/co-est00int-tot.csv",
    "co-est2019-alldata.csv": "https://www2.census.gov/programs-surveys/popest/datasets/2010-2019/counties/totals/co-est2019-alldata.csv",
}
STATE_ABBREVIATIONS = dict(zip(
    "01 04 05 06 08 09 10 11 12 13 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 31 32 33 34 35 36 37 38 39 40 41 42 44 45 46 47 48 49 50 51 53 54 55 56".split(),
    "AL AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split(),
))
FIPS_CHANGES = {"46113": "46102", "51515": "51019"}


def download_sources(directory):
    """Download missing Census source files without replacing existing copies."""
    directory.mkdir(parents=True, exist_ok=True)
    for name, url in SOURCES.items():
        path = directory / name
        if not path.exists():
            with urllib.request.urlopen(url, timeout=60) as response:
                contents = response.read()
            temporary = path.with_suffix(".download")
            temporary.write_bytes(contents)
            temporary.replace(path)


def prepare_population(early_path, late_path):
    """Build a balanced county-year denominator from the two Census vintages."""
    frames = []
    sources = []
    state_checks = []
    for path, years in [(early_path, range(2006, 2010)), (late_path, range(2010, 2016))]:
        path = Path(path)
        columns = ["SUMLEV", "STATE", "COUNTY", "STNAME", "CTYNAME"] + [f"POPESTIMATE{y}" for y in years]
        source = pd.read_csv(path, encoding="latin-1", dtype="string", usecols=columns)
        source["state_fips"] = source.STATE.str.zfill(2)
        source["county_fips"] = source.state_fips + source.COUNTY.str.zfill(3)
        source = source[source.state_fips.isin(STATE_ABBREVIATIONS)].copy()
        level = pd.to_numeric(source.SUMLEV, errors="raise")
        counties = source[level.eq(50)].copy()
        states = source[level.eq(40)].copy()
        if counties.county_fips.duplicated().any() or states.state_fips.duplicated().any():
            raise ValueError("Duplicate Census geography rows")
        if not counties.county_fips.str.fullmatch(r"\d{5}").all() or counties.COUNTY.str.fullmatch(r"0+").any():
            raise ValueError("Invalid county identifiers")
        for year in years:
            # Reconcile native county totals before harmonizing county geography.
            value = f"POPESTIMATE{year}"
            population = pd.to_numeric(counties[value], errors="raise")
            if not (np.isfinite(population) & population.gt(0) & population.mod(1).eq(0)).all():
                raise ValueError("Population must be finite positive integers")
            totals = counties.assign(population=population).groupby("state_fips").population.sum()
            expected = pd.to_numeric(states.set_index("state_fips")[value], errors="raise")
            if not totals.sort_index().equals(expected.sort_index()):
                raise ValueError(f"County populations do not sum to state totals: {year}")
            state_checks.extend({"state_fips": key, "year": year, "population": int(total)} for key, total in totals.items())
            frame = counties[["county_fips", "state_fips", "STNAME", "CTYNAME"]].rename(
                columns={"STNAME": "state_name", "CTYNAME": "county_name"}
            ).copy()
            frame["population"] = population.astype("int64")
            frame["year"] = year
            frame["population_source"] = path.name
            frames.append(frame)
        sources.append({"file": path.name, "url": SOURCES.get(path.name), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    population = pd.concat(frames, ignore_index=True)
    # Map historical county codes onto the reference geography, then sum components.
    population["source_county_fips"] = population.county_fips
    population["county_fips"] = population.county_fips.replace(FIPS_CHANGES)
    late_names = frames[-1].set_index("county_fips").county_name
    population["county_name"] = population.county_fips.map(late_names)
    if population.county_name.isna().any():
        raise ValueError("Population geography needs an additional documented crosswalk")
    population = population.groupby(
        ["county_fips", "year", "state_fips", "state_name", "county_name", "population_source"], as_index=False
    ).agg(population=("population", "sum"), population_components=("source_county_fips", "nunique"))
    population["state"] = population.state_fips.map(STATE_ABBREVIATIONS)
    population["geography_harmonized"] = population.county_fips.isin(FIPS_CHANGES.values())
    population = population.sort_values(["county_fips", "year"]).reset_index(drop=True)
    counts = population.groupby("county_fips").year.nunique()
    if population.duplicated(["county_fips", "year"]).any() or not counts.eq(10).all():
        raise ValueError("Population panel is not unique and balanced over 2006–2015")
    audit = {
        "status": "complete", "validated": True, "sources": sources,
        "county_year_rows": len(population), "counties": int(population.county_fips.nunique()),
        "years": [2006, 2015], "reference_date": "July 1",
        "geography_crosswalk": FIPS_CHANGES,
        "state_total_checks": state_checks,
        "note": "Annual estimates; Bedford city is combined with Bedford County in 2006–2009. Later estimates already include it. Shannon is renamed to Oglala Lakota.",
    }
    return population, audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw"))
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.download:
        download_sources(args.raw_dir)
    result, audit = prepare_population(*(args.raw_dir / name for name in SOURCES))
    args.output.mkdir(parents=True, exist_ok=False)
    result.to_parquet(args.output / "population_county_year.parquet", index=False)
    result.to_csv(args.output / "population_county_year.csv", index=False)
    (args.output / "population_audit.json").write_text(json.dumps(audit, indent=2) + "\n")
    print(f"Population: {len(result):,} county-years; validated")


if __name__ == "__main__":
    main()
