import os
import time
import requests
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

OWM_API_KEY = os.getenv("OPENWEATHERMAP_API_KEY")
OWM_BASE = "https://api.openweathermap.org/data/2.5"
OPEN_METEO_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
OPEN_METEO_FORECAST = "https://api.open-meteo.com/v1/forecast"
CACHE_DIR = Path("data/raw")


def _ensure_cache_dir():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _owm_forecast(lat: float, lon: float) -> list[dict]:
    url = f"{OWM_BASE}/forecast"
    params = {
        "lat": lat,
        "lon": lon,
        "appid": OWM_API_KEY,
        "units": "metric",
    }
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    rows = []
    for entry in data.get("list", []):
        ts = pd.to_datetime(entry["dt"], unit="s", utc=True)
        main = entry.get("main", {})
        wind = entry.get("wind", {})
        rows.append({
            "timestamp": ts,
            "temperature": main.get("temp"),
            "humidity": main.get("humidity"),
            "pressure": main.get("pressure"),
            "wind_speed": wind.get("speed"),
            "wind_deg": wind.get("deg"),
            "weather_desc": entry.get("weather", [{}])[0].get("description", ""),
        })
    return rows


def _open_meteo_historical(lat: float, lon: float, start_date: str, end_date: str) -> list[dict]:
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start_date,
        "end_date": end_date,
        "daily": "temperature_2m_mean,relative_humidity_2m_mean,surface_pressure_mean,wind_speed_10m_mean,wind_direction_10m_dominant",
        "hourly": "temperature_2m,relative_humidity_2m,surface_pressure,wind_speed_10m,wind_direction_10m",
        "timezone": "Asia/Kolkata",
    }
    resp = requests.get(OPEN_METEO_ARCHIVE, params=params, timeout=60)
    resp.raise_for_status()
    data = resp.json()

    hourly = data.get("hourly", {})
    timestamps = hourly.get("time", [])
    temps = hourly.get("temperature_2m", [])
    humidity = hourly.get("relative_humidity_2m", [])
    pressure = hourly.get("surface_pressure", [])
    wind_speed = hourly.get("wind_speed_10m", [])
    wind_dir = hourly.get("wind_direction_10m", [])

    rows = []
    for i, t in enumerate(timestamps):
        rows.append({
            "timestamp": pd.to_datetime(t).tz_localize("Asia/Kolkata"),
            "temperature": temps[i] if i < len(temps) else None,
            "humidity": humidity[i] if i < len(humidity) else None,
            "pressure": pressure[i] if i < len(pressure) else None,
            "wind_speed": wind_speed[i] if i < len(wind_speed) else None,
            "wind_deg": wind_dir[i] if i < len(wind_dir) else None,
        })
    return rows


def pull_weather(
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
        output_csv = "data/raw/weather_openmeteo.csv"

    all_rows = []
    for _, row in mapping.iterrows():
        lat, lon = row["latitude"], row["longitude"]
        sname = row["canonical_name"]
        print(f"Pulling historical weather (Open-Meteo) for {sname} ({lat}, {lon})...")

        try:
            rows = _open_meteo_historical(lat, lon, start_date, end_date)
            for r in rows:
                r["canonical_name"] = sname
                r["latitude"] = lat
                r["longitude"] = lon
                r["source"] = "openmeteo_historical"
            all_rows.extend(rows)
            time.sleep(0.5)
        except Exception as e:
            print(f"  ERROR Open-Meteo for {sname}: {e}")

    df = pd.DataFrame(all_rows)
    if not df.empty:
        df.to_csv(output_csv, index=False)
        print(f"Saved {len(df)} rows to {output_csv}")
    return df


def pull_owm_forecast_all(
    mapping_csv: str = "ingestion/station_id_mapping.csv",
    output_csv: str | None = None,
) -> pd.DataFrame:
    _ensure_cache_dir()
    mapping = pd.read_csv(mapping_csv)

    if output_csv is None:
        output_csv = "data/raw/weather_owm_forecast.csv"

    all_rows = []
    for _, row in mapping.iterrows():
        lat, lon = row["latitude"], row["longitude"]
        sname = row["canonical_name"]
        print(f"Pulling OWM forecast for {sname} ({lat}, {lon})...")

        try:
            rows = _owm_forecast(lat, lon)
            for r in rows:
                r["canonical_name"] = sname
                r["latitude"] = lat
                r["longitude"] = lon
                r["source"] = "owm_forecast"
            all_rows.extend(rows)
            time.sleep(0.5)
        except Exception as e:
            print(f"  ERROR OWM forecast for {sname}: {e}")

    df = pd.DataFrame(all_rows)
    if not df.empty:
        df.to_csv(output_csv, index=False)
        print(f"Saved {len(df)} rows to {output_csv}")
    return df


if __name__ == "__main__":
    print("=== Weather Client ===")
    start = (datetime.utcnow() - timedelta(days=90)).strftime("%Y-%m-%d")
    end = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
    print(f"Historical window: {start} to {end}")
    df_hist = pull_weather(start_date=start, end_date=end)
    if not df_hist.empty:
        print(f"Historical: {len(df_hist)} rows, stations: {df_hist['canonical_name'].nunique()}")

    df_fcst = pull_owm_forecast_all()
    if not df_fcst.empty:
        print(f"Forecast: {len(df_fcst)} rows, stations: {df_fcst['canonical_name'].nunique()}")
