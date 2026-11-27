"""Estimate county-based pre/post and difference-in-differences for one state."""

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


YEARS = list(range(2006, 2016))
STATE_YEAR = ["state_fips", "year"]
COUNTY_YEAR = ["county_fips", "year"]
SHIPMENTS = "base_weight_g_per_person"
MORTALITY = "deaths_unintentional_per_100k"
OUTCOMES = {SHIPMENTS: "Opioid shipments\nGrams per resident",
            MORTALITY: "Unintentional drug overdose deaths\nDeaths per 100,000 residents"}
ELIGIBILITY = {SHIPMENTS: "shipments_primary_eligible", MORTALITY: "mortality_primary_eligible"}
CONTEXT = ["unemployment_rate_pct", "median_household_income_usd",
           "population_65_plus_share", "active_nonfederal_md_per_10000"]


def read_inputs(root):
    panel = pd.read_parquet(root / "state_policy/county_year_with_policies.parquet")
    policies = pd.read_parquet(root / "state_policy/state_year_policies.parquet")
    context = pd.read_parquet(root / "state_context/state_year_context.parquet")
    for frame, keys in [(panel, COUNTY_YEAR), (policies, STATE_YEAR), (context, STATE_YEAR)]:
        if frame[keys].isna().any().any() or frame.duplicated(keys).any():
            raise ValueError(f"Missing or duplicate keys in {keys}")
    if not (panel.county_fips.str.fullmatch(r"\d{5}").all()
            and panel.county_fips.str[:2].eq(panel.state_fips).all()
            and np.isfinite(panel.population).all() and panel.population.gt(0).all()):
        raise ValueError("Invalid county geography or population")
    return panel, policies, context


def state_rates(panel, outcome, eligible, equal_counties=False):
    """Summarize observed county-years and retain population coverage."""
    universe = panel.groupby(STATE_YEAR).population.sum()
    observed = panel.loc[eligible, STATE_YEAR + ["county_fips", "population", outcome]]
    rows = []
    for (fips, year), group in observed.groupby(STATE_YEAR):
        rates = group[outcome].to_numpy(dtype=float)
        if not np.isfinite(rates).all():
            raise ValueError(f"Nonfinite eligible rate for {fips}, {year}, {outcome}")
        weights = np.ones(len(group)) if equal_counties else group.population.to_numpy(dtype=float)
        mean = np.average(rates, weights=weights)
        se = np.nan
        if len(group) > 1:
            leave_one_out = (mean * weights.sum() - rates * weights) / (weights.sum() - weights)
            se = np.sqrt((len(group) - 1) / len(group) * ((leave_one_out - leave_one_out.mean()) ** 2).sum())
        population = group.population.sum()
        rows.append((fips, year, mean, se, len(group), population, population / universe.loc[fips, year]))
    return pd.DataFrame(rows, columns=STATE_YEAR + [
        "rate", "county_jackknife_se", "counties", "population_covered", "population_share"
    ])


