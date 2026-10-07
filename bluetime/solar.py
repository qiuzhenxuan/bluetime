"""太阳几何引擎（离线，纯标准库）。

算法：NOAA Solar Calculator 公式（精度约 0.01°），配合二分法求解
太阳高度角穿越指定角度的时刻，从而得到蓝调窗口起止与时长。

所有内部计算在 UTC 下完成，仅在输出时转本地时间。
"""

import math
from datetime import datetime, timedelta, timezone

_DEG = math.pi / 180.0

# 日出日落标准修正：含大气折射的日面边缘触地平线（太阳中心高度角）
SUNRISE_ALT = -0.833
# 三档暮光
CIVIL_TWILIGHT_ALT = -6.0
NAUTICAL_TWILIGHT_ALT = -12.0
ASTRONOMICAL_TWILIGHT_ALT = -18.0


def _julian_day(dt_utc: datetime) -> float:
    """UTC 时刻 -> 儒略日。"""
    y, m = dt_utc.year, dt_utc.month
    d = dt_utc.day + (dt_utc.hour + dt_utc.minute / 60.0
                      + (dt_utc.second + dt_utc.microsecond / 1e6) / 3600.0) / 24.0
    if m <= 2:
        y -= 1
        m += 12
    a = y // 100
    b = 2 - a + a // 4
    return (math.floor(365.25 * (y + 4716)) + math.floor(30.6001 * (m + 1))
            + d + b - 1524.5)


def _solar_declination_and_eot(jd: float):
    """返回 (太阳赤纬 deg, 均时差 minutes)。"""
    t = (jd - 2451545.0) / 36525.0

    # 几何平均黄经
    l0 = (280.46646 + t * (36000.76983 + t * 0.0003032)) % 360.0
    # 几何平均近点角
    m = 357.52911 + t * (35999.05029 - 0.0001537 * t)
    # 地球轨道偏心率
    e = 0.016708634 - t * (0.000042037 + 0.0000001267 * t)
    # 中心差
    c = (math.sin(m * _DEG) * (1.914602 - t * (0.004817 + 0.000014 * t))
         + math.sin(2 * m * _DEG) * (0.019993 - 0.000101 * t)
         + math.sin(3 * m * _DEG) * 0.000289)
    true_lon = l0 + c

    # 章动与光行差修正后的视黄经
    omega = 125.04 - 1934.136 * t
    app_lon = true_lon - 0.00569 - 0.00478 * math.sin(omega * _DEG)

    # 黄赤交角（含章动修正）
    eps0 = 23.0 + (26.0 + (21.448 - t * (46.815 + t * (0.00059 - t * 0.001813))) / 60.0) / 60.0
    eps = eps0 + 0.00256 * math.cos(omega * _DEG)

    # 赤纬
    decl = math.asin(math.sin(eps * _DEG) * math.sin(app_lon * _DEG)) / _DEG

    # 均时差（minutes）
    y = math.tan(eps * _DEG / 2.0) ** 2
    eot = 4.0 * (
        y * math.sin(2 * l0 * _DEG)
        - 2 * e * math.sin(m * _DEG)
        + 4 * e * y * math.sin(m * _DEG) * math.cos(2 * l0 * _DEG)
        - 0.5 * y * y * math.sin(4 * l0 * _DEG)
        - 1.25 * e * e * math.sin(2 * m * _DEG)
    ) / _DEG

    return decl, eot


def solar_position(lat: float, lon: float, dt_utc: datetime):
    """给定经纬度与 UTC 时刻，返回 (太阳高度角 deg, 太阳方位角 deg)。

    方位角约定：正北为 0°，顺时针增加（东 90°、南 180°、西 270°）。
    """
    jd = _julian_day(dt_utc)
    decl, eot = _solar_declination_and_eot(jd)

    # 真太阳时（小时）与太阳时角
    utc_hours = dt_utc.hour + dt_utc.minute / 60.0 + (
        dt_utc.second + dt_utc.microsecond / 1e6) / 3600.0
    true_solar_time = utc_hours + lon / 15.0 + eot / 60.0
    # 时角必须归一化到 (-180°, 180°]：负值表示上午（太阳偏东），正值表示下午（偏西）。
    # 否则跨 UTC 日界时（如北京早晨）会被误判为下午，方位角朝反。
    hour_angle = 15.0 * (true_solar_time - 12.0)
    hour_angle = (hour_angle + 180.0) % 360.0 - 180.0

    lat_r, decl_r, ha_r = lat * _DEG, decl * _DEG, hour_angle * _DEG
    sin_alt = (math.sin(lat_r) * math.sin(decl_r)
               + math.cos(lat_r) * math.cos(decl_r) * math.cos(ha_r))
    sin_alt = max(-1.0, min(1.0, sin_alt))
    alt = math.asin(sin_alt) / _DEG

    cos_az = (math.sin(decl_r) - math.sin(lat_r) * sin_alt) / (
        math.cos(lat_r) * math.cos(math.asin(sin_alt)))
    cos_az = max(-1.0, min(1.0, cos_az))
    az = math.acos(cos_az) / _DEG
    if hour_angle > 0:      # 午后：方位角转到 180°~360°
        az = 360.0 - az
    return alt, az


def _bisect_altitude(lat, lon, t_lo, t_hi, target):
    """在 [t_lo, t_hi] 内二分求解 高度角 = target 的时刻。"""
    f_lo = solar_position(lat, lon, t_lo)[0] - target
    for _ in range(50):
        t_mid = t_lo + (t_hi - t_lo) / 2
        f_mid = solar_position(lat, lon, t_mid)[0] - target
        if f_lo * f_mid <= 0:
            t_hi = t_mid
        else:
            t_lo = t_mid
            f_lo = f_mid
    return t_lo + (t_hi - t_lo) / 2


