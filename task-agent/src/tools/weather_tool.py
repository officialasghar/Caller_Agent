import logging

import aiohttp
from livekit.agents import RunContext, function_tool

logger = logging.getLogger("agent")

WEATHER_CODES = {
    0: "clear sky", 1: "mostly clear", 2: "partly cloudy", 3: "overcast",
    45: "foggy", 48: "foggy", 51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain",
    71: "light snow", 73: "snow", 75: "heavy snow",
    80: "rain showers", 81: "rain showers", 82: "heavy rain showers",
    95: "a thunderstorm", 96: "a thunderstorm with hail", 99: "a thunderstorm with hail",
}


@function_tool
async def lookup_weather(context: RunContext, location: str):
    """Use this tool to look up the current weather in a city.

    If the location can't be found, tell the user the weather is unavailable for it.

    Args:
        location: The city name, for example Lahore or London.
    """
    logger.info(f"Looking up weather for {location}")

    try:
        timeout = aiohttp.ClientTimeout(total=8)
        async with aiohttp.ClientSession(timeout=timeout) as http:
            async with http.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": location, "count": 1},
            ) as resp:
                geo = await resp.json()

            results = geo.get("results")
            if not results:
                return f"Weather is unavailable because {location} was not found."
            place = results[0]

            async with http.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": place["latitude"],
                    "longitude": place["longitude"],
                    "current": "temperature_2m,apparent_temperature,relative_humidity_2m,wind_speed_10m,weather_code",
                    "timezone": "auto",
                },
            ) as resp:
                data = await resp.json()

        current = data["current"]
        condition = WEATHER_CODES.get(current["weather_code"], "unclear conditions")

        return (
            f"In {place['name']}, it is {condition}. "
            f"Temperature is {round(current['temperature_2m'])} degrees Celsius, "
            f"feels like {round(current['apparent_temperature'])}. "
            f"Humidity is {current['relative_humidity_2m']} percent, "
            f"and wind is {round(current['wind_speed_10m'])} kilometers per hour."
        )
    except Exception as e:
        logger.error(f"Weather lookup failed: {e}")
        return "The weather service is not responding right now."