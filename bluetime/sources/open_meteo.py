"""Open-Meteo 数据源。

两个职责已**解耦**（因为主源改成和风后，气象与 AOD 来源不同）：
    fetch_weather()  气象：总/低/中/高云量、能见度、湿度、降水、云底高度
    fetch_aod()      AOD：只有 Open-Meteo 有，和风不提供，需单独短超时获取

两者都免费、无需 Key（非商业用途）。
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from .. import config, net
from .base import WeatherBundle

_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_AIR_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"

_FORECAST_HOURLY = ",".join([
    "cloud_cover", "cloud_cover_low", "cloud_cover_mid", "cloud_cover_high",
    "visibility", "relative_humidity_2m", "precipitation",
    "temperature_2m", "dew_point_2m", "boundary_layer_height",
])
_AIR_HOURLY = "aerosol_optical_depth,ozone"


def fetch_weather(lat, lon, cache=None, forecast_days=3, timeout=None):
    """取气象数据（不含 AOD）。"""
    if cache is not None:
        cached = cache.get_weather(lat, lon)
        if cached is not None:
            return WeatherBundle.from_dict(cached)

    timeout = timeout or config.NETWORK["weather_timeout_seconds"]
    data = net.http_get_json(_FORECAST_URL, {
        "latitude": round(lat, 5), "longitude": round(lon, 5),
        "hourly": _FORECAST_HOURLY,
        "timezone": "UTC", "forecast_days": forecast_days,
    }, timeout=timeout)

    bundle = _parse_weather(data)
    if cache is not None:
        cache.set_weather(lat, lon, bundle)
    return bundle


def fetch_aod(lat, lon, cache=None, forecast_days=3, timeout=None):
    """取 AOD（及其顺带的表层臭氧）。

    返回 dict：{times, aod550, ozone_surface, degraded, note}
    失败时抛异常，由上层决定是否降级（缺 AOD 会被评分引擎剔除并重新归一化权重）。
    """
    if cache is not None:
        hit = cache.get_json("aod", _aod_key(lat, lon))
        if hit is not None:
            return _from_cached(hit)

    timeout = timeout or config.NETWORK["aod_timeout_seconds"]
    data = net.http_get_json(_AIR_URL, {
        "latitude": round(lat, 5), "longitude": round(lon, 5),
        "hourly": _AIR_HOURLY,
        "timezone": "UTC", "forecast_days": forecast_days,
    }, timeout=timeout)

    hourly = data.get("hourly", {})
    times = [_parse_time(t) for t in hourly.get("time", [])]
    out = {
        "times": times,
        "aod550": [float(v) if v is not None else None
                   for v in hourly.get("aerosol_optical_depth", [])],
        # 注意：这里的 ozone 是「表层臭氧 μg/m³」，不是总柱臭氧，不能当 ozone_du 用
        "ozone_surface": [float(v) if v is not None else None
                          for v in hourly.get("ozone", [])],
        "degraded": False,
        "note": None,
    }
    if cache is not None:
        cache.set_json("aod", _aod_key(lat, lon),
                       {"times": [t.isoformat() for t in times],
                        "aod550": out["aod550"],
                        "ozone_surface": out["ozone_surface"]},
                       ttl=config.CACHE["aod_ttl"])
    return out


def _from_cached(hit):
    return {
        "times": [datetime.fromisoformat(t) for t in hit.get("times", [])],
        "aod550": hit.get("aod550", []),
        "ozone_surface": hit.get("ozone_surface", []),
        "degraded": False,
        "note": "AOD 来自缓存",
    }


def _aod_key(lat, lon):
    return f"{round(lat, 4)},{round(lon, 4)}"


def _parse_weather(fc):
    hourly = fc.get("hourly", {})
    times = [_parse_time(t) for t in hourly.get("time", [])]
    b = WeatherBundle(source="open_meteo")

    mapping = {
        "cloud_cover": "cloud_cover",
        "cloud_cover_low": "cloud_cover_low",
        "cloud_cover_mid": "cloud_cover_mid",
        "cloud_cover_high": "cloud_cover_high",
        "visibility_km": "visibility",
        "relative_humidity": "relative_humidity_2m",
        "precipitation": "precipitation",
    }
    for ours, theirs in mapping.items():
        vals = hourly.get(theirs)
        if vals:
            b.set_series(ours, times, [float(v) if v is not None else None for v in vals])

    # 能见度：Open-Meteo 单位为米，统一换算成公里
    if "visibility_km" in b._hourly:
        t, v = b._hourly["visibility_km"]
        b._hourly["visibility_km"] = (t, [(x / 1000.0) if x is not None else None
                                          for x in v])

    # 云底高度：由 温度-露点差 估算（≈125 m/°C），仅作辅助
    temps, dews = hourly.get("temperature_2m"), hourly.get("dew_point_2m")
    if temps and dews:
        b.set_series("cloud_base_height", times, [
            None if t_ is None or d_ is None else max(0.0, 125.0 * (t_ - d_))
            for t_, d_ in zip(temps, dews)])
    return b


def _parse_time(s):
    """解析 Open-Meteo 的 ISO 时间，统一为「带时区的 UTC」。"""
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
