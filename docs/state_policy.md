# State regulation changes

The analysis-period indicator covers five tracked policy categories: PDMP access, mandatory PDMP use, pain clinic regulation, naloxone access, and overdose Good Samaritan laws. The source is the State Policy & Politics Database (SPPD), version 1.3.1, distributed with the [SPPD replication files](https://github.com/SPPDindex/SPPD-State-Policy-Index). See the [SPPD codebook](https://dornsife.usc.edu/cesr/wp-content/uploads/sites/54/2025/03/State-Policy-Politics-Database-SPPD2.pdf) and its original source, [Lee et al. (2021)](https://jamanetwork.com/journals/jamanetworkopen/fullarticle/2776301). The Stata input remains in `data/raw/`.

The source annual indicators describe laws in effect during the year. The script uses 2005 as a baseline and codes a tracked adoption when a category changes from 0 in the preceding year to 1 in the current year. This method measures transitions at annual resolution. Amendments within a category that stays active and policy categories outside the five selected fields are outside its scope.

- `state_regulation_change`: whether at least one tracked additional policy was adopted in the given year.
- `state_regulation_change_during_study`: whether at least one such adoption occurred in 2006–2015. Use this retrospective field to screen control candidates.
- `*_active` and `*_adopted`: separate source status and derived adoption indicators by category.

Florida's 2010 pain-clinic adoption is recorded separately in `focal_pain_clinic_adoption` as part of the focal intervention. Other Florida adoptions still count. The 2012 prescribing intervention is in Washington state (WA, FIPS `53`). That focal rule is outside these five source fields. Washington, DC (FIPS `11`) is a separate jurisdiction. The focal treatment and post-policy variables can be defined separately when constructing the DiD samples.

False means that no adoption was observed across all tracked categories with known coverage. Missing evidence remains null unless another tracked category establishes an adoption. SPPD contains the 50 states. The output also includes DC with null policy indicators. Alaska remains in the state policy table because the source covers it and is outside the county analysis panel.

From the repository root, download `SPPD_V1_3.1.dta` from the replication files into `data/raw/` and run:

```bash
python src/merge_state_policies.py --sppd data/raw/SPPD_V1_3.1.dta --county-panel outputs/county_panel/county_year.parquet --output outputs/state_policy
```

Use a new output directory for each run. The script writes the state-year table, a separate county panel joined on state FIPS and year, and a source and coverage audit.
