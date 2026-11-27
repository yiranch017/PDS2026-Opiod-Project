# Opioid Policy Project

We examine changes in opioid shipments and overdose mortality following prescribing regulations in Florida (2010) and Washington (2012), using county-year data from 2006–2015.

## Data

- DEA ARCOS shipment records
- County-level Vital Statistics mortality records
- County population estimates

## Merged data

Start with [outputs/county_panel/county_year.parquet](outputs/county_panel/county_year.parquet), or its [CSV copy](outputs/county_panel/county_year.csv). It has one row per county-year: 3,113 counties × 2006–2015 = 31,130 rows, including possible control states. Join keys are `county_fips` (five-character text) and `year`. When reading CSV, specify `dtype={"county_fips": "string", "state_fips": "string"}` to preserve leading zeros.

Main fields:

| Field | Meaning |
| --- | --- |
| `population` | Annual July 1 Census population |
| `hydrocodone_oxycodone_g_per_person` | Main shipment measure, grams per resident |
| `deaths_unintentional_per_100k` | Reported unintentional drug-overdose deaths per 100,000; not opioid-specific |
| `deaths_unintentional_status` | Reported, absent from export, explicitly missing, or boundary problem |
| `deaths_unintentional_lower_per_100k`, `deaths_unintentional_upper_per_100k` | Separate suppression sensitivity bounds; not imputed outcomes |
| `has_shipment_record` | Whether any retained community-pharmacy/practitioner shipment was found |
| `shipment_single_transaction_flag` | At least one drug total dominated by one shipment |
| `mortality_primary_eligible`, `shipments_primary_eligible`, `joint_primary_eligible` | Availability/geography checks, separately by outcome |
| `joint_complete_2006_2015` | Optional consistent county sample; both primary outcomes available in all ten years |

Missing counts and shipments stay missing. All counties remain in the file; eligibility flags let us choose the analysis sample explicitly. The [cleaning notes](docs/data_cleaning.md#final-checks-and-decisions) explain the exclusions, geography changes, and remaining source limitations. No control states are chosen by the cleaning code.

Other shared files:

- [Population table and audit](outputs/population_clean)
- [Shipment tables and reviews](outputs/shipments_clean_final)
- [Merged-data audit](outputs/county_panel/panel_audit.json)
- [State/year coverage](outputs/county_panel/coverage_by_state_year.csv)
- [Large-shipment decisions](outputs/county_panel/shipment_review_decisions.csv)
- [Drug-specific rates](outputs/county_panel/shipments_drug_rates.parquet)

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

Download and clean annual population first:

```bash
python src/clean_population.py --download --output outputs/population_clean
```

This uses Census annual estimates for 2006–2015, checks county sums against state totals, and handles Shannon/Oglala Lakota and Bedford geography changes. It reads the 2006–2009 estimates from the intercensal file and 2010–2015 from Vintage 2019. Source files stay in `data/raw/`.

Place the provided Vital Statistics ZIP in `data/raw/US_VitalStatistics.zip`, then run:

```bash
python src/clean_shipments.py --shipments outputs/arcos_full/shipments_by_buyer_type.parquet --mortality-zip data/raw/US_VitalStatistics.zip --population outputs/population_clean/population_county_year.parquet --output outputs/shipments_clean_final
```

The main measure includes community pharmacies and practitioners. Other buyer types are retained separately. County names are matched to five-digit FIPS using the Census population reference. [Cleaning decisions](docs/data_cleaning.md) describes the choices and remaining limitations.

To create the merged table using Haoye's mortality cleaning:

```bash
python src/build_county_panel.py --population outputs/population_clean/population_county_year.parquet --shipments-dir outputs/shipments_clean_final --mortality-zip data/raw/US_VitalStatistics.zip --output outputs/county_panel
```

Use new output directories when rerunning these steps; the shared outputs are already provided.

## Outputs

The shipment tables and review files are in [outputs/shipments_clean_final](outputs/shipments_clean_final). [shipments_county_year.parquet](outputs/shipments_clean_final/shipments_county_year.parquet) now uses the full Census county-year universe: 31,130 rows, keyed by `county_fips` and `year`.

| File | Contents |
| --- | --- |
| `shipments_by_buyer_type.parquet` | Shipment totals by source location, year, drug, and buyer type |
| `audit.json` | Processing status, exclusions, and validation results |
| `drug_labels.csv` | Drug codes and names in the source data |
| `shipments_county_year.parquet` | Main shipment measure keyed by `county_fips` and `year` |
| `shipments_county_year_drug.parquet` | Main shipment measure with separate drug codes |
| `shipments_by_buyer_type_fips.parquet` | All retained buyer channels, with geographic matching results |
| `cleaning_audit.json` | Cleaning exclusions and reconciliation checks |

Cleaning also produces CSV files for unmatched locations, missing county-years, buyer types, drug-label disagreements, and large shipments requiring review. Use a cleaned output only when `cleaning_audit.json` reports `status: complete` and `validated: true`. The shared cleaned outputs are tracked; raw data and intermediate runs stay outside version control.

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
