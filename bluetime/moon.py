"""月相与月面照明比例（离线，纯标准库）。

精度足够用于「满月冲淡蓝调纯净度」这一用途（照明比例误差几个百分点）。
"""

import math
from datetime import datetime, timezone

# 参考新月时刻（2000-01-06 18:14 UTC）的儒略日
_KNOWN_NEW_MOON_JD = 2451550.1
# 朔望月长度（天）
_SYNODIC_MONTH = 29.530588853

_PHASE_NAMES = [
    (0.0, "新月"), (1.84566, "娥眉月"), (5.53699, "上弦月"), (9.22831, "盈凸月"),
    (12.91963, "满月"), (16.61096, "亏凸月"), (20.30228, "下弦月"), (23.99361, "残月"),
    (27.68493, "新月"),
]


def _julian_day(dt_utc: datetime) -> float:
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


def moon_state(dt_utc: datetime):
    """返回 dict：月龄（天）、照明比例 0~1、相位名。

    照明比例 0 = 新月（无月光干扰），1 = 满月（干扰最强）。
    """
    jd = _julian_day(dt_utc)
    age = (jd - _KNOWN_NEW_MOON_JD) % _SYNODIC_MONTH
    illumination = (1.0 - math.cos(2.0 * math.pi * age / _SYNODIC_MONTH)) / 2.0

    name = _PHASE_NAMES[-1][1]
    for start, label in _PHASE_NAMES:
        if age >= start:
            name = label

    return {
        "age_days": round(age, 2),
        "illumination": round(illumination, 3),
        "phase_name": name,
    }


def moon_score(dt_utc: datetime) -> float:
    """把月相转成「对蓝调的干扰得分」：0 = 干扰最强（满月），1 = 无干扰（新月）。

    用于综合质量分；蓝度指数不受月相影响。
    """
    return 1.0 - moon_state(dt_utc)["illumination"]
