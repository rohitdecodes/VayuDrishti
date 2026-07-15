import os
import time
import json
import requests
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

WAQI_TOKEN = os.getenv("WAQI_TOKEN")
WAQI_BASE = "https://api.waqi.info"
CACHE_DIR = Path("data/raw")


def _ensure_cache_dir():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def search_station(station_name: str) -> list[dict]:
    url = f"{WAQI_BASE}/search/"
    params = {"token": WAQI_TOKEN, "keyword": station_name}
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != "ok":
        print(f"  WAQI search warning for '{station_name}': {data.get('data', data)}")
        return []
    return data.get("data", [])


def discover_station_ids(mapping_csv: str = "ingestion/station_id_mapping.csv") -> pd.DataFrame:
    mapping = pd.read_csv(mapping_csv, dtype={"waqi_station_id": str, "cpcb_station_id": str}, keep_default_na=False)
    if "waqi_station_name" not in mapping.columns:
        mapping["waqi_station_name"] = ""

    for idx, row in mapping.iterrows():
        existing_id = str(row.get("waqi_station_id", "")).strip()
        if existing_id and existing_id not in ("nan", "") and not existing_id.startswith("0"):
            continue
        results = search_station(row["canonical_name"])
        time.sleep(0.3)

        best_id = None
        best_name = None
        best_dist = 999.0
        for r in results:
            uid = str(r.get("uid", ""))
            name = r.get("station", {}).get("name", "")
            lat = r.get("station", {}).get("geo", [None, None])[0]
            lon = r.get("station", {}).get("geo", [None, None])[1]
            if lat and lon:
                dist = ((lat - row["latitude"]) ** 2 + (lon - row["longitude"]) ** 2) ** 0.5
                if dist < 0.05 and dist < best_dist:
                    best_id = uid
                    best_name = name
                    best_dist = dist

        if best_id is not None:
            mapping.at[idx, "waqi_station_id"] = best_id
            mapping.at[idx, "waqi_station_name"] = best_name
            print(f"  Mapped {row['canonical_name']} -> WAQI ID {best_id} ({best_name}) dist={best_dist:.4f}")
        else:
            print(f"  WARNING: No WAQI match within 0.05 deg for {row['canonical_name']}")

    mapping.to_csv(mapping_csv, index=False)
    return mapping


def pull_station_feed(station_id: str) -> dict | None:
    url = f"{WAQI_BASE}/feed/@{station_id}/"
    params = {"token": WAQI_TOKEN}
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != "ok":
        print(f"  WAQI feed warning for {station_id}: {data.get('data', data)}")
        return None
    return data.get("data")


def pull_historical(
    mapping_csv: str = "ingestion/station_id_mapping.csv",
    start_date: str | None = None,
    end_date: str | None = None,
    output_csv: str | None = None,
) -> pd.DataFrame:
    _ensure_cache_dir()
    mapping = pd.read_csv(mapping_csv)
    mapping = mapping.dropna(subset=["waqi_station_id"])

    if start_date is None:
        end_date = datetime.utcnow()
        start_date = (end_date - timedelta(days=90)).strftime("%Y-%m-%d")
        end_date = end_date.strftime("%Y-%m-%d")

    if output_csv is None:
        output_csv = "data/raw/waqi_historical.csv"

    all_rows = []
    for _, row in mapping.iterrows():
        sid = row["waqi_station_id"]
        sname = row["canonical_name"]
        print(f"Pulling WAQI historical for {sname} ({sid})...")
        feed = pull_station_feed(sid)
        if feed is None:
            continue

        forecast_daily = feed.get("forecast", {}).get("daily", {})
        iaqi = feed.get("iaqi", {})

        aqi_val = feed.get("aqi", None)
        ts = feed.get("time", {}).get("iso", None)

        all_rows.append({
            "canonical_name": sname,
            "waqi_station_id": sid,
            "timestamp": ts,
            "aqi": aqi_val,
            "pm25": iaqi.get("pm25", {}).get("v") if "pm25" in iaqi else None,
            "pm10": iaqi.get("pm10", {}).get("v") if "pm10" in iaqi else None,
            "no2": iaqi.get("no2", {}).get("v") if "no2" in iaqi else None,
            "so2": iaqi.get("so2", {}).get("v") if "so2" in iaqi else None,
            "co": iaqi.get("co", {}).get("v") if "co" in iaqi else None,
            "o3": iaqi.get("o3", {}).get("v") if "o3" in iaqi else None,
            "temperature": iaqi.get("t", {}).get("v") if "t" in iaqi else None,
            "humidity": iaqi.get("h", {}).get("v") if "h" in iaqi else None,
            "wind": iaqi.get("w", {}).get("v") if "w" in iaqi else None,
            "source": "waqi",
        })
        time.sleep(0.3)

    df = pd.DataFrame(all_rows)
    if not df.empty:
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
        df.to_csv(output_csv, index=False)
        print(f"Saved {len(df)} rows to {output_csv}")
    return df


if __name__ == "__main__":
    print("=== WAQI Client ===")
    print("Discovering WAQI station IDs...")
    mapping = discover_station_ids()
    print("\nPulling current AQI for all stations...")
    df = pull_historical()
    if not df.empty:
        print(df[["canonical_name", "timestamp", "aqi"]].to_string())
