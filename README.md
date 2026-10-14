# NYC ride-hailing trip pay

Jonathan Yan | Independent Data Analysis  
September – October 2026

## Question

How does recorded driver pay per passenger-occupied hour vary by platform
and date? How sensitive are the results to cleaning rules and aggregation
choices?

This project extends my paper, "Algorithmic Management and Wage Volatility
in Platform Work," by examining what public trip records can measure.
It describes one month; it does not estimate the effects of a policy change.

## Run

Save `analyze.py` beside this README. Requires Python 3.11 or later,
approximately 1 GB of free disk space, and a few GB of memory.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python analyze.py
```

These package versions were tested with Python 3.12. On Windows, activate
with `.venv\Scripts\activate`.

The script downloads the approximately 487 MB Parquet file and zone lookup,
then processes 200,000 rows at a time. Existing downloads are reused.

For less memory:

```bash
python analyze.py --batch-size 50000
```

If a download is corrupt, remove that cached file and rerun.

An existing local copy can be used with:

```bash
python analyze.py --input data/raw/fhvhv_tripdata_2025-07.parquet --zones data/raw/taxi_zone_lookup.csv
```

## Files

- `analyze.py`: download, cleaning, aggregation, figures, and execution record.
- `data/raw/`: downloaded source files; exclude this folder from Git.
- `outputs/summary.csv`: platform totals and sensitivity results.
- `outputs/daily.csv`: platform-by-date summaries.
- `outputs/zone_hour.csv`: platform-by-pickup-zone-by-hour summaries.
- `outputs/cleaning_audit.csv`: sequential exclusions and retained counts.
- `outputs/daily_pay.png`: daily occupied-hour pay.
- `outputs/weighting_comparison.png`: comparison of aggregation methods.
- `outputs/findings.md`: numerical summary.
- `outputs/manifest.json`: execution time, input/script hashes, and versions.

## Sources

- [TLC trip records](https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page)
- [July 2025 HVFHV Parquet](https://d37ci6vzurychx.cloudfront.net/trip-data/fhvhv_tripdata_2025-07.parquet)
- [Taxi zone lookup](https://d37ci6vzurychx.cloudfront.net/misc/taxi_zone_lookup.csv)
- [HVFHV data dictionary](https://www.nyc.gov/assets/tlc/downloads/pdf/data_dictionary_trip_records_hvfhs.pdf)

Each row represents a reported trip. `trip_time` is passenger-trip seconds;
`driver_pay` excludes tips and tolls and is net of commission, surcharges,
and taxes. It does not subtract vehicle operating expenses.

HV0003 identifies Uber; HV0005 identifies Lyft. Dates and hours use the
recorded local timestamps without UTC conversion.

## Cleaning

Apply these rules in order and report each rejection count:

1. Keep Uber/Lyft records with a pickup in July 2025.
2. Require finite, positive driver pay, miles, and duration.
3. Require positive pickup-to-drop-off elapsed time and agreement with
   reported duration within the larger of 60 seconds and 1%.
   This tolerance is an analysis choice, not a TLC requirement.
4. Require a known NYC origin and named destination; exclude unknown
   zones 264/265. Named destinations such as EWR remain eligible.
5. For the primary analysis, require `shared_match_flag == 'N'`.
   Pooled records can overlap in occupied time; missing flags are excluded.

No passenger-fare filter, guessed deduplication, or pay winsorization is
applied. The available columns do not establish a unique trip key.
Nonpositive payments could represent adjustments, and geographic
exclusions restrict coverage rather than prove records are wrong.

## Measures

- **Pay per occupied hour:** `3600 * sum(driver_pay) / sum(trip_time)`.
- **Mean trip hourly equivalent:** `mean(3600 * driver_pay / trip_time)`.
- **Pay per mile:** `sum(driver_pay) / sum(trip_miles)`.

The first hourly measure weights trips by duration; the second weights
every trip equally. Neither measures a typical driver's hourly wage.
Zone/hour cells are descriptive aggregates, not matched routes or
causal controls.

## Results

Processed **19,653,012** records; retained **18,346,549** primary trips.

| Platform | Retained trips | Pay / occupied hour | Mean trip hourly equivalent | Pay / mile |
|---|---:|---:|---:|---:|
| Lyft | 5,261,503 | $58.80 | $58.44 | $4.21 |
| Uber | 13,085,046 | $62.29 | $62.31 | $4.28 |

Sequential exclusions:

- 8,501 nonpositive/nonfinite numeric records.
- 718 timestamp-duration disagreements.
- 928,254 records failing the geography rule.
- Then 368,990 pooled/unknown-status records.

Platform and pickup-month exclusions were zero.

## Sensitivity checks

Compare the primary sample with:

- Nonpooled trips lasting 60–10,800 seconds and averaging at most 100 mph.
- The same screen with a five-minute minimum duration.
- All pooling statuses, interpreted only as a record-level comparison.

The first screen changes each platform's aggregate hourly rate by less
than $0.01. The five-minute screen gives $58.69 for Lyft and $62.11 for
Uber. Including all pooling statuses gives $58.80 and $61.38 respectively;
overlapping trip intervals prevent interpreting that denominator as
fleet occupied hours. Full results are in `summary.csv`.

## Limits

Waiting, pickup travel, expenses, online availability, and driver
identifiers are absent. The analysis cannot measure take-home wages,
driver supply, individual earnings volatility, lockouts, or legal
compliance. Platform differences may reflect trip, time, geography,
and vehicle composition. One month's completed trips do not identify
a policy effect.

The file is a full month of submitted records, not a random sample of
drivers or proof of complete coverage. No significance tests are reported.
Measurement error and selection remain despite the large row count.
The duration/speed checks do not address missing or excluded payments.

## Reproduction checks

The script reconciles input rows with exclusions and checks that primary
counts agree across tables. Hashes identify the exact sources and code
used in each execution. Running it again records the new execution date;
it does not overwrite history with a fixed date.
