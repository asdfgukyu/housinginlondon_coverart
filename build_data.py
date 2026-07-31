#!/usr/bin/env python3
"""
Regenerate data/borough_data.csv from the raw London Datastore series.

Source: "Net additions to dwelling stock" (net additional dwellings by borough),
London Datastore dataset `net-additional-dwellings-borough`, derived from
MHCLG Live Table 122. Open Government Licence v2.
  https://data.london.gov.uk/dataset/net-additional-dwellings-borough/

By default it picks the most recent year present in the raw file. To pin a
specific year, pass it as an argument, e.g.:  python build_data.py 2018-19

To refresh with a newer release, download the updated CSV over
data/net_additions_raw.csv and re-run this script.
"""
import csv
import sys

RAW = "data/net_additions_raw.csv"
OUT = "data/borough_data.csv"


def num(s):
    return int(str(s).replace(",", "").replace('"', "").strip())


def main():
    rows = list(csv.DictReader(open(RAW)))
    boroughs = [r for r in rows if r["Code"].startswith("E09")]  # 33 London boroughs
    years = sorted({r["Year"] for r in boroughs})
    year = sys.argv[1] if len(sys.argv) > 1 else years[-1]
    if year not in years:
        sys.exit(f"year {year!r} not in data; available: {years[0]}..{years[-1]}")

    picked = [r for r in boroughs if r["Year"] == year]
    picked.sort(key=lambda r: -num(r["Net_additions"]))

    with open(OUT, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["code", "name", "year", "net_additions"])
        for r in picked:
            w.writerow([r["Code"], r["Area"], r["Year"], num(r["Net_additions"])])

    total = sum(num(r["Net_additions"]) for r in picked)
    print(f"wrote {OUT}: {len(picked)} boroughs, year {year}, London total {total:,}")


if __name__ == "__main__":
    main()
