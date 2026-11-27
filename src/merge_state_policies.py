"""Add documented state policy adoptions to the cleaned county-year panel."""

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd


POLICIES = {
    "pdmp_access": "pdmp_access",
    "pdmp_mandate": "pdmp_mandate",
    "pillmill": "pain_clinic_law",
    "naloxone": "naloxone_access",
    "samaritan": "good_samaritan",
}
STATE_YEAR = ["state_fips", "year"]
STUDY_YEARS = range(2006, 2016)
SOURCE_URL = "https://github.com/SPPDindex/SPPD-State-Policy-Index/blob/main/SPPD_V1_3.1.dta"


def any_known_change(flags):
    """Return false only when every tracked flag is known and false."""
    any_true = flags.fillna(False).any(axis=1)
    return any_true.astype("boolean").mask(~any_true & flags.isna().any(axis=1))


def prepare_state_policies(source, jurisdictions):
    """Compare consecutive years of SPPD policy status on a complete state-year grid."""
    history = source.loc[source.year.between(2005, 2015), STATE_YEAR + list(POLICIES)].copy()
    history["state_fips"] = history.state_fips.astype("Int64").astype("string").str.zfill(2)
    history["year"] = history.year.astype(int)
    if history[STATE_YEAR].isna().any().any() or history.duplicated(STATE_YEAR).any():
        raise ValueError("Invalid or duplicate policy source keys")
    if not history[list(POLICIES)].apply(lambda col: col.isna() | col.isin([0, 1])).all().all():
        raise ValueError("Expected binary or missing policy status in SPPD")

    # Reindex before shifting so a gap cannot be mistaken for the preceding year.
    grid = pd.MultiIndex.from_product([sorted(jurisdictions), range(2005, 2016)], names=STATE_YEAR)
    active = history.set_index(STATE_YEAR).reindex(grid).rename(columns=POLICIES).astype("boolean")
    previous = active.groupby(level="state_fips").shift()
    adopted = (active & ~previous).mask(active.isna() | previous.isna())
    result = active.add_suffix("_active").join(adopted.add_suffix("_adopted")).reset_index()
    result = result[result.year.isin(STUDY_YEARS)].copy()
    flags = result[[f"{name}_adopted" for name in POLICIES.values()]].copy()

    # Florida's 2010 pain-clinic adoption belongs to the focal intervention package.
    focal = result.state_fips.eq("12") & result.year.eq(2010)
    result["focal_pain_clinic_adoption"] = flags.pain_clinic_law_adopted.where(focal, False)
    flags.loc[focal, "pain_clinic_law_adopted"] = False
    result["state_regulation_change"] = any_known_change(flags)
    # A statewide study-window flag is useful when screening potential controls.
    result["state_regulation_change_during_study"] = result.groupby("state_fips")[
        "state_regulation_change"
    ].transform(lambda values: True if values.fillna(False).any() else False if values.notna().all() else pd.NA)
    result["state_regulation_change_during_study"] = result["state_regulation_change_during_study"].astype("boolean")
    return result.reset_index(drop=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sppd", required=True, type=Path)
    parser.add_argument("--county-panel", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    source = pd.read_stata(args.sppd, columns=STATE_YEAR + list(POLICIES), convert_categoricals=False)
    panel = pd.read_parquet(args.county_panel)
    keys = ["county_fips", "year"]
    if panel[keys + ["state_fips"]].isna().any().any() or panel.duplicated(keys).any():
        raise ValueError("Invalid or duplicate county-panel keys")
    if not panel.state_fips.eq(panel.county_fips.str[:2]).all() or not panel.year.isin(STUDY_YEARS).all():
        raise ValueError("County-panel state or year is outside the expected scope")

    # Keep all states in the policy table and preserve every county-year in the join.
    jurisdictions = set(source.state_fips.dropna().astype(int).astype(str).str.zfill(2)) | set(panel.state_fips)
    state_year = prepare_state_policies(source, jurisdictions | {"11"})
    joined = panel[STATE_YEAR].merge(state_year, on=STATE_YEAR, how="left", validate="many_to_one", indicator=True)
    if len(joined) != len(panel) or not joined["_merge"].eq("both").all():
        raise ValueError("State policy merge did not preserve the county-panel keys")
    merged = panel.join(joined.drop(columns=STATE_YEAR + ["_merge"]))
    audit = {
        "source": SOURCE_URL,
        "source_sha256": hashlib.sha256(args.sppd.read_bytes()).hexdigest(),
        "years": [min(STUDY_YEARS), max(STUDY_YEARS)],
        "tracked_policies": list(POLICIES.values()),
        "state_year_rows": len(state_year),
        "county_year_rows": len(merged),
        "state_year_change_counts": {
            str(value): int(count)
            for value, count in state_year.state_regulation_change.value_counts(dropna=False).items()
        },
    }
    args.output.mkdir(parents=True, exist_ok=False)
    state_year.to_parquet(args.output / "state_year_policies.parquet", index=False)
    merged.to_parquet(args.output / "county_year_with_policies.parquet", index=False)
    (args.output / "state_policy_audit.json").write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(audit, indent=2))


if __name__ == "__main__":
    main()
