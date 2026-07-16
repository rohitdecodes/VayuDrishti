from pydantic import BaseModel


class StationForecast(BaseModel):
    station: str
    horizon_hours: int
    forecast_aqi: float
    category: str


class UnitForecast(BaseModel):
    unit_id: str
    unit_name: str
    centroid_lat: float
    centroid_lon: float
    forecast_aqi: float
    confidence_tier: str
    confidence_label: str
    confidence_description: str
    nearest_station: str
    distance_to_nearest_station_km: float


class ForecastGeoJSON(BaseModel):
    type: str = "FeatureCollection"
    features: list[dict]


class UnitForecastResponse(BaseModel):
    unit_id: str
    unit_name: str
    horizon_hours: int
    forecast_aqi: float
    confidence_tier: str
    confidence_label: str
    confidence_description: str
    nearest_station: str
    distance_to_nearest_station_km: float
    centroid_lat: float
    centroid_lon: float
    geometry: dict | None = None
