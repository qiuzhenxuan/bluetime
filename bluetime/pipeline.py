"""流程编排：把一个「地点 + 日期」算成完整的蓝调报告。

这是云函数唯一需要调用的入口（compute_day）。
内置「首个用户触发计算 + 缓存」策略：
    静态项（地形剖面）永久缓存；动态项（气象）按 TTL 缓存；
    并发时通过 cache.try_acquire 做单飞，避免同一城市被重复计算。
"""

from datetime import datetime, timedelta, timezone

from . import cache as cache_mod
from . import config, elevation as elevation_mod, horizon, moon, score, solar
from .sources import base as sources_base

_SAMPLES_PER_WINDOW = 12
_LOCK_TTL = 45          # 单飞锁最长持有秒数


def get_or_compute(lat, lon, local_date, tz_offset=None, with_terrain=True,
                   cache=None, use_cache=True):
    """按「首个访客触发计算并缓存，其余命中缓存」的策略取结果。

    返回 (result, meta)：
        result  完整报告；若正在被他人计算则为 None
        meta    {from_cache, computing, cache_key}
    并发时通过单飞锁保证同一「城市+日期」只算一次，
    未被抢到锁的调用返回 computing=True，前端可提示「正在计算」并稍后重试。
    """
    if cache is None and use_cache:
        cache = cache_mod.Cache()
    if cache is None:
        return compute_day(lat, lon, local_date, tz_offset=tz_offset,
                           with_terrain=with_terrain, cache=None), \
               {"from_cache": False, "computing": False, "cache_key": None}

    key = f"{round(lat, 3)},{round(lon, 3)}|{local_date}"
    hit = cache.get_json("day", key)
    if hit is not None:
        return hit, {"from_cache": True, "computing": False, "cache_key": key}

    if not cache.try_acquire("day", key, lock_ttl=_LOCK_TTL):
        return None, {"from_cache": False, "computing": True, "cache_key": key}

    try:
        result = compute_day(lat, lon, local_date, tz_offset=tz_offset,
                             with_terrain=with_terrain, cache=cache)
        cache.release("day", key, obj=result, ttl=_ttl_for(local_date, tz_offset))
        return result, {"from_cache": False, "computing": False, "cache_key": key}
    except Exception:
        cache.release("day", key, obj=None)   # 失败则释放占位，下次可重算
        raise


def _ttl_for(local_date, tz_offset):
    """当日结果用短 TTL（预报更新快），未来日期用长 TTL。"""
    if tz_offset is None:
        return config.CACHE["future_ttl"]
    now_local = (datetime.now(timezone.utc) + timedelta(hours=tz_offset)).date()
    return (config.CACHE["today_ttl"] if str(local_date) == str(now_local)
            else config.CACHE["future_ttl"])


def compute_day(lat, lon, local_date, tz_offset=None, with_terrain=True,
                use_cache=True, cache=None, warnings=None, elevation=None):
    """计算某地某本地日期的早晚蓝调报告。

    elevation: 观测点海拔（米）。None 时自动从 Open-Meteo 海拔接口取（永久缓存）；
               取不到则按 0 处理（不做地平线下沉修正）。海拔只影响日出/日落，
               **不影响蓝调窗口**（后者由太阳几何高度角定义）。
    """
    warnings = warnings if warnings is not None else []
    cache = cache if cache is not None else (cache_mod.Cache() if use_cache else None)

    if tz_offset is None:
        # 中国境内自动取 UTC+8（中国只用单一区时，按经度推会给西部城市错 1~2 小时）
        tz_offset = solar.local_timezone_offset(lon, lat)

    # --- 海拔 → 地平线下沉（只修正日出/日落）---
    if elevation is None:
        elevation = elevation_mod.fetch(lat, lon, cache=cache)
    if elevation is None:
        warnings.append("海拔获取失败，日出/日落按海平面处理（未做地平线下沉修正）")
    dip = elevation_mod.horizon_dip_deg(elevation)

    windows = solar.blue_hour_windows(
        lat, lon, local_date, tz_offset=tz_offset,
        core_min=config.BLUE_HOUR["core_min"],
        core_max=config.BLUE_HOUR["core_max"],
        horizon_dip=dip)

    # --- 地形剖面：静态，永久缓存 ---
    hz = {"profile": None, "considered": False, "source": "flat", "note": "未启用"}
    if with_terrain:
        hz = _horizon_cached(cache, lat, lon, warnings)

    # --- 气象 + AOD：并发获取；气象主备切换，AOD 短超时可缺 ---
    weather, w_warn = sources_base.fetch_all(lat, lon, cache=cache)
    warnings.extend(w_warn)

    result = {
        "lat": lat,
        "lon": lon,
        "local_date": str(local_date),
        "tz_offset": tz_offset,
        "elevation_m": None if elevation is None else round(float(elevation), 1),
        "horizon_dip_deg": round(dip, 3),
        "weather_source": weather.source if weather else None,
        "terrain": {
            "considered": bool(hz.get("considered")),
            "source": hz.get("source"),
            "note": hz.get("note"),
            "points": len(hz.get("profile") or []),
        },
        "warnings": warnings,
        "windows": {},
    }

    for name in ("morning", "evening"):
        w = windows.get(name)
        if w is None:
            result["windows"][name] = None
            warnings.append(f"{name} 无蓝调窗口（极昼/极夜或高纬夏季）")
            continue
        result["windows"][name] = _score_window(
            name, w, lat, lon, tz_offset, hz, weather, warnings)

    # 供前端展示的时间轴
    result["context"] = {
        "sunrise": _iso(windows["sunrise"], tz_offset),
        "sunset": _iso(windows["sunset"], tz_offset),
        "civil_dusk": _iso(windows["civil_dusk"], tz_offset),
        "civil_dawn": _iso(windows["civil_dawn"], tz_offset),
        "nautical_dusk": _iso(windows["nautical_dusk"], tz_offset),
        "astronomical_dusk": _iso(windows["astronomical_dusk"], tz_offset),
    }
    return result


