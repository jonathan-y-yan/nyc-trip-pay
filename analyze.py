#!/usr/bin/env python3
"""TLC trip pay: cleaning, descriptive summaries, and sensitivity checks."""

from pathlib import Path
from datetime import datetime, timezone
import argparse
import hashlib
import json
import platform
import shutil
import urllib.request

import numpy as np
import pandas as pd
import pyarrow
import pyarrow.parquet as pq
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent
BASE = "https://d37ci6vzurychx.cloudfront.net"
PLATFORMS = {"HV0003": "Uber", "HV0005": "Lyft"}
COLS = [
    "hvfhs_license_num",
    "pickup_datetime",
    "dropoff_datetime",
    "PULocationID",
    "DOLocationID",
    "trip_miles",
    "trip_time",
    "driver_pay",
    "shared_match_flag",
]
SUMS = ["n", "pay", "seconds", "miles", "sum_trip_rate"]
BOROUGHS = ["Bronx", "Brooklyn", "Manhattan", "Queens", "Staten Island"]


def download(url, path):
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".part")
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "TLC-research/1.0"}
        )
        with urllib.request.urlopen(req, timeout=120) as response:
            with temp.open("wb") as f:
                shutil.copyfileobj(response, f)
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def clean(batch, month, zones):
    d = batch.copy()
    audit = {"input_rows": len(d)}

    def keep(mask, reason):
        nonlocal d
        mask = mask.fillna(False)
        audit[reason] = int((~mask).sum())
        d = d.loc[mask].copy()

    keep(
        d.hvfhs_license_num.isin(PLATFORMS),
        "other_or_missing_platform",
    )
    for c in ["pickup_datetime", "dropoff_datetime"]:
        d[c] = pd.to_datetime(d[c], errors="coerce")

    start = pd.Timestamp(month + "-01")
    stop = start + pd.offsets.MonthBegin(1)
    keep(
        d.pickup_datetime.ge(start) & d.pickup_datetime.lt(stop),
        "pickup_outside_month_or_missing",
    )

    for c in [
        "PULocationID", "DOLocationID",
        "trip_miles", "trip_time", "driver_pay",
    ]:
        d[c] = pd.to_numeric(d[c], errors="coerce")

    finite = np.isfinite(
        d[["trip_miles", "trip_time", "driver_pay"]]
    ).all(axis=1)
    keep(
        finite & d.trip_miles.gt(0)
        & d.trip_time.gt(0) & d.driver_pay.gt(0),
        "nonpositive_or_nonfinite_numeric",
    )

    elapsed = (
        d.dropoff_datetime - d.pickup_datetime
    ).dt.total_seconds()

    # 60s or 1% tolerance for timestamp agreement
    tolerance = np.maximum(60, 0.01 * d.trip_time)
    keep(
        elapsed.gt(0) & elapsed.sub(d.trip_time).abs().le(tolerance),
        "timestamp_duration_disagreement",
    )

    # Keep named destinations, including EWR; exclude unknown zones 264/265.
    nyc = set(
        zones.loc[zones.Borough.isin(BOROUGHS), "LocationID"]
    )
    known = set(
        zones.loc[
            zones.Zone.notna() & ~zones.LocationID.isin([264, 265]),
            "LocationID",
        ]
    )
    keep(
        d.PULocationID.isin(nyc) & d.DOLocationID.isin(known),
        "unknown_zone_or_nonnyc_origin",
    )

    audit["valid_rows_before_pool_filter"] = len(d)
    d["platform"] = d.hvfhs_license_num.map(PLATFORMS)
    d["date"] = d.pickup_datetime.dt.strftime("%Y-%m-%d")
    d["hour"] = d.pickup_datetime.dt.hour
    d["borough"] = d.PULocationID.map(
        zones.set_index("LocationID").Borough
    )
    d["n"] = 1
    d["pay"] = d.driver_pay
    d["seconds"] = d.trip_time
    d["miles"] = d.trip_miles
    d["sum_trip_rate"] = 3600 * d.driver_pay / d.trip_time

    # Pooled trip records can overlap in occupied time.
    d["nonpooled"] = d.shared_match_flag.eq("N")
    d["plausible"] = (
        d.trip_time.between(60, 10800)
        & (3600 * d.trip_miles / d.trip_time).le(100)
    )
    audit["pool_or_unknown_excluded_from_primary"] = int(
        (~d.nonpooled).sum()
    )
    audit["primary_rows"] = int(d.nonpooled.sum())
    return d, audit


