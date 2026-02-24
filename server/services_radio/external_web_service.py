import httpx
import datetime
from collections import defaultdict
from async_lru import alru_cache

from keys import api_secrets
from services import log_service

class WebService:
    def __init__(self):
        self.weather_cache = {}
        self.WEATHER_CACHE_DURATION = 3600

    @alru_cache(maxsize=100)
    async def retrieve_artist_biography(self, artist_name):
        async def get_wikidata_biography(w_id, w_client):
            wikidata_url = f"https://www.wikidata.org/wiki/Special:EntityData/{w_id}.json"
            try:
                w_response = await w_client.get(wikidata_url)
                w_response.raise_for_status()
                wikidata_data = w_response.json()
                entities = wikidata_data.get('entities', {})
                entity = entities.get(w_id, {})
                sitelinks = entity.get('sitelinks', {})
                wikipedia_link = sitelinks.get('enwiki', {}).get('url')

                if not wikipedia_link:
                    return f"No Wikipedia link found for artist '{artist_name}'"

                wiki_api_url = wikipedia_link.replace('https://en.wikipedia.org/wiki/', 'https://en.wikipedia.org/api/rest_v1/page/summary/')
                wiki_res = await w_client.get(wiki_api_url)
                wiki_res.raise_for_status()
                wiki_data = wiki_res.json()
                biography = wiki_data.get('extract')

                if not biography:
                    return f"No biography found on Wikipedia for artist '{artist_name}'"
                return biography
            except httpx.RequestError as err:
                log_service.error(f"External Web Service: Error fetching biography: {str(err)}")
                return f"Error retrieving biography for '{artist_name}': {str(err)}"

        try:
            base_url = "https://musicbrainz.org/ws/2/artist/"
            query_url = f"{base_url}?query={artist_name}&fmt=json"
            async with httpx.AsyncClient(headers={'User-Agent': 'PLAiR/1.0 ( mail@plair.com )'}) as client:
                response = await client.get(query_url)
                response.raise_for_status()
                artists = response.json().get('artists', [])

                if not artists:
                    return f"No artist found for '{artist_name}'"

                artist_id = artists[0]['id']
                artist_url = f"{base_url}{artist_id}?inc=url-rels&fmt=json"
                response = await client.get(artist_url)
                response.raise_for_status()
                artist_data = response.json()

            relations = artist_data.get('relations', [])
            target_wikidata_url = next((relation['url']['resource'] for relation in relations if relation['type'] == 'wikidata'), None)

            if not target_wikidata_url:
                return f"No Wikidata link found for artist '{artist_name}'"

            wikidata_id = target_wikidata_url.split('/')[-1]
            async with httpx.AsyncClient() as client:
                return await get_wikidata_biography(wikidata_id, client)
        except httpx.RequestError as e:
            log_service.error(f"External Web Service: Error in API request: {str(e)}")
            return f"Error retrieving information for '{artist_name}': {str(e)}"

    async def retrieve_weather_data(self, latitude, longitude, forecast_type='current'):
        cache_key = f"{latitude},{longitude},{forecast_type}"
        if cache_key in self.weather_cache:
            cached_time, cached_data = self.weather_cache[cache_key]
            if (datetime.datetime.now() - cached_time).total_seconds() < self.WEATHER_CACHE_DURATION:
                log_service.external("Weather: Retrieved weather data from cache")
                return cached_data

        weather_api_key = api_secrets.WEATHER_API_KEY
        base_url = "https://api.openweathermap.org/data/2.5/"
        endpoint = "weather" if forecast_type == 'current' else "forecast"
        endpoint_url = f"{base_url}{endpoint}?lat={float(latitude)}&lon={float(longitude)}&appid={weather_api_key}&units=metric"

        async with httpx.AsyncClient() as client:
            response = await client.get(endpoint_url)

        if response.status_code != 200:
            log_service.error("External Web Service: Failed to retrieve weather information.")
            return None

        weather_data = response.json()
        formatter = {
            'current': self.format_current_weather,
            'today': lambda data: self.format_daily_forecast(data, 0),
            'tomorrow': lambda data: self.format_daily_forecast(data, 1),
            'week': self.format_weekly_forecast
        }.get(forecast_type)

        if not formatter:
            log_service.error("External Web Service: Invalid forecast type.")
            return None

        formatted_weather = formatter(weather_data)
        self.weather_cache[cache_key] = (datetime.datetime.now(), formatted_weather)
        return formatted_weather

    @staticmethod
    def format_current_weather(current_data):
        try:
            main = current_data['main']
            wind = current_data['wind']
            weather = current_data['weather'][0]
            sys = current_data['sys']
            return (
                f"Current Weather in {current_data['name']}:\n"
                f"Temperature: {int(main['temp'])}°C, Feels Like: {int(main['feels_like'])}°C, "
                f"Humidity: {main['humidity']}%, Pressure: {main['pressure']} hPa, "
                f"Visibility: {current_data.get('visibility', 'N/A')} meters, "
                f"Wind Speed: {wind['speed']} m/s, Wind Direction: {wind['deg']}°, "
                f"Cloudiness: {current_data['clouds']['all']}%, "
                f"Weather: {weather['description']}, Icon: {weather['icon']}, "
                f"Sunrise: {datetime.datetime.fromtimestamp(sys['sunrise']).strftime('%H:%M:%S')}, "
                f"Sunset: {datetime.datetime.fromtimestamp(sys['sunset']).strftime('%H:%M:%S')}"
            )
        except KeyError as e:
            return f"Error retrieving current weather data: {e}"

    @staticmethod
    def format_daily_forecast(forecast_data, day_offset=0):
        target_date = (datetime.datetime.now() + datetime.timedelta(days=day_offset)).date()
        day_name = "Today's" if day_offset == 0 else "Tomorrow's"
        forecast_str = f"{day_name} Forecast:\n"
        for entry in forecast_data['list']:
            entry_time = datetime.datetime.fromtimestamp(entry['dt'])
            if entry_time.date() == target_date:
                main = entry['main']
                wind = entry['wind']
                weather = entry['weather'][0]
                forecast_str += (
                    f"{entry_time.strftime('%H:%M')} - "
                    f"Temp: {int(main['temp'])}°C, Feels Like: {int(main['feels_like'])}°C, "
                    f"Humidity: {main['humidity']}%, "
                    f"Wind Speed: {wind['speed']} m/s, Wind Direction: {wind['deg']}°, "
                    f"Weather: {weather['description']}\n"
                )
        return forecast_str if forecast_str != f"{day_name} Forecast:\n" else f"No data available for {day_name.lower()[:-2]}."

    @staticmethod
    def format_weekly_forecast(forecast_data):
        daily_summaries = defaultdict(lambda: {'temps': [], 'descs': []})
        for entry in forecast_data['list']:
            entry_time = datetime.datetime.fromtimestamp(entry['dt'])
            date = entry_time.date()
            daily_summaries[date]['temps'].append(entry['main']['temp'])
            daily_summaries[date]['descs'].append(entry['weather'][0]['description'])
        forecast_str = "Weekly Forecast:\n"
        for date, data in daily_summaries.items():
            high_temp = max(data['temps'])
            low_temp = min(data['temps'])
            common_desc = max(set(data['descs']), key=data['descs'].count)
            forecast_str += f"{date.strftime('%A, %B %d')} - High: {int(high_temp)}°C, Low: {int(low_temp)}°C, Weather: {common_desc}\n"
        return forecast_str