"""和风天气数据源（主源，国内节点、延迟低）。

接口：小时天气预报 v1（官方主推，1km，全球）
    GET {host}/weather/v1/hourly/{lat}/{lon}?hours=72
    Header: X-QW-Api-Key: <key>

认证：本项目用 API KEY，走请求头 X-QW-Api-Key（**不要**再同时传 key= 查询参数，
     和风会判定为「同时使用多种认证方式」并拒绝）。后续如需切 JWT 只改本模块。

已实测确认（2026-10-07，厦门集美区）：
    · v1 路径可用，?hours=72 返回 72 条逐小时数据。
    · v1 字段：cloudCover(0~1)、humidity(0~1)、visibility(m)、
              temperature / dewPoint / precipitation.amount（均为 {value,unit}）、
              forecastTime（UTC，形如 2026-10-07T14:00Z）。
    · ❌ 和风**不提供分层云量**（低/中/高云），只有总云量 cloudCover。
    · ❌ 和风不提供 AOD 与总柱臭氧，这两项由 Open-Meteo 单独获取。
"""

from datetime import datetime, timezone

from .. import config, net
from .base import WeatherBundle

_SERIES_FIELDS = (
    "cloud_cover", "cloud_cover_low", "cloud_cover_mid", "cloud_cover_high",
    "visibility_km", "relative_humidity", "precipitation", "cloud_base_height",
)


def fetch(lat, lon, cache=None, forecast_days=3, timeout=None):
    key = config.SOURCES.get("qweather_key")
    host = config.SOURCES.get("qweather_host")
    if not key or not host:
        raise RuntimeError("未配置和风天气 Key/Host")

    if cache is not None:
        cached = cache.get_weather(lat, lon, source="qweather")
        if cached is not None:
            return WeatherBundle.from_dict(cached)

    base = host.strip().rstrip("/")
    if not base.startswith("http"):
        base = "https://" + base
    path = config.SOURCES.get("qweather_path", "/weather/v1/hourly/{lat}/{lon}")
    hours = int(config.SOURCES.get("qweather_hours", 72))
    # 和风要求经纬度最多两位小数（约 1.1 km，区级精度足够）
    url = base + path.format(lat=f"{lat:.2f}", lon=f"{lon:.2f}")
    data = net.http_get_json(
        url,
        params={"hours": hours, "localTime": "false"},
        timeout=timeout or config.NETWORK["weather_timeout_seconds"],
        headers={"X-QW-Api-Key": key},
    )

    bundle = _parse(data)
    if cache is not None:
        cache.set_weather(lat, lon, bundle)
    return bundle


def _parse(data):
    hours = data.get("hours") or data.get("hourly") or []
    if not hours:
        raise RuntimeError(f"和风返回中找不到逐小时数据：{list(data.keys())[:8]}")

    times = []
    series = {f: [] for f in _SERIES_FIELDS}
    for h in hours:
        rec = _extract(h)
        times.append(rec["time"])
        for f in _SERIES_FIELDS:
            series[f].append(rec[f])

    # 过滤掉全为 None 的字段（和风不返回分层云量，低/中/高云会在这里被丢掉）
    bundle = WeatherBundle(source="qweather")
    dropped = []
    for field, vals in series.items():
        if any(v is not None for v in vals):
            bundle.set_series(field, times, vals)
        else:
            dropped.append(field)

    notes = ["和风不提供 AOD 与总柱臭氧（由 Open-Meteo 单独获取）"]
    if "cloud_cover_low" in dropped:
        notes.append("⚠️ 和风不提供分层云量（低/中/高云），评分将缺少这三项因子")
    if dropped:
        notes.append("未返回的字段：" + ", ".join(dropped))
    bundle.note = "；".join(notes)
    bundle.degraded = True
    return bundle


def _extract(h):
    """从一个小时对象提取标准字段；兼容 v1（嵌套）与 v7（扁平）两种结构。"""
    if "forecastTime" in h or "cloudCover" in h:      # v1
        return {
            "time": _parse_time(h.get("forecastTime")),
            "cloud_cover": _scaled(h.get("cloudCover"), 100.0),     # 0~1 -> %
            "cloud_cover_low": None,                                # 和风无分层云量
            "cloud_cover_mid": None,
            "cloud_cover_high": None,
            "visibility_km": _scaled(h.get("visibility"), 0.001),   # m -> km
            "relative_humidity": _scaled(h.get("humidity"), 100.0),  # 0~1 -> %
            "precipitation": _num(_dig(h, "precipitation", "amount")),
            "cloud_base_height": _cloud_base(_num(h.get("temperature")),
                                             _num(h.get("dewPoint"))),
        }
    # v7（扁平、字符串数值）
    return {
        "time": _parse_time(h.get("fxTime") or h.get("time")),
        "cloud_cover": _num(h.get("cloud")),
        "cloud_cover_low": None,
        "cloud_cover_mid": None,
        "cloud_cover_high": None,
        "visibility_km": _num(h.get("vis")),
        "relative_humidity": _num(h.get("humidity")),
        "precipitation": _num(h.get("precip")),
        "cloud_base_height": _cloud_base(_num(h.get("temp")), _num(h.get("dew"))),
    }


def _num(v):
    """取数值；支持 {value,unit} 嵌套、数字、数字字符串。"""
    if isinstance(v, dict):
        v = v.get("value")
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _dig(d, *keys):
    for k in keys:
        if isinstance(d, dict):
            d = d.get(k)
        else:
            return None
    return d


def _scaled(v, factor):
    x = _num(v)
    return None if x is None else x * factor


def _cloud_base(temp, dew):
    """由温度-露点差估算云底高度（≈125 m/°C），仅作辅助。"""
    if temp is None or dew is None:
        return None
    return max(0.0, 125.0 * (temp - dew))


def _parse_time(s):
    """和风 v1 时间形如 2026-10-07T14:00Z，v7 形如 2026-10-07T22:00+08:00。"""
    if not s:
        return None
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
