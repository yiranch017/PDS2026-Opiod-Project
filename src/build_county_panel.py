"""Join cleaned shipments, Haoye's mortality table, and annual population."""

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from clean_mortality import CAUSES, prepare_mortality
from clean_population import FIPS_CHANGES


KEYS = ["county_fips", "year"]
BOUNDARY_REVIEW = {"51019", "51199", "51700"}


def validate_keys(frame, label):
    """Require one row per valid county-year before merging."""
    if frame[KEYS].isna().any().any() or frame.duplicated(KEYS).any():
        raise ValueError(f"Missing or duplicate {label} county-year keys")
    if not frame.county_fips.astype("string").str.fullmatch(r"\d{5}").all():
        raise ValueError(f"Invalid {label} FIPS codes")
    if not (pd.to_numeric(frame.year, errors="raise").between(2006, 2015) & frame.year.mod(1).eq(0)).all():
        raise ValueError(f"Invalid {label} years")


def align_mortality(mortality, population):
    """Align source counts and reporting statuses to the population geography."""
    validate_keys(mortality, "mortality")
    source = mortality.copy()
    # Remove obsolete geography only after confirming it has no reported counts.
    obsolete = source.county_fips.eq("51560")
    death_columns = [f"deaths_{cause}" for cause in CAUSES.values()]
    if source.loc[obsolete, death_columns].notna().any().any():
        raise ValueError("Unexpected reported deaths for abolished Clifton Forge city")
    source = source[~obsolete].copy()
    source["source_county_fips"] = source.county_fips
    source["county_fips"] = source.county_fips.replace(FIPS_CHANGES)
    population_keys = pd.MultiIndex.from_frame(population[KEYS])
    if not pd.MultiIndex.from_frame(source[KEYS]).isin(population_keys).all():
        raise ValueError("Mortality contains geography outside the population panel")
    result = population[KEYS].copy()
    result["mortality_geography_comparable"] = ~result.county_fips.isin(BOUNDARY_REVIEW)
    present = source.groupby(KEYS).size().rename("mortality_source_components").reset_index()
    result = result.merge(present, on=KEYS, how="left", validate="one_to_one")
    result["mortality_source_components"] = result.mortality_source_components.fillna(0).astype("int64")
    result["has_mortality_source_row"] = result.mortality_source_components.gt(0)
    # Keep observed subtotals separate from counts eligible for rate calculations.
    for cause in CAUSES.values():
        column = f"deaths_{cause}"
        grouped = source.groupby(KEYS)
        summary = grouped[column].agg(
            subtotal=lambda values: values.sum(min_count=1),
            complete=lambda values: values.notna().all(),
        ).reset_index()
        explicit = grouped[f"{column}_status"].agg(lambda values: values.eq("explicit_missing").any())
        summary["explicit"] = explicit.to_numpy()
        summary = summary.rename(columns={"subtotal": f"{column}_reported_subtotal"})
        result = result.merge(summary, on=KEYS, how="left", validate="one_to_one")
        complete = result.complete.fillna(False).astype(bool) & result.mortality_geography_comparable
        result[column] = result[f"{column}_reported_subtotal"].where(complete).astype("Int64")
        result[f"{column}_reported"] = result[column].notna()
        status = pd.Series("not_in_export", index=result.index, dtype="string")
        status.loc[~result.has_mortality_source_row] = "no_source_county_year"
        status.loc[result.explicit.fillna(False).astype(bool)] = "explicit_missing"
        status.loc[complete] = "reported"
        status.loc[~result.mortality_geography_comparable] = "boundary_not_comparable"
        result[f"{column}_status"] = status
        # Bound absent causes only when the county-year appears in the export.
        lower = result[column].copy()
        upper = result[column].copy()
        absent = status.eq("not_in_export")
        lower.loc[absent], upper.loc[absent] = 0, 9
        result[f"{column}_lower"] = lower
        result[f"{column}_upper"] = upper
        result = result.drop(columns=["complete", "explicit"])
    return result


