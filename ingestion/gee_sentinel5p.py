import os
import json
import time
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()

GEE_PROJECT_ID = os.getenv("GEE_PROJECT_ID")
GEE_KEY_PATH = os.getenv("GEE_SERVICE_ACCOUNT_JSON")
CACHE_DIR = Path("data/raw")

NO2_COLLECTION = "COPERNICUS/S5P/OFFL/L3_NO2"
AER_COLLECTION = "COPERNICUS/S5P/OFFL/L3_AER_AI"


def _ensure_cache_dir():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _init_gee() -> "ee":
    import ee

    if GEE_KEY_PATH and os.path.exists(GEE_KEY_PATH):
        with open(GEE_KEY_PATH) as f:
            key_data = json.load(f)
        service_account = key_data.get("client_email")
        credentials = ee.ServiceAccountCredentials(service_account, GEE_KEY_PATH)
        ee.Initialize(credentials, project=GEE_PROJECT_ID)
    else:
        try:
            ee.Initialize(project=GEE_PROJECT_ID)
        except Exception:
            ee.Initialize()
    return ee


def _point_scale() -> int:
    return 1000


def _extract_no2_for_point(
    ee, lat: float, lon: float, start_date: str, end_date: str
) -> list[dict]:
    point = ee.Geometry.Point([lon, lat])
    collection = (
        ee.ImageCollection(NO2_COLLECTION)
        .filterDate(start_date, end_date)
        .filterBounds(point)
        .select("tropospheric_NO2_column_number_density")
    )

    size = collection.size().getInfo()
    if size == 0:
        print(f"    No NO2 images found for ({lat}, {lon})")
        return []

    img_list = collection.toList(size)
    rows = []
    for i in range(size):
        img = ee.Image(img_list.get(i))
        date_millis = img.date().millis().getInfo()
        ts = datetime.fromtimestamp(date_millis / 1000, tz=timezone.utc)
        try:
            val = img.reduceRegion(
                reducer=ee.Reducer.mean(),
                geometry=point.buffer(1000),
                scale=_point_scale(),
                bestEffort=True,
            ).get("tropospheric_NO2_column_number_density").getInfo()
            if val is not None:
                rows.append({
                    "timestamp": ts,
                    "latitude": lat,
                    "longitude": lon,
                    "no2_tropospheric": val,
                })
        except Exception as e:
            print(f"    NO2 reduceRegion error for {ts}: {e}")
    return rows


def _extract_aer_for_point(
    ee, lat: float, lon: float, start_date: str, end_date: str
) -> list[dict]:
    point = ee.Geometry.Point([lon, lat])
    collection = (
        ee.ImageCollection(AER_COLLECTION)
        .filterDate(start_date, end_date)
        .filterBounds(point)
        .select("absorbing_aerosol_index")
    )

    size = collection.size().getInfo()
    if size == 0:
        print(f"    No AER images found for ({lat}, {lon})")
        return []

    img_list = collection.toList(size)
    rows = []
    for i in range(size):
        img = ee.Image(img_list.get(i))
        date_millis = img.date().millis().getInfo()
        ts = datetime.fromtimestamp(date_millis / 1000, tz=timezone.utc)
        try:
            val = img.reduceRegion(
                reducer=ee.Reducer.mean(),
                geometry=point.buffer(1000),
                scale=_point_scale(),
                bestEffort=True,
            ).get("absorbing_aerosol_index").getInfo()
            if val is not None:
                rows.append({
                    "timestamp": ts,
                    "latitude": lat,
                    "longitude": lon,
                    "absorbing_aerosol_index": val,
                })
        except Exception as e:
            print(f"    AER reduceRegion error for {ts}: {e}")
    return rows


def pull_sentinel5p(
    mapping_csv: str = "ingestion/station_id_mapping.csv",
    start_date: str | None = None,
    end_date: str | None = None,
    output_no2_csv: str | None = None,
    output_aer_csv: str | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    _ensure_cache_dir()
    mapping = pd.read_csv(mapping_csv)

    if start_date is None:
        end_date = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
        start_date = (datetime.utcnow() - timedelta(days=91)).strftime("%Y-%m-%d")
    if output_no2_csv is None:
        output_no2_csv = "data/raw/sentinel5p_no2.csv"
    if output_aer_csv is None:
        output_aer_csv = "data/raw/sentinel5p_aer.csv"

    print(f"Initializing GEE with project: {GEE_PROJECT_ID}")
    ee = _init_gee()

    all_no2 = []
    all_aer = []
    for _, row in mapping.iterrows():
        lat, lon = row["latitude"], row["longitude"]
        sname = row["canonical_name"]
        print(f"Pulling Sentinel-5P for {sname} ({lat}, {lon})...")

        # NO2
        try:
            no2_rows = _extract_no2_for_point(ee, lat, lon, start_date, end_date)
            for r in no2_rows:
                r["canonical_name"] = sname
            all_no2.extend(no2_rows)
            print(f"  NO2: {len(no2_rows)} observations")
        except Exception as e:
            print(f"  NO2 ERROR for {sname}: {e}")

        # Aerosol index
        try:
            aer_rows = _extract_aer_for_point(ee, lat, lon, start_date, end_date)
            for r in aer_rows:
                r["canonical_name"] = sname
            all_aer.extend(aer_rows)
            print(f"  AER: {len(aer_rows)} observations")
        except Exception as e:
            print(f"  AER ERROR for {sname}: {e}")

        time.sleep(0.2)

    df_no2 = pd.DataFrame(all_no2)
    df_aer = pd.DataFrame(all_aer)

    if not df_no2.empty:
        df_no2.to_csv(output_no2_csv, index=False)
        print(f"Saved {len(df_no2)} NO2 rows to {output_no2_csv}")
    else:
        print("WARNING: No NO2 data extracted")

    if not df_aer.empty:
        df_aer.to_csv(output_aer_csv, index=False)
        print(f"Saved {len(df_aer)} AER rows to {output_aer_csv}")
    else:
        print("WARNING: No AER data extracted")

    return df_no2, df_aer


if __name__ == "__main__":
    print("=== Sentinel-5P GEE Client ===")
    start = (datetime.utcnow() - timedelta(days=90)).strftime("%Y-%m-%d")
    end = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
    print(f"Window: {start} to {end}")
    print("Note: Sentinel-5P OFFL has a ~2-week latency (no recent days).")
    df_no2, df_aer = pull_sentinel5p(start_date=start, end_date=end)
