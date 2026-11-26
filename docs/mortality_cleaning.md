# Mortality cleaning and handoff

The per-cause `_status` fields distinguish `reported` counts, source rows marked `explicit_missing`, and rows `not_in_export`. An absent row may represent a zero or a suppressed count. Both missing statuses leave the count null. The audit includes status counts by state FIPS, year, and cause.

Run from the repository root:

```bash
python src/clean_mortality.py --zip "data/raw/US_VitalStatistics.zip" --output outputs/mortality_clean
```

The input is the provided CDC WONDER Underlying Cause of Death ZIP. This step keeps 2006–2015, excludes Alaska, territories, and non-county footer rows, and creates one row per observed county-year. `county_fips` is five-character text; `year` is an integer. The output is `mortality_county_year.parquet`, a CSV copy, and `mortality_audit.json`.

`deaths_unintentional` is the D1 drug poisoning category (X40–X44) and is the primary available overdose outcome. D2 (suicide), D3 (homicide), and D4 (undetermined) are retained separately. Each count has a corresponding `_reported` flag. Absent rows remain null: the source query excludes zero and suppressed counts below 10, so their actual values cannot be distinguished. Explicit `Missing` rows also remain null. A null count is never a zero. Do not add the reported categories and call the result total overdose deaths; the missing components make such a sum incomplete.

The provided files contain cause categories, not the substances involved. They cannot identify opioid-specific, prescription-specific, heroin, or fentanyl deaths. The proposal's opioid-specific extension requires another dataset and a separate definition. County overdose rates per 100,000 require annual county population and are calculated after that dataset is joined.

For later integration, validate one-to-one keys on both sides and merge on `county_fips` and `year`. Keep mortality nulls and inspect unmatched geography. The cleaned shipments table in `outputs/shipments_clean_final/shipments_county_year.parquet` uses the same keys. This step does not choose controls or create treatment indicators.
