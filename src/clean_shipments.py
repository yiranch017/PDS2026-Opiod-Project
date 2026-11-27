"""Prepare county-year shipment tables and geographic matching reports."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re
import unicodedata
import zipfile

import numpy as np
import pandas as pd

from process_arcos import AGGREGATIONS, KEYS, resource_guard, write_json


STATES = set("AL AR AZ CA CO CT DC DE FL GA HI IA ID IL IN KS KY LA MA MD ME MI MN MO MS MT NC ND NE NH NJ NM NV NY OH OK OR PA RI SC SD TN TX UT VA VT WA WI WV WY".split())
PHARMACIES = {"RETAIL PHARMACY", "CHAIN PHARMACY"}
PRACTITIONERS = {
    "PRACTITIONER", "MLP-NURSE PRACTITIONER", "MLP-PHYSICIAN ASSISTANT",
    "MLP-NATUROPATHIC PHYSICIAN", "MLP-OPTOMETRIST", "MLP-REGISTERED PHARMACIST",
}
PAIR_CODES = {"9143", "9193"}


def buyer_group(value):
    if value in PHARMACIES:
        return "pharmacy"
    if value in PRACTITIONERS or re.fullmatch(
        r"(?:PRACTITIONER|MLP-NURSE PRACTITIONER|MLP-PHYSICIAN ASSISTANT)-DW/\d+(?:SW)?", value
    ):
        return "practitioner"
    if "HOSP" in value:
        return "hospital"
    if "DETOX" in value or "MAINT" in value:
        return "treatment_program"
    if value in {"CENTRAL FILL PHARMACY", "M/O PHARMACY"}:
        return "central_or_mail_order"
    if "FED" in value or "MIL" in value:
        return "federal_or_military"
    return "other"


def normalize_county(value):
    value = "".join(
        c for c in unicodedata.normalize("NFKD", str(value)) if not unicodedata.combining(c)
    ).upper().strip()
    value = re.sub(r"\s+(?:COUNTY|PARISH|BOROUGH|CENSUS AREA|MUNICIPALITY)$", "", value)
    value = re.sub(r"\bSAINT\b", "ST", value)
    value = re.sub(r"\bSAINTE\b", "STE", value)
    return re.sub(r"[^A-Z0-9]", "", value)


def county_reference(archive_path, start_year, end_year):
    frames = []
    with zipfile.ZipFile(archive_path) as archive:
        for year in range(start_year, end_year + 1):
            with archive.open(f"Underlying Cause of Death, {year}.txt") as stream:
                frame = pd.read_csv(stream, sep="\t", dtype={"County Code": "string"})
            frame = frame.dropna(subset=["County", "County Code"])
            frame = frame[frame["County Code"].str.fullmatch(r"\d{1,5}", na=False)].copy()
            if not pd.to_numeric(frame["Year"], errors="coerce").eq(year).all():
                raise ValueError(f"Year values disagree with the mortality filename: {year}")
            location = frame["County"].str.rsplit(", ", n=1, expand=True)
            frame["county_name"], frame["state"] = location[0], location[1]
            frame["county_fips"] = frame["County Code"].str.zfill(5)
            frame["state_fips"] = frame["county_fips"].str[:2]
            frame["year"] = year
            frames.append(frame[["state", "state_fips", "county_name", "county_fips", "year"]].drop_duplicates())
    reference = pd.concat(frames, ignore_index=True)
    reference = reference[reference.state.isin(STATES)].copy()
    reference["county_key"] = reference.county_name.map(normalize_county)
    if reference.duplicated(["county_fips", "year"]).any():
        raise ValueError("County reference has conflicting FIPS-year rows")
    if reference.duplicated(["state", "county_key", "year"]).any():
        raise ValueError("County normalization produces ambiguous reference names")
    return reference


def match_counties(shipments, reference):
    left = shipments.copy()
    left["county_key"] = left.buyer_county.map(normalize_county)
    result = left.merge(
        reference,
        left_on=["buyer_state", "county_key", "year"],
        right_on=["state", "county_key", "year"],
        how="left", validate="many_to_one", indicator=True,
    )
    result["match_method"] = np.where(result["_merge"].eq("both"), "normalized_name", "unmatched")
    result = result.drop(columns="_merge")
    # Documented Census changes, only used when the reference has the target county.
    historical_names = {("SD", "SHANNON"): "46102", ("VA", "BEDFORDCITY"): "51019"}
    targets = reference.set_index(["county_fips", "year"])
    for index, row in result[result.county_fips.isna()].iterrows():
        target = historical_names.get((row.buyer_state, row.county_key))
        if target and (target, row.year) in targets.index:
            match = targets.loc[(target, row.year)]
            for column in ["state", "state_fips", "county_name"]:
                result.at[index, column] = match[column]
            result.at[index, "county_fips"] = target
            result.at[index, "match_method"] = "documented_geography_change"
    cities = reference[reference.state.eq("VA") & reference.county_name.str.endswith(" city")].copy()
    cities["city_alias"] = cities.county_name.str.replace(r" city$", "", regex=True).map(normalize_county)
    alias_counts = cities.groupby(["state", "city_alias", "year"]).county_fips.transform("nunique")
    cities = cities[alias_counts.eq(1)]
    occupied = set(reference[["state", "county_key", "year"]].itertuples(index=False, name=None))
    cities = cities[
        [key not in occupied for key in cities[["state", "city_alias", "year"]].itertuples(index=False, name=None)]
    ]
    lookup = {
        (row.state, row.city_alias, row.year): row
        for row in cities.itertuples(index=False)
    }
    for index, row in result[result.county_fips.isna()].iterrows():
        match = lookup.get((row.buyer_state, row.county_key, row.year))
        if match is not None:
            for column in ["state", "state_fips", "county_name", "county_fips"]:
                result.at[index, column] = getattr(match, column)
            result.at[index, "match_method"] = "unique_city_alias"
    return result


def summarize_main(matched):
    eligible = matched[
        matched.buyer_group.isin(["pharmacy", "practitioner"]) & matched.county_fips.notna()
    ].copy()
    by_drug = eligible.groupby(
        ["county_fips", "year", "drug_code", "drug_name"], as_index=False, observed=True
    ).agg(AGGREGATIONS)
    if by_drug.empty:
        raise ValueError("No pharmacy/practitioner records matched the county reference")
    limits = by_drug.groupby(["year", "drug_code"], observed=True).base_weight_g.transform(
        lambda values: values.quantile(0.99)
    )
    by_drug["upper_1pct_within_drug_year"] = by_drug.base_weight_g.gt(0) & by_drug.base_weight_g.ge(limits)
    by_drug["largest_shipment_share"] = np.where(
        by_drug.base_weight_g.gt(0), by_drug.largest_shipment_g / by_drug.base_weight_g, np.nan
    )
    by_drug["dominant_single_shipment"] = (
        by_drug.shipment_records.ge(10) & by_drug.largest_shipment_share.ge(0.5)
    )
    county_year = by_drug.groupby(["county_fips", "year"], as_index=False, observed=True).agg(AGGREGATIONS)
    pair = by_drug[by_drug.drug_code.isin(PAIR_CODES)].groupby(
        ["county_fips", "year"], as_index=False, observed=True
    ).agg(hydrocodone_oxycodone_g=("base_weight_g", "sum"), hydrocodone_oxycodone_records=("shipment_records", "sum"))
    county_year = county_year.merge(pair, on=["county_fips", "year"], how="left", validate="one_to_one")
    return by_drug, county_year


def clean(args):
    source_audit = json.loads((args.shipments.parent / "audit.json").read_text())
    if not source_audit.get("validated") or source_audit.get("status") != "complete":
        raise ValueError("Source processing is incomplete or unvalidated")
    if source_audit.get("mode") != "full" and not args.allow_pilot:
        raise ValueError("Pilot input requires --allow-pilot")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    audit = {"status": "running", "validated": False, "source_mode": source_audit["mode"]}
    try:
        with resource_guard(output, args.max_memory_mib, args.min_free_gib) as peak:
            shipments = pd.read_parquet(args.shipments, dtype_backend="pyarrow")
            if shipments.duplicated(KEYS).any():
                raise ValueError("Duplicate source aggregate keys")
            labels = pd.read_csv(args.shipments.parent / "drug_labels.csv", dtype={"drug_code": "string"})
            canonical = labels.sort_values(["source_records", "drug_name"], ascending=[False, True]).drop_duplicates("drug_code")
            canonical = canonical[["drug_code", "drug_name"]].rename(columns={"drug_name": "canonical_drug_name"})
            label_review = labels.merge(canonical, on="drug_code", validate="many_to_one")
            label_review["label_disagrees"] = label_review.drug_name.ne(label_review.canonical_drug_name)
            label_review.to_csv(output / "drug_label_review.csv", index=False)
            shipments = shipments.merge(canonical, on="drug_code", how="left", validate="many_to_one")
            if shipments.canonical_drug_name.isna().any():
                raise ValueError("Missing display label for an observed drug code")
            shipments = shipments.rename(columns={"canonical_drug_name": "drug_name"})
            roles = {value: buyer_group(value) for value in shipments.buyer_type.unique()}
            shipments["buyer_group"] = shipments.buyer_type.map(roles)
            scope = shipments.buyer_state.isin(STATES)
            outside_scope = shipments[~scope].groupby("buyer_state", as_index=False, observed=True).agg(AGGREGATIONS)
            outside_scope.to_csv(output / "outside_study_geography.csv", index=False)
            within_scope = shipments[scope].copy()
            buyer_summary = within_scope.groupby(["buyer_type", "buyer_group"], as_index=False, observed=True).agg(AGGREGATIONS)
            buyer_summary["included_in_main"] = buyer_summary.buyer_group.isin(["pharmacy", "practitioner"])
            buyer_summary.to_csv(output / "buyer_type_summary.csv", index=False)
            if getattr(args, "population", None):
                reference = pd.read_parquet(args.population)[["state", "state_fips", "county_name", "county_fips", "year"]].copy()
                reference = reference[reference.year.between(source_audit["start_year"], source_audit["end_year"])]
                reference["county_key"] = reference.county_name.map(normalize_county)
                if reference.duplicated(["county_fips", "year"]).any() or reference.duplicated(["state", "county_key", "year"]).any():
                    raise ValueError("Population reference has ambiguous keys")
            else:
                reference = county_reference(args.mortality_zip, source_audit["start_year"], source_audit["end_year"])
            reference.to_csv(output / "county_reference.csv", index=False)
            geo_input = within_scope.groupby(["buyer_state", "buyer_county", "year"], as_index=False, observed=True).agg(AGGREGATIONS)
            geography = match_counties(geo_input, reference)
            geography.to_csv(output / "county_matching_review.csv", index=False)
            geography[geography.county_fips.isna()].to_csv(output / "unmatched_counties.csv", index=False)
            map_columns = ["buyer_state", "buyer_county", "year", "county_fips", "match_method"]
            matched = within_scope.merge(
                geography[map_columns], on=["buyer_state", "buyer_county", "year"],
                how="left", validate="many_to_one",
            )
            matched.to_parquet(output / "shipments_by_buyer_type_fips.parquet", index=False)
            by_drug, county_year = summarize_main(matched)
            panel = reference.drop(columns="county_key").merge(
                county_year, on=["county_fips", "year"], how="left", validate="one_to_one"
            )
            panel["has_shipment_record"] = panel.shipment_records.notna()
            panel[~panel.has_shipment_record].to_csv(output / "county_years_without_shipments.csv", index=False)
            by_drug.to_parquet(output / "shipments_county_year_drug.parquet", index=False)
            panel.to_parquet(output / "shipments_county_year.parquet", index=False)
            review = by_drug[by_drug.upper_1pct_within_drug_year | by_drug.dominant_single_shipment]
            review.sort_values("base_weight_g", ascending=False).to_csv(output / "large_shipments_review.csv", index=False)
            direct = matched.buyer_group.isin(["pharmacy", "practitioner"])
            main_source = matched[direct & matched.county_fips.notna()]
            source_records = int(shipments.shipment_records.sum())
            main_records = int(main_source.shipment_records.sum())
            if source_records != source_audit["aggregate_records"]:
                raise ValueError("Source record totals do not match the stream audit")
            if len(shipments) != source_audit["aggregate_rows"] or not math.isclose(
                math.fsum(shipments.base_weight_g), source_audit["aggregate_weight_g"], rel_tol=1e-10, abs_tol=1e-7
            ):
                raise ValueError("Source group counts or weights do not match the stream audit")
            if main_records != int(by_drug.shipment_records.sum()):
                raise ValueError("Cleaned record totals do not reconcile")
            if by_drug.duplicated(["county_fips", "year", "drug_code"]).any():
                raise ValueError("Duplicate cleaned county-year-drug keys")
            if not math.isclose(math.fsum(main_source.base_weight_g), math.fsum(by_drug.base_weight_g), rel_tol=1e-10, abs_tol=1e-7):
                raise ValueError("Cleaned weights do not reconcile")
            if panel.duplicated(["county_fips", "year"]).any():
                raise ValueError("Duplicate county-year panel keys")
            audit.update(
                status="complete", validated=True, source_records=source_records,
                source_weight_g=math.fsum(shipments.base_weight_g),
                outside_scope_records=int(shipments.loc[~scope, "shipment_records"].sum()),
                outside_scope_weight_g=math.fsum(shipments.loc[~scope, "base_weight_g"]),
                excluded_buyer_records=int(matched.loc[~direct, "shipment_records"].sum()),
                excluded_buyer_weight_g=math.fsum(matched.loc[~direct, "base_weight_g"]),
                unmatched_direct_buyer_records=int(matched.loc[direct & matched.county_fips.isna(), "shipment_records"].sum()),
                unmatched_direct_buyer_weight_g=math.fsum(matched.loc[direct & matched.county_fips.isna(), "base_weight_g"]),
                main_records=main_records, main_weight_g=math.fsum(by_drug.base_weight_g),
                main_drug_rows=len(by_drug), county_year_rows=len(panel),
                county_years_with_shipments=int(panel.has_shipment_record.sum()),
                county_years_without_shipments=int((~panel.has_shipment_record).sum()),
                unmatched_name_years=int(geography.county_fips.isna().sum()),
                flagged_large_county_year_drug_rows=len(review),
                source_label_disagreements=int(label_review.loc[label_review.label_disagrees, "source_records"].sum()),
                sampled_peak_rss_mib=round(peak["rss_mib"], 2),
                county_reference_source="annual_census_population" if getattr(args, "population", None) else "mortality_export",
            )
            if source_records != sum(audit[name] for name in ["outside_scope_records", "excluded_buyer_records", "unmatched_direct_buyer_records", "main_records"]):
                raise ValueError("Cleaning exclusions do not reconcile")
            if not math.isclose(audit["source_weight_g"], math.fsum(audit[name] for name in [
                "outside_scope_weight_g", "excluded_buyer_weight_g", "unmatched_direct_buyer_weight_g", "main_weight_g"
            ]), rel_tol=1e-10, abs_tol=1e-7):
                raise ValueError("Cleaning weight exclusions do not reconcile")
            write_json(output / "cleaning_audit.json", audit)
            print(json.dumps(audit, indent=2), flush=True)
            return audit
    except BaseException as error:
        audit.update(status="failed", validated=False, error=str(error))
        write_json(output / "cleaning_audit.json", audit)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shipments", type=Path, required=True)
    parser.add_argument("--mortality-zip", type=Path, required=True)
    parser.add_argument("--population", type=Path, help="Annual Census county-year table; preferred geographic reference")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-pilot", action="store_true")
    parser.add_argument("--max-memory-mib", type=int, default=1024)
    parser.add_argument("--min-free-gib", type=float, default=10)
    clean(parser.parse_args())


if __name__ == "__main__":
    main()
