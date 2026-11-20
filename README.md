# PDS2026-Opiod-Project

## ARCOS shipment processing

This workflow follows the IDS720 [Big Data Strategies](https://practicaldatascience.org/notebooks/PDS_not_yet_in_coursera/30_big_data/20_big_data_strategies.html) and [Parquet](https://practicaldatascience.org/notebooks/PDS_not_yet_in_coursera/30_big_data/30_parquet.html) readings: import chunks with pandas, retain needed fields and records, aggregate, and combine smaller summaries.

Use a Python environment containing `requirements.txt`. Set `ARCOS_ZIP` locally to the existing ZIP's path, then run:

```bash
# Bounded pilot: first 100,000 source records.
python src/process_arcos.py --zip "$ARCOS_ZIP" --output outputs/arcos_pilot

# Full stream: explicitly opt in; use a new output directory.
python src/process_arcos.py --zip "$ARCOS_ZIP" --output outputs/arcos_full --full
```

The archive is read directly, without extraction. The default chunk is 50,000 rows with `usecols` and explicit string dtypes. Each chunk is filtered and aggregated by buyer state, buyer county name, year, and drug code. Compact Parquet summaries are merged in pairs and summed again, including keys crossing chunk boundaries. This keeps only the current chunk or current pair of summaries in memory; it never collects raw chunks. Late reductions still need memory proportional to the unique aggregate keys, so a background resource guard interrupts if process RAM exceeds 1,024 MiB or free disk falls below 10 GiB. Limits are configurable; they are protective thresholds, not an exact allocation cap.

The default analysis eligibility is sale transactions (`S`), 2006–2015, outside Alaska, with valid locations/drug codes and finite nonnegative active base weights. All states otherwise remain available for later control selection. Missing/nonfinite and negative weights are audited and excluded, not imputed. Zero weights remain valid. All transaction codes and drug labels are inventoried before eligibility filtering. Confirm the final drug set and transaction rules with the team before constructing the study outcome; this preprocessing output includes every eligible drug code, rather than silently choosing an opioid subset. No deduplication is applied: identical-looking transactions may be legitimate separate shipments.

Successful output contains:

| File | Contents |
| --- | --- |
| `shipments_county_name_year_drug.parquet` | `buyer_state`, `buyer_county`, `year`, `drug_code`, `base_weight_g`, `shipment_records` |
| `audit.json` | Run mode/status, eligibility/exclusion counts, transaction codes, reconciliation checks, progress, resource measurements |
| `drug_labels.csv` | Source drug-code/name combinations and their record counts |

`base_weight_g` is the sum of `CALC_BASE_WT_IN_GM`, in grams, not pills, MME, prescriptions, or consumption. County names must be mapped to reviewed five-character FIPS before joining mortality/population. There is no population normalization or policy/control selection at this stage. Ciera's mortality script remains on `arcos-data` and is unchanged by this work.

Always check `audit.json`: `status` must be `complete`, `validated` true, and `mode` `full` before interpreting an output as national processing. The script verifies unique group keys, retained/excluded record accounting, and agreement between accepted-record and aggregated weight totals. Audit problem counts can overlap; `excluded_*` counts instead record each rejected row's first failing eligibility rule and are mutually exclusive.

Failed runs keep partial files for inspection. They are not resumable and must not be used as completed results. Existing output directories are refused to prevent overwriting or double counting. Restart in a new directory; ZIP streams must be reread from the beginning. Keep raw inputs and local outputs outside Git, as configured in `.gitignore`.

Run the checks with:

```bash
python -m unittest discover -s tests -v
```

Use a separate feature branch and PR for changes. Fetch/pull before continuing work, push at meaningful milestones, and discuss shipment definitions, FIPS mappings, and mortality integration in PR comments.
