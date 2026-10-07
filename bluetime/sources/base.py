"""数据源抽象与标准化。

统一字段（缺省为 None）：
    cloud_cover / cloud_cover_low / cloud_cover_mid / cloud_cover_high   %
    visibility_km                                                        km
    relative_humidity                                                    %
    precipitation                                                        mm/h
    cloud_base_height                                                    m
    aod550                                                               -
    ozone_du                                                             总柱臭氧（Dobson）；Open-Meteo 不提供
"""

from datetime import datetime, timezone

from .. import config


class WeatherBundle:
    """一次取数的标准化结果。"""

    FIELDS = (
        "cloud_cover", "cloud_cover_low", "cloud_cover_mid", "cloud_cover_high",
        "visibility_km", "relative_humidity", "precipitation",
        "cloud_base_height", "aod550", "ozone_du",
    )

    def __init__(self, source, fetched_at=None, **kwargs):
        self.source = source
        self.fetched_at = fetched_at or datetime.now(timezone.utc)
        self.degraded = False
        self.note = None
        self._hourly = {}       # field -> (times[], values[]) 供插值
        for f in self.FIELDS:
            setattr(self, f, kwargs.get(f))

    def set_series(self, field, times, values):
        """登记逐小时序列，之后可用 at() 插值到任意时刻。"""
        self._hourly[field] = (times, values)

    def at(self, field, target_utc):
        """把逐小时序列线性插值到目标 UTC 时刻。"""
        series = self._hourly.get(field)
        if not series:
            return getattr(self, field, None)
        times, values = series
        return interp_hourly(times, values, target_utc)

    def as_dict(self, target_utc=None):
        out = {"source": self.source, "degraded": self.degraded, "note": self.note}
        for f in self.FIELDS:
            out[f] = self.at(f, target_utc) if target_utc else getattr(self, f)
        return out

    @classmethod
    def from_dict(cls, blob):
        """从缓存结构还原（含逐小时序列）。"""
        b = cls(source=blob.get("source", "cache"))
        b.degraded = bool(blob.get("degraded"))
        b.note = blob.get("note")
        for f in cls.FIELDS:
            setattr(b, f, blob.get(f))
        for field, series in (blob.get("_series") or {}).items():
            times = [datetime.fromisoformat(t) for t in series.get("times", [])]
            b.set_series(field, times, series.get("values", []))
        return b


def interp_hourly(times, values, target):
    """在逐小时序列上做线性插值；超出两端则取端点值。"""
    if not times or not values:
        return None
    if target <= times[0]:
        return values[0]
    if target >= times[-1]:
        return values[-1]
    for i in range(len(times) - 1):
        t0, t1 = times[i], times[i + 1]
        if t0 <= target <= t1:
            v0, v1 = values[i], values[i + 1]
            if v0 is None or v1 is None:
                return v0 if v0 is not None else v1
            span = (t1 - t0).total_seconds()
            if span <= 0:
                return v0
            ratio = (target - t0).total_seconds() / span
            return v0 + (v1 - v0) * ratio
    return values[-1]


def fetch_all(lat, lon, cache=None, forecast_days=3):
    """取全部气象与大气成分数据，所有请求**并发**发出。

    气象：主源优先；主源缺失的字段用备用源补齐
         （和风不提供分层云量，低/中/高云固定由 Open-Meteo 补）。
    AOD：和风不提供，固定由 Open-Meteo 空气质量接口单独获取，且用短超时（默认 3 s）。
         超时/失败不致命——评分引擎会剔除 AOD 因子并重新归一化权重。

    返回 (WeatherBundle, warnings:list[str])
    """
    from concurrent.futures import ThreadPoolExecutor

    from . import open_meteo

    warnings = []
    need_fill = config.SOURCES.get("primary", "qweather") != "open_meteo"

    with ThreadPoolExecutor(max_workers=3) as pool:
        f_weather = pool.submit(_fetch_weather, lat, lon, cache, forecast_days)
        f_aod = pool.submit(open_meteo.fetch_aod, lat, lon, cache, forecast_days,
                            config.NETWORK["aod_timeout_seconds"])
        # 主源不提供分层云量，提前并发拉一份 Open-Meteo 用于补齐
        f_fill = (pool.submit(open_meteo.fetch_weather, lat, lon, cache, forecast_days)
                  if need_fill else None)

        bundle, w_warn = f_weather.result()     # 气象失败则整体失败，向上抛
        warnings.extend(w_warn)

        if f_fill is not None:
            try:
                filled = _fill_missing(bundle, f_fill.result())
                if filled:
                    warnings.append("以下字段主源不提供，已用 Open-Meteo 补齐："
                                    + ", ".join(filled))
            except Exception as e:
                warnings.append(f"Open-Meteo 补齐失败，主源缺少的字段将缺失：{str(e)[:120]}")

        try:
            aod = f_aod.result()
            if aod.get("times"):
                bundle.set_series("aod550", aod["times"], aod["aod550"])
        except Exception as e:
            warnings.append(f"AOD 获取失败，已剔除该因子：{str(e)[:120]}")
    return bundle, warnings


def _fill_missing(dst, src):
    """把 src 有、dst 没有的逐小时字段补进 dst（dst 优先，只补缺口）。"""
    filled = []
    for f in src.FIELDS:
        if f in dst._hourly or f not in src._hourly:
            continue
        times, values = src._hourly[f]
        dst.set_series(f, times, values)
        filled.append(f)
    if filled and dst.source != src.source:
        dst.source = f"{dst.source} + {src.source}(补齐)"
    return filled


def _fetch_weather(lat, lon, cache, forecast_days):
    """按配置的主/备源取气象，失败自动降级。返回 (bundle, warnings)。"""
    from . import open_meteo, qweather

    order = [config.SOURCES.get("primary", "qweather")]
    fallback = config.SOURCES.get("fallback")
    if fallback and fallback not in order:
        order.append(fallback)

    warnings = []
    for name in order:
        try:
            if name == "qweather":
                return qweather.fetch(lat, lon, cache=cache,
                                      forecast_days=forecast_days), warnings
            if name == "open_meteo":
                return open_meteo.fetch_weather(lat, lon, cache=cache,
                                                forecast_days=forecast_days), warnings
            warnings.append(f"未知数据源：{name}")
        except Exception as e:
            warnings.append(f"数据源 {name} 不可用，已降级：{str(e)[:120]}")
    raise RuntimeError("所有气象数据源均不可用：" + "；".join(warnings))
