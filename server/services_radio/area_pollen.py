from datetime import datetime, timezone
from typing import Optional

import pytz

from config import settings
from services_radio import area_signals
from services_radio.area_signals import AreaContext, AreaSignal, Cell, google_error, raise_for_google
from services_radio.dj_content_bank import TalkingPoint

POLLEN_URL = "https://pollen.googleapis.com/v1/forecast:lookup"
NAME = "pollen"
FORECAST_DAYS = 2
MAX_PLANTS = 3
UNSUPPORTED_REASONS = {"INVALID_ARGUMENT", "NOT_FOUND", "FAILED_PRECONDITION"}
LEVELS = {0: "none", 1: "very low", 2: "low", 3: "moderate", 4: "high", 5: "very high"}


def _entries(items: list) -> list[dict]:
    entries = []
    for item in items or []:
        index = item.get("indexInfo") or {}
        if index.get("value") is None:
            continue
        entries.append({"name": str(item.get("displayName") or item.get("code") or "").strip(),
                        "value": int(index["value"]), "category": str(index.get("category") or ""),
                        "in_season": bool(item.get("inSeason"))})
    return sorted(entries, key=lambda e: e["value"], reverse=True)


def parse_pollen(data: dict) -> Optional[dict]:
    days = []
    for info in data.get("dailyInfo") or []:
        date = info.get("date") or {}
        try:
            day = f"{int(date['year']):04d}-{int(date['month']):02d}-{int(date['day']):02d}"
        except (KeyError, TypeError, ValueError):
            continue
        types, plants = _entries(info.get("pollenTypeInfo")), _entries(info.get("plantInfo"))
        if types or plants:
            days.append({"date": day, "types": types, "plants": plants})
    if not days:
        return None
    return {"days": days, "region_code": str(data.get("regionCode") or "")}


def local_day(tz_name: Optional[str], now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    try:
        zone = pytz.timezone(tz_name) if tz_name else pytz.utc
    except pytz.UnknownTimeZoneError:
        zone = pytz.utc
    return now.astimezone(zone).strftime("%Y-%m-%d")


class PollenSignal(AreaSignal):
    name = NAME
    api = "pollen"
    enabled = settings.AREA_POLLEN_ENABLED
    cell_m = settings.AREA_POLLEN_CELL_M
    refresh_s = settings.AREA_POLLEN_REFRESH_S
    empty_ttl_s = settings.AREA_EMPTY_TTL_S
    serve_stale_s = 86400
    daily_cap = settings.AREA_POLLEN_DAILY_CAP

    async def fetch(self, cell: Cell) -> Optional[dict]:
        params = {"location.latitude": f"{cell.center[0]:.5f}", "location.longitude": f"{cell.center[1]:.5f}",
                  "days": FORECAST_DAYS, "languageCode": "en", "plantsDescription": "false"}
        response = await area_signals.google_get(POLLEN_URL, self.api_key(), params)
        if response.status_code in (400, 404) and google_error(response) in UNSUPPORTED_REASONS:
            return None
        raise_for_google(response)
        return parse_pollen(response.json())

    def talking_points(self, cell: Cell, payload: dict, context: AreaContext,
                       now: Optional[datetime] = None) -> list[TalkingPoint]:
        today = local_day(context.tz_name, now)
        day = next((d for d in payload.get("days") or [] if d.get("date") == today), None)
        if not day or not day.get("types"):
            return []
        peak = max(t["value"] for t in day["types"])
        if peak < settings.AREA_POLLEN_CUE_MIN_INDEX:
            return []
        key = f"pollen:{cell.key}:{today}:{peak}"
        if self.already_aired(context.subject, key):
            return []
        notable = [t for t in day["types"] if t["value"] >= 2]
        levels = ", ".join(f"{t['name'].lower()} {(t['category'] or LEVELS.get(t['value'], '')).lower()}"
                           for t in notable)
        plants = [p["name"] for p in day.get("plants") or [] if p["in_season"] and p["value"] >= 2][:MAX_PLANTS]
        text = f"Pollen forecast for the listener's area today is {LEVELS.get(peak, 'high')}: {levels}."
        if plants:
            text += f" In season: {', '.join(plants)}."
        priority = 0.6 if peak >= 5 else 0.5
        return [TalkingPoint(key, "pollen", f"{text} (Google pollen data)", priority, untrusted=True,
                             on_pick=self.airing_marker(context.subject, key), source="google_pollen")]