def build_panel(population, shipments, mortality, drugs):
    """Join the sources and calculate rates and analysis eligibility flags."""
    for label, frame in [("population", population), ("shipments", shipments), ("mortality", mortality)]:
        validate_keys(frame, label)
    if not (np.isfinite(population.population) & population.population.gt(0)).all():
        raise ValueError("Invalid population denominator")
    if not population.groupby("county_fips").year.nunique().eq(10).all():
        raise ValueError("Population must cover all ten study years for every county")
    aligned = align_mortality(mortality, population)
    panel = population.merge(aligned, on=KEYS, validate="one_to_one")
    # Verify that shipments cover exactly the same county-year keys.
    measures = ["base_weight_g", "shipment_records", "largest_shipment_g", "hydrocodone_oxycodone_g", "hydrocodone_oxycodone_records", "has_shipment_record"]
    joined = panel.merge(shipments[KEYS + measures], on=KEYS, how="outer", indicator=True, validate="one_to_one")
    if joined._merge.ne("both").any():
        raise ValueError("Shipment cleaning must cover exactly the population county-year universe")
    panel = joined.drop(columns="_merge")
    observed = panel.has_shipment_record.astype(bool)
    if panel.loc[~observed, measures[:-1]].notna().any().any():
        raise ValueError("Missing shipments were replaced with numbers")
    for column in ["base_weight_g", "hydrocodone_oxycodone_g"]:
        if (panel[column].dropna() < 0).any() or not np.isfinite(panel[column].dropna()).all():
            raise ValueError("Invalid shipment weight")
        panel[f"{column}_per_person"] = panel[column] / panel.population
    panel["has_hydrocodone_oxycodone_record"] = panel.hydrocodone_oxycodone_records.notna()
    # Use the same annual population denominator for observed counts and bounds.
    for cause in CAUSES.values():
        column = f"deaths_{cause}"
        for suffix in ["", "_lower", "_upper"]:
            panel[f"{column}{suffix}_per_100k"] = panel[f"{column}{suffix}"] / panel.population * 100_000
    if drugs.duplicated(KEYS + ["drug_code"]).any():
        raise ValueError("Duplicate county-year-drug keys")
    drug_rates = drugs.merge(population[KEYS + ["population"]], on=KEYS, how="left", validate="many_to_one")
    if drug_rates.population.isna().any():
        raise ValueError("Drug shipments have no population match")
    drug_rates["grams_per_person"] = drug_rates.base_weight_g / drug_rates.population
    # Roll up review flags while retaining drug-level rates for secondary analysis.
    flags = drugs.groupby(KEYS).agg(
        shipment_upper_total_flag=("upper_1pct_within_drug_year", "any"),
        shipment_single_transaction_flag=("dominant_single_shipment", "any"),
    ).reset_index()
    panel = panel.merge(flags, on=KEYS, how="left", validate="one_to_one")
    for column in ["shipment_upper_total_flag", "shipment_single_transaction_flag"]:
        panel[column] = panel[column].fillna(False).astype(bool)
    panel["mortality_primary_eligible"] = panel.deaths_unintentional_reported
    panel["geography_analysis_eligible"] = ~panel.county_fips.isin(BOUNDARY_REVIEW)
    # Base primary shipment eligibility on total retained weight, including valid zeros.
    panel["shipments_primary_eligible"] = (
        observed & panel.base_weight_g.notna() & panel.geography_analysis_eligible
    )
    panel["joint_primary_eligible"] = panel.mortality_primary_eligible & panel.shipments_primary_eligible
    # Flag counties with complete coverage for each outcome across the full study period.
    for measure in ["mortality", "shipments", "joint"]:
        column = f"{measure}_primary_eligible"
        panel[f"{measure}_complete_2006_2015"] = panel.groupby("county_fips")[column].transform("all")
    panel = panel.sort_values(KEYS).reset_index(drop=True)
    validate_keys(panel, "merged panel")
    return panel, drug_rates


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--population", type=Path, required=True)
    parser.add_argument("--shipments-dir", type=Path, required=True)
    parser.add_argument("--mortality-zip", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    population = pd.read_parquet(args.population)
    shipments = pd.read_parquet(args.shipments_dir / "shipments_county_year.parquet")
    drugs = pd.read_parquet(args.shipments_dir / "shipments_county_year_drug.parquet")
    shipment_audit = json.loads((args.shipments_dir / "cleaning_audit.json").read_text())
    if shipment_audit.get("status") != "complete" or shipment_audit.get("validated") is not True:
        raise ValueError("Shipment input is incomplete or unvalidated")
    mortality, mortality_audit = prepare_mortality(args.mortality_zip)
    panel, drug_rates = build_panel(population, shipments, mortality, drugs)
    # Reconcile the joined shipment totals before writing any outputs.
    if not math.isclose(math.fsum(panel.base_weight_g.dropna()), shipment_audit["main_weight_g"], rel_tol=1e-10):
        raise ValueError("Merged shipment weights do not reconcile")
    if int(panel.shipment_records.sum()) != shipment_audit["main_records"]:
        raise ValueError("Merged shipment records do not reconcile")
    args.output.mkdir(parents=True, exist_ok=False)
    # Preserve the source mortality table alongside the harmonized panel for review.
    mortality.to_parquet(args.output / "mortality_source_county_year.parquet", index=False)
    obsolete = mortality[mortality.county_fips.eq("51560")].copy()
    obsolete["exclusion_reason"] = "Clifton Forge city ceased to be a county equivalent in 2001; source only contains missing placeholders."
    obsolete.to_csv(args.output / "obsolete_mortality_geography.csv", index=False)
    (args.output / "mortality_audit.json").write_text(json.dumps(mortality_audit, indent=2) + "\n")
    panel.to_parquet(args.output / "county_year.parquet", index=False)
    panel.to_csv(args.output / "county_year.csv", index=False)
    drug_rates.to_parquet(args.output / "shipments_drug_rates.parquet", index=False)
    # Keep flagged shipments in the dataset and record why each was retained.
    review = drug_rates[drug_rates.upper_1pct_within_drug_year | drug_rates.dominant_single_shipment].copy()
    review["decision"] = np.where(review.dominant_single_shipment, "retain_flagged_single_transaction", "retain_flagged_high_total")
    review["reason"] = np.where(
        review.dominant_single_shipment,
        "A single shipment dominates this drug-year total; retain with a sensitivity flag because aggregate data cannot verify a transaction error.",
        "High volume alone is not evidence of an error; retain and use population-normalized rates.",
    )
    review.sort_values("base_weight_g", ascending=False).to_csv(args.output / "shipment_review_decisions.csv", index=False)
    # Report coverage by state and year, including the population with observed D1 counts.
    coverage = panel.groupby(["state", "year"], as_index=False).agg(
        county_years=("county_fips", "size"), population=("population", "sum"),
        shipment_county_years=("has_shipment_record", "sum"),
        shipment_primary_eligible_county_years=("shipments_primary_eligible", "sum"),
        mortality_no_source_county_years=("has_mortality_source_row", lambda rows: (~rows).sum()),
        mortality_reported_county_years=("deaths_unintentional_reported", "sum"),
        joint_eligible_county_years=("joint_primary_eligible", "sum"),
        counties_with_single_transaction_flags=("shipment_single_transaction_flag", "sum"),
    )
    reported_population = panel.assign(
        mortality_reported_population=panel.population.where(panel.deaths_unintentional_reported, 0)
    ).groupby(["state", "year"], as_index=False).mortality_reported_population.sum()
    coverage = coverage.merge(reported_population, on=["state", "year"], validate="one_to_one")
    coverage["mortality_reported_population_share"] = coverage.mortality_reported_population / coverage.population
    coverage.to_csv(args.output / "coverage_by_state_year.csv", index=False)
    panel.loc[~panel.joint_primary_eligible, KEYS + ["state", "county_name", "has_shipment_record", "has_hydrocodone_oxycodone_record", "deaths_unintentional_status"]].to_csv(args.output / "missingness_review.csv", index=False)
    columns = [{"column": name, "dtype": str(dtype)} for name, dtype in panel.dtypes.items()]
    pd.DataFrame(columns).to_csv(args.output / "columns.csv", index=False)
    # Record input fingerprints and counts so a later build can be verified.
    audit = {
        "status": "complete", "validated": True, "county_year_rows": len(panel),
        "counties": int(panel.county_fips.nunique()), "years": [2006, 2015],
        "shipment_records": int(panel.shipment_records.sum()),
        "shipment_weight_g": math.fsum(panel.base_weight_g.dropna()),
        "county_years_with_shipments": int(panel.has_shipment_record.sum()),
        "county_years_without_shipments": int((~panel.has_shipment_record).sum()),
        "county_years_shipments_primary_eligible": int(panel.shipments_primary_eligible.sum()),
        "county_years_without_mortality_source": int((~panel.has_mortality_source_row).sum()),
        "county_years_with_reported_unintentional_deaths": int(panel.deaths_unintentional_reported.sum()),
        "joint_primary_eligible_rows": int(panel.joint_primary_eligible.sum()),
        "counties_complete_joint_2006_2015": int(panel.loc[panel.joint_complete_2006_2015, "county_fips"].nunique()),
        "flagged_drug_rows": len(review), "boundary_not_comparable_rows": int((~panel.mortality_geography_comparable).sum()),
        "obsolete_mortality_placeholder_rows": len(obsolete),
        "input_sha256": {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in [args.population, args.mortality_zip, args.shipments_dir / "shipments_county_year.parquet", args.shipments_dir / "shipments_county_year_drug.parquet"]},
        "note": "All population county-years retained. Primary shipments are total retained base-weight grams per person. Primary mortality is reported unintentional drug overdose, not opioid-specific. Bedford, York, and Newport News are flagged for boundary changes and excluded from default eligibility. Their mortality rates are withheld. Sensitivity bounds apply only to absent causes in county-years present in the mortality export. No controls selected.",
    }
    (args.output / "panel_audit.json").write_text(json.dumps(audit, indent=2) + "\n")
    print(json.dumps({key: value for key, value in audit.items() if key != "input_sha256"}, indent=2))


if __name__ == "__main__":
    main()
