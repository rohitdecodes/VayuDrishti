# Phase 1 Summary — VayuDrishti

**Status:** COMPLETE | **Date:** July 15, 2026

---

## Data Source Journey

We tried three AQI historical data sources and ended with OpenAQ (real CPCB sensors):

| Source | Resolution | Historical? | Status |
|--------|-----------|-------------|--------|
| CPCB data.gov.in API | Ground stations | No (API unreachable) | Dead |
| CPCB CCR portal CSV | Ground stations | No (single snapshot) | Dead |
| Open-Meteo CAMS | 40km regional reanalysis | Yes (91 days) | Worked, but stations in same grid cell have identical values (corr=1.0) |
| **OpenAQ v3** | **Real CPCB ground sensors** | **Yes (91 days)** | **Adopted — 3 Delhi stations active** |

The CAMS bug: Anand Vihar and Punjabi Bagh fall in the same ~40km grid cell → correlation 1.000 (identical values). OpenAQ: same pair → correlation 0.583 (genuinely different per-station readings).

## What Was Done

| Task | Description | Status |
|------|-------------|--------|
| D1-T1 | API keys: WAQI, CPCB, OpenWeatherMap, NASA FIRMS, OpenAQ | Done |
| D1-T2 | GEE Cloud project approved (`et-ai-502511`) | Done |
| D1-T3 | Repo scaffold, `.env`, `.gitignore`, `requirements.txt` | Done |
| D1-T4 | WAQI client — 8 Delhi stations mapped to WAQI UIDs | Done |
| D1-T5 | OpenWeatherMap + Open-Meteo weather client (91 days, 8 stations) | Done |
| D1-T6 | GEE Sentinel-5P client (code ready, IAM blocked) | Built |
| D1-T7 | NASA FIRMS client (0 detections — expected July) | Done |
| D1-T8 | OpenAQ: 121,587 measurements pulled for 3 stations over 91 days | Done |
| D1-T9 | Join + validate: 5,805 rows, 0 duplicates, IST timezone | Done |
| D2-T1 | Feature engineering: 81 features (6 lags × 8h, 3 rolling windows, calendar) | Done |
| D2-T2 | Chronological train/val split (Jun 30 cutoff, no leakage) | Done |
| D2-T3 | Persistence + seasonal-naive baselines computed | Done |
| D2-T4 | 3 LightGBM models trained (24h/48h/72h) on OpenAQ | Done |
| D2-T5 | RMSE comparison table: CAMS vs OpenAQ | Done |
| D2-T6 | `forecast(location, horizon) -> AQI` — 3 stations, all horizons | Done |

## Results: OpenAQ vs CAMS

| Horizon | OpenAQ RMSE | OpenAQ Persist | CAMS RMSE | CAMS Persist | Beats? |
|---------|------------|---------------|-----------|-------------|--------|
| 24h | **33.47** | 42.82 | 52.36 | 67.07 | YES (-22%) |
| 48h | **35.53** | 50.65 | 52.44 | 81.07 | YES (-30%) |
| 72h | **35.88** | 47.65 | 54.63 | 90.61 | YES (-25%) |

OpenAQ models are better (lower RMSE) despite training on only 3 stations vs CAMS's 8. Real per-station sensor data enables learning genuine patterns; CAMS's regional smoothing inflated RMSE.

### Station coverage

| Station | OpenAQ | Data range |
|---------|--------|------------|
| RK Puram | YES | 2016–July 2026 |
| Anand Vihar | YES | 2016–July 2026 |
| Punjabi Bagh | YES | 2016–July 2026 |
| DTU | No | Ended 2018 |
| ITO | No | Ended 2018 |
| Mandir Marg | No | Ended 2018 |
| Siri Fort | No | Not in OpenAQ |
| IGI Airport | No | Ended ~2018 |

## Issues

1. **GEE blocked** — grant `roles/serviceusage.serviceUsageConsumer` to `vayudrishti-gee@et-ai-502511.iam.gserviceaccount.com` at GCP IAM
2. **5 stations missing OpenAQ data** — CPCB stopped feeding these stations to OpenAQ around 2018. IGI Airport (T3) exists but has 0 measurements. Day 4 stretch-add if fixed.

## Deliverables

| Artifact | Path |
|----------|------|
| Feature store (SQLite) | `features/feature_store.db` |
| OpenAQ features CSV | `data/processed/features_openaq.csv` |
| OpenAQ 24h model | `models/forecast_24h_openaq.pkl` |
| OpenAQ 48h model | `models/forecast_48h_openaq.pkl` |
| OpenAQ 72h model | `models/forecast_72h_openaq.pkl` |
| OpenAQ baseline report | `models/baseline_report_openaq.json` |
| OpenAQ pivoted data | `data/raw/openaq_pivoted.csv` |
| Station mapping | `ingestion/station_id_mapping.csv` |
