"""
IDW interpolation + confidence tiering for VayuDrishti Phase 2.
Takes 3 station forecasts and a GeoJSON of boundary units,
produces per-unit AQI estimates with honesty-labeled confidence tiers.
"""
import json
import math
import sys
from pathlib import Path

import numpy as np
import yaml
from shapely.geometry import shape, Point

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def load_config():
    with open(PROJECT_ROOT / "config" / "interpolation_config.yaml") as f:
        return yaml.safe_load(f)


def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def centroid_from_geometry(geom):
    poly = shape(geom)
    c = poly.centroid
    return c.y, c.x


def assign_confidence(distance_km, thresholds):
    for tier_key in ["high", "medium", "low"]:
        t = thresholds[tier_key]
        if distance_km <= t["max_distance_km"]:
            return tier_key, t["label"], t["description"]
    return "low", thresholds["low"]["label"], thresholds["low"]["description"]


def idw_interpolate(station_values, station_coords, target_lat, target_lon, power=2, epsilon=0.001):
    weights = []
    for (slat, slon) in station_coords:
        d = haversine_km(slat, slon, target_lat, target_lon)
        d = max(d, epsilon)
        weights.append(1.0 / (d ** power))

    weights = np.array(weights)
    weights = weights / weights.sum()

    interpolated = np.dot(weights, np.array(station_values))
    return float(interpolated)


def compute_interpolation(horizon: int) -> list[dict]:
    config = load_config()
    stations = config["stations"]
    thresholds = config["confidence_tiers"]
    idw_cfg = config["idw"]

    station_coords = [(s["lat"], s["lon"]) for s in stations]

    from models.train import forecast

    station_values = []
    for s in stations:
        f = forecast(s["name"], horizon)
        if f is None:
            raise RuntimeError(f"Forecast failed for {s['name']} at {horizon}h")
        station_values.append(f["forecast_aqi"])

    with open(PROJECT_ROOT / "data" / "boundaries" / "delhi-ac-clean.geojson") as f:
        geojson = json.load(f)

    results = []
    for feature in geojson["features"]:
        props = feature["properties"]
        unit_id = props.get("unit_id", props.get("A_CNST_NM", "").upper().replace(" ", "_"))
        unit_name = props.get("A_CNST_NM", unit_id)
        geom = feature["geometry"]

        centroid_lat, centroid_lon = centroid_from_geometry(geom)

        distances = [
            haversine_km(centroid_lat, centroid_lon, slat, slon)
            for slat, slon in station_coords
        ]
        min_distance = min(distances)
        nearest_station_idx = int(np.argmin(distances))

        tier_key, tier_label, tier_desc = assign_confidence(min_distance, thresholds)

        aqi = idw_interpolate(
            station_values,
            station_coords,
            centroid_lat,
            centroid_lon,
            power=idw_cfg["power"],
            epsilon=idw_cfg["epsilon_km"],
        )

        results.append({
            "unit_id": unit_id,
            "unit_name": unit_name,
            "centroid_lat": round(centroid_lat, 6),
            "centroid_lon": round(centroid_lon, 6),
            "forecast_aqi": round(aqi, 1),
            "confidence_tier": tier_key,
            "confidence_label": tier_label,
            "confidence_description": tier_desc,
            "nearest_station": stations[nearest_station_idx]["name"],
            "distance_to_nearest_station_km": round(min_distance, 2),
        })

    return results


def sanity_check(results):
    config = load_config()
    stations = config["stations"]
    for s in stations:
        best = None
        best_dist = float("inf")
        for r in results:
            d = haversine_km(r["centroid_lat"], r["centroid_lon"], s["lat"], s["lon"])
            if d < best_dist:
                best_dist = d
                best = r
        print(f"  {s['name']}: nearest unit = {best['unit_name']} ({best_dist:.2f} km), AQI = {best['forecast_aqi']}")


if __name__ == "__main__":
    for h in [24, 48, 72]:
        print(f"\n=== Horizon {h}h ===")
        results = compute_interpolation(h)
        print(f"  Units: {len(results)}")
        tiers = {}
        for r in results:
            t = r["confidence_tier"]
            tiers[t] = tiers.get(t, 0) + 1
        print(f"  Tiers: {tiers}")
        print(f"  AQI range: {min(r['forecast_aqi'] for r in results):.1f} – {max(r['forecast_aqi'] for r in results):.1f}")
        sanity_check(results)