def _score_window(name, w, lat, lon, tz_offset, hz, weather, warnings):
    start, end = w["start_utc"], w["end_utc"]

    # 窗口内均匀采样，用于地形可见性与因子取值
    samples = []
    for i in range(_SAMPLES_PER_WINDOW):
        t = start + (end - start) * (i / (_SAMPLES_PER_WINDOW - 1))
        alt, az = solar.solar_position(lat, lon, t)
        samples.append((t, alt, az))

    terrain = horizon.evaluate_window(
        hz.get("profile"), bool(hz.get("considered")), samples,
        band_top=config.TERRAIN["glow_band_top"],
        sample_azimuth=w["mid_azimuth"])

    mid = start + (end - start) / 2
    values = {}
    if weather is not None:
        values = {
            "cloud_cover": weather.at("cloud_cover", mid),
            "cloud_cover_low": weather.at("cloud_cover_low", mid),
            "cloud_cover_mid": weather.at("cloud_cover_mid", mid),
            "cloud_cover_high": weather.at("cloud_cover_high", mid),
            "visibility_km": weather.at("visibility_km", mid),
            "aod550": weather.at("aod550", mid),
            "ozone_du": weather.at("ozone_du", mid),   # 主源不提供 → 会被自动剔除
            "relative_humidity": weather.at("relative_humidity", mid),
            "cloud_base_height": weather.at("cloud_base_height", mid),
        }
    else:
        warnings.append(f"{name}：无气象数据，仅输出几何窗口")

    precip = values.get("precipitation")
    if precip is None and weather is not None:
        precip = weather.at("precipitation", mid)

    m_state = moon.moon_state(mid.replace(tzinfo=timezone.utc))
    m_score = 1.0 - m_state["illumination"]

    scores = score.evaluate(
        factor_values=values,
        window_minutes=w["minutes"],
        terrain_score=terrain["score"],
        moon_score=m_score,
        precipitation=precip,
    )

    if "ozone_du" in scores["blue_index"]["missing_factors"]:
        warnings.append(f"{name}：总柱臭氧缺失（未接 CAMS），已按剩余因子重新归一化")

    return {
        "start_local": _iso(start, tz_offset),
        "end_local": _iso(end, tz_offset),
        "minutes": w["minutes"],
        "sun_azimuth": w["mid_azimuth"],
        "sun_altitude": w["mid_altitude"],
        "terrain": terrain,
        "moon": m_state,
        "weather": {
            "source": weather.source if weather else None,
            "cloud_cover": _r(values.get("cloud_cover")),
            "cloud_cover_low": _r(values.get("cloud_cover_low")),
            "cloud_cover_mid": _r(values.get("cloud_cover_mid")),
            "cloud_cover_high": _r(values.get("cloud_cover_high")),
            "visibility_km": _r(values.get("visibility_km")),
            "aod550": _r(values.get("aod550")),
            "ozone_du": _r(values.get("ozone_du")),
            "relative_humidity": _r(values.get("relative_humidity")),
            "cloud_base_height": _r(values.get("cloud_base_height")),
            "precipitation": _r(precip),
        },
        "blue_index": scores["blue_index"],
        "quality": scores["quality"],
    }


# ---------- 缓存辅助 ----------

def _horizon_cached(cache, lat, lon, warnings):
    key = f"{round(lat, 4)},{round(lon, 4)}"
    if cache is not None:
        hit = cache.get_json("horizon", key)
        if hit is not None:
            return hit
    hz = horizon.fetch_horizon_profile(lat, lon)
    if not hz.get("considered"):
        warnings.append(f"地形遮挡未生效（{hz.get('note')}），已按直地平线处理")
    if cache is not None:
        cache.set_json("horizon", key, hz, ttl=0)     # 静态，永久
    return hz


def _iso(dt_utc, tz_offset):
    if dt_utc is None:
        return None
    return (dt_utc + timedelta(hours=tz_offset)).strftime("%Y-%m-%d %H:%M:%S")


def _r(v, nd=2):
    return None if v is None else round(v, nd)
