import os
import json
import pickle
import warnings
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta

warnings.filterwarnings("ignore")

MODEL_DIR = Path("models")

VALIDATION_DAYS = 14
N_ESTIMATORS = 500
LEARNING_RATE = 0.05
NUM_LEAVES = 31
EARLY_STOPPING_ROUNDS = 50

# ───────────────────────── D2-T2: Chronological split ─────────────────────────


def chronological_split(
    df: pd.DataFrame,
    ts_col: str = "timestamp",
    validation_days: int = VALIDATION_DAYS,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    print("\n=== D2-T2: CHRONOLOGICAL TRAIN/VALIDATION SPLIT ===")

    df = df.sort_values(ts_col).copy()
    max_ts = df[ts_col].max()
    cutoff = max_ts - timedelta(days=validation_days)

    train = df[df[ts_col] <= cutoff].copy()
    val = df[df[ts_col] > cutoff].copy()

    # Sanity check: no temporal leakage
    train_max = train[ts_col].max()
    val_min = val[ts_col].min()
    assert train_max <= val_min, f"Temporal leak: train max {train_max} > val min {val_min}"

    print(f"  Train: {len(train)} rows ({train[ts_col].min()} to {train[ts_col].max()})")
    print(f"  Val:   {len(val)} rows ({val[ts_col].min()} to {val[ts_col].max()})")
    print("  Temporal leak check: PASS (train end <= val start)")

    return train, val


# ───────────────────────── D2-T3: Baselines ─────────────────────────


def compute_baselines(
    df: pd.DataFrame,
    ts_col: str = "timestamp",
    station_col: str = "canonical_name",
    horizons: list = None,
) -> dict:
    if horizons is None:
        horizons = [24, 48, 72]

    print("\n=== D2-T3: BASELINES ===")
    df = df.sort_values([station_col, ts_col]).copy()

    results = {}
    for h in horizons:
        # Persistence: forecast(t+h) = actual(t)
        df["persistence_pred"] = df.groupby(station_col)["pm25"].shift(h)
        # Seasonal-naive: forecast(t+h) = actual(t+h-7days)
        seasonal_lag = h + 24 * 7
        df["seasonal_pred"] = df.groupby(station_col)["pm25"].shift(seasonal_lag)

        mask = df[f"target_{h}h"].notna()
        actual = df.loc[mask, f"target_{h}h"].values
        persistence_pred = df.loc[mask, "persistence_pred"].values
        seasonal_pred = df.loc[mask, "seasonal_pred"].values

        mask_p = ~np.isnan(persistence_pred)
        mask_s = ~np.isnan(seasonal_pred)

        results[f"persistence_{h}h"] = {
            "rmse": float(np.sqrt(np.mean((actual[mask_p] - persistence_pred[mask_p]) ** 2))),
            "mae": float(np.mean(np.abs(actual[mask_p] - persistence_pred[mask_p]))),
        }
        results[f"seasonal_{h}h"] = {
            "rmse": float(np.sqrt(np.mean((actual[mask_s] - seasonal_pred[mask_s]) ** 2))),
            "mae": float(np.mean(np.abs(actual[mask_s] - seasonal_pred[mask_s]))),
        }
        print(f"  {h}h persistence:  RMSE={results[f'persistence_{h}h']['rmse']:.2f}, MAE={results[f'persistence_{h}h']['mae']:.2f}")
        print(f"  {h}h seasonal:     RMSE={results[f'seasonal_{h}h']['rmse']:.2f}, MAE={results[f'seasonal_{h}h']['mae']:.2f}")

    for h in horizons:
        df = df.drop(columns=["persistence_pred", "seasonal_pred"], errors="ignore")

    return results


# ───────────────────────── D2-T4: Train LightGBM ─────────────────────────


def train_lightgbm(
    X_train: pd.DataFrame,
    y_train: pd.Series,
    X_val: pd.DataFrame,
    y_val: pd.Series,
    horizon: int,
) -> tuple:
    import lightgbm as lgb

    print(f"\n  Training LightGBM for {horizon}h horizon...")

    dtrain = lgb.Dataset(X_train, label=y_train)
    dval = lgb.Dataset(X_val, label=y_val, reference=dtrain)

    params = {
        "objective": "regression",
        "metric": "rmse",
        "num_leaves": NUM_LEAVES,
        "learning_rate": LEARNING_RATE,
        "max_depth": -1,
        "verbosity": -1,
        "seed": 42,
        "feature_pre_filter": False,
    }

    model = lgb.train(
        params,
        dtrain,
        valid_sets=[dtrain, dval],
        valid_names=["train", "val"],
        num_boost_round=N_ESTIMATORS,
        callbacks=[
            lgb.early_stopping(EARLY_STOPPING_ROUNDS),
            lgb.log_evaluation(50),
        ],
    )

    preds = model.predict(X_val)
    rmse = np.sqrt(np.mean((y_val.values - preds) ** 2))
    mae = np.mean(np.abs(y_val.values - preds))
    print(f"  {horizon}h val RMSE: {rmse:.2f}, MAE: {mae:.2f}")

    return model, rmse, mae


# ───────────────────────── D2-T4 + D2-T5: Train + Evaluate ─────────────────────────


def train_all_models(
    features_csv: str = "data/processed/features.csv",
    model_dir: str = "models",
    dataset_label: str = "default",
) -> dict:
    MOD_DIR = Path(model_dir)
    MOD_DIR.mkdir(parents=True, exist_ok=True)
    print(f"\n=== Training on: {features_csv} [{dataset_label}] ===")

    print("\n=== D2-T4: TRAIN MODELS ===")
    df = pd.read_csv(features_csv, parse_dates=["timestamp"])

    train, val = chronological_split(df)

    # Identify feature columns (exclude ID/target/metadata columns)
    exclude_prefixes = ["target_", "canonical_", "timestamp", "latitude", "longitude", "persistence_", "seasonal_"]
    feature_cols = [c for c in df.columns if not any(c.startswith(ep) for ep in exclude_prefixes)]
    print(f"  Feature columns: {len(feature_cols)}")

    results = {"baselines": {}, "models": {}}

    for h in [24, 48, 72]:
        target_col = f"target_{h}h"

        t = train.dropna(subset=feature_cols + [target_col])
        v = val.dropna(subset=feature_cols + [target_col])

        X_t = t[feature_cols]
        y_t = t[target_col]
        X_v = v[feature_cols]
        y_v = v[target_col]

        print(f"\n  {h}h: train={len(X_t)}, val={len(X_v)}")

        model, rmse, mae = train_lightgbm(X_t, y_t, X_v, y_v, h)

        # Save model
        model_suffix = f"_{dataset_label}" if dataset_label != "default" else ""
        model_path = MOD_DIR / f"forecast_{h}h{model_suffix}.pkl"
        with open(model_path, "wb") as f:
            pickle.dump(model, f)

        # Save feature columns for inference
        feat_path = MOD_DIR / f"feature_cols_{h}h{model_suffix}.json"
        with open(feat_path, "w") as f:
            json.dump(feature_cols, f)

        results["models"][f"{h}h"] = {"rmse": float(rmse), "mae": float(mae)}

    # Baselines
    print("\n=== D2-T3: BASELINES (on validation split) ===")
    baseline_results = compute_baselines(val)
    results["baselines"] = baseline_results

    # ──────────────────── D2-T5: Compare ────────────────────
    print("\n=== D2-T5: MODEL vs BASELINES ===")
    print(f"{'Horizon':<10} {'Model RMSE':>12} {'Model MAE':>12} {'Persist RMSE':>14} {'Persist MAE':>14} {'Season RMSE':>13} {'Season MAE':>13}")
    print("-" * 90)

    for h in [24, 48, 72]:
        m = results["models"].get(f"{h}h", {})
        p = results["baselines"].get(f"persistence_{h}h", {})
        s = results["baselines"].get(f"seasonal_{h}h", {})

        m_rmse = m.get("rmse", float("nan"))
        m_mae = m.get("mae", float("nan"))
        p_rmse = p.get("rmse", float("nan"))
        p_mae = p.get("mae", float("nan"))
        s_rmse = s.get("rmse", float("nan"))
        s_mae = s.get("mae", float("nan"))

        beat_p = "BEATS" if m_rmse < p_rmse else "LOSES"
        print(f"{h}h{' ':<8} {m_rmse:>12.2f} {m_mae:>12.2f} {p_rmse:>14.2f} {p_mae:>14.2f} {s_rmse:>13.2f} {s_mae:>13.2f}  {beat_p}")

    # Save results
    report = {
        "baselines": results["baselines"],
        "models": results["models"],
        "feature_count": len(feature_cols),
        "train_rows": len(train),
        "val_rows": len(val),
        "validation_days": VALIDATION_DAYS,
    }
    report_suffix = f"_{dataset_label}" if dataset_label != "default" else ""
    report_path = MOD_DIR / f"baseline_report{report_suffix}.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\n  Report saved to {report_path}")

    # Hard check: 24h must beat persistence
    m24_rmse = results["models"].get("24h", {}).get("rmse", float("inf"))
    p24_rmse = results["baselines"].get("persistence_24h", {}).get("rmse", float("inf"))
    if m24_rmse < p24_rmse:
        print("\n  PHASE GATE CHECK: Model beats persistence at 24h -- PASS")
    else:
        print(f"\n  PHASE GATE CHECK: Model RMSE {m24_rmse:.2f} >= Persistence RMSE {p24_rmse:.2f} -- FAIL")
        print("  *** STOP AND DEBUG BEFORE PROCEEDING ***")

    return results


