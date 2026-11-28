# State economic, demographic, and healthcare context

The output has one row per state FIPS and year for the 50 states and DC, 2006–2015. It is a separate input for future sample design.

| Field | Definition and source |
| --- | --- |
| `unemployment_rate_pct` | Published annual-average unemployment rate in percent, [BLS LAUS](https://www.bls.gov/lau/tables.htm), period M13 |
| `median_household_income_usd` | State median household income in nominal dollars, [Census SAIPE](https://www.census.gov/data/datasets/time-series/demo/saipe.html) |
| `state_population`, `population_65_plus`, `population_65_plus_share` | [Census Population Estimates](https://www.census.gov/programs-surveys/popest.html) for July 1. The 2006–2009 estimates use the 2000–2010 intercensal release. The 2010–2015 estimates use Vintage 2019 and six mutually exclusive race categories for total origin and both sexes. |
| `active_nonfederal_md`, `active_nonfederal_md_per_10000` | [HRSA Area Health Resources Files](https://data.hrsa.gov/data/download), field family `f08857`, active non-federal M.D.s summed across counties and divided by the state population for the per-10,000 measure |

The HRSA inputs are the 2009–2010 and 2016–2017 county releases. They supply the physician count series for 2006–2008 and 2010–2015. The two selected releases do not provide a 2009 observation for this series. Alaska and Virginia also have incomplete county counts in 2010–2015. A state total is left null if any source county count is missing. The income field is a nominal household median.

`active_nonfederal_md_observed_per_10000` uses only counties with a reported physician count and matching Census population. The count and population use the same counties, including counties that report zero physicians. `physician_population_coverage_share` is their population divided by the statewide population. The observed county count, population, and physician subtotal are also retained. Bedford County, York County, and Newport News are excluded from these auxiliary measures because of boundary comparability. Alaska has no county population records in the project input. The auxiliary measures remain null when there are no matched observations, including in 2009. Compare candidates using consistent observed years. Any future imputation should be kept in separate analysis fields.

Keep the following source files under `data/raw/`:

- Census `st-est00int-agesex.csv` and `sc-est2019-alldata6.csv`, plus annual SAIPE `est06all.txt` through `est15all.txt`.
- BLS [public API](https://www.bls.gov/developers/), annual averages for 2006–2015. Request `LAUST{state_fips}0000000000003` for the 50 states and DC, in ascending FIPS order and batches of 20. Save the three JSON responses as `bls_laus_0.json`, `bls_laus_20.json`, and `bls_laus_40.json`.
- HRSA `ahrf2009.asc`, `ahrf2017.asc`, and their corresponding technical documentation ZIPs `AHRF_USER_TECH_2009-2010.zip` and `AHRF_USER_TECH_2016-2017.zip`.

Run from the project root with a new output directory:

```bash
python src/clean_state_context.py --output outputs/state_context
```

The script writes the state-year Parquet and a JSON coverage audit. Join to the state policy table on `state_fips` and `year` when building analysis inputs.
