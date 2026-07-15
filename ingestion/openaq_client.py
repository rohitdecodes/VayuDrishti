import os
import time
import requests
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("OPENAQ_API_KEY", "b7482661ea503499a9c692c668911b7aa7aa83a242c1b8d07f1c45d62fa0098b")
BASE = "https://api.openaq.org/v3"
CACHE_DIR = Path("data/raw")

POLLUTANT_PARAM_IDS = {
    "pm25": 2,
    "pm10": 1,
    "no2": 15,
    "so2": 6,
    "co": 4,
    "o3": 3,
}


def _ensure_cache_dir():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _rate_limit_sleep():
    time.sleep(0.2)


def _fetch_all_pages(url: str, params: dict, headers: dict, max_pages: int = 50) -> list[dict]:
    all_results = []
    for page in range(1, max_pages + 1):
        p = {**params, "page": page}
        resp = requests.get(url, params=p, headers=headers, timeout=60)
        if resp.status_code == 404:
            break
        if resp.status_code == 429:
            print(f"  Rate limited, waiting 5s...")
            time.sleep(5)
            resp = requests.get(url, params=p, headers=headers, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])
        if not results:
            break
        all_results.extend(results)
        found = data.get("meta", {}).get("found", 0)
        if isinstance(found, str):
            if found.startswith(">"):
                found = int(found[1:]) + 1
            else:
                found = int(found)
        if len(all_results) >= found or len(results) < params.get("limit", 100):
            break
        _rate_limit_sleep()
    return all_results


KNOWN_DELHI_LOCATION_IDS = [17, 50, 235, 15, 103, 13, 236, 431]


def discover_delhi_locations(mapping_csv: str = "ingestion/station_id_mapping.csv") -> dict[int, dict]:
    print("=== Mapping Delhi stations via OpenAQ location lookup ===")

    headers = {"X-API-Key": API_KEY}
    mapping = pd.read_csv(mapping_csv)

    all_delhi_locs = []

    # Fetch each known Delhi location's details
    for loc_id in KNOWN_DELHI_LOCATION_IDS:
        resp = requests.get(f"{BASE}/locations/{loc_id}", headers=headers, timeout=30)
        _rate_limit_sleep()
        if resp.status_code != 200:
            print(f"  Location {loc_id}: HTTP {resp.status_code}")
            continue
        data = resp.json()
        results = data.get("results", [])
        if results:
            all_delhi_locs.append(results[0])
        else:
            print(f"  Location {loc_id}: no results")

    # Also search for Siri Fort/Sirifort/IGI Airport specifically
    for search_term in ["Siri Fort", "Sirifort", "IGI Airport", "ITO", "DTU"]:
        params = {"country_id": "IN", "limit": 50}
        url = f"{BASE}/locations"
        resp = requests.get(url, params=params, headers=headers, timeout=30)
        _rate_limit_sleep()
        if resp.status_code != 200:
            continue
        data = resp.json()
        for loc in data.get("results", []):
            name = (loc.get("name") or "")
            locl = (loc.get("locality") or "")
            if any(t.lower() in name.lower() or t.lower() in locl.lower() for t in [search_term]):
                if loc["id"] not in [l["id"] for l in all_delhi_locs]:
                    all_delhi_locs.append(loc)
                    print(f"  Extra found: [{loc['id']}] {name}")

    print(f"  Delhi locations found: {len(all_delhi_locs)}")

    matched = {}
    used_location_ids = set()

    for _, row in mapping.iterrows():
        target_lat, target_lon = float(row["latitude"]), float(row["longitude"])
        best_dist = 999.0
        best_loc = None

        for loc in all_delhi_locs:
            coord = loc.get("coordinates")
            if not coord:
                continue
            lat = coord.get("latitude")
            lon = coord.get("longitude")
            if lat is None or lon is None:
                continue
            dist = ((lat - target_lat) ** 2 + (lon - target_lon) ** 2) ** 0.5
            if dist < 0.05 and dist < best_dist:
                best_dist = dist
                best_loc = loc

        if best_loc:
            loc_id = best_loc["id"]
            name = best_loc.get("name", "")

            if loc_id in used_location_ids:
                print(f"  SKIPPED {row['canonical_name']} -> [{loc_id}] {name} (already used by another station)")
                continue

            sensors = best_loc.get("sensors", [])
            sensor_map = {}
            for s in sensors:
                sname = s.get("parameter", {}).get("name")
                sid = s.get("id")
                # Prefer highest sensor ID (old sensors stopped ~2018; new IDs are active)
                if sname not in sensor_map or sid > sensor_map[sname]:
                    sensor_map[sname] = sid

            matched[loc_id] = {
                "location_id": loc_id,
                "name": name,
                "canonical_name": row["canonical_name"],
                "latitude": row["latitude"],
                "longitude": row["longitude"],
                "sensors": sensor_map,
            }
            used_location_ids.add(loc_id)
            print(f"  Mapped {row['canonical_name']} -> [{loc_id}] {name} dist={best_dist:.4f}")
        else:
            print(f"  WARNING: No OpenAQ match for {row['canonical_name']}")

    return matched


