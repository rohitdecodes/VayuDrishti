"""
Build feature store using OpenAQ (real CPCB) as AQI target.
Reuses the same weather features and feature engineering from the CAMS pipeline.
"""
import sys
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, ".")

from features.build_features import (
    _normalize_timestamp_ist,
    _deduplicate_station_timestamps,
    _apply_missing_data_policy,
    engineer_features,
)

IST = "Asia/Kolkata"
MAX_FORWARD_FILL_HOURS = 3
MAX_MISSING_FRACTION = 0.15


def build_openaq_joined(
    aq_csv: str = "data/raw/openaq_pivoted.csv",
    weather_csv: str = "data/raw/weather_openmeteo.csv",
    output_features: str = "data/processed/features_openaq.csv",
) -> pd.DataFrame:
    print("=== Building OpenAQ feature store ===")

    df_aq = pd.read_csv(aq_csv, parse_dates=["timestamp"])
    df_weather = pd.read_csv(weather_csv, parse_dates=["timestamp"])

    # Filter weather to stations present in AQ
    aq_stations = set(df_aq["canonical_name"].unique())
    df_weather = df_weather[df_weather["canonical_name"].isin(aq_stations)]

    print(f"AQ rows: {len(df_aq)}, Weather rows: {len(df_weather)}")
    print(f"Stations: {aq_stations}")

    # Normalize timestamps
    df_aq = _normalize_timestamp_ist(df_aq)
    df_weather = _normalize_timestamp_ist(df_weather)

    # Round AQ timestamps to hour (OpenAQ has 15-min raw data)
    df_aq["ts_hour"] = df_aq["timestamp"].dt.round("h")

    # Hourly aggregate: mean per station-hour
    df_aq_hourly = df_aq.groupby(
        ["canonical_name", "location_id", "latitude", "longitude", "ts_hour"]
    ).agg({
        "pm25": "mean",
        "pm10": "mean",
        "no2": "mean",
        "so2": "mean",
        "co": "mean",
        "o3": "mean",
    }).reset_index()

    df_aq_hourly["timestamp"] = df_aq_hourly["ts_hour"]
    df_aq_hourly = _deduplicate_station_timestamps(df_aq_hourly)

    # Round weather to hour
    df_weather["ts_hour"] = df_weather["timestamp"].dt.round("h")
    df_weather_hourly = df_weather.groupby(
        ["canonical_name", "ts_hour"]
    ).agg({
        "temperature": "mean",
        "humidity": "mean",
        "pressure": "mean",
        "wind_speed": "mean",
        "wind_deg": "mean",
    }).reset_index()

    # Join
    print(f"AQ hourly: {len(df_aq_hourly)}, Weather hourly: {len(df_weather_hourly)}")

    joined = df_aq_hourly.merge(
        df_weather_hourly,
        on=["canonical_name", "ts_hour"],
        how="left",
    )
    joined["timestamp"] = joined["ts_hour"]
    joined = joined.drop(columns=["ts_hour"])

    print(f"Joined: {len(joined)} rows")

    # Missing-data policy
    joined = _apply_missing_data_policy(joined)
    joined = _deduplicate_station_timestamps(joined)

    print(f"After policy: {len(joined)} rows, {joined.canonical_name.nunique()} stations")

    # Null check
    for col in ["pm25", "temperature"]:
        nulls = joined[col].isna().sum()
        print(f"  {col} nulls: {nulls} / {len(joined)} ({nulls/len(joined)*100:.1f}%)")

    # Save intermediate
    joined.to_csv("data/processed/openaq_joined.csv", index=False)

    # Feature engineering
    df_feat, feature_cols, target_cols = engineer_features(joined)
    # engineer_features always writes to data/processed/features.csv — copy to our path
    import shutil
    shutil.copy("data/processed/features.csv", output_features)
    print(f"  Copied features to {output_features} ({len(df_feat)} rows)")

    # Save an intermediate joined copy for inspection
    joined_csv = output_features.replace(".csv", "_joined.csv")
    joined.to_csv(joined_csv, index=False)

    print(f"\nFeature store: {len(df_feat)} rows, {len(feature_cols)} features")
    return df_feat


if __name__ == "__main__":
    build_openaq_joined()
