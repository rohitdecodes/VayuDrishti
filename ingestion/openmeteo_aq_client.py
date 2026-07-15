import os, requests, pandas as pd
from datetime import datetime, timedelta
from pathlib import Path

CACHE_DIR = Path("data/raw")

DELHI_BBOX = {"lat_min": 28.4, "lat_max": 28.9, "lon_min": 76.8, "lon_max": 77.4}


def _ensure_cache_dir():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def pull_openmeteo_air_quality(
    mapping_csv: str = "ingestion/station_id_mapping.csv",
    start_date: str | None = None,
    end_date: str | None = None,
    output_csv: str | None = None,
) -> pd.DataFrame:
    _ensure_cache_dir()
    mapping = pd.read_csv(mapping_csv)

    if start_date is None:
        end_date = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
        start_date = (datetime.utcnow() - timedelta(days=91)).strftime("%Y-%m-%d")
    if output_csv is None:
        output_csv = "data/raw/openmeteo_air_quality.csv"

    all_rows = []
    for _, row in mapping.iterrows():
        lat, lon = row["latitude"], row["longitude"]
        sname = row["canonical_name"]
        print(f"Pulling Open-Meteo air quality for {sname} ({lat}, {lon})...")

        params = {
            "latitude": lat,
            "longitude": lon,
            "start_date": start_date,
            "end_date": end_date,
            "hourly": [
                "pm2_5",
                "pm10",
                "nitrogen_dioxide",
                "sulphur_dioxide",
                "carbon_monoxide",
                "ozone",
                "european_aqi",
            ],
            "timezone": "Asia/Kolkata",
        }
        param_str = "&".join(f"hourly={p}" for p in params["hourly"])
        url = f"https://air-quality-api.open-meteo.com/v1/air-quality?latitude={lat}&longitude={lon}&start_date={start_date}&end_date={end_date}&{param_str}&timezone=Asia%2FKolkata"

        try:
            resp = requests.get(url, timeout=60)
            resp.raise_for_status()
            data = resp.json()
            hourly = data.get("hourly", {})
            times = hourly.get("time", [])
            pm25 = hourly.get("pm2_5", [])
            pm10 = hourly.get("pm10", [])
            no2 = hourly.get("nitrogen_dioxide", [])
            so2 = hourly.get("sulphur_dioxide", [])
            co = hourly.get("carbon_monoxide", [])
            o3 = hourly.get("ozone", [])
            eaqi = hourly.get("european_aqi", [])

            for i, t in enumerate(times):
                all_rows.append({
                    "timestamp": pd.to_datetime(t).tz_localize("Asia/Kolkata"),
                    "canonical_name": sname,
                    "latitude": lat,
                    "longitude": lon,
                    "pm25": pm25[i] if i < len(pm25) else None,
                    "pm10": pm10[i] if i < len(pm10) else None,
                    "no2": no2[i] if i < len(no2) else None,
                    "so2": so2[i] if i < len(so2) else None,
                    "co": co[i] if i < len(co) else None,
                    "o3": o3[i] if i < len(o3) else None,
                    "european_aqi": eaqi[i] if i < len(eaqi) else None,
                    "source": "openmeteo_aq",
                })
            print(f"  {len(times)} hourly observations")
        except Exception as e:
            print(f"  ERROR for {sname}: {e}")

    df = pd.DataFrame(all_rows)
    if not df.empty:
        df.to_csv(output_csv, index=False)
        print(f"Saved {len(df)} rows to {output_csv}")
    return df


if __name__ == "__main__":
    print("=== Open-Meteo Air Quality Client ===")
    end_date = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
    start_date = (datetime.utcnow() - timedelta(days=91)).strftime("%Y-%m-%d")
    print(f"Window: {start_date} to {end_date}")
    df = pull_openmeteo_air_quality(start_date=start_date, end_date=end_date)
    if not df.empty:
        print(df[["canonical_name", "timestamp", "pm25", "pm10"]].head(20).to_string())
