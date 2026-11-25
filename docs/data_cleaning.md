# Data cleaning notes

- Keep 2006–2015 so shipments and mortality cover the same years. Keep possible control states for now; we can choose the comparison groups after looking at pre-policy trends.

- Keep sales (`S`). Returns and other transaction types aren't included, so we're measuring incoming sales, not sales minus returns.

- Main buyer group: retail pharmacies, chain pharmacies, and community practitioners, including the relevant nurse practitioner, physician assistant, and waiver categories. We think these are closer to the supply available to patients. Keep practitioners because otherwise we could miss changes in physician dispensing.

- Don't mix in distributors, manufacturers, labs, or reverse distributors. Drugs can pass through these businesses before reaching patients, so counting every sale could count the same supply more than once. Keep hospital, treatment-program, mail-order, and federal/military channels separately too. They're still in the buyer-type table if we want to compare later.

- Keep the 49 states other than Alaska, plus DC. Drop territories and other jurisdictions. Alaska is excluded as specified in the project instructions.

- Use five-digit county FIPS + year for merging. Keep FIPS as text so we don't lose leading zeros. The lookup uses all cause categories in the mortality files, because only using overdose rows would miss counties with unreported overdose deaths.

- Names need some cleaning first: Saint/St., De Soto/DeSoto, punctuation, spacing, accents, etc. Match within the same state and year. Keep cities separate from counties! Only accept a shortened Virginia city name when it has one clear match and doesn't conflict with a county. Leave the rest for review rather than guessing.

- Don't turn absent county-years into zero shipments. We don't know whether that means no shipments or missing/excluded records. Keep a coverage flag. The mortality reference can also miss small county-years, so we'll need the population data to check coverage and geographic changes.

- Group by drug code. A few drug names disagree with their codes, so use the most common name for display and keep the disagreements in a review file. This doesn't tell us which original field was wrong.

- Keep all 14 drug codes separately, plus a combined hydrocodone/oxycodone measure. That gives us options without rereading the ZIP. The weight is in grams, not pills or prescriptions. Also, equal weights of different opioids don't mean equal potency.

- Exclude missing, infinite, and negative weights, but keep valid zeros. Record what gets excluded. Don't automatically remove identical-looking transactions either; they could be separate purchases.

- Flag unusually large totals, don't automatically delete them. Current checks: highest 1% within each drug-year, or one shipment making up at least half the annual weight across ten or more transactions. These are screening rules, because we think size alone isn't enough evidence of an error.

- Still need to investigate those flags. Pharmacies can serve people outside their county, and buyer labels can be wrong. The earlier Washington Post misidentified-pharmacy list hasn't been applied: its download wasn't available, and its documented coverage doesn't span our full study period.

- For the merge: `county_fips` as five-character text, `year` as an integer, one row per county-year. Mortality needs its own cleaning first, including keeping unreported deaths distinguishable from zero. Shipment cleaning doesn't change the mortality outcomes.

## Still to check

- **County matching:** 67 county-year locations didn't match the reference. None are in Florida or Washington. This leaves 1,649 otherwise eligible pharmacy/practitioner shipment records out of the main table. Check `unmatched_counties.csv` against the population data: some small counties may just be missing from the mortality reference, while names such as Anderson, CT, need closer inspection.

- **Missing county-years:** 803 county-years in the reference have no retained main-group shipments. This includes Glades County, FL, in 2006–2008. Keep these missing for now; we still need to figure out whether they reflect no shipments, excluded records, or coverage gaps. See `county_years_without_shipments.csv`.

- **Large values:** 3,645 county-year-drug totals are flagged in `large_shipments_review.csv`. These aren't automatically errors. Look at the largest single shipment, its share of the annual total, and the number of transactions before deciding what to do. Population will also help distinguish a high-volume county from an unusually high per-person rate.

- **Drug names:** 120 source-file records have a drug label that differs from the most common label for that code. They're listed in `drug_label_review.csv`. The display labels are standardized, but the disagreements still need review if they affect which drugs we include.

- **Before merging:** the current table has 30,796 county-year rows, with retained shipments in 29,993 of them. Check this coverage against annual population data before fixing the analysis sample. We'll also need to agree on the mortality categories and handling of unreported deaths.

## References

- [ARCOS definitions](https://arcos.nd.edu/definitions)
- [Washington Post data notes](https://github.com/wpinvestigative/arcos#about-this-data)
- [Misidentified pharmacy list](https://wpinvestigative.github.io/arcos/reference/not_pharmacies.html)
