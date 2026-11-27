# Data cleaning notes

- Keep 2006–2015 so shipments and mortality cover the same years. Keep possible control states for now; we can choose the comparison groups after looking at pre-policy trends.

- Keep sales (`S`). Returns and other transaction types aren't included, so we're measuring incoming sales, not sales minus returns.

- Main buyer group: retail pharmacies, chain pharmacies, and community practitioners, including the relevant nurse practitioner, physician assistant, and waiver categories. We think these are closer to the supply available to patients. Keep practitioners because otherwise we could miss changes in physician dispensing.

- Don't mix in distributors, manufacturers, labs, or reverse distributors. Drugs can pass through these businesses before reaching patients, so counting every sale could count the same supply more than once. Keep hospital, treatment-program, mail-order, and federal/military channels separately too. They're still in the buyer-type table if we want to compare later.

- Keep the 49 states other than Alaska, plus DC. Drop territories and other jurisdictions. Alaska is excluded as specified in the project instructions.

- Use five-digit county FIPS + year for merging. Keep FIPS as text so we don't lose leading zeros. The final lookup uses the annual Census population universe, because even the all-cause mortality files miss some small counties.

- Names need some cleaning first: Saint/St., De Soto/DeSoto, punctuation, spacing, accents, etc. Match within the same state and year. Keep cities separate from counties! Only accept a shortened Virginia city name when it has one clear match and doesn't conflict with a county. Leave the rest for review rather than guessing.

- Don't turn absent county-years into zero shipments. We don't know whether that means no shipments or missing/excluded records. Keep a coverage flag. The Census reference gives us the county-year universe independently of whether deaths or shipments were reported.

- Group by drug code. A few drug names disagree with their codes, so use the most common name for display and keep the disagreements in a review file. This doesn't tell us which original field was wrong.

- Keep all 14 drug codes separately, plus a combined hydrocodone/oxycodone measure. That gives us options without rereading the ZIP. The weight is in grams, not pills or prescriptions. Also, equal weights of different opioids don't mean equal potency.

- Exclude missing, infinite, and negative weights, but keep valid zeros. Record what gets excluded. Don't automatically remove identical-looking transactions either; they could be separate purchases.

- Flag unusually large totals, don't automatically delete them. Current checks: highest 1% within each drug-year, or one shipment making up at least half the annual weight across ten or more transactions. These are screening rules, because we think size alone isn't enough evidence of an error.

- Pharmacies can serve people outside their county, and buyer labels can be wrong. Retain the flagged values with sensitivity flags, as explained below. The earlier Washington Post misidentified-pharmacy list hasn't been applied: its download wasn't available, and its documented coverage doesn't span our full study period.

- For the merge: `county_fips` as five-character text, `year` as an integer, one row per county-year. Haoye's mortality cleaning is now connected to the shipment and population tables. Unreported deaths stay missing.

## Final checks and decisions

- **County matching:** switched to the Census lookup and recovered 1,640 eligible shipment records. The 67 unmatched locations are down to five county-years: Anderson, CT, and Burlington, MT. Those aren't counties in the stated states. Exclude their nine records (about 0.44 grams) because we can't safely infer the right location from these aggregates. They're still in `unmatched_counties.csv` and the audit.

- **County coverage:** keep all 31,130 county-years (3,113 counties × 10 years). The earlier 30,796-row table used the mortality export as its reference; this version also includes counties absent from that export. We have main-group shipments in 30,036 county-years. The other 1,094 stay missing, including Glades County, FL, in 2006–2008. That's an absence of retained shipments, not proof of zero supply.

- **Population:** use July 1 Census estimates for each year: the 2000–2010 intercensal series for 2006–2009 and Vintage 2019 for 2010–2015. The NHGIS file in Downloads has two five-year periods, so we're not relabeling those as annual observations. Every county population is positive, keys are unique, and county totals reconcile to state totals. Source URLs and file hashes are in `population_audit.json`.

- **Geography changes:** use Oglala Lakota's code (`46102`) for former Shannon County (`46113`). Combine Bedford city with Bedford County (`51019`) in the population and shipment tables. The later population estimates already include the city, so don't add it twice! Bedford, York County, and Newport News have boundary changes affecting comparisons. Keep their rows, but flag them out of the default eligibility measures and withhold their mortality rates. We think that's safer than treating incompatible areas as a change in mortality. One 2015 Clifton Forge city row is an obsolete missing placeholder; save it separately rather than attaching it to a real county.

- **Large shipments:** retain the 3,645 flagged county-year-drug totals. Big annual totals alone don't establish an error: the largest one is Broward's 2010 oxycodone total, spread across 101,912 transactions, with the largest transaction about 0.1% of the annual weight. Single-transaction cases are a different concern; keep those flags so analysis can be rerun excluding affected county-years. `shipment_review_decisions.csv` records the retention rule, transaction share, counts, and grams per person. This is a screening decision, not verification of every transaction.

- **Drug labels:** standardize names by drug code and retain weights under the original code. The full raw source has 120 records whose labels disagree with the modal label for their code; the label review counts include years/transactions outside the final sample too. We're not guessing a different drug code from a conflicting name. Keep the original disagreement report. Combined hydrocodone/oxycodone grams are available; they are not pills, prescriptions, or potency-adjusted doses.

- **Mortality:** keep Haoye's D1 unintentional drug-overdose outcome, with suicide, homicide, and undetermined categories separate. These are not opioid-specific deaths. The merged table has 6,291 reported, geographically comparable D1 county-years. An absent count stays null. The 0–9 sensitivity bounds apply only to absent causes in county-years present in the source export. The 344 county-years with no source rows, explicit `Missing` values, and boundary problems retain null bounds.

- **Analysis sample:** keep separate shipment, mortality, and joint eligibility flags. Primary shipment eligibility uses total retained opioid base weight per resident on comparable geography. Hydrocodone and oxycodone together are a secondary measure. There are 341 counties with both primary measures available for all ten years. The state-year audit reports primary shipment eligibility, county-years without mortality source rows, and the population share with reported D1 outcomes.

- **Buyer classification limit:** the Washington Post misidentified-pharmacy endpoint still isn't available. We haven't claimed to correct individual buyer identities from county aggregates. The buyer-type exclusions and single-transaction flags remain important limitations.

- **Final files:** start with `outputs/county_panel/county_year.parquet`. The updated shipment tables, annual population, source mortality copy, reviews, and audits are shared too. Use `panel_audit.json` to check completion and reconciliation. See [mortality_cleaning.md](mortality_cleaning.md) for Haoye's source cleaning.

## References

- [ARCOS definitions](https://arcos.nd.edu/definitions)
- [Washington Post data notes](https://github.com/wpinvestigative/arcos#about-this-data)
- [Misidentified pharmacy list](https://wpinvestigative.github.io/arcos/reference/not_pharmacies.html)
- [Annual population, 2000–2010](https://www2.census.gov/programs-surveys/popest/datasets/2000-2010/intercensal/county/)
- [Annual population, Vintage 2019](https://www2.census.gov/programs-surveys/popest/datasets/2010-2019/counties/totals/)
- [Census county changes, 2010s](https://www.census.gov/programs-surveys/geography/technical-documentation/county-changes.2010.html)
- [Census county changes, 2000s](https://www.census.gov/programs-surveys/geography/technical-documentation/county-changes/2000.html)