def select_controls(policies, context, rates, treated, intervention, exclude, count):
    """Screen around the intervention and rank on pre-policy trajectories and context."""
    policy_columns = [column for column in policies if column.endswith("_adopted")]
    eligible = {}
    history = {}
    for fips, state in policies.groupby("state_fips"):
        near = state[state.year.between(intervention - 1, intervention + 1)]
        focal_year = state[state.year.eq(intervention)]
        if fips == treated or fips in exclude:
            reason = "focal or excluded jurisdiction"
        elif len(near) != 3 or near[policy_columns].isna().any().any() or focal_year.pain_clinic_law_active.isna().any():
            reason = "unknown policy coverage"
        elif near[policy_columns].any().any() or focal_year.pain_clinic_law_active.any():
            reason = "adoption near intervention or active pain-clinic law"
        else:
            reason = ""
        eligible[fips] = reason
        history[fips] = "; ".join(
            f"{record.year} {column.removesuffix('_adopted')}"
            for record in state.itertuples()
            for column in policy_columns
            if pd.notna(getattr(record, column)) and getattr(record, column)
        )

    audit = context[["state_fips", "state_name"]].drop_duplicates().set_index("state_fips")
    audit["exclusion"] = pd.Series(eligible).reindex(audit.index).fillna("unknown policy coverage")
    audit["tracked_adoptions"] = pd.Series(history)
    audit["no_tracked_adoption_2006_2015"] = (
        policies.groupby("state_fips").state_regulation_change_during_study.first().eq(False)
    )
    pre_years = [year for year in YEARS if year < intervention]
    context_pre = context[context.year.lt(intervention)]
    # Use common observed physician years, including a uniform exclusion of 2009.
    physician_years = context_pre.dropna(subset=[CONTEXT[-1]]).year.unique()
    context_pre = context_pre.assign(physician_observed=context_pre[CONTEXT[-1]].notna())
    demographics = context_pre.groupby("state_fips")[CONTEXT].mean()
    physician_counts = context_pre.groupby("state_fips").physician_observed.sum()
    demographics.loc[physician_counts.ne(len(physician_years)), CONTEXT[-1]] = np.nan
    pre_changes = {}
    for outcome, table in rates.items():
        series = table[table.year.lt(intervention)].pivot(index="state_fips", columns="year", values="rate")
        shares = table[table.year.lt(intervention)].pivot(index="state_fips", columns="year", values="population_share")
        changes = series[pre_years].subtract(series[pre_years[0]], axis=0).iloc[:, 1:]
        pre_changes[outcome] = changes
        full_years = table.groupby("state_fips").year.nunique().eq(len(YEARS))
        adequate = shares[pre_years].min(axis=1, skipna=False).ge(.5) & full_years
        audit[f"{outcome}_min_pre_population_share"] = shares[pre_years].min(axis=1, skipna=False)
        audit.loc[audit.exclusion.eq("") & ~adequate.reindex(audit.index, fill_value=False),
                  "exclusion"] = "insufficient outcome coverage"
    audit.loc[audit.exclusion.eq("") & demographics.isna().any(axis=1), "exclusion"] = "incomplete pre-policy context"
    audit = audit.join(demographics.add_prefix("pre_mean_"))
    candidates = audit.index[audit.exclusion.eq("")]
    if len(candidates) < count:
        raise ValueError(f"Only {len(candidates)} states pass the control screen")

    for name, features in [*pre_changes.items(), ("context", demographics)]:
        scale = features.loc[candidates].std().replace(0, 1)
        distance = features.subtract(features.loc[treated]).div(scale)
        audit[f"{name}_distance"] = distance.pow(2).mean(axis=1, skipna=False).pow(.5).where(audit.exclusion.eq(""))
    audit["score"] = (.4 * audit[f"{SHIPMENTS}_distance"] + .4 * audit[f"{MORTALITY}_distance"]
                      + .2 * audit.context_distance)
    if audit.loc[candidates, "score"].isna().any():
        raise ValueError("Incomplete pre-policy outcomes or context for the treated state")
    prescribing_columns = ["pdmp_access_adopted", "pdmp_mandate_adopted", "pain_clinic_law_adopted"]
    post_prescribing = policies.loc[policies.year.gt(intervention)]
    known = post_prescribing[prescribing_columns].notna().all(axis=1).groupby(post_prescribing.state_fips).all()
    adopted = post_prescribing[prescribing_columns].any(axis=1).groupby(post_prescribing.state_fips).any()
    audit["no_post_prescribing_adoption"] = known & ~adopted
    audit = audit.sort_values(["score", "state_name"])
    selected = audit.index[:count].tolist()
    audit["selected"] = audit.index.isin(selected)
    return selected, audit


def comparison(table, treated, controls):
    """Compare the treated state with an equal-weight average of the comparison states."""
    treated_rates = table[table.state_fips.eq(treated)].set_index("year")
    control_rows = table[table.state_fips.isin(controls)]
    control_rates = control_rows.pivot(index="year", columns="state_fips", values="rate").reindex(columns=controls)
    if list(treated_rates.index) != YEARS or list(control_rates.index) != YEARS or control_rates.isna().any().any():
        raise ValueError("A study state has a year without an eligible outcome")
    result = pd.DataFrame({
        "treated": treated_rates.rate, "treated_se": treated_rates.county_jackknife_se,
        "controls": control_rates.mean(axis=1), "controls_se": control_rates.sem(axis=1),
    })
    result["gap"] = result.treated - result.controls
    return result


def contrasts(annual, intervention, include_transition=False, last_year=2015):
    pre = annual.loc[annual.index < intervention, ["treated", "controls", "gap"]]
    first_post = intervention if include_transition else intervention + 1
    post = annual.loc[first_post:last_year, ["treated", "controls", "gap"]]
    delta = post.mean() - pre.mean()
    slope, intercept = np.polyfit(pre.index - intervention, pre.gap, 1)
    return {
        "treated_pre": pre.treated.mean(), "treated_post": post.treated.mean(),
        "control_pre": pre.controls.mean(), "control_post": post.controls.mean(),
        "treated_change": delta.treated, "control_change": delta.controls, "did": delta.gap,
        "pre_gap_slope_per_year": slope,
        "change_in_gap_slope_per_year": np.polyfit(post.index - intervention, post.gap, 1)[0] - slope
        if len(post) > 1 else np.nan,
        "did_after_extrapolating_pre_gap_trend": (post.gap - (intercept + slope * (post.index - intervention))).mean(),
    }


