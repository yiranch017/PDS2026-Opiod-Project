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

Shipments are grouped by buyer state, county name, year, and drug code. Processing keeps sale transactions (`S`) from 2006–2015 outside Alaska. Missing, nonfinite, and negative weights are excluded and recorded in the audit. Drug codes are retained for selecting the opioid categories used in the analysis.

## Outputs

| File | Contents |
| --- | --- |
| `shipments_county_name_year_drug.parquet` | Total active drug weight in grams and shipment record counts by county, year, and drug |
| `audit.json` | Processing status, exclusions, and validation results |
| `drug_labels.csv` | Drug codes and names in the source data |

County names still need to be matched to FIPS codes before merging population and mortality. Raw data and generated outputs are kept outside version control.

## Tests

```bash
python -m unittest discover -s tests -v
```
