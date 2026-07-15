import pandas as pd
import numpy as np
from pathlib import Path

IST = "Asia/Kolkata"

MAX_FORWARD_FILL_HOURS = 3
MAX_MISSING_FRACTION = 0.15


def _normalize_timestamp_ist(df: pd.DataFrame, col: str = "timestamp") -> pd.DataFrame:
    df = df.copy()
    df[col] = pd.to_datetime(df[col], errors="coerce")
    if df[col].dt.tz is None:
        df[col] = df[col].dt.tz_localize("UTC").dt.tz_convert(IST)
    else:
        df[col] = df[col].dt.tz_convert(IST)
    return df


def _deduplicate_station_timestamps(
    df: pd.DataFrame, station_col: str = "canonical_name", ts_col: str = "timestamp"
) -> pd.DataFrame:
    return df.drop_duplicates(subset=[station_col, ts_col], keep="first")


def _apply_missing_data_policy(
    df: pd.DataFrame,
    station_col: str = "canonical_name",
    ts_col: str = "timestamp",
) -> pd.DataFrame:
    df = df.sort_values([station_col, ts_col]).copy()

    result = df.copy().set_index(ts_col).groupby(station_col)

    # Check missing fraction per station
    total_rows = df.groupby(station_col).size()
    missing_rows = df[df[["pm25", "pm10", "no2", "temperature", "humidity", "wind_speed"]].isna().all(axis=1)].groupby(station_col).size()
    missing_frac = (missing_rows / total_rows).fillna(0)

    drop_stations = missing_frac[missing_frac > MAX_MISSING_FRACTION].index.tolist()
    if drop_stations:
        print(f"  Dropping stations with >{MAX_MISSING_FRACTION*100:.0f}% missing:")
        for s in drop_stations:
            print(f"    {s}: {missing_frac[s]*100:.1f}%")

    keep_stations = [s for s in df[station_col].unique() if s not in drop_stations]
    df = df[df[station_col].isin(keep_stations)]

    # Forward-fill within station group, max 3h ceiling
    parts = []
    for sid in keep_stations:
        g = df[df[station_col] == sid].copy()
        g = g.sort_values(ts_col)
        g = g.set_index(ts_col)
        g = g.ffill(limit=MAX_FORWARD_FILL_HOURS)
        g = g.reset_index()
        parts.append(g)
    df = pd.concat(parts, ignore_index=True)

    return df