def drug_contrasts(root, sample, treated, controls, intervention):
    """Decompose total recorded shipment weight by drug on the same county-year support."""
    drug_fields = COUNTY_YEAR + ["drug_code", "drug_name", "base_weight_g"]
    drugs = pd.read_parquet(root / "county_panel/shipments_drug_rates.parquet", columns=drug_fields)
    counties = sample.loc[sample.shipments_primary_eligible, COUNTY_YEAR + ["state_fips", "population"]]
    observed = drugs.merge(counties, on=COUNTY_YEAR, validate="many_to_one")
    weights = observed.groupby(STATE_YEAR + ["drug_code"]).base_weight_g.sum().unstack(fill_value=0)
    population = counties.groupby(STATE_YEAR).population.sum()
    weights = weights.reindex(population.index, fill_value=0)
    if not np.allclose(weights.sum(axis=1), sample.loc[sample.shipments_primary_eligible]
                       .groupby(STATE_YEAR).base_weight_g.sum()):
        raise ValueError("Drug weights do not reconcile to the county panel")
    rates = weights.div(population, axis=0)
    names = drugs.set_index("drug_code").drug_name.to_dict()
    estimates = []
    for code in rates:
        series = rates[code].unstack("state_fips")
        annual = pd.DataFrame({"treated": series[treated], "controls": series[controls].mean(axis=1)})
        annual["gap"] = annual.treated - annual.controls
        estimates.append({"drug_code": code, "drug_name": names[code], **contrasts(annual, intervention)})
    return pd.DataFrame(estimates)


def draw(annual, name, intervention, path, pre_post=False):
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.1), layout="constrained")
    series = {"treated": (name, "#155B73")}
    if not pre_post:
        series["controls"] = ("Comparison states", "#914C29")
    for ax, (outcome, title) in zip(axes, OUTCOMES.items()):
        data = annual[outcome]
        ax.set_title(title, loc="left", fontweight="bold", fontsize=11)
        ax.axvline(intervention, color="#56616A", linewidth=1, linestyle=":")
        for column, (label, color) in series.items():
            ax.scatter(intervention, data.loc[intervention, column], s=25, facecolors="white",
                       edgecolors=color, zorder=3)
            for period in (data.loc[data.index < intervention], data.loc[data.index > intervention]):
                values = period[column]
                ax.plot(period.index, values, "o", color=color, markersize=4)
                fitted = np.polyval(np.polyfit(period.index - intervention, values, 1), period.index - intervention)
                ax.plot(period.index, fitted, color=color, lw=1.5,
                        label=label if period.index[0] == YEARS[0] else None)
                se = period[f"{column}_se"]
                ax.fill_between(period.index, values - se, values + se, color=color, alpha=.15, linewidth=0)
        ax.set_xticks(YEARS[::2])
        ax.set_xlabel("Year")
        ax.spines[["top", "right"]].set_visible(False)
        ax.grid(axis="y", color="#E3E8EB", linewidth=.7)
        ax.set_axisbelow(True)
        ax.legend(frameon=False, fontsize=9, loc="best")
    fig.suptitle(f"{name} before and after the {intervention} policy rollout" if pre_post else
                 f"{name} and its comparison states, {YEARS[0]}–{YEARS[-1]}", fontweight="bold")
    fig.supxlabel("Dots show annual rates. Lines fit each period separately. Shading shows ±1 descriptive standard error.\n"
                  f"Open dots mark {intervention}, which is excluded from the main estimates.", fontsize=8)
    fig.savefig(path, dpi=160)
    plt.close(fig)


