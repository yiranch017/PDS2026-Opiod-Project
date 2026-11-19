import pandas as pd
import zipfile

dfs = []

with zipfile.ZipFile("US_VitalStatistics.zip") as z:
    for year in range(2006, 2016):
        filename = f"Underlying Cause of Death, {year}.txt"

        with z.open(filename) as f:
            df = pd.read_csv(f, sep="\t")
            dfs.append(df)

mortality = pd.concat(dfs, ignore_index=True)
mortality = mortality.dropna(subset=["County"])

#preliminary EDA
print(mortality.shape)
print(mortality.head())
print(mortality.tail())
print(mortality.columns)

#rm data where columns are nan
print(mortality.isnull().sum())

#check data again
print(mortality.isnull().sum())
print(mortality.head())
print(mortality.tail())
print(mortality["Year"].value_counts().sort_index())

#keep only fl and wa
mortality_fl_wa = mortality[mortality["County"].str.endswith(("FL", "WA"))].reset_index(drop=True)

#check data again
'''
print(mortality_fl_wa.isnull().sum())
print(mortality_fl_wa.head())
print(mortality_fl_wa.tail())
'''

#unique counties per year
print(mortality_fl_wa.groupby("Year")["County Code"].nunique())

#deaths per year and causes of death
mortality_fl_wa["Deaths"] = pd.to_numeric(mortality_fl_wa["Deaths"], errors="coerce")
print(mortality_fl_wa.groupby("Year")["Deaths"].sum())
print(mortality_fl_wa["Drug/Alcohol Induced Cause"].value_counts())

#check unique causes of death and corresponding codes
print(mortality_fl_wa[["Drug/Alcohol Induced Cause", "Drug/Alcohol Induced Cause Code"]].drop_duplicates())

#filter for drug-related deaths only
mortality_fl_wa_drug =mortality_fl_wa[mortality_fl_wa["Drug/Alcohol Induced Cause"] == ["Drug poisonings (overdose) Unintentional (X40-X44)", "Drug poisonings (overdose) Suicide (X60-X64)", "Drug poisonings (overdose) Undetermined (Y10-Y14)", "All other drug-induced causes"]]

#check missing values after filtering
print(mortality_fl_wa_drug.isnull().sum())

#check number of observations for each year
print(mortality_fl_wa_drug["Year"].value_counts().sort_index())

#check unique counties represented in each state
print(mortality_fl_wa_drug.groupby("Year")["County Code"].nunique())

#check number of counties with reported drug deaths per year


#separate fl and wa into different dataframes


#calculate descriptive statistics for drug-related deaths in each state


#calculate total reported drug-related deaths per year for fl and wa


#calculate average reported drug-related deaths per county by year


#check trends in drug-related deaths from 2006-2015


#calculate percent change in reported drug deaths from year to year


#check for counties with missing drug-related death observations


#investigate suppressed mortality observations (counts below 10)


#merge county population data using county fips and year
#(complete after census dataset is available)


#calculate drug-related mortality rates per 100,000 residents


#calculate descriptive statistics for mortality rates in fl and wa


#compare annual mortality rates between fl and wa


#identify pre-policy and post-policy periods for each state
#fl policy: 2010
#wa policy: 2012


#calculate average mortality rates before and after each policy


#calculate absolute and percent changes in mortality rates after policies


#examine trends in mortality rates before the policy changes


#compare short-term and longer-term changes after implementation


#identify potential control states with similar pre-policy trends


#check parallel trends between treatment and control states


#calculate difference-in-differences estimates for fl and wa


#estimate uncertainty / standard errors for did estimates


#compare mortality did results with opioid shipment did results
#(complete after merging arcos dataset)


#summarize findings for fl and wa


#evaluate whether mortality changes support the project hypothesis