def _crossings(lat, lon, start_utc, target, step_minutes=10):
    """在 [start_utc, start_utc+24h) 内找出所有高度角穿越 target 的时刻。

    返回 [(时刻, 'rising'|'setting'), ...]，按时间升序。
    """
    out = []
    t = start_utc
    end = start_utc + timedelta(days=1)
    prev_t, prev_a = t, solar_position(lat, lon, t)[0]
    t = t + timedelta(minutes=step_minutes)
    while t <= end:
        a = solar_position(lat, lon, t)[0]
        if (prev_a - target) * (a - target) < 0:
            cross = _bisect_altitude(lat, lon, prev_t, t, target)
            direction = "rising" if a > prev_a else "setting"
            out.append((cross, direction))
        prev_t, prev_a = t, a
        t = t + timedelta(minutes=step_minutes)
    return out


def _pick(crossings, direction):
    """从穿越列表中取出指定方向的一个。"""
    for t, d in crossings:
        if d == direction:
            return t
    return None


# 中国（含港澳台）大致边界：lat_min, lat_max, lon_min, lon_max
_CHINA_BBOX = (18.0, 53.6, 73.0, 135.5)


def local_timezone_offset(lon: float, lat: float = None) -> float:
    """推算本地时区偏移（小时）。用于调用方未显式给时区时。

    中国境内（含港澳台）**一律返回 +8**：中国陆地横跨约 62 个经度但只用一个时区，
    若按经度推算，拉萨会得到 +6、乌鲁木齐 +6、三亚 +7，本地时刻会错 1~2 小时。
    境外按经度粗略推算（每 15° 一小时），未考虑夏令时。
    """
    if lat is not None:
        lat_lo, lat_hi, lon_lo, lon_hi = _CHINA_BBOX
        if lat_lo <= lat <= lat_hi and lon_lo <= lon <= lon_hi:
            return 8.0
    return round(lon / 15.0)


def blue_hour_windows(lat, lon, local_date, tz_offset=None,
                      core_min=-6.0, core_max=-4.0, horizon_dip=0.0):
    """计算某地某本地日期的蓝调窗口。

    lat/lon: 纬度/经度（度，东经为正）
    local_date: datetime.date，本地日期
    tz_offset: 本地时区相对 UTC 的小时数；None 时按经度推算
    horizon_dip: 地平线下沉角（度，由海拔决定）。**只修正日出/日落**——
        站在高处时可见地平线下沉，日出更早、日落更晚；
        蓝调窗口由太阳几何高度角定义，与海拔无关，故不受其影响。

    返回 dict：
        morning / evening 各含 start / end / minutes / mid_altitude / mid_azimuth
        以及 sunrise / sunset / civil/nautical/astronomical twilight 供前端展示
        若某事件不存在（极昼/极夜），对应值为 None
    """
    if tz_offset is None:
        tz_offset = local_timezone_offset(lon, lat)

    # 本地日 00:00 对应的 UTC 时刻
    day_start_utc = (datetime(local_date.year, local_date.month, local_date.day,
                              tzinfo=timezone.utc)
                     - timedelta(hours=tz_offset))

    c_min = _crossings(lat, lon, day_start_utc, core_min)   # -6° 穿越
    c_max = _crossings(lat, lon, day_start_utc, core_max)   # -4° 穿越
    # 日出/日落含地平线下沉修正；暮光（-6/-12/-18）是几何定义，不加
    c_sun = _crossings(lat, lon, day_start_utc, SUNRISE_ALT - horizon_dip)
    c_civil = _crossings(lat, lon, day_start_utc, CIVIL_TWILIGHT_ALT)
    c_naut = _crossings(lat, lon, day_start_utc, NAUTICAL_TWILIGHT_ALT)
    c_astro = _crossings(lat, lon, day_start_utc, ASTRONOMICAL_TWILIGHT_ALT)

    def _window(start, end):
        if start is None or end is None:
            return None
        minutes = (end - start).total_seconds() / 60.0
        if minutes <= 0:
            return None
        mid = start + (end - start) / 2
        alt, az = solar_position(lat, lon, mid)
        return {
            "start_utc": start,
            "end_utc": end,
            "minutes": round(minutes, 1),
            "mid_altitude": round(alt, 2),
            "mid_azimuth": round(az, 1),
        }

    # 早晨：高度角从 -6° 升到 -4°
    morning = _window(_pick(c_min, "rising"), _pick(c_max, "rising"))
    # 傍晚：高度角从 -4° 降到 -6°
    evening = _window(_pick(c_max, "setting"), _pick(c_min, "setting"))

    return {
        "lat": lat,
        "lon": lon,
        "local_date": local_date,
        "tz_offset": tz_offset,
        "morning": morning,
        "evening": evening,
        "sunrise": _pick(c_sun, "rising"),
        "sunset": _pick(c_sun, "setting"),
        "civil_dusk": _pick(c_civil, "setting"),
        "nautical_dusk": _pick(c_naut, "setting"),
        "astronomical_dusk": _pick(c_astro, "setting"),
        "civil_dawn": _pick(c_civil, "rising"),
    }


def to_local(dt_utc, tz_offset):
    """UTC 时刻 -> 本地时刻（返回带该偏移的 aware datetime）。"""
    if dt_utc is None:
        return None
    return dt_utc + timedelta(hours=tz_offset)