def run(root, output, treated, intervention, exclude, count):
    panel, policies, context = read_inputs(root)
    primary = {outcome: state_rates(panel, outcome, panel[flag]) for outcome, flag in ELIGIBILITY.items()}
    controls, ranking = select_controls(policies, context, primary, treated, intervention, exclude, count)
    sample = panel[panel.state_fips.isin([treated, *controls])].copy()
    alternate = ranking.index[ranking.score.notna() & ranking.no_post_prescribing_adoption].tolist()[:count]
    ranking["selected_no_post_prescribing_adoption"] = ranking.index.isin(alternate) & (len(alternate) == count)
    annual = {outcome: comparison(table, treated, controls) for outcome, table in primary.items()}
    estimates = []

    def record(outcome, scenario, series, **settings):
        estimates.append({"outcome": outcome, "scenario": scenario, **contrasts(series, intervention, **settings)})

    for outcome, flag in ELIGIBILITY.items():
        eligible = sample[flag]
        record(outcome, "main", annual[outcome])
        record(outcome, "include_transition_in_post", annual[outcome], include_transition=True)
        record(outcome, "first_full_post_year", annual[outcome], last_year=intervention + 1)
        record(outcome, "first_two_full_post_years", annual[outcome], last_year=intervention + 2)
        if len(alternate) == count:
            record(outcome, "controls_with_no_post_prescribing_adoption", comparison(primary[outcome], treated, alternate))
        for fips in controls:
            rest = [state for state in controls if state != fips]
            record(outcome, f"omit_{fips}", comparison(primary[outcome], treated, rest))
        complete = eligible.groupby(sample.county_fips).transform("all")
        for scenario, mask, equal_counties in [
            ("complete_counties", eligible & complete, False),
            ("equal_county_weights", eligible, True),
        ]:
            record(outcome, scenario, comparison(state_rates(sample, outcome, mask, equal_counties), treated, controls))

    flagged = sample.shipment_upper_total_flag | sample.shipment_single_transaction_flag
    shipments = state_rates(sample, SHIPMENTS, sample.shipments_primary_eligible & ~flagged)
    record(SHIPMENTS, "exclude_flagged_shipments", comparison(shipments, treated, controls))
    secondary = "hydrocodone_oxycodone_g_per_person"
    drugs = state_rates(sample, secondary, sample.shipments_primary_eligible & sample[secondary].notna())
    record(secondary, "reported_hydrocodone_oxycodone", comparison(drugs, treated, controls))

    # For missing D1 counts with upstream bounds, align the endpoints with the
    # sign of each cell in the DiD contrast. The endpoints share the same support.
    bounds_eligible = sample.deaths_unintentional_status.eq("not_in_export")
    upper_in_positive_cells = sample.state_fips.eq(treated).eq(sample.year.gt(intervention))
    for endpoint, use_upper in [("low", ~upper_in_positive_cells), ("high", upper_in_positive_cells)]:
        lower = sample.deaths_unintentional_lower_per_100k
        upper = sample.deaths_unintentional_upper_per_100k
        bounded = sample.assign(**{MORTALITY: sample[MORTALITY].mask(bounds_eligible, lower.where(~use_upper, upper))})
        eligible = (sample.mortality_primary_eligible | bounds_eligible) & bounded[MORTALITY].notna()
        record(MORTALITY, f"conditional_did_{endpoint}", comparison(state_rates(bounded, MORTALITY, eligible), treated, controls))

    drug_results = drug_contrasts(root, sample, treated, controls, intervention)
    output.mkdir(parents=True, exist_ok=True)
    drug_results.to_csv(output / "drug_shipment_estimates.csv", index=False, float_format="%.8f")
    ranking.to_csv(output / "control_selection.csv", float_format="%.8f")
    pd.DataFrame(estimates).to_csv(output / "estimates.csv", index=False, float_format="%.8f")
    pd.concat(
        [table[table.state_fips.isin([treated, *controls])].assign(outcome=outcome)
         for outcome, table in primary.items()]
    ).to_csv(output / "coverage.csv", index=False, float_format="%.8f")
    trends = pd.concat(annual, names=["outcome"]).reset_index()
    trends["event_year"] = trends.year - intervention
    trends["gap_change_vs_last_pre"] = trends["gap"] - trends.outcome.map(
        {outcome: table.loc[intervention - 1, "gap"] for outcome, table in annual.items()}
    )
    trends.to_csv(output / "annual_trends.csv", index=False, float_format="%.8f")
    draw(annual, ranking.loc[treated, "state_name"], intervention, output / "difference_in_differences.png")
    draw(annual, ranking.loc[treated, "state_name"], intervention, output / "appendix_pre_post.png", pre_post=True)
    print("Selected comparison states:", ", ".join(ranking.loc[controls, "state_name"]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, default=Path("outputs"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--treated", default="12", help="Two-digit state FIPS (default: Florida)")
    parser.add_argument("--intervention", type=int, default=2010)
    parser.add_argument("--exclude", nargs="*", default=[], help="Additional state FIPS to exclude")
    parser.add_argument("--control-count", type=int, default=3)
    args = parser.parse_args()
    if args.intervention not in YEARS[2:-2] or args.control_count < 3:
        parser.error("Provide at least two years on either side and at least three controls")
    exclude = {"02", "11", "12", "53"} | set(args.exclude)
    run(args.inputs, args.output, args.treated, args.intervention, exclude, args.control_count)


if __name__ == "__main__":
    main()
