# Opioid Policy Project

We examine changes in opioid shipments and overdose mortality following prescribing regulations in Florida (2010) and Washington (2012), using county-year data from 2006–2015.

## Data

- DEA ARCOS shipment records
- County-level Vital Statistics mortality records
- County population estimates

## Process shipments

Install the dependencies in `requirements.txt`, then run a sample:

```bash
python src/process_arcos.py --zip /path/to/arcos_all.zip --output outputs/arcos_pilot
```

To process the full archive:

```bash
python src/process_arcos.py --zip /path/to/arcos_all.zip --output outputs/arcos_full --full
```

Use a new output directory for each run. The script reads the ZIP directly in 50,000-row chunks and combines the aggregated Parquet files.

Shipments are grouped by buyer state, county name, year, drug code, and buyer type. Processing keeps sale transactions (`S`) from 2006–2015 outside Alaska. Missing, nonfinite, and negative weights are excluded and recorded in the audit.

## Clean shipments

Place the provided Vital Statistics ZIP in `data/raw/US_VitalStatistics.zip`, then run:

```bash
python src/clean_shipments.py --shipments outputs/arcos_full/shipments_by_buyer_type.parquet --mortality-zip data/raw/US_VitalStatistics.zip --output outputs/shipments_clean
```

The main measure includes community pharmacies and practitioners. Other buyer types are retained separately. County names are matched to five-digit FIPS using the annual mortality files. [Cleaning decisions](docs/data_cleaning.md) describes the choices and remaining limitations.

## Outputs

The cleaned tables and review files are in [outputs/shipments_clean_final](outputs/shipments_clean_final). Start with [shipments_county_year.parquet](outputs/shipments_clean_final/shipments_county_year.parquet) for the mortality/population merge: 30,796 rows, keyed by `county_fips` and `year`.

| File | Contents |
| --- | --- |
| `shipments_by_buyer_type.parquet` | Shipment totals by source location, year, drug, and buyer type |
| `audit.json` | Processing status, exclusions, and validation results |
| `drug_labels.csv` | Drug codes and names in the source data |
| `shipments_county_year.parquet` | Main shipment measure keyed by `county_fips` and `year` |
| `shipments_county_year_drug.parquet` | Main shipment measure with separate drug codes |
| `shipments_by_buyer_type_fips.parquet` | All retained buyer channels, with geographic matching results |
| `cleaning_audit.json` | Cleaning exclusions and reconciliation checks |

Cleaning also produces CSV files for unmatched locations, missing county-years, buyer types, drug-label disagreements, and large shipments requiring review. Use a cleaned output only when `cleaning_audit.json` reports `status: complete` and `validated: true`. Population normalization and mortality merging follow this step. The shared cleaned outputs are tracked; raw data and intermediate runs stay outside version control.

## Clean mortality

From the repository root, run:

```bash
python src/clean_mortality.py --zip data/raw/US_VitalStatistics.zip --output outputs/mortality_clean
```

If the supplied ZIP is beside the repository, use `--zip "../US_VitalStatistics.zip"`.

| File in `outputs/mortality_clean/` | Contents |
| --- | --- |
| `mortality_county_year.parquet` | County-year table for merging, with five-character text FIPS codes |
| `mortality_county_year.csv` | CSV copy with blank cells for missing counts |
| `mortality_audit.json` | Source exclusions and reporting coverage by state, year, and cause |

The supplied ZIP produces 30,796 county-year rows for 2006–2015, excluding Alaska and territories. D1 represents unintentional drug overdose deaths. The per-cause status fields distinguish reported counts (`reported`), source rows marked missing (`explicit_missing`), and rows absent from the export (`not_in_export`). Both missing states keep the count null. The export cannot distinguish suppressed counts from zeros when a row is absent.

Join the mortality and shipment tables on `county_fips` and `year`, checking that each key is unique on both sides. Join annual county population before computing rates. See the [mortality cleaning notes](docs/mortality_cleaning.md) for the outcome definitions and limitations.

## Tests

```bash
python -m unittest discover -s tests -v
```
