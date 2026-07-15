import os
import csv
import io
import requests
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

FIRMS_MAP_KEY = os.getenv("NASA_FIRMS_MAP_KEY")
FIRMS_BASE = "https://firms.modaps.eosdis.nasa.gov/api"
CACHE_DIR = Path("data/raw")

DELHI_BBOX = [76.8, 28.4, 77.4, 28.9]


def _ensure_cache_dir():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _parse_firms_csv(text: str) -> list[dict]:
    if not text.strip():
        return []
    reader = csv.DictReader(io.StringIO(text))
    return list(reader)


def pull_firms_viirs_snpp(days: int = 10) -> list[dict]:
    bbox = ",".join(str(x) for x in DELHI_BBOX)
    url = f"{FIRMS_BASE}/area/csv/{FIRMS_MAP_KEY}/VIIRS_SNPP_NRT/{bbox}/{days}"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return _parse_firms_csv(resp.text)


def pull_firms_modis(days: int = 10) -> list[dict]:
    bbox = ",".join(str(x) for x in DELHI_BBOX)
    url = f"{FIRMS_BASE}/area/csv/{FIRMS_MAP_KEY}/MODIS_NRT/{bbox}/{days}"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return _parse_firms_csv(resp.text)


def pull_firms(
    output_csv: str | None = None,
) -> pd.DataFrame:
    _ensure_cache_dir()

    if output_csv is None:
        output_csv = "data/raw/firms_fire.csv"

    all_rows = []
    for source_name, fetcher, max_days in [
        ("VIIRS_SNPP_NRT", pull_firms_viirs_snpp, 5),
        ("MODIS_NRT", pull_firms_modis, 10),
    ]:
        print(f"Pulling FIRMS {source_name} (last {max_days}d)...")
        try:
            rows = fetcher(days=max_days)
            for r in rows:
                r["source"] = source_name
            print(f"  {len(rows)} fire detections")
            all_rows.extend(rows)
        except Exception as e:
            print(f"  ERROR: {e}")

    df = pd.DataFrame(all_rows)
    if not df.empty:
        for col in ["latitude", "longitude", "brightness", "bright_t31", "frp"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        if "acq_date" in df.columns:
            df["acq_date"] = pd.to_datetime(df["acq_date"], errors="coerce")
        df.to_csv(output_csv, index=False)
        print(f"Saved {len(df)} FIRMS rows to {output_csv}")
    else:
        print("WARNING: No FIRMS fire detections found")

    return df


if __name__ == "__main__":
    print("=== NASA FIRMS Client ===")
    df = pull_firms()
    if not df.empty:
        print(df[["latitude", "longitude", "acq_date", "frp", "source"]].head(20).to_string())
