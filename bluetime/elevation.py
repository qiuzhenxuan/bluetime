"""观测点海拔（Open-Meteo Elevation API，免费、无需 Key）。

海拔只用于修正**以可见地平线为基准的时刻**——即日出/日落：
站在高处时地平线下沉，日出更早、日落更晚。

    dip(度) ≈ 1.76′ × √h(m)      （h 为海拔米数）

**不修正蓝调窗口**：蓝调由「太阳几何高度角」定义（-5°~-7.5°），
与海拔无关，站在珠峰和海边是同一个太阳高度角。

海拔是静态量，永久缓存。
"""

import math

from . import config, net

_ELEV_URL = "https://api.open-meteo.com/v1/elevation"


def fetch(lat, lon, cache=None, timeout=None):
    """取海拔（米）。失败返回 None（调用方按 0 处理，即不做修正）。"""
    key = f"{round(lat, 4)},{round(lon, 4)}"
    if cache is not None:
        hit = cache.get_json("elevation", key)
        if hit is not None:
            return hit.get("elevation_m")

    try:
        data = net.http_get_json(
            _ELEV_URL,
            {"latitude": round(lat, 5), "longitude": round(lon, 5)},
            timeout=timeout or config.NETWORK["timeout_seconds"],
        )
        arr = data.get("elevation") or []
        if not arr or arr[0] is None:
            return None
        elev = float(arr[0])
    except Exception:
        return None

    if cache is not None:
        cache.set_json("elevation", key, {"elevation_m": elev}, ttl=0)   # 静态，永久
    return elev


def horizon_dip_deg(elevation_m):
    """地平线下沉角（度）。elevation_m 为 None 或 <=0 时返回 0。"""
    if elevation_m is None or elevation_m <= 0:
        return 0.0
    return (1.76 * math.sqrt(elevation_m)) / 60.0
