"""Weather for Jarvis, from Open-Meteo (free, no API key).

A background thread refreshes the forecast every REFRESH_S seconds, so
asking Jarvis about the weather is instant and the HUD can show it too.
If the internet is down, the last forecast is kept and marked stale.
"""

import json
import threading
import time
import urllib.parse
import urllib.request

# Beersheba, Israel (looked up with Open-Meteo's geocoding API).
PLACE = "Beersheba"
LATITUDE = 31.25181
LONGITUDE = 34.7913

REFRESH_S = 15 * 60  # Open-Meteo updates its "current" values every 15 minutes
RETRY_S = 60         # after a failed fetch, try again sooner

URL = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode({
    "latitude": LATITUDE,
    "longitude": LONGITUDE,
    "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m,is_day",
    "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,sunrise,sunset",
    "timezone": "auto",
    "forecast_days": 2,
})

# WMO weather codes -> words Jarvis can say. https://open-meteo.com/en/docs
CONDITIONS = {
    0: "clear skies", 1: "mostly clear", 2: "partly cloudy", 3: "overcast",
    45: "foggy", 48: "freezing fog",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
    56: "freezing drizzle", 57: "heavy freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain",
    66: "freezing rain", 67: "heavy freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
    80: "light showers", 81: "showers", 82: "violent showers",
    85: "snow showers", 86: "heavy snow showers",
    95: "a thunderstorm", 96: "a thunderstorm with hail", 99: "a severe thunderstorm with hail",
}


def describe(code):
    return CONDITIONS.get(code, "unknown conditions")


class Weather:
    def __init__(self):
        self.data = None      # the latest forecast, simplified (see _simplify)
        self.fetched_at = 0   # time.time() of the last successful fetch
        threading.Thread(target=self._loop, daemon=True).start()

    @property
    def stale(self):
        return time.time() - self.fetched_at > 2 * REFRESH_S

    def _loop(self):
        while True:
            try:
                with urllib.request.urlopen(URL, timeout=15) as r:
                    self.data = _simplify(json.load(r))
                self.fetched_at = time.time()
                time.sleep(REFRESH_S)
            except Exception as e:  # offline, DNS failure, bad response...
                print(f"weather fetch failed: {e}")
                time.sleep(RETRY_S)

    def get(self):
        """The forecast for the HUD (None until the first fetch succeeds)."""
        if not self.data:
            return None
        return {**self.data, "place": PLACE, "stale": self.stale}


def _simplify(raw):
    c, d = raw["current"], raw["daily"]
    day = lambda i: {
        "condition": describe(d["weather_code"][i]),
        "code": d["weather_code"][i],
        "high": d["temperature_2m_max"][i],
        "low": d["temperature_2m_min"][i],
        "rain_chance": d["precipitation_probability_max"][i],
    }
    return {
        "temp": c["temperature_2m"],
        "feels_like": c["apparent_temperature"],
        "humidity": c["relative_humidity_2m"],
        "wind_kmh": c["wind_speed_10m"],
        "condition": describe(c["weather_code"]),
        "code": c["weather_code"],
        "is_day": bool(c["is_day"]),
        "sunrise": d["sunrise"][0][-5:],  # "2026-10-16T06:44" -> "06:44"
        "sunset": d["sunset"][0][-5:],
        "today": day(0),
        "tomorrow": day(1),
    }
