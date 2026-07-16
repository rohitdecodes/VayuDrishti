"""
VayuDrishti Phase 2 — Advisory & Q&A Agent
Single module, two modes: advisory generation and grounded Q&A.
Strict grounding: only references features that exist in the trained model.
"""
import json
import os
from pathlib import Path

from groq import Groq

PROJECT_ROOT = Path(__file__).parent.parent

MODEL = "llama-3.1-8b-instant"
TEMPERATURE = 0.3
MAX_TOKENS = 300

REAL_FEATURES = """
The AQI forecast model was trained ONLY on these real data features, and NOTHING else:
- Ground-station sensor measurements: PM2.5, PM10, NO2, SO2, CO, O3
- Weather measurements from local stations: temperature, humidity, pressure, wind speed, wind direction
- Temporal patterns: hour, day of week, month, stubble-burning season indicator
- Lagged values (1h to 72h back) and rolling averages (6h, 24h, 72h) of PM2.5, PM10, NO2, temperature, humidity, wind speed

IMPORTANT — The model does NOT use and has NO knowledge of:
- Satellite data (NO2 tropospheric column, aerosol optical depth)
- Fire/hotspot satellite detections
- Traffic or industrial emissions data
- Any remote sensing or earth observation products

You MUST NOT mention or imply the use of satellite, fire, remote sensing, or earth observation data.
If asked about these, state clearly: "Our current model is built on ground-station data and weather measurements only. Satellite and fire data are not part of this model."
"""

SYSTEM_PROMPT_ADVISORY = f"""You are VayuDrishti, Delhi's air quality forecast advisor. Your job is to provide a brief, scientifically-grounded 2-3 sentence advisory for a specific Delhi constituency, in both English and Hindi.

{REAL_FEATURES}

You will receive:
1. Constituency name
2. Forecast AQI value and CPCB category (Good 0-50 / Satisfactory 51-100 / Moderate 101-200 / Poor 201-300 / Very Poor 301-400 / Severe 401+)
3. Confidence tier (High/Medium/Low) with description
4. The top 2-3 real features that drove this forecast

RULES:
- Write 2-3 sentences in plain English, then repeat the same message in Hindi (Devanagari script).
- If confidence tier is Low, the FIRST sentence in both languages MUST state plainly: "This estimate is far from our monitoring stations — treat it as directional only."
- Ground every claim in the provided features. If the top feature is humidity trends, talk about humidity. If it's lagged PM2.5, talk about recent pollution levels.
- NEVER mention satellite, fire hotspot, remote sensing, earth observation, or any data source not listed above.
- Be concise, direct, and helpful.
- Format your response exactly as:

EN: <English advisory text>
HI: <Hindi advisory text>
"""

SYSTEM_PROMPT_QA = f"""You are VayuDrishti, Delhi's air quality information assistant. Answer user questions about air quality forecasts for a specific Delhi constituency.

{REAL_FEATURES}

You will receive:
1. Constituency name and forecast context (AQI, category, confidence tier)
2. The top real features driving the forecast
3. A user's question

RULES:
- Answer ONLY using the provided context and features. Do not use external knowledge.
- If the answer cannot be found in the provided context, say: "I can only answer based on the data from our ground-station model. The information you're asking about is not available in this forecast."
- NEVER mention satellite, fire hotspot, remote sensing, earth observation, or any data source not listed above.
- If asked about satellite/fire/remote sensing data, say: "Our current model uses ground-station sensor data and weather measurements. Satellite and fire data are not part of this model's training."
- Keep answers to 2-3 sentences.
- Be concise and honest about what the model knows.
"""


def _get_client():
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        from dotenv import load_dotenv
        load_dotenv()
        api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY not found in environment")
    return Groq(api_key=api_key)


def _load_importances(horizon: int) -> dict:
    path = PROJECT_ROOT / "models" / f"feature_importances_{horizon}h_openaq.json"
    if not path.exists():
        return {}
    with open(path) as f:
        return json.load(f)


def _top_features(importances: dict, n: int = 3) -> list[tuple[str, int]]:
    items = list(importances.items())
    return items[:n]


