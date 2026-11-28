"""Build the proposal's state-year economic, demographic, and healthcare context."""

import argparse
import json
import re
from pathlib import Path
from zipfile import ZipFile

import pandas as pd


YEARS = range(2006, 2016)
KEYS = ["state_fips", "year"]
AGE_FILES = (
    ("st-est00int-agesex.csv", range(2006, 2010)),
    ("sc-est2019-alldata6.csv", range(2010, 2016)),
)
HEALTH_FILES = (
    ("ahrf2009.asc", "AHRF_USER_TECH_2009-2010.zip", range(2006, 2009)),
    ("ahrf2017.asc", "AHRF_USER_TECH_2016-2017.zip", range(2010, 2016)),
)


def age_context(raw):
    """Calculate age 65+ shares using one population vintage per year."""
    tables = []
    for name, years in AGE_FILES:
        estimates = [f"POPESTIMATE{year}" for year in years]
        data = pd.read_csv(raw / name, encoding="latin-1", dtype={"STATE": "string"})
        data = data[data.SEX.eq(0) & data.AGE.between(0, 85) & data.STATE.str.zfill(2).ne("00")]
        if "ORIGIN" in data:
            # Origin 0 includes all origins. Require the six mutually exclusive race categories.
            data = data[data.ORIGIN.eq(0)]
            if set(data.RACE) != set(range(1, 7)) or data.duplicated(["STATE", "AGE", "RACE"]).any():
                raise ValueError("Expected six non-overlapping Census race categories")
        data = data.assign(state_fips=data.STATE.str.zfill(2))
        totals = data.groupby(["state_fips", "NAME"])[estimates].sum().stack().rename("state_population")
        older = data[data.AGE.ge(65)].groupby(["state_fips", "NAME"])[estimates].sum().stack()
        table = totals.to_frame().assign(population_65_plus=older).reset_index()
        table["year"] = table.pop("level_2").str.removeprefix("POPESTIMATE").astype(int)
        table["population_65_plus_share"] = table.population_65_plus / table.state_population
        tables.append(table.rename(columns={"NAME": "state_name"}))
    return pd.concat(tables, ignore_index=True)


def income_context(raw):
    """Read statewide SAIPE median household income estimates in current dollars."""
    rows = []
    for year in YEARS:
        path = raw / f"est{year % 100:02d}all.txt"
        for line in path.read_text(encoding="latin-1").splitlines():
            fields = line.split()
            if len(fields) > 20 and fields[0].isdigit() and fields[0] != "00" and fields[1] == "0":
                rows.append((fields[0].zfill(2), year, int(fields[20])))
    return pd.DataFrame(rows, columns=KEYS + ["median_household_income_usd"])


def unemployment_context(raw):
    """Read published BLS LAUS annual-average unemployment rates (period M13)."""
    rows = []
    for batch in (0, 20, 40):
        response = json.loads((raw / f"bls_laus_{batch}.json").read_text(encoding="utf-8-sig"))
        if response["status"] != "REQUEST_SUCCEEDED":
            raise ValueError(f"BLS request failed: {response['message']}")
        for series in response["Results"]["series"]:
            match = re.fullmatch(r"LAUST(\d{2})0000000000003", series["seriesID"])
            if not match:
                raise ValueError(f"Unexpected BLS series: {series['seriesID']}")
            rows.extend(
                (match[1], int(row["year"]), float(row["value"]))
                for row in series["data"]
                if row["period"] == "M13" and int(row["year"]) in YEARS
            )
    return pd.DataFrame(rows, columns=KEYS + ["unemployment_rate_pct"])


