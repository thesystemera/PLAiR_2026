import httpx
from config.settings import settings
from services import log_service

class EventsService:
    def __init__(self):
        pass

    async def get_ticketmaster_events(self, city, country_code, start_date, end_date):
        url = "https://app.ticketmaster.com/discovery/v2/events.json"
        params = {
            "apikey": settings.TICKETMASTER_API_KEY,
            "city": city,
            "countryCode": country_code,
            "startDateTime": start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "endDateTime": end_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "size": 200,
            "sort": "date,asc"
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, params=params)
                response.raise_for_status()

            data = response.json()
            events = data.get("_embedded", {}).get("events", [])

            if not events:
                log_service.external("Events: No events found")
                return []

            unique_events = {}
            for event in events:
                key = (event.get('name'), event.get("_embedded", {}).get("venues", [{}])[0].get("name"))
                if key not in unique_events:
                    unique_events[key] = event

            log_service.external(f"Events: Found {len(unique_events)} unique events")

            formatted_events = []
            for event in unique_events.values():
                price_range = event.get('priceRanges', [{}])[0]
                formatted_event = {
                    "name": event.get('name', 'N/A'),
                    "date": event.get('dates', {}).get('start', {}).get('localDate', 'N/A'),
                    "time": event.get('dates', {}).get('start', {}).get('localTime', 'N/A'),
                    "venue": event.get("_embedded", {}).get("venues", [{}])[0].get("name", 'N/A'),
                    "description": (event.get('description') or event.get('info') or "No description available")[:500],
                    "url": event.get('url', 'N/A'),
                    "genre": event.get('classifications', [{}])[0].get('genre', {}).get('name', 'N/A'),
                    "subgenre": event.get('classifications', [{}])[0].get('subGenre', {}).get('name', 'N/A'),
                    "price_range": f"{price_range.get('min', 'N/A')} - {price_range.get('max', 'N/A')} {price_range.get('currency', '')}" if price_range else 'N/A',
                    "ticket_status": event.get('dates', {}).get('status', {}).get('code', 'N/A')
                }
                formatted_events.append(formatted_event)

            return formatted_events

        except Exception as e:
            log_service.error(f"An error occurred while fetching events: {str(e)}")
            return []