def build_joined_table(
    aq_csv: str = "data/raw/openmeteo_air_quality.csv",
    weather_csv: str = "data/raw/weather_openmeteo.csv",
    waqi_csv: str = "data/raw/waqi_historical.csv",
    output_db: str = "features/feature_store.db",
) -> pd.DataFrame:
    print("=== D1-T9: JOIN & VALIDATE ===")

    print(f"Loading AQ: {aq_csv}")
    df_aq = pd.read_csv(aq_csv)

    print(f"Loading Weather: {weather_csv}")
    df_weather = pd.read_csv(weather_csv)

    print(f"Loading WAQI: {waqi_csv}")
    df_waqi = pd.read_csv(waqi_csv)

    # Step 1: Timezone normalization to IST
    print("\n[1] Timezone normalization to IST...")
    df_aq = _normalize_timestamp_ist(df_aq)
    df_weather = _normalize_timestamp_ist(df_weather)
    df_waqi = _normalize_timestamp_ist(df_waqi)

    print(f"  AQ timezone: {df_aq['timestamp'].dt.tz}")
    print(f"  Weather timezone: {df_weather['timestamp'].dt.tz}")

    # Step 2: Deduplicate per source
    print("\n[2] Deduplicating...")
    df_aq = _deduplicate_station_timestamps(df_aq)
    df_weather = _deduplicate_station_timestamps(df_weather)
    print(f"  AQ after dedup: {len(df_aq)} rows")
    print(f"  Weather after dedup: {len(df_weather)} rows")

    # Step 3: Round timestamps to hour for consistent join
    df_aq["ts_hour"] = df_aq["timestamp"].dt.round("h")
    df_weather["ts_hour"] = df_weather["timestamp"].dt.round("h")

    # Step 4: Merge AQ + Weather on (station, rounded timestamp)
    print("\n[3] Joining AQ + Weather on (station, ts_hour)...")
    keep_cols_aq = [
        "canonical_name", "latitude", "longitude", "ts_hour",
        "pm25", "pm10", "no2", "so2", "co", "o3", "european_aqi",
    ]
    keep_cols_weather = [
        "canonical_name", "ts_hour",
        "temperature", "humidity", "pressure", "wind_speed", "wind_deg",
    ]

    df_joined = df_aq[keep_cols_aq].merge(
        df_weather[keep_cols_weather],
        on=["canonical_name", "ts_hour"],
        how="left",
        suffixes=("", "_weather"),
    )

    print(f"  Joined: {len(df_joined)} rows")
    df_joined["timestamp"] = df_joined["ts_hour"]
    df_joined = df_joined.drop(columns=["ts_hour"])

    # Step 5: Missing data policy
    print("\n[4] Applying missing-data policy...")
    print(f"  MAX_FORWARD_FILL_HOURS = {MAX_FORWARD_FILL_HOURS}")
    print(f"  MAX_MISSING_FRACTION = {MAX_MISSING_FRACTION}")
    df_joined = _apply_missing_data_policy(df_joined)

    # Step 6: Final deduplication
    df_joined = _deduplicate_station_timestamps(df_joined)
    print(f"\n  Final rows: {len(df_joined)}")
    print(f"  Stations remaining: {df_joined['canonical_name'].nunique()}")

    # Step 7: Verify acceptance criteria
    print("\n[5] Acceptance criteria verification:")
    dup_check = df_joined.groupby(["canonical_name", "timestamp"]).size()
    dup_count = (dup_check > 1).sum()
    print(f"  Duplicate (station, timestamp) rows: {dup_count}" +
          (" PASS" if dup_count == 0 else " FAIL"))

    print(f"  Columns: {list(df_joined.columns)}")
    for col in ["pm25", "pm10", "no2", "temperature", "humidity", "wind_speed"]:
        null_count = df_joined[col].isna().sum()
        null_pct = null_count / len(df_joined) * 100
        print(f"  {col}: {null_count} null ({null_pct:.1f}%)")

    # Step 8: Write to SQLite
    import sqlite3
    db_path = Path(output_db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    conn = sqlite3.connect(str(db_path))
    df_joined.to_sql("joined_features", conn, index=False, if_exists="replace")
    conn.close()
    print(f"\n  Saved to {output_db}")

    # Also save CSV for inspection
    csv_out = "data/processed/joined_features.csv"
    df_joined.to_csv(csv_out, index=False)
    print(f"  Saved to {csv_out}")

    return df_joined


LAG_HOURS = [1, 2, 3, 6, 12, 24, 48, 72]
ROLLING_WINDOWS = [6, 24, 72]


def engineer_features(
    df: pd.DataFrame,
    station_col: str = "canonical_name",
    ts_col: str = "timestamp",
) -> pd.DataFrame:
    print("\n=== D2-T1: FEATURE ENGINEERING ===")

    df = df.sort_values([station_col, ts_col]).copy()
    df["hour"] = df[ts_col].dt.hour
    df["day_of_week"] = df[ts_col].dt.dayofweek
    df["month"] = df[ts_col].dt.month

    # Stubble-burning season indicator (Oct-Nov for Delhi)
    df["stubble_season"] = df["month"].isin([10, 11]).astype(int)

    lag_cols = ["pm25", "pm10", "no2", "temperature", "humidity", "wind_speed"]
    new_cols = []

    for station in df[station_col].unique():
        mask = df[station_col] == station
        idx = df.index[mask]

        for col in lag_cols:
            for lag in LAG_HOURS:
                lag_name = f"{col}_lag_{lag}h"
                df.loc[idx, lag_name] = (
                    df.loc[idx, col].shift(lag)
                )
                new_cols.append(lag_name)

        for col in lag_cols:
            for window in ROLLING_WINDOWS:
                roll_name = f"{col}_roll_{window}h"
                df.loc[idx, roll_name] = (
                    df.loc[idx, col].rolling(window=window, min_periods=1).mean()
                )
                new_cols.append(roll_name)

    # Target columns (future PM2.5 at horizons)
    for station in df[station_col].unique():
        mask = df[station_col] == station
        idx = df.index[mask]
        for h in [24, 48, 72]:
            df.loc[idx, f"target_{h}h"] = df.loc[idx, "pm25"].shift(-h)

    original_cols = [
        "canonical_name", "timestamp", "latitude", "longitude",
        "pm25", "pm10", "no2", "so2", "co", "o3",
        "temperature", "humidity", "pressure", "wind_speed", "wind_deg",
        "hour", "day_of_week", "month", "stubble_season",
    ]
    target_cols = ["target_24h", "target_48h", "target_72h"]

    # Deduplicate: each lag/roll name is shared across stations, not per-station
    unique_new_cols = list(dict.fromkeys(new_cols))
    feature_cols = original_cols[4:] + [c for c in unique_new_cols if c not in original_cols]
    feature_cols = list(dict.fromkeys(feature_cols))

    # Build final column list: only columns that actually exist in df
    existing_df_cols = [c for c in original_cols + unique_new_cols + target_cols if c in df.columns]
    df = df[[c for c in existing_df_cols if c in df.columns]].copy()

    print(f"  Columns in final df: {len(df.columns)} (feature cols: {len(feature_cols)})")

    # Drop rows where target is NaN (end of series)
    before = len(df)
    df = df.dropna(subset=target_cols, how="all")
    print(f"  Rows before NaN target drop: {before}, after: {len(df)}")

    # Drop rows where PM25 (the core predictor) is NaN due to lag padding
    before = len(df)
    df = df.dropna(subset=["pm25"])
    print(f"  Rows after pm25 NaN drop: {len(df)}")

    print(f"  Feature count: {len(feature_cols)}")
    print(f"  Final rows: {len(df)}, Stations: {df[station_col].nunique()}")

    out_csv = "data/processed/features.csv"
    df.to_csv(out_csv, index=False)
    print(f"  Saved features to {out_csv}")

    return df, feature_cols, target_cols


if __name__ == "__main__":
    df = build_joined_table()
    print("\n=== D1-T9 COMPLETE ===")
    print(df[["canonical_name", "timestamp", "pm25", "temperature"]].head(20).to_string())

    df_feat, feature_cols, target_cols = engineer_features(df)
    print(f"\n=== D2-T1 COMPLETE ===")
    print(f"Features: {len(feature_cols)}")
    print(f"Targets: {target_cols}")
    print(f"Shape: {df_feat.shape}")