def physician_context(raw, county_population=None):
    """Read fixed-width HRSA counts and sum active non-federal M.D.s by state."""
    tables = []
    county_counts = []
    for filename, tech_zip, years in HEALTH_FILES:
        with ZipFile(raw / tech_zip) as archive:
            sas_name = next(name for name in archive.namelist() if name.lower().endswith(".sas"))
            layout = archive.read(sas_name).decode("latin-1")
        fields = dict.fromkeys(["f00002", *(f"f08857{year % 100:02d}" for year in years)])
        for start, field, width in re.findall(r"@(\d+)\s+(f\d+)\s+\$?\s*(\d+)\.", layout, flags=re.I):
            if field.lower() in fields:
                fields[field.lower()] = (int(start) - 1, int(start) - 1 + int(width))
        if any(colspec is None for colspec in fields.values()):
            raise ValueError(f"Missing physician fields in {tech_zip}")
        county = pd.read_fwf(
            raw / filename, colspecs=list(fields.values()), names=list(fields),
            dtype="string", encoding="latin-1", na_values=["."]
        )
        county = county[county.f00002.str.fullmatch(r"\d{5}", na=False)].copy()
        if county.f00002.duplicated().any():
            raise ValueError(f"Duplicate AHRF county keys in {filename}")
        county["state_fips"] = county.f00002.str[:2]
        for year in years:
            count = pd.to_numeric(county[f"f08857{year % 100:02d}"], errors="raise")
            groups = count.groupby(county.state_fips)
            total = groups.sum(min_count=1).where(groups.count().eq(groups.size()))
            tables.append(total.rename("active_nonfederal_md").reset_index().assign(year=year))
            if county_population is not None:
                county_counts.append(pd.DataFrame({
                    "county_fips": county.f00002, "year": year, "observed_md": count,
                }))
    totals = pd.concat(tables, ignore_index=True)
    if county_population is None:
        return totals
    coverage = physician_coverage(county_population, pd.concat(county_counts, ignore_index=True))
    return totals.merge(coverage, on=KEYS, how="left", validate="one_to_one")


def physician_coverage(population, counts):
    """Calculate physician density using the same counties in both parts of the rate."""
    keys = ["county_fips", "year"]
    # Harmonize the Shannon rename. Exclude Bedford and other boundary-review
    # counties from this rate because the numerator and denominator can differ.
    counts = counts.assign(county_fips=counts.county_fips.replace({"46113": "46102"}))
    comparable = ~population.county_fips.isin({"51019", "51199", "51700"})
    observed = population.loc[comparable].merge(
        counts.dropna(subset=["observed_md"]), on=keys, how="inner", validate="one_to_one"
    )
    coverage = observed.groupby(KEYS, as_index=False).agg(
        physician_observed_counties=("county_fips", "size"),
        physician_observed_population=("population", "sum"),
        physician_observed_md=("observed_md", "sum"),
    )
    coverage["active_nonfederal_md_observed_per_10000"] = (
        coverage.physician_observed_md / coverage.physician_observed_population * 10_000
    )
    return coverage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw"))
    parser.add_argument("--county-population", type=Path,
                        default=Path("outputs/population_clean/population_county_year.parquet"))
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    context = age_context(args.raw_dir)
    if len(context) != 510 or context.duplicated(KEYS).any():
        raise ValueError("Expected a unique 51-jurisdiction by 10-year Census grid")
    population = pd.read_parquet(args.county_population, columns=["county_fips", "state_fips", "year", "population"])
    for table in (income_context(args.raw_dir), unemployment_context(args.raw_dir),
                  physician_context(args.raw_dir, population)):
        context = context.merge(table, on=KEYS, how="left", validate="one_to_one")
    if not (
        context.state_population.gt(0).all()
        and context.population_65_plus_share.between(0, 1).all()
        and context.median_household_income_usd.gt(0).all()
        and context.unemployment_rate_pct.between(0, 100).all()
    ):
        raise ValueError("Incomplete or invalid statewide economic or population data")
    if not context.active_nonfederal_md.dropna().ge(0).all():
        raise ValueError("Invalid physician count")
    context["active_nonfederal_md_per_10000"] = context.active_nonfederal_md / context.state_population * 10_000
    context["physician_population_coverage_share"] = (
        context.physician_observed_population / context.state_population
    )
    if not context.physician_population_coverage_share.dropna().between(0, 1).all():
        raise ValueError("Physician coverage falls outside the statewide population")
    context = context.sort_values(KEYS).reset_index(drop=True)
    coverage = {
        "state_year_rows": len(context),
        "observations_by_year": context.groupby("year")[
            ["unemployment_rate_pct", "median_household_income_usd", "population_65_plus_share",
             "active_nonfederal_md", "active_nonfederal_md_observed_per_10000"]
        ].count().to_dict(),
    }
    args.output.mkdir(parents=True, exist_ok=False)
    context.to_parquet(args.output / "state_year_context.parquet", index=False)
    (args.output / "state_context_audit.json").write_text(json.dumps(coverage, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(coverage, indent=2))


if __name__ == "__main__":
    main()
