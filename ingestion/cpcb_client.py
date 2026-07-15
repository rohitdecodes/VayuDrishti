import os
import requests
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

CPCB_API_KEY = os.getenv("DATA_GOV_IN_API_KEY")
CPCB_RESOURCE_ID = "3b01bcb8-0b14-4abf-b6f2-c1bfd384ba69"
CPCB_BASE = "https://api.data.gov.in/resource"
CACHE_DIR = Path("data/raw")


def _ensure_cache_dir():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _build_cpcb_station_name(canonical: str) -> str:
    synonyms = {
        "RK Puram": ["R K Puram", "RK Puram", "R.K. Puram", "R. K. Puram"],
        "Anand Vihar": ["Anand Vihar", "Anand Vihar, Delhi", "Anand Vihar - Delhi"],
        "ITO": ["ITO", "ITO Delhi", "ITO, Delhi", "Bahadur Shah Zafar Marg", "BSZ Marg"],
        "IGI Airport": ["IGI Airport", "IGI Airport T3", "Indira Gandhi International Airport", "Delhi Airport"],
        "Punjabi Bagh": ["Punjabi Bagh", "Punjabi Bagh, Delhi"],
        "Mandir Marg": ["Mandir Marg", "Mandir Marg, Delhi"],
        "Siri Fort": ["Siri Fort", "Siri Fort, Delhi", "Sri Fort"],
        "DTU": ["DTU", "Delhi Technological University", "DTU Delhi"],
    }
    return synonyms.get(canonical, [canonical])


def pull_cpcb_for_date(date_str: str, offset: int = 0, limit: int = 1000) -> list[dict]:
    params = {
        "api-key": CPCB_API_KEY,
        "format": "json",
        "limit": limit,
        "offset": offset,
        "filters[date]": date_str,
    }
    resp = requests.get(f"{CPCB_BASE}/{CPCB_RESOURCE_ID}", params=params, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    return data.get("records", [])


def pull_cpcb_window(
    start_date: str | None = None,
    end_date: str | None = None,
    mapping_csv: str = "ingestion/station_id_mapping.csv",
    output_csv: str | None = None,
) -> pd.DataFrame:
    _ensure_cache_dir()
    mapping = pd.read_csv(mapping_csv)

    if start_date is None:
        end_date = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
        start_date = (datetime.utcnow() - timedelta(days=91)).strftime("%Y-%m-%d")
    if output_csv is None:
        output_csv = "data/raw/cpcb_historical.csv"

    name_to_canonical = {}
    for _, row in mapping.iterrows():
        for syn in _build_cpcb_station_name(row["canonical_name"]):
            name_to_canonical[syn.lower()] = row["canonical_name"]

    all_rows = []
    current = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")

    while current <= end:
        date_str = current.strftime("%Y-%m-%d")
        print(f"  Pulling CPCB for {date_str}...")
        try:
            records = pull_cpcb_for_date(date_str, limit=2000)
        except Exception as e:
            print(f"  ERROR pulling CPCB for {date_str}: {e}")
            current += timedelta(days=1)
            continue

        for rec in records:
            station_raw = str(rec.get("station", "")).strip()
            matched_canonical = name_to_canonical.get(station_raw.lower())
            if matched_canonical is None:
                continue

            aqi = rec.get("pollutant_avg", rec.get("aqi", None))
            all_rows.append({
                "canonical_name": matched_canonical,
                "cpcb_station_name": station_raw,
                "timestamp": rec.get("last_update", rec.get("date", date_str)),
                "aqi": pd.to_numeric(aqi, errors="coerce") if aqi else None,
                "pm25": pd.to_numeric(rec.get("pm2_5"), errors="coerce") if rec.get("pm2_5") else None,
                "pm10": pd.to_numeric(rec.get("pm10"), errors="coerce") if rec.get("pm10") else None,
                "no2": pd.to_numeric(rec.get("no2"), errors="coerce") if rec.get("no2") else None,
                "so2": pd.to_numeric(rec.get("so2"), errors="coerce") if rec.get("so2") else None,
                "co": pd.to_numeric(rec.get("co"), errors="coerce") if rec.get("co") else None,
                "o3": pd.to_numeric(rec.get("o3"), errors="coerce") if rec.get("o3") else None,
                "source": "cpcb",
            })
        current += timedelta(days=1)

    df = pd.DataFrame(all_rows)
    if not df.empty:
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
        df.to_csv(output_csv, index=False)
        print(f"Saved {len(df)} rows to {output_csv}")
    else:
        print("WARNING: No CPCB records matched for the selected stations.")

    cpcb_names = {r["cpcb_station_name"] for r in all_rows}
    if cpcb_names:
        print(f"  Matched CPCB station names: {cpcb_names}")
        mapping_new = mapping.copy()
        mapping_new["cpcb_station_id"] = mapping_new["cpcb_station_id"].astype(str)
        for _, row in mapping_new.iterrows():
            if pd.isna(row["cpcb_station_id"]) or str(row["cpcb_station_id"]).strip() == "":
                for syn in _build_cpcb_station_name(row["canonical_name"]):
                    if syn in cpcb_names:
                        mapping_new.at[_, "cpcb_station_id"] = syn
                        break
        mapping_new["cpcb_station_id"] = mapping_new["cpcb_station_id"].replace("nan", "")
        mapping_new.to_csv(mapping_csv, index=False)

    return df


if __name__ == "__main__":
    print("=== CPCB Client ===")
    df = pull_cpcb_window()
    if not df.empty:
        print(df[["canonical_name", "timestamp", "aqi"]].head(20).to_string())
        print(f"Total rows: {len(df)}")