def fetch_sensor_measurements(
    sensor_id: int,
    start_date: str,
    end_date: str,
    parameter_name: str,
) -> pd.DataFrame:
    headers = {"X-API-Key": API_KEY}
    url = f"{BASE}/sensors/{sensor_id}/measurements"

    all_results = []
    page = 1
    max_pages = 200

    while page <= max_pages:
        params = {
            "limit": 1000,
            "page": page,
            "datetime_from": start_date,
            "datetime_to": end_date,
        }
        resp = requests.get(url, params=params, headers=headers, timeout=90)
        if resp.status_code == 404:
            break
        if resp.status_code in (429, 500, 502, 503):
            print(f"    Server error {resp.status_code}, waiting 10s...")
            time.sleep(10)
            continue
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])
        if not results:
            break
        all_results.extend(results)
        page += 1
        _rate_limit_sleep()

        if len(results) < 1000:
            break

    if not all_results:
        return pd.DataFrame()

    rows = []
    for r in all_results:
        period = r.get("period", {})
        dt = period.get("datetimeFrom", {})
        ts_local = dt.get("local") or dt.get("utc")
        if ts_local:
            ts_local = pd.to_datetime(ts_local)
            if ts_local.tz is None:
                ts_local = ts_local.tz_localize("UTC")
        rows.append({
            "timestamp": ts_local,
            "value": r.get("value"),
            "units": r.get("parameter", {}).get("units", "?"),
            "parameter": parameter_name,
        })

    df = pd.DataFrame(rows)
    return df


def pull_openaq_historical(
    start_date: str | None = None,
    end_date: str | None = None,
    mapping_csv: str = "ingestion/station_id_mapping.csv",
    output_csv: str | None = None,
) -> pd.DataFrame:
    _ensure_cache_dir()

    if start_date is None:
        end = datetime.now(timezone.utc)
        end_date = (end - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        start_date = (end - timedelta(days=91)).strftime("%Y-%m-%dT%H:%M:%SZ")
    if output_csv is None:
        output_csv = "data/raw/openaq_historical.csv"

    print(f"=== OpenAQ Historical Pull ===")
    print(f"Window: {start_date} to {end_date}")

    matched = discover_delhi_locations(mapping_csv)
    print(f"\nFetching measurements for {len(matched)} stations:")

    all_data = []

    for loc_id, info in matched.items():
        canonical = info["canonical_name"]
        print(f"\n  {canonical} [{loc_id}]")

        station_rows = []

        for param_name, sensor_id in info["sensors"].items():
            if param_name not in POLLUTANT_PARAM_IDS:
                continue
            print(f"    {param_name} (sensor {sensor_id})...", end=" ", flush=True)
            df = fetch_sensor_measurements(sensor_id, start_date, end_date, param_name)
            if df.empty:
                print("0 measurements")
                continue

            print(f"{len(df)} measurements")

            df["canonical_name"] = canonical
            df["location_id"] = loc_id
            df["latitude"] = info["latitude"]
            df["longitude"] = info["longitude"]
            station_rows.append(df)

        if station_rows:
            combined = pd.concat(station_rows, ignore_index=True)
            all_data.append(combined)

    if not all_data:
        print("\nWARNING: No data fetched from OpenAQ")
        return pd.DataFrame()

    full_df = pd.concat(all_data, ignore_index=True)
    print(f"\nTotal raw measurements: {len(full_df)}")
    print(f"Stations with data: {full_df['canonical_name'].nunique()}")
    print(f"Date range: {full_df['timestamp'].min()} to {full_df['timestamp'].max()}")

    full_df.to_csv(output_csv, index=False)
    print(f"Saved to {output_csv}")

    return full_df


def pivot_openaq_to_wide(
    input_csv: str = "data/raw/openaq_historical.csv",
    output_csv: str = "data/raw/openaq_pivoted.csv",
) -> pd.DataFrame:
    print("\n=== Pivoting OpenAQ data to wide format ===")
    df = pd.read_csv(input_csv, parse_dates=["timestamp"])
    print(f"  Raw rows: {len(df)}")

    # Drop rows with NA values
    before = len(df)
    df = df.dropna(subset=["value"])
    print(f"  After NA drop: {len(df)} (dropped {before - len(df)})")

    # Pivot: each parameter becomes a column
    # First deduplicate (station, timestamp, parameter)
    df_grouped = df.groupby(["canonical_name", "location_id", "latitude", "longitude", "timestamp", "parameter"])["value"].mean().reset_index()

    pivoted = df_grouped.pivot_table(
        index=["canonical_name", "location_id", "latitude", "longitude", "timestamp"],
        columns="parameter",
        values="value",
        aggfunc="mean",
    ).reset_index()

    # Rename columns
    rename_map = {p: p for p in POLLUTANT_PARAM_IDS if p in pivoted.columns}
    pivoted = pivoted.rename(columns=rename_map)

    # Ensure key columns exist
    for col in ["pm25", "pm10", "no2", "so2", "co", "o3"]:
        if col not in pivoted.columns:
            pivoted[col] = None

    # Round timestamps to hour
    pivoted["timestamp"] = pivoted["timestamp"].dt.round("h")

    print(f"  Pivoted rows: {len(pivoted)}")
    print(f"  Columns: {list(pivoted.columns)}")
    print(f"  Stations: {pivoted['canonical_name'].nunique()}")

    for col in ["pm25", "pm10", "no2"]:
        if col in pivoted.columns:
            print(f"  {col}: {pivoted[col].notna().sum()} non-null, mean={pivoted[col].mean():.1f}")

    pivoted.to_csv(output_csv, index=False)
    print(f"  Saved to {output_csv}")
    return pivoted


if __name__ == "__main__":
    print("=== OpenAQ Client ===\n")
    end = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    start = (datetime.now(timezone.utc) - timedelta(days=91)).strftime("%Y-%m-%dT%H:%M:%SZ")

    raw = pull_openaq_historical(start_date=start, end_date=end)
    if not raw.empty:
        pivoted = pivot_openaq_to_wide()