def _build_user_prompt_advisory(unit_name: str, aqi: float, category: str,
                                confidence_tier: str, confidence_label: str,
                                confidence_desc: str, top_features: list) -> str:
    features_str = "\n".join([f"- {name} (importance: {imp})" for name, imp in top_features])
    return f"""Constituency: {unit_name}
Forecast AQI: {aqi:.1f} ({category})
Confidence: {confidence_label} — {confidence_desc}

Top features driving this forecast:
{features_str}"""


def _build_user_prompt_qa(unit_name: str, aqi: float, category: str,
                          confidence_tier: str, confidence_label: str,
                          confidence_desc: str, top_features: list,
                          question: str) -> str:
    features_str = "\n".join([f"- {name} (importance: {imp})" for name, imp in top_features])
    return f"""Constituency: {unit_name}
Forecast AQI: {aqi:.1f} ({category})
Confidence: {confidence_label} — {confidence_desc}

Top features driving this forecast:
{features_str}

User Question: {question}"""


def generate_advisory(unit_name: str, forecast_aqi: float, category: str,
                      confidence_tier: str, confidence_label: str,
                      confidence_desc: str, horizon: int) -> dict:
    importances = _load_importances(horizon)
    top = _top_features(importances, n=3)

    user_prompt = _build_user_prompt_advisory(
        unit_name, forecast_aqi, category,
        confidence_tier, confidence_label, confidence_desc, top
    )

    client = _get_client()
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT_ADVISORY},
            {"role": "user", "content": user_prompt},
        ],
        temperature=TEMPERATURE,
        max_tokens=MAX_TOKENS,
    )

    raw_text = response.choices[0].message.content.strip()

    en_text = ""
    hi_text = ""
    lines = raw_text.split("\n")
    for line in lines:
        if line.startswith("EN:") or line.startswith("EN :"):
            en_text = line.split(":", 1)[1].strip() if ":" in line else ""
        elif line.startswith("HI:") or line.startswith("HI :"):
            hi_text = line.split(":", 1)[1].strip() if ":" in line else ""
        elif en_text and not hi_text and not line.startswith("EN") and not line.startswith("HI"):
            if line.strip():
                en_text += " " + line.strip()
        elif hi_text and line.strip() and not line.startswith("HI"):
            hi_text += " " + line.strip()

    return {
        "unit_name": unit_name,
        "forecast_aqi": forecast_aqi,
        "category": category,
        "confidence_tier": confidence_tier,
        "confidence_label": confidence_label,
        "horizon_hours": horizon,
        "top_features": [{"feature": name, "importance": imp} for name, imp in top],
        "advisory_en": en_text or raw_text,
        "advisory_hi": hi_text or "",
    }


def answer_question(unit_name: str, forecast_aqi: float, category: str,
                    confidence_tier: str, confidence_label: str,
                    confidence_desc: str, horizon: int, question: str) -> dict:
    importances = _load_importances(horizon)
    top = _top_features(importances, n=3)

    user_prompt = _build_user_prompt_qa(
        unit_name, forecast_aqi, category,
        confidence_tier, confidence_label, confidence_desc, top, question
    )

    client = _get_client()
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT_QA},
            {"role": "user", "content": user_prompt},
        ],
        temperature=TEMPERATURE,
        max_tokens=MAX_TOKENS,
    )

    answer = response.choices[0].message.content.strip()

    return {
        "unit_name": unit_name,
        "forecast_aqi": forecast_aqi,
        "category": category,
        "confidence_tier": confidence_tier,
        "horizon_hours": horizon,
        "question": question,
        "answer": answer,
    }


def verify_no_satellite(text: str) -> list[str]:
    disclaimer_phrases = [
        "not part of this model", "not in this model", "not use satellite",
        "not used in this forecast", "does not include satellite",
        "not available in this", "no satellite data", "satellite data are not",
        "satellite and fire data are not",
    ]
    text_lower = text.lower()
    if any(p in text_lower for p in disclaimer_phrases):
        return []

    banned = ["satellite", "fire hotspot", "remote sensing", "earth observation",
              "aerosol optical", "tropospheric", "no2 column", "aod",
              "modis", "viirs", "sentinel", "tropomi", "gee", "google earth engine"]
    found = []
    for term in banned:
        if term in text_lower:
            found.append(term)
    return found
