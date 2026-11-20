# ARCOS streaming and mortality integration plan

Reviewed 2026-11-20. This branch contains planning only; no full ARCOS run has been launched and no mortality code has been changed.

## Current repository

- `main` contains the initial README.
- `arcos-data` contains Ciera's `US_VitalStatistics.py` and mortality archive.
- Keep ARCOS processing in a separate `src/process_arcos.py`; merge the resulting tables later rather than inserting shipment processing into the mortality script.

## Processing sequence

1. Open the ZIP with `zipfile.ZipFile`, then open the TSV member as a stream; never use `extractall`, `read()` of the whole member, or collect raw chunks in a list.
2. Use `csv.reader` and resolve needed column positions from the actual header. Read records sequentially; do not retain unused fields between iterations.
3. Process 50,000 records per batch. Keep national candidate coverage for 2006–2015, exclude Alaska, and document the treatment of transaction codes. The pilot used `TRANSACTION_CODE == 'S'`; audit the full-file code distribution before finalizing this rule.
4. Aggregate `CALC_BASE_WT_IN_GM` within each batch by `BUYER_STATE`, `BUYER_COUNTY`, year, and `DRUG_CODE`. This field is active base weight in grams. Keep drug-specific totals; do not assume all drug quantities have equal potency or equate shipments with prescriptions consumed.
5. Flush each batch's small aggregate into an on-disk SQLite accumulator using additive upserts; then release its dictionary. This bounds memory by the current batch rather than the entire transaction history. Keys may span batches, so totals must be combined across batches.
6. Keep explicit counters for malformed dates, missing geography, invalid/nonfinite weights, negative weights, transaction codes, excluded years, and retained records. Preserve audit counts instead of silently dropping problematic records. Audit every record even if it will later be excluded from the analysis sample.
7. Periodically log rows processed, elapsed time, process memory, and output size; use an approximately 1 GiB process-memory warning threshold and stop before disk free space falls below 10 GiB. Start with one processing worker. Do not run parallel readers over the same compressed member.
8. Reconcile accepted-record weight totals with aggregated totals. Save national county/year/drug totals and a quality summary; build the final county-year total only after agreeing on included drugs.
9. Map buyer state and county names to five-character county FIPS using a reviewed geographic crosswalk. ARCOS has buyer county names, not a county-FIPS field. Report unmatched/ambiguous locations and reconcile their weights before merging.
10. Merge population and mortality on county FIPS plus year, with uniqueness and merge-cardinality checks. Apply final control-state selections downstream.

SQLite commits preserve partial aggregates for inspection, but do not make a stopped ZIP stream cheaply resumable. Do not claim automatic resume or rerun against an existing accumulator in a way that double-counts records. A full rerun should use a fresh temporary accumulator and publish outputs only on successful completion.

## Ciera's mortality script: integration review

- The ZIP-reading loop is reusable and the mortality input is small. It already demonstrates reading compressed data without extraction.
- At line 54, comparing a Series to a four-item list with `==` is not a membership filter. Use `.isin(...)`, preferably with reviewed cause codes.
- `D9` means all other drug-induced causes; it should not be automatically included in an outcome labeled overdose deaths. The inspected 2006 file contains overdose categories `D1`, `D2`, and `D4`; verify coverage across all years and document the definition.
- The FL/WA-only filter at line 33 should be a downstream analysis subset. Preserve a national cleaned table for control-state comparisons.
- Read county codes as text and normalize to five characters; normalize years to integers before joining. Avoid float-to-string FIPS such as `1001.0`.
- Aggregate causes to county-year deliberately. A sum of reported categories may undercount total overdose deaths when another category is unreported. Keep category coverage flags and do not treat unreported categories or absent county-years as zero.
- Refactor top-level code into a function/CLI with an explicit input path and output table; retain the original source archive unchanged.

## GitHub collaboration

- Keep this plan on `yiran/arcos-streaming-plan` and request review before treating definitions as agreed.
- Open a separate implementation branch, e.g. `yiran/arcos-streaming`, from current `main` once the plan is reviewed.
- Fetch before starting/resuming work. Pull with `--ff-only` on the current branch when appropriate; integrate newer `main` commits explicitly rather than overwriting shared branches.
- Commit and push at useful milestones: pilot, aggregation/audit implementation, then validated output interface. Do not repeatedly push incomplete raw-data runs.
- Use issues and PR comments to discuss shipment units, mortality definitions, FIPS mappings, and merge keys. Each member should review another member's PR.
- Do not force-push Ciera's branch, silently edit her work, or merge unfinished source branches solely to obtain the data.
- Keep the large ARCOS ZIP, SQLite working files, and raw data out of Git; add targeted ignore rules with implementation. Small derived tables require an explicit sharing decision.

## Immediate deliverables

1. Review this plan and resolve mortality/control-coverage questions.
2. Implement a pilot-default CLI with an explicit full-run option.
3. Validate batch accumulation against a small reference aggregation, including keys crossing batch boundaries and invalid input handling.
4. Run a larger bounded pilot, then launch the full stream only after checking disk headroom and logging.
