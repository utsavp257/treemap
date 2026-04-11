// src/services/weatherService.js

export async function fetchWeather(lat, lng) {
  const key = import.meta.env.VITE_OPENWEATHER_API_KEY
  const url = `https://api.openweathermap.org/data/2.5/weather?lat=${lat}&lon=${lng}&appid=${key}&units=metric`
  
  try {
    const res = await fetch(url)
    const d = await res.json()

    // d.rain?.["1h"] or d.rain?.["3h"] — may be undefined if no rain
    const rainMm1h = d.rain?.['1h'] ?? d.rain?.['3h'] ?? 0
    const rainMm7d = rainMm1h * 24 * 7  // rough estimate for demo

    return {
      rainMm7d: parseFloat(rainMm7d.toFixed(1)),
      sunHours7d: 38.0,           // Default for demo
      humidity: d.main.humidity,
      tempC: parseFloat(d.main.temp.toFixed(1)),
      droughtStress: rainMm7d < 5
    }
  } catch (err) {
    console.error("Weather fetch failed:", err)
    return { rainMm7d: 12, sunHours7d: 38, humidity: 68, tempC: 18.5, droughtStress: false }
  }
}