def aggregate(d, keys):
    return (
        d.groupby(keys, observed=True, dropna=False)[SUMS]
        .sum()
        .reset_index()
    )


def finish(parts, keys):
    out = (
        pd.concat(parts, ignore_index=True)
        .groupby(keys, observed=True, dropna=False)[SUMS]
        .sum()
        .reset_index()
    )
    out["pay_per_occupied_hour"] = 3600 * out.pay / out.seconds
    out["mean_trip_hourly_equivalent"] = out.sum_trip_rate / out.n
    out["pay_per_mile"] = out.pay / out.miles
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--month", default="2025-07", help="YYYY-MM")
    parser.add_argument("--input", type=Path, help="Local trip Parquet")
    parser.add_argument("--zones", type=Path, help="Local TLC zone CSV")
    parser.add_argument("--output", type=Path, default=ROOT / "outputs")
    parser.add_argument("--batch-size", type=int, default=200000)
    args = parser.parse_args()

    if pd.Period(args.month, freq="M").strftime("%Y-%m") != args.month:
        parser.error("Month must be YYYY-MM")

    raw = ROOT / "data" / "raw"
    source_url = (
        f"{BASE}/trip-data/fhvhv_tripdata_{args.month}.parquet"
    )
    source = args.input or raw / f"fhvhv_tripdata_{args.month}.parquet"
    zones_path = args.zones or raw / "taxi_zone_lookup.csv"

    if not args.input:
        download(source_url, source)
    if not args.zones:
        download(f"{BASE}/misc/taxi_zone_lookup.csv", zones_path)

    zones = pd.read_csv(zones_path)
    assert zones.LocationID.is_unique, "Zone IDs must be unique"

    parquet = pq.ParquetFile(source)
    missing = set(COLS) - set(parquet.schema_arrow.names)
    if missing:
        raise ValueError(f"Missing required columns: {sorted(missing)}")

    tables = {"summary": [], "daily": [], "zone_hour": []}
    audit_total = {}

    # No deduplication: these columns do not provide a unique trip identifier.
    for batch in parquet.iter_batches(
        batch_size=args.batch_size, columns=COLS
    ):
        d, audit = clean(batch.to_pandas(), args.month, zones)
        for key, value in audit.items():
            audit_total[key] = audit_total.get(key, 0) + value

        variants = {
            "primary_nonpooled": d.nonpooled,
            "sensitivity_plausible": d.nonpooled & d.plausible,
            "sensitivity_min5min": (
                d.nonpooled & d.plausible & d.trip_time.ge(300)
            ),
            "sensitivity_all_pool_status": pd.Series(True, index=d.index),
        }
        for name, mask in variants.items():
            sub = d.loc[mask]
            if len(sub):
                grouped = aggregate(sub, ["platform"])
                grouped["variant"] = name
                tables["summary"].append(grouped)

        primary = d.loc[d.nonpooled]
        if len(primary):
            tables["daily"].append(
                aggregate(primary, ["platform", "date"])
            )
            tables["zone_hour"].append(
                aggregate(
                    primary,
                    ["platform", "borough", "PULocationID", "hour"],
                )
            )

    if not tables["daily"]:
        raise ValueError(
            "No eligible rows. Inspect schema, date window, and filters."
        )

    outputs = args.output
    outputs.mkdir(parents=True, exist_ok=True)
    summary = finish(tables["summary"], ["variant", "platform"])
    daily = finish(tables["daily"], ["platform", "date"])
    zone_hour = finish(
        tables["zone_hour"],
        ["platform", "borough", "PULocationID", "hour"],
    )

    for name, frame in [
        ("summary", summary),
        ("daily", daily),
        ("zone_hour", zone_hour),
    ]:
        frame.to_csv(outputs / f"{name}.csv", index=False)

    pd.DataFrame(
        list(audit_total.items()), columns=["stage", "rows"]
    ).to_csv(outputs / "cleaning_audit.csv", index=False)

    assert audit_total["input_rows"] == parquet.metadata.num_rows
    excluded = sum(
        audit_total[k]
        for k in [
            "other_or_missing_platform",
            "pickup_outside_month_or_missing",
            "nonpositive_or_nonfinite_numeric",
            "timestamp_duration_disagreement",
            "unknown_zone_or_nonnyc_origin",
        ]
    )
    assert (
        excluded + audit_total["valid_rows_before_pool_filter"]
        == audit_total["input_rows"]
    )

    primary = summary.loc[summary.variant.eq("primary_nonpooled")]
    assert (
        int(primary.n.sum())
        == audit_total["primary_rows"]
        == int(daily.n.sum())
        == int(zone_hour.n.sum())
    )

    plt.rcParams.update({
        "font.size": 10,
        "axes.spines.top": False,
        "axes.spines.right": False,
    })
    fig, ax = plt.subplots(figsize=(9, 4.5))
    for name, group in daily.groupby("platform"):
        group = group.sort_values("date")
        ax.plot(
            pd.to_datetime(group.date),
            group.pay_per_occupied_hour,
            label=name,
            linewidth=2,
        )
    ax.set(
        title=f"Recorded pay per passenger-occupied hour | {args.month}",
        ylabel="USD / passenger-occupied hour",
        xlabel="Pickup date",
    )
    ax.legend()
    ax.grid(alpha=.2)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(outputs / "daily_pay.png", dpi=180)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(8, 4.5))
    ix = np.arange(len(primary))
    ax.bar(
        ix - .18, primary.pay_per_occupied_hour, .36,
        label="Ratio of totals (time weighted)",
    )
    ax.bar(
        ix + .18, primary.mean_trip_hourly_equivalent, .36,
        label="Mean of trip rates (trip weighted)",
    )
    ax.set(
        xticks=ix,
        xticklabels=primary.platform,
        ylabel="USD / passenger-occupied hour",
        title="Different weighting answers different questions",
    )
    ymax = max(
        primary.pay_per_occupied_hour.max(),
        primary.mean_trip_hourly_equivalent.max(),
    )
    ax.set_ylim(0, float(ymax) * 1.25)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(outputs / "weighting_comparison.png", dpi=180)
    plt.close(fig)

    lines = [
        f"# Observed results: {args.month}",
        "",
        f'Processed {audit_total["input_rows"]:,} source rows; '
        f'retained {audit_total["primary_rows"]:,} primary nonpooled trips.',
        "",
        "| Platform | Trips | Pay / occupied hour | "
        "Mean trip hourly equivalent | Pay / mile |",
        "|---|---:|---:|---:|---:|",
    ]
    for r in primary.itertuples():
        lines.append(
            f"| {r.platform} | {r.n:,.0f} | "
            f"${r.pay_per_occupied_hour:.2f} | "
            f"${r.mean_trip_hourly_equivalent:.2f} | "
            f"${r.pay_per_mile:.2f} |"
        )

    lines += [
        "",
        "Sensitivity to the pre-specified duration/speed screen:",
    ]
    for r in primary.itertuples():
        s = summary.loc[
            summary.variant.eq("sensitivity_plausible")
            & summary.platform.eq(r.platform)
        ].iloc[0]
        delta = s.pay_per_occupied_hour - r.pay_per_occupied_hour
        lines.append(
            f"- {r.platform}: ${s.pay_per_occupied_hour:.2f}/occupied hour; "
            f"change {delta:+.2f}; {int(s.n):,} retained trips."
        )

    lines += [
        "",
        "Interpretation: these are descriptive totals for recorded trips. "
        "They are not take-home wages, driver-level averages, causal "
        "platform effects, or evidence of legal violations. No online "
        "waiting time, pickup travel, operating costs, unique drivers, "
        "or driver supply is measured. Pooled records are excluded from "
        "the primary result to avoid overlapping occupied intervals; "
        "the all-status sensitivity is only a record-level comparison.",
    ]
    (outputs / "findings.md").write_text("\n".join(lines) + "\n")

    manifest = {
        "run_utc": datetime.now(timezone.utc).isoformat(),
        "month": args.month,
        "source_url": (
            source_url if not args.input else "local input (see input_filename)"
        ),
        "input_filename": source.name,
        "input_bytes": source.stat().st_size,
        "source_sha256": sha256(source),
        "zones_sha256": sha256(zones_path),
        "script_sha256": sha256(Path(__file__)),
        "rows": audit_total,
        "versions": {
            "python": platform.python_version(),
            "pandas": pd.__version__,
            "numpy": np.__version__,
            "matplotlib": matplotlib.__version__,
            "pyarrow": pyarrow.__version__,
        },
    }
    (outputs / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n"
    )
    print("\n".join(lines))


if __name__ == "__main__":
    main()

