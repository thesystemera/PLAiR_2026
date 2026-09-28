from datetime import datetime, timezone
from typing import Optional

from config import settings
from services_radio import area_signals
from services_radio.area_signals import AreaContext, AreaSignal, Cell, google_error, raise_for_google
from services_radio.dj_content_bank import TalkingPoint, clip

AIR_QUALITY_URL = "https://airquality.googleapis.com/v1/currentConditions:lookup"
NAME = "air_quality"
HEALTH_MAX_CHARS = 150
UNSUPPORTED_REASONS = {"INVALID_ARGUMENT", "NOT_FOUND", "FAILED_PRECONDITION"}
POLLUTANTS = {
    "pm25": "fine particles (PM2.5)", "pm10": "coarse particles (PM10)", "o3": "ozone",
    "no2": "nitrogen dioxide", "so2": "sulphur dioxide", "co": "carbon monoxide",
}


def parse_air_quality(data: dict) -> Optional[dict]:
    indexes = data.get("indexes") or []
    universal = next((i for i in indexes if i.get("code") == "uaqi"), None)
    if universal is None or universal.get("aqi") is None:
        return None
    local = next((i for i in indexes if i.get("code") != "uaqi" and i.get("aqi") is not None), None)
    dominant = str(universal.get("dominantPollutant") or (local or {}).get("dominantPollutant") or "").lower()
    health = (data.get("healthRecommendations") or {}).get("generalPopulation") or ""
    return {
        "uaqi": int(universal["aqi"]),
        "category": str(universal.get("category") or ""),
        "dominant": POLLUTANTS.get(dominant, dominant),
        "local_name": str((local or {}).get("displayName") or ""),
        "local_aqi": (local or {}).get("aqiDisplay") or (local or {}).get("aqi"),
        "local_category": str((local or {}).get("category") or ""),
        "health": clip(" ".join(str(health).split()), HEALTH_MAX_CHARS),
        "observed_at": str(data.get("dateTime") or ""),
        "region_code": str(data.get("regionCode") or ""),
    }


def _category(payload: dict) -> str:
    return (payload.get("category") or "").strip().rstrip(".")


class AirQualitySignal(AreaSignal):
    name = NAME
    api = "air_quality"
    enabled = settings.AREA_AIR_QUALITY_ENABLED
    cell_m = settings.AREA_AIR_QUALITY_CELL_M
    refresh_s = settings.AREA_AIR_QUALITY_REFRESH_S
    empty_ttl_s = settings.AREA_EMPTY_TTL_S
    serve_stale_s = 3600
    daily_cap = settings.AREA_AIR_QUALITY_DAILY_CAP

    async def fetch(self, cell: Cell) -> Optional[dict]:
        body = {
            "location": {"latitude": cell.center[0], "longitude": cell.center[1]},
            "extraComputations": ["HEALTH_RECOMMENDATIONS", "LOCAL_AQI"],
            "universalAqi": True,
            "languageCode": "en",
        }
        response = await area_signals.google_post(AIR_QUALITY_URL, self.api_key(), body)
        if response.status_code in (400, 404) and google_error(response) in UNSUPPORTED_REASONS:
            return None
        raise_for_google(response)
        return parse_air_quality(response.json())

    def derive(self, previous: Optional[dict], payload: dict) -> dict:
        if previous and previous.get("uaqi") is not None:
            payload["previous_uaqi"] = int(previous["uaqi"])
            payload["previous_category"] = _category(previous)
        return payload

    def talking_points(self, cell: Cell, payload: dict, context: AreaContext) -> list[TalkingPoint]:
        uaqi = payload.get("uaqi")
        if uaqi is None:
            return []
        category = _category(payload)
        previous = payload.get("previous_uaqi")
        delta = uaqi - previous if previous is not None else 0
        changed = previous is not None and abs(delta) >= settings.AREA_AIR_QUALITY_CUE_DELTA and \
            category.lower() != (payload.get("previous_category") or "").lower()
        poor = uaqi < settings.AREA_AIR_QUALITY_CUE_BELOW_UAQI
        if not poor and not changed:
            return []
        day = datetime.now(timezone.utc).strftime("%Y%m%d")
        key = f"air:{cell.key}:{category.lower()}:{day}"
        if self.already_aired(context.subject, key):
            return []
        pollutant = f", mostly {payload['dominant']}" if payload.get("dominant") else ""
        if poor:
            text = f"Air quality around the listener right now: {category.lower()} (Universal AQI {uaqi} of 100{pollutant})."
            if payload.get("health"):
                text += f" Advice: {payload['health']}"
            priority = 0.8 if uaqi < 20 else 0.65
        else:
            direction = "improved" if delta > 0 else "dropped"
            text = (f"Air quality around the listener has {direction} to {category.lower()} "
                    f"(Universal AQI {uaqi} of 100, was {previous}{pollutant}).")
            priority = 0.5 if delta > 0 else 0.6
        return [TalkingPoint(key, "air", f"{text} (Google air quality data)", priority, untrusted=True,
                             on_pick=self.airing_marker(context.subject, key), source="google_air_quality")]
