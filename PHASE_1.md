# 🟦 PHASE 1 — Core Foundation
### Implementation Plan · VayuDrishti · ET AI Hackathon 2026 (PS5)

> Companion to `README.md`. This is still a **planning document** — no source code here. Task-level detail only, so execution on Day 1–2 has zero ambiguity. Do not start Phase 2 (`PHASE_2.md`) until the [Phase Gate](#-phase-1-gate--definition-of-done) at the bottom is fully checked.

**Timeline:** Day 1 – Day 2 of 5 · **Realistic budget:** ~5–6 focused hours/day (evening + early morning, minus gym block, per the blueprint's own reality check)

---

## 🏷️ Task Flag Legend

| Flag | Meaning |
|---|---|
| 🤖 **AI** | Claude can generate the code/boilerplate directly — you run, verify, adjust |
| 🧍 **Manual** | Requires you personally — account signups, external approvals, judgment calls Claude cannot make for you |
| 🤝 **Hybrid** | Claude writes/generates it, you run it and make the call on the output |

---

## 🎯 Phase Objective

Establish a reliable, cached, multi-source data pipeline and a first set of forecasting models that **beat honest baselines** — with the comparison documented, not asserted.

**In scope:** ingestion clients, feature store, feature engineering, persistence + seasonal-naive baselines, 3 LightGBM models (24h/48h/72h), and a single callable `forecast(location, horizon) → AQI` interface.

**Explicitly out of scope for this phase** (these start in `PHASE_2.md`): IDW spatial interpolation, ward boundary integration, FastAPI layer, the Advisory/Q&A agent, and anything frontend. Building any of these early is scope creep — resist it, even if Day 2 finishes early.

---

## 🧰 Prerequisites (before Day 1 starts)

- [ ] Python 3.11+ installed
- [ ] Git installed, GitHub repo created (`vayudrishti`)
- [ ] Code editor / terminal ready
- [ ] A Google account available for GEE Cloud project registration

*(API key registrations are Day 1, Hour 1 — see D1-T1 below, not a pre-phase step, matching the blueprint's own sequencing.)*

---

## 📅 DAY 1 — Data Pipeline & Validation
*Highest-risk day. The join validation at the end (D1-T9) matters more than any code written today.*

### D1-T1 — Register all API accounts 🧍 *(~45 min)*
- WAQI token → https://aqicn.org/data-platform/token/ (email signup, near-instant)
- data.gov.in account + API key → https://data.gov.in (DigiLocker SSO supported) → locate the CPCB resource in the open data catalogue
- OpenWeatherMap free tier key → https://openweathermap.org/api
- NASA FIRMS MAP_KEY → https://firms.modaps.eosdis.nasa.gov/api/map_key/ (instant)

**Acceptance:** all 4 keys saved locally in `.env` (never committed).

### D1-T2 — Start GEE Cloud project registration 🧍 *(~20 min to submit; approval not instant)*
Do this **immediately**, first hour, since it's the one step with a real external approval process. Register a Cloud project + pass the noncommercial "Community Tier" eligibility questionnaire at https://earthengine.google.com/noncommercial/.

**Fallback if this stalls past Day 1:** Copernicus Data Space Ecosystem (https://dataspace.copernicus.eu) gives direct Sentinel-5P access with a different API shape — only switch if GEE is genuinely blocking, not preemptively.

### D1-T3 — Scaffold repo structure & environment 🤖 *(~20 min)*
Create the Phase 1 subset of the directory structure, `requirements.txt`, `.env.example`, `.gitignore`.

```
vayudrishti/
├── data/raw/                # gitignored
├── data/processed/          # gitignored
├── ingestion/
│   ├── waqi_client.py
│   ├── cpcb_client.py
│   ├── openweather_client.py
│   ├── gee_sentinel5p.py
│   └── firms_client.py
├── features/
│   ├── build_features.py
│   └── feature_store.db     # gitignored
├── models/
│   ├── train.py
│   ├── baseline.py
│   └── (model artifacts)    # gitignored
├── .env.example
├── .gitignore
└── requirements.txt
```
**`.gitignore` must include:** `.env`, `data/raw/`, `data/processed/`, `*.db`, `*.pkl`

### D1-T4 — Build WAQI + CPCB ingestion clients 🤖 *(~45–60 min)*
`waqi_client.py`: pull current + historical AQI for a chosen 5–8 Delhi stations (highest CAAQMS density areas).
`cpcb_client.py`: pull the same window from data.gov.in as a cross-check layer.

> ⚠️ **Gotcha to build in from the start:** WAQI and CPCB very likely use **different station naming conventions**. Don't assume string-matching works. Build an explicit `station_id_mapping.csv` (WAQI station ID ↔ CPCB station ID ↔ canonical internal ID ↔ lat/lon) as a first-class artifact, not an afterthought.

### D1-T5 — Build OpenWeatherMap client 🤖 *(~30 min)*
Pull historical + forecast weather (wind speed/direction, temperature, humidity) for the same station coordinates/window.

### D1-T6 — Build Sentinel-5P (GEE) client 🤝 *(~45–60 min, blocked on D1-T2 approval)*
Pull NO2 / aerosol optical depth (AOD) time series per station point via the GEE Python API.

**If D1-T2 approval hasn't landed by the time you reach this task:** skip it, mark satellite data as blocked, and proceed — see the Day 1 flag rule below. Do not wait.

### D1-T7 — Build NASA FIRMS client 🤖 *(~20 min)*
Pull active fire/thermal-anomaly detections near the bounding box for the date range (stubble-burning proxy signal).

### D1-T8 — Pull 60–90 days of historical data, all sources 🤝 *(~30–45 min runtime, can run in background)*
Run all ingestion clients (D1-T4, D1-T5, D1-T6 if available, D1-T7) against the full 60–90 day window. Let this run in the background while you prep the next task.

### D1-T9 — Join & validate 🤝 *(~45–60 min)* — **THE critical checkpoint**
Join all sources into one clean table on **mapped station ID + normalized timestamp**.

> ⚠️ **Two gotchas that will silently corrupt everything downstream if skipped:**
> 1. **Timezone normalization.** WAQI timestamps, OpenWeatherMap (UTC), and GEE Earth Engine collections (UTC) are not guaranteed to share a convention. Pick one (recommend: normalize everything to IST at the join step) and document it in the join script itself.
> 2. **Missing-data policy, decided explicitly, not implicitly.** Real station data has gaps. Decide now: drop stations with >15% missing readings in the window; for smaller gaps, forward-fill up to a 3-hour ceiling, never further. Write this rule into the code as a named constant, not a magic number buried in a `dropna()`.

**Acceptance criteria:**
- [ ] One row per (station, timestamp), zero duplicate timestamps per station
- [ ] Missing-data policy applied and documented
- [ ] Station ID mapping table verified against at least 2–3 known stations by hand

**Day 1 exception rule (from the blueprint, honor it exactly):** if satellite data (D1-T6) isn't cleanly joined by end of day, **do not let it block Day 2.** Proceed with station history + weather only. Satellite becomes a Day 4 stretch-add in Phase 3, never a Day 1 gate. Every other item in D1-T9's acceptance criteria is non-negotiable.

---

## 📅 DAY 2 — Feature Engineering, Baselines & Forecasting Models

### D2-T1 — Feature engineering 🤖 *(~60–90 min)*
Build, per station-timestamp row:
- Lags: t-1 through t-72h
- Rolling means: 6h, 24h, 72h windows
- Calendar flags: hour-of-day, day-of-week, stubble-burning season indicator (roughly Oct–Nov in Delhi)
- Weather features (from D1-T5)
- Satellite features (from D1-T6), if available — otherwise omit cleanly, don't impute fake satellite values

### D2-T2 — Chronological train/validation split 🤝 *(~15 min)*
> ⚠️ **This is a correctness issue, not a style choice.** Split by **time** — e.g., last 10–14 days as validation, everything before as training. A random k-fold split leaks future information into training and will silently inflate your RMSE numbers, which quietly destroys the one technical claim (beating the baseline) the whole pitch rests on. Sanity-check this yourself; don't just trust a library default.

### D2-T3 — Implement both baselines 🤖 *(~20–30 min)*
- **Persistence:** `forecast(t+h) = actual(t)`
- **Seasonal-naive:** `forecast(t+h) = actual(t+h−7days)` (same hour, same day-of-week, prior week)
- Compute RMSE and MAE for both on the validation split, per horizon (24h/48h/72h)

### D2-T4 — Train 3 LightGBM models 🤖 *(~45–60 min)*
One model per horizon (24h, 48h, 72h) — **kept separate and simple**, not one clever multi-output model (per the blueprint: no slack for debugging cleverness this week).

**Reasonable starting hyperparameters** (tune only if there's time left):
- `objective`: regression (RMSE)
- `num_leaves`: 31
- `learning_rate`: 0.05
- `n_estimators`: 300–500, with early stopping on validation RMSE
- `max_depth`: -1 (unconstrained, controlled via `num_leaves`)

### D2-T5 — Evaluate against baselines & document honestly 🤝 *(~30 min)*
Produce one RMSE/MAE table: **model vs. persistence vs. seasonal-naive**, per horizon.

**Judgment gate:** the model must beat persistence at 24h, at minimum. If 72h underperforms — expected, per published work on this problem — that is **not** a phase-gate failure, but it must be documented plainly, not hidden or rounded away. This is exactly the risk the original blueprint flagged as the single biggest threat to the pitch; the fix is honesty in this table, not a better-looking number.

### D2-T6 — Wrap as a single callable interface 🤖 *(~20 min)*
Expose one clean function: `forecast(location, horizon) → AQI`. This is the only interface Phase 2's IDW interpolation and API layer will consume — keep it boring and stable.

---

## ⏱️ Time Budget Summary

| Day | Est. Total | Budget | Headroom |
|---|---|---|---|
| Day 1 | ~6 hrs | 5–6 hrs | Tight — debugging will eat any slack, especially D1-T9 |
| Day 2 | ~4 hrs | 5–6 hrs | ~1–2 hrs buffer — bank it for D2-T2/T5 if the numbers are messy |

If Day 1 overruns past 6 hours, the join validation (D1-T9) is the one task allowed to spill into Day 2 morning — everything else should hold the line.

---

## 🚦 Blockers & Escalation Rules

| If this happens... | Then do this |
|---|---|
| GEE approval hasn't landed by Day 1 afternoon | Proceed without satellite features; retry GEE in parallel, don't block on it |
| Satellite join is messy/incomplete by EOD Day 1 | Drop it from Day 1–2 scope entirely; revisit only as a Day 4 stretch-add |
| A station has >15% missing data in the window | Drop that station from this build, don't try to salvage it |
| Model doesn't beat persistence at 24h | Stop and debug before Day 2 ends — this is the actual phase-gate risk, not a footnote |
| Model doesn't beat baselines at 72h only | Document honestly, proceed — expected, and explicitly not a blocker |

---

## ✅ Phase 1 Gate — Definition of Done

Do not start `PHASE_2.md` until every box below is checked:

- [ ] All 4 API keys + GEE registration status confirmed (approved or fallback decided)
- [ ] Ingestion clients built for WAQI, CPCB, OpenWeatherMap, NASA FIRMS (+ GEE if available)
- [ ] Station ID mapping table built and spot-verified
- [ ] 60–90 days of historical data pulled, all sources
- [ ] Clean joined table: no duplicate timestamps, timezone normalized, missing-data policy applied
- [ ] Chronological (not random) train/validation split confirmed
- [ ] Persistence and seasonal-naive baselines computed
- [ ] 3 LightGBM models trained (24h/48h/72h)
- [ ] RMSE/MAE table produced: model vs. both baselines, per horizon
- [ ] Model beats persistence at 24h (hard requirement) — 72h honestly documented either way
- [ ] `forecast(location, horizon) → AQI` callable and stable

**Deliverables handed to Phase 2:** joined feature table (SQLite), 3 trained model artifacts, baseline comparison report, the `forecast()` interface.
