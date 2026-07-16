"""
VayuDrishti Phase 3 — FastAPI Backend
Forecast + Advisory + Q&A endpoints + Static Frontend
"""
import json
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from interpolation.idw import compute_interpolation, centroid_from_geometry
from backend.schemas import ForecastGeoJSON, UnitForecastResponse
from agents.advisory import generate_advisory, answer_question, verify_no_satellite

app = FastAPI(
    title="VayuDrishti API",
    description="Delhi AQI Forecast — 3-station IDW interpolation with confidence tiering",
    version="0.3.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

frontend_dir = PROJECT_ROOT / "frontend"
if frontend_dir.exists():
    app.mount("/static", StaticFiles(directory=str(frontend_dir)), name="static")

    @app.get("/")
    def root():
        return RedirectResponse(url="/static/index.html")

VALID_HORIZONS = {24, 48, 72}


def _build_geojson(results: list[dict], horizon: int) -> dict:
    with open(PROJECT_ROOT / "data" / "boundaries" / "delhi-ac-clean.geojson") as f:
        geojson = json.load(f)

    result_map = {r["unit_id"]: r for r in results}

    features = []
    for feature in geojson["features"]:
        unit_id = feature["properties"]["unit_id"]
        r = result_map.get(unit_id)
        if r is None:
            continue
        features.append({
            "type": "Feature",
            "geometry": feature["geometry"],
            "properties": {
                "unit_id": r["unit_id"],
                "unit_name": r["unit_name"],
                "horizon_hours": horizon,
                "forecast_aqi": r["forecast_aqi"],
                "confidence_tier": r["confidence_tier"],
                "confidence_label": r["confidence_label"],
                "confidence_description": r["confidence_description"],
                "nearest_station": r["nearest_station"],
                "distance_to_nearest_station_km": r["distance_to_nearest_station_km"],
                "centroid_lat": r["centroid_lat"],
                "centroid_lon": r["centroid_lon"],
            },
        })

    return {"type": "FeatureCollection", "features": features}


@app.get("/forecast/{horizon}")
def get_forecast_map(horizon: int):
    if horizon not in VALID_HORIZONS:
        raise HTTPException(status_code=400, detail=f"Horizon must be one of {sorted(VALID_HORIZONS)}")
    results = compute_interpolation(horizon)
    geojson = _build_geojson(results, horizon)
    return JSONResponse(content=geojson)


@app.get("/forecast/{unit_id}/{horizon}")
def get_unit_forecast(unit_id: str, horizon: int):
    if horizon not in VALID_HORIZONS:
        raise HTTPException(status_code=400, detail=f"Horizon must be one of {sorted(VALID_HORIZONS)}")
    results = compute_interpolation(horizon)
    for r in results:
        if r["unit_id"] == unit_id.upper():
            with open(PROJECT_ROOT / "data" / "boundaries" / "delhi-ac-clean.geojson") as f:
                geojson = json.load(f)
            geometry = None
            for feature in geojson["features"]:
                if feature["properties"]["unit_id"] == unit_id.upper():
                    geometry = feature["geometry"]
                    break
            return UnitForecastResponse(
                unit_id=r["unit_id"],
                unit_name=r["unit_name"],
                horizon_hours=horizon,
                forecast_aqi=r["forecast_aqi"],
                confidence_tier=r["confidence_tier"],
                confidence_label=r["confidence_label"],
                confidence_description=r["confidence_description"],
                nearest_station=r["nearest_station"],
                distance_to_nearest_station_km=r["distance_to_nearest_station_km"],
                centroid_lat=r["centroid_lat"],
                centroid_lon=r["centroid_lon"],
                geometry=geometry,
            )
    raise HTTPException(status_code=404, detail=f"Unit '{unit_id}' not found")


class QARequest(BaseModel):
    question: str


def _get_unit_context(unit_id: str, horizon: int) -> dict:
    results = compute_interpolation(horizon)
    for r in results:
        if r["unit_id"] == unit_id.upper():
            return r
    return None


@app.post("/advisory/{unit_id}/{horizon}")
def get_advisory(unit_id: str, horizon: int):
    if horizon not in VALID_HORIZONS:
        raise HTTPException(status_code=400, detail=f"Horizon must be one of {sorted(VALID_HORIZONS)}")

    ctx = _get_unit_context(unit_id, horizon)
    if ctx is None:
        raise HTTPException(status_code=404, detail=f"Unit '{unit_id}' not found")

    result = generate_advisory(
        unit_name=ctx["unit_name"],
        forecast_aqi=ctx["forecast_aqi"],
        category=_aqi_category(ctx["forecast_aqi"]),
        confidence_tier=ctx["confidence_tier"],
        confidence_label=ctx["confidence_label"],
        confidence_desc=ctx["confidence_description"],
        horizon=horizon,
    )

    sat_check = verify_no_satellite(result["advisory_en"] + " " + result["advisory_hi"])
    result["satellite_reference_check"] = "CLEAN" if not sat_check else "WARNING: " + ", ".join(sat_check)

    return result


@app.post("/qa/{unit_id}")
def ask_question(unit_id: str, request: QARequest):
    ctx = _get_unit_context(unit_id, 24)
    if ctx is None:
        raise HTTPException(status_code=404, detail=f"Unit '{unit_id}' not found")

    result = answer_question(
        unit_name=ctx["unit_name"],
        forecast_aqi=ctx["forecast_aqi"],
        category=_aqi_category(ctx["forecast_aqi"]),
        confidence_tier=ctx["confidence_tier"],
        confidence_label=ctx["confidence_label"],
        confidence_desc=ctx["confidence_description"],
        horizon=24,
        question=request.question,
    )

    sat_check = verify_no_satellite(result["answer"])
    result["satellite_reference_check"] = "CLEAN" if not sat_check else "WARNING: " + ", ".join(sat_check)

    return result


def _aqi_category(aqi: float) -> str:
    if aqi <= 50:
        return "Good"
    elif aqi <= 100:
        return "Satisfactory"
    elif aqi <= 200:
        return "Moderate"
    elif aqi <= 300:
        return "Poor"
    elif aqi <= 400:
        return "Very Poor"
    return "Severe"


@app.get("/health")
def health():
    return {"status": "ok", "service": "VayuDrishti API", "version": "0.3.0"}