# ───────────────────────── D2-T6: forecast() interface ─────────────────────────


def forecast(canonical_name: str, horizon: int, suffix: str = "openaq") -> dict | None:
    MOD_DIR = MODEL_DIR

    model_path = MOD_DIR / f"forecast_{horizon}h_{suffix}.pkl"
    feat_path = MOD_DIR / f"feature_cols_{horizon}h_{suffix}.json"

    # Fallback: try without suffix if not found
    if not model_path.exists():
        model_path = MOD_DIR / f"forecast_{horizon}h.pkl"
    if not feat_path.exists():
        feat_path = MOD_DIR / f"feature_cols_{horizon}h.json"
    features_path = f"data/processed/features_{suffix}.csv"
    if not Path(features_path).exists():
        features_path = "data/processed/features.csv"

    if not model_path.exists() or not feat_path.exists():
        print(f"Model for {horizon}h not found at {model_path}")
        return None

    df = pd.read_csv(features_path, parse_dates=["timestamp"])
    station_data = df[df["canonical_name"] == canonical_name].copy()
    if station_data.empty:
        print(f"No data for station '{canonical_name}'")
        return None

    station_data = station_data.sort_values("timestamp")

    with open(feat_path) as f:
        feature_cols = json.load(f)

    with open(model_path, "rb") as f:
        model = pickle.load(f)

    latest = station_data.tail(1)
    X = latest[feature_cols]
    aqi = float(model.predict(X)[0])

    result = {
        "station": canonical_name,
        "horizon_hours": horizon,
        "forecast_aqi": round(aqi, 1),
        "pm25": round(aqi, 1),
        "timestamp": str(latest["timestamp"].values[0]),
    }

    # Compute AQI category based on CPCB scale
    if aqi <= 50:
        result["category"] = "Good"
    elif aqi <= 100:
        result["category"] = "Satisfactory"
    elif aqi <= 200:
        result["category"] = "Moderate"
    elif aqi <= 300:
        result["category"] = "Poor"
    elif aqi <= 400:
        result["category"] = "Very Poor"
    else:
        result["category"] = "Severe"

    return result


# ───────────────────────── MAIN ─────────────────────────


if __name__ == "__main__":
    import lightgbm
    import os
    print(f"LightGBM version: {lightgbm.__version__}")

    dataset = os.environ.get("FEATURES_CSV", "data/processed/features.csv")
    label = os.environ.get("DATASET_LABEL", "openaq")

    results = train_all_models(features_csv=dataset, dataset_label=label)

    print("\n=== D2-T6: TEST forecast() INTERFACE ===")
    for station in ["Anand Vihar", "RK Puram", "Punjabi Bagh"]:
        for h in [24, 48, 72]:
            f = forecast(station, h)
            if f:
                print(f"  {station} {h}h: AQI={f['forecast_aqi']:.0f} ({f['category']})")
