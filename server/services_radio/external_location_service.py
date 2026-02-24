import asyncio
import googlemaps
from config.settings import settings
from services import log_service

class LocationService:
    def __init__(self):
        self.gmaps = googlemaps.Client(key=settings.GOOGLE_PLACES_API_KEY)
        log_service.system("Location Service Initialized")

    async def get_nearby_places(self, query, location, radius=1500, max_results=5):
        try:
            places_result = await asyncio.to_thread(
                self.gmaps.places_nearby,  # type: ignore
                location=location,
                keyword=query,
                radius=radius,
                rank_by="prominence"
            )

            results = []
            for place in places_result.get('results', [])[:max_results]:
                place_details_result = await asyncio.to_thread(
                    self.gmaps.place,  # type: ignore
                    place['place_id'],
                    fields=[
                        'name', 'formatted_address', 'formatted_phone_number', 'website',
                        'rating', 'user_ratings_total', 'reviews', 'opening_hours', 'price_level'
                    ]
                )
                place_details = place_details_result.get('result', {})

                results.append({
                    'name': place_details.get('name'),
                    'address': place_details.get('formatted_address'),
                    'phone': place_details.get('formatted_phone_number'),
                    'website': place_details.get('website'),
                    'rating': place_details.get('rating'),
                    'user_ratings_total': place_details.get('user_ratings_total'),
                    'reviews': place_details.get('reviews', [])[:2],
                    'types': place.get('types'),
                    'open_now': place_details.get('opening_hours', {}).get('open_now'),
                    'opening_hours': place_details.get('opening_hours', {}).get('weekday_text'),
                    'price_level': place_details.get('price_level')
                })

            return results

        except Exception as e:
            log_service.error(f"Error in nearby search: {e}")
            return []

    async def get_location_search_report(self, query, location):
        search_terms = query.replace("nearby", "").replace("around", "").strip()
        results = await self.get_nearby_places(search_terms, location)

        if not results:
            return f"No results found for '{search_terms}' near the specified location."

        report = f"Search Results for '{search_terms}':\n\n"
        for i, place in enumerate(results, 1):
            name_with_link = f"<a href='{place['website']}' target='_blank'>{place['name']}</a>" if place['website'] else place['name']
            report += f"{i}. {name_with_link}\n"
            report += f"   Address: {place['address']}\n"
            if place['phone']:
                report += f"   Phone: {place['phone']}\n"
            if place['rating']:
                report += f"   Rating: {place['rating']}/5 ({place['user_ratings_total']} reviews)\n"
            report += f"   Types: {', '.join(place['types'])}\n"
            report += f"   Open now: {'Yes' if place['open_now'] else 'No'}\n"
            if place['opening_hours']:
                report += "   Opening Hours:\n"
                for hours in place['opening_hours']:
                    report += f"      {hours}\n"
            if place['price_level']:
                report += f"   Price Level: {'$' * place['price_level']}\n"
            if place['reviews']:
                report += "   Recent Reviews:\n"
                for review in place['reviews']:
                    report += f"      - {review['text'][:100]}... (Rating: {review['rating']}/5)\n"
            report += "\n"

        